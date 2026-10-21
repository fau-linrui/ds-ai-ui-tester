#!/usr/bin/env python
"""用例静态闸门：严格契约、断言来源、POM 降级预算与工作区边界。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.contracts import DIRECT_ACTIONS, contract_errors, is_pom_case, read_case

SELECTOR_HINTS = ("::", "//", "nth-child", "xpath", "css=", ">>")
STRONG_ASSERTIONS = {"text", "value", "count"}
ALLOWED_WRITE_PREFIXES = ("spec/", "docs/", "tests/ai_generated/", "pages/_ai/", "sample-ems/")


def iter_cases(directory: Path):
    yield from sorted(directory.rglob("*.json"))


def find_targets(node, parent_key=""):
    if isinstance(node, dict):
        if parent_key in {"target", "within"}:
            yield node
        for key, value in node.items():
            yield from find_targets(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from find_targets(item, parent_key)


def check_target(target: dict, where: str) -> list[str]:
    return [
        f"{where}: target.{key} 疑似选择器：{value!r}"
        for key, value in target.items()
        if key == "testid" and isinstance(value, str) and any(h in value for h in SELECTOR_HINTS)
    ]


def check_case(case: dict, max_raw: float, *, require_pom: bool = False) -> tuple[list[str], dict]:
    errors = contract_errors(case)
    stats = {"steps": 0, "assertions": 0, "raw": 0, "pom": False}
    if errors:
        return errors, stats
    steps = case["steps"]
    stats["steps"] = len(steps)
    stats["pom"] = is_pom_case(case, require_pom)
    for index, step in enumerate(steps):
        for target in find_targets(step):
            errors.extend(check_target(target, f"steps[{index}]"))

    if case.get("priority") in ("P0", "P1") and not (case.get("sources") or case.get("requirement")):
        errors.append("P0/P1 用例缺少 sources / requirement（期望值必须有依据）")

    for step in steps:
        action = step["action"]
        if action in {"expectUrl", "expectNetwork"}:
            stats["assertions"] += 1
        elif action == "expect" and (
            "page" in step or step["assertion"]["type"] in STRONG_ASSERTIONS
        ):
            stats["assertions"] += 1
    if stats["assertions"] == 0 and "smoke" not in case.get("tags", []):
        errors.append("缺少有效断言：只有 visible 不算，至少补 text/value/count 或 expectUrl")

    bypass = [
        s for s in steps
        if s["action"] == "raw" or (stats["pom"] and s["action"] in DIRECT_ACTIONS)
    ]
    stats["raw"] = len(bypass)
    for step in bypass:
        if not step.get("pom_gap"):
            errors.append(f"{step['action']} 绕过 POM，必须写 pom_gap")
    ratio = len(bypass) / len(steps)
    if ratio > max_raw:
        errors.append(f"raw/POM 绕过占比 {ratio:.0%} 超过预算 {max_raw:.0%}")
    return errors, stats


def check_boundary() -> list[str]:
    """Working-tree advisory check; OS/tool permissions remain a separate concern."""
    try:
        changed = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        return [f"边界检查失败：{exc}"]
    paths = []
    entries = iter(changed.split("\0"))
    for entry in entries:
        if not entry:
            continue
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            paths.append(next(entries, ""))
    return [
        f"越界修改：{path}（只允许新增到 tests/ai_generated/ 或 pages/_ai/）"
        for path in paths
        if path and not path.endswith(".pyc")
        and not path.startswith(ALLOWED_WRITE_PREFIXES)
        and path.startswith(("pages/", "tests/", "conftest.py"))
    ]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="spec/cases")
    parser.add_argument("--max-raw", type=float, default=0.2)
    parser.add_argument("--require-pom", action="store_true", help="强制对所有用例执行 POM 降级策略")
    parser.add_argument("--boundary", action="store_true", help="检查本地未提交变更，不替代工具权限")
    args = parser.parse_args()
    if not 0 <= args.max_raw <= 1:
        parser.error("--max-raw must be between 0 and 1")
    directory = Path(args.cases)
    if not directory.is_dir():
        print(f"[lint] 目录不存在：{directory}")
        return 2
    files = list(iter_cases(directory))
    if not files:
        print(f"[lint] 没有用例：{directory}")
        return 1

    failed = 0
    pom_steps = pom_raw = 0
    for path in files:
        try:
            case = read_case(path)
        except (OSError, ValueError) as exc:
            print(f"[lint] FAIL {path}: {exc}")
            failed += 1
            continue
        errors, stats = check_case(case, args.max_raw, require_pom=args.require_pom)
        if stats["pom"]:
            pom_steps += stats["steps"]
            pom_raw += stats["raw"]
        if errors:
            failed += 1
            print(f"[lint] FAIL {path}")
            for message in errors:
                print(f"       - {message}")
        else:
            print(f"[lint] OK {path} ({stats['steps']} steps / {stats['assertions']} assertions / {stats['raw']} bypass)")
    if pom_steps and pom_raw / pom_steps > args.max_raw:
        failed += 1
        print(f"[lint] GLOBAL POM bypass {pom_raw / pom_steps:.0%} > {args.max_raw:.0%}")
    if args.boundary:
        problems = check_boundary()
        for message in problems:
            print(f"[lint] BOUNDARY {message}")
        failed += len(problems)
    print(f"[lint] {len(files)} cases, {failed} problems")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
