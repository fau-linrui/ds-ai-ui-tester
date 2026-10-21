"""Shared structural and semantic validation for case authors and the compiler."""
from __future__ import annotations

import json
import keyword
import re
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

TRIGGER_ACTIONS = {"goto", "click", "fill", "select", "check", "press", "raw", "call"}
DIRECT_ACTIONS = {"click", "fill", "select", "check", "press", "waitFor", "raw"}
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


class CaseValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    path = Path(__file__).resolve().parents[1] / "spec" / "case.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def read_case(path: Path) -> dict:
    def reject_constant(value: str):
        raise ValueError(f"JSON contains a non-finite number: {value}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


def is_pom_case(case: dict, require_pom: bool = False) -> bool:
    return bool(require_pom or case.get("mode") == "pom" or case.get("pages") or any(
        s.get("action") == "call" or (s.get("action") == "expect" and "page" in s)
        for s in case.get("steps", [])
    ))


def iter_targets(target: dict):
    yield target
    if "within" in target:
        yield from iter_targets(target["within"])


def contract_errors(case) -> list[str]:
    # The public compiler API also accepts Python values; require actual JSON data.
    try:
        json.dumps(case, allow_nan=False)
    except (TypeError, ValueError) as exc:
        return [f"case is not JSON data: {exc}"]

    errors = []
    for error in sorted(validator().iter_errors(case), key=lambda e: str(list(e.absolute_path))):
        path = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
        message = error.message
        errors.append(f"{path}: {message[:300]}")
    if errors:
        return errors

    if not re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[0-9]{3,}", case["id"]):
        errors.append(f"invalid case id: {case['id']!r}")

    declared = set(case.get("fixtures", [])) | set(case.get("pages", {}))
    for name in declared:
        if keyword.iskeyword(name) or not IDENTIFIER.fullmatch(name):
            errors.append(f"invalid Python fixture name: {name!r}")

    if case.get("mode") == "ui" and is_pom_case(case):
        errors.append("mode=ui cannot contain POM bindings or calls; use mode=pom")

    captures: list[str] = []
    steps = case["steps"]
    for index, step in enumerate(steps):
        where = f"steps[{index}]"
        action = step["action"]
        if "target" in step:
            for target in iter_targets(step["target"]):
                anchors = set(target) & {"testid", "role", "href", "label", "placeholder", "text"}
                if len(anchors) > 1 and not (anchors == {"role", "href"} and target["role"] == "link"):
                    errors.append(f"{where}: target has conflicting locator strategies: {sorted(anchors)}")
                if "name" in target and not ("role" in target or "href" in target):
                    errors.append(f"{where}: target.name needs role or href")
                if "exact" in target and not (
                    anchors & {"label", "placeholder", "text"}
                    or ("name" in target and anchors & {"role", "href"})
                ):
                    errors.append(f"{where}: exact needs a name, label, placeholder or text locator")

        if action in {"call", "expect"} and "page" in step:
            if step["page"] not in declared:
                errors.append(f"{where}: page 变量 {step['page']!r} 未在 pages/fixtures 中声明")
            if not step["method"].isidentifier() or keyword.iskeyword(step["method"]):
                errors.append(f"{where}: invalid method name: {step['method']!r}")
            for name in step.get("args", {}):
                if not name.isidentifier() or keyword.iskeyword(name):
                    errors.append(f"{where}: invalid keyword argument: {name!r}")

        if action == "captureNetwork":
            name = step.get("name") or f"net_{len(captures) + 1}"
            if name in captures:
                errors.append(f"{where}: duplicate network capture name: {name!r}")
            captures.append(name)
            if index + 1 >= len(steps) or steps[index + 1]["action"] not in TRIGGER_ACTIONS:
                errors.append(f"{where}: captureNetwork must immediately precede an action")
        elif action == "expectNetwork":
            name = step.get("name")
            if name and name not in captures:
                errors.append(f"{where}: unknown network capture: {name!r}")
            elif not name and len(captures) != 1:
                errors.append(f"{where}: specify name after exactly one or more network captures")

    return errors


def validate_case(case: dict) -> None:
    errors = contract_errors(case)
    if errors:
        raise CaseValidationError(errors)
