#!/usr/bin/env python
"""用例静态闸门：在渲染之前拦住不合规的 JSON。

检查六件事：
  1. 结构与必填字段
  2. target 是否只有语义字段（禁止选择器）
  3. 断言强度（只有 visible 不算）
  4. 来源（P0/P1 必须有 sources 或 requirement）
  5. raw 降级预算（超过阈值即失败）
  6. page 变量是否已声明

用法：
    python tools/lint.py --cases spec/cases
    python tools/lint.py --cases spec/cases --boundary
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SEMANTIC_KEYS = {"testid", "role", "label", "placeholder", "text", "href"}
SELECTOR_HINTS = ("::", "//", "nth-child", "xpath", "css=", ">>")
STRONG_ASSERTIONS = {"text", "value", "count"}
ALLOWED_ACTIONS = {
    "goto", "click", "fill", "select", "check", "press", "waitFor",
    "call", "raw", "captureNetwork", "expectNetwork", "expectUrl", "expect",
}
ALLOWED_WRITE_PREFIXES = ("spec/", "docs/", "tests/ai_generated/", "pages/_ai/", "sample-ems/")


def iter_cases(directory: Path):
    for path in sorted(directory.rglob("*.json")):
        yield path


def find_targets(node, parent_key=""):
    """递归找出所有 target 对象。"""
    if isinstance(node, dict):
        if parent_key == "target":
            yield node
        for key, value in node.items():
            yield from find_targets(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from find_targets(item, parent_key)


def check_target(target: dict, where: str) -> list[str]:
    errors = []
    if not any(k in target for k in SEMANTIC_KEYS):
        errors.append(f"{where}: target 缺少语义字段（testid/role/label/text）")
    for key, value in target.items():
        if isinstance(value, str) and any(h in value for h in SELECTOR_HINTS):
            errors.append(f"{where}: target.{key} 疑似选择器：{value!r}")
    return errors


def check_case(case: dict, max_raw: float) -> tuple[list[str], dict]:
    errors: list[str] = []
    steps = case.get("steps") or []

    for field in ("id", "title", "steps"):
        if not case.get(field):
            errors.append(f"缺少必填字段：{field}")
    if not re.match(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[0-9]{3,}$", str(case.get("id", ""))):
        errors.append(f"id 格式不合法：{case.get('id')!r}")

    for index, step in enumerate(steps):
        action = step.get("action")
        where = f"steps[{index}]"
        if action not in ALLOWED_ACTIONS:
            errors.append(f"{where}: 未知 action {action!r}")
        for target in find_targets(step):
            errors.extend(check_target(target, where))

    # 来源
    if str(case.get("priority", "")).upper() in ("P0", "P1") and not (case.get("sources") or case.get("requirement")):
        errors.append("P0/P1 用例缺少 sources / requirement（期望值必须有依据）")

    # 断言强度（smoke 用例例外：它本来就只验"页面渲染出来了"）
    is_smoke = "smoke" in (case.get("tags") or [])
    strong = 0
    for step in steps:
        action = step.get("action")
        if action == "expectUrl" or action == "expectNetwork":
            strong += 1
        elif action == "expect":
            if step.get("page") and step.get("method"):
                strong += 1
            elif (step.get("assertion") or {}).get("type") in STRONG_ASSERTIONS:
                strong += 1
    if strong == 0 and not is_smoke:
        errors.append("缺少有效断言：只有 visible 不算，至少补 text/value/count 或 expectUrl")

    # raw 预算
    raw_steps = [s for s in steps if s.get("action") == "raw"]
    for step in raw_steps:
        if not step.get("pom_gap"):
            errors.append("raw 步骤必须写 pom_gap（债务要可统计）")
    ratio = len(raw_steps) / len(steps) if steps else 0
    if ratio > max_raw:
        errors.append(f"raw 占比 {ratio:.0%} 超过预算 {max_raw:.0%}")

    # page 变量声明
    declared = set((case.get("pages") or {}).keys()) | set(case.get("fixtures") or [])
    for index, step in enumerate(steps):
        if step.get("action") in ("call", "expect") and step.get("page"):
            if step["page"] not in declared:
                errors.append(f"steps[{index}]: page 变量 {step['page']!r} 未在 pages/fixtures 中声明")

    stats = {"steps": len(steps), "assertions": strong, "raw": len(raw_steps)}
    return errors, stats


def check_boundary() -> list[str]:
    try:
        changed = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        return [f"跳过边界检查：{exc}"]
    problems = []
    for line in changed.splitlines():
        path = line[3:].strip().strip('"')
        if not path or path.endswith(".pyc"):
            continue
        if path.startswith(ALLOWED_WRITE_PREFIXES):
            continue
        if path.startswith(("pages/", "tests/", "conftest.py")):
            problems.append(f"越界修改：{path}（只允许新增到 tests/ai_generated/ 或 pages/_ai/）")
    return problems


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="spec/cases")
    parser.add_argument("--max-raw", type=float, default=0.2)
    parser.add_argument("--boundary", action="store_true")
    args = parser.parse_args()

    directory = Path(args.cases)
    if not directory.exists():
        print(f"[lint] 目录不存在：{directory}")
        return 2

    total = 0
    failed = 0
    for path in iter_cases(directory):
        total += 1
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[lint] FAIL {path.name}: 非法 JSON（{exc}）")
            failed += 1
            continue

        errors, stats = check_case(case, args.max_raw)
        if errors:
            failed += 1
            print(f"[lint] FAIL {path.name}  ({case.get('id', '?')})")
            for message in errors:
                print(f"       - {message}")
        else:
            print(f"[lint] OK   {path.name}  ({stats['steps']} 步 / {stats['assertions']} 断言 / {stats['raw']} raw)")

    if args.boundary:
        problems = check_boundary()
        for message in problems:
            print(f"[lint] BOUNDARY {message}")
        failed += len([p for p in problems if p.startswith("越界")])

    print(f"\n[lint] {total} 个用例，{failed} 个问题")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
