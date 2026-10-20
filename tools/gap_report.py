#!/usr/bin/env python
"""基于 manifest 生成 POM 覆盖报告与缺口清单。

回答三个问题：
  1. 哪些页面 / 方法根本没被用上（死代码或未覆盖）
  2. AI 用例需要哪些 POM 能力，现在缺哪些
  3. 测试里有多少定位调用绕过了 POM（技术债热图）

用法：
    python tools/gap_report.py --manifest manifest.json --cases spec/cases --out gap.md
    python tools/gap_report.py --manifest manifest.json --tests tests --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_cases(cases_dir: Path) -> list[dict]:
    cases = []
    for path in sorted(cases_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[gap] 跳过非法 JSON：{path}")
            continue
        if isinstance(data, dict) and "steps" in data:
            data["_file"] = path.name
            cases.append(data)
    return cases


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--cases", default="")
    parser.add_argument("--tests", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    pages = {p["class"]: p for p in manifest["pages"]}
    active = {name: p for name, p in pages.items() if not p.get("is_base")}
    caps = {name: {m["name"] for m in page["methods"]} for name, page in pages.items()}

    # 1) 页面使用情况：哪些 Page 被测试引用
    used: Counter[str] = Counter()
    for test in manifest["tests"]:
        for cls in test.get("uses_pages", []):
            used[cls] += 1

    unused_pages = [name for name in active if used[name] == 0]
    empty_pages = [name for name, page in active.items() if not page["methods"]]
    pom_debt = {
        "legacy": sum(p["style"].get("legacy", 0) for p in active.values()),
        "sleep": sum(p["style"].get("sleep", 0) for p in active.values()),
        "asserts_in_page": sum(p["style"].get("asserts", 0) for p in active.values()),
    }
    tests_without_pom = [
        t["name"] for t in manifest["tests"] if not t.get("uses_pages") and "page" in t.get("params", [])
    ]

    # 2) 用例缺口
    gaps: Counter[tuple[str, str]] = Counter()
    raw_steps: list[dict] = []
    case_count = 0
    call_steps = 0

    if args.cases:
        cases_dir = Path(args.cases)
        if cases_dir.exists():
            for case in load_cases(cases_dir):
                case_count += 1
                for step in case.get("steps", []):
                    action = step.get("action")
                    if action == "call":
                        call_steps += 1
                        page_name = step.get("page", "")
                        method = step.get("method", "")
                        if page_name not in caps:
                            gaps[(page_name, "(整个类缺失)")] += 1
                        elif method not in caps[page_name]:
                            gaps[(page_name, method)] += 1
                    elif action == "raw":
                        raw_steps.append(
                            {
                                "case": case.get("id", "?"),
                                "gap": step.get("pom_gap", "(未标注)"),
                                "target": step.get("target", {}),
                            }
                        )

    # 3) POM 绕过
    bypass = manifest["stats"]["locator_bypass"]
    bypass_total = sum(bypass.values())
    test_functions = max(manifest["stats"]["test_functions"], 1)
    bypass_rate = bypass_total / test_functions

    report = {
        "pages": len(active),
        "page_methods": sum(len(p["methods"]) for p in active.values()),
        "pages_used": len(active) - len(unused_pages),
        "unused_pages": unused_pages,
        "empty_pages": empty_pages,
        "pom_debt": pom_debt,
        "tests_without_pom": tests_without_pom,
        "cases": case_count,
        "call_steps": call_steps,
        "gaps": [{"page": p, "method": m, "count": c} for (p, m), c in gaps.most_common()],
        "raw_steps": raw_steps,
        "bypass": bypass,
        "bypass_per_test": round(bypass_rate, 2),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    # 控制台摘要
    print(f"[gap] POM 覆盖：{len(active) - len(unused_pages)}/{len(active)} 个业务 Page 被测试引用")
    if unused_pages:
        print(f"      未被引用：{', '.join(unused_pages[:8])}")
    if empty_pages:
        print(f"      空壳 Page：{', '.join(empty_pages)}")
    print(f"[gap] POM 内部债务：find_element={pom_debt['legacy']} sleep={pom_debt['sleep']} 页面内断言={pom_debt['asserts_in_page']}")
    print(f"[gap] 定位调用绕过 POM：{bypass_total} 次（{bypass_rate:.1f} 次/测试函数）")
    if tests_without_pom:
        print(f"      完全未用 POM 的测试：{', '.join(tests_without_pom[:5])}")
    if args.cases:
        print(f"[gap] 用例 {case_count} 条，call 步骤 {call_steps} 个，缺口 {len(gaps)} 项，raw 步骤 {len(raw_steps)} 个")

    if gaps:
        print("\n[gap] 缺口 TOP:")
        for (page_name, method), count in gaps.most_common(args.top):
            print(f"  {count:3d}x  {page_name}.{method}")

    if raw_steps:
        print("\n[gap] raw 降级步骤：")
        for item in raw_steps[: args.top]:
            print(f"  {item['case']}  {item['gap']}")

    # Markdown 报告
    if args.out:
        lines = [
            "# POM 覆盖与缺口报告",
            "",
            f"- 业务 Page 类：{len(active)} 个（被测试引用 {len(active) - len(unused_pages)} 个）",
            f"- POM 方法：{sum(len(p['methods']) for p in active.values())} 个",
            f"- 测试函数：{manifest['stats']['test_functions']} 个",
            f"- 定位绕过：{bypass_total} 次（{bypass_rate:.2f} 次/函数）",
            f"- POM 内部债务：find_element={pom_debt['legacy']}、sleep={pom_debt['sleep']}、页面内断言={pom_debt['asserts_in_page']}",
            "",
            "## 0. 结论摘要",
            "",
            f"- 空壳 Page：{len(empty_pages)} 个" + (f"（{', '.join(empty_pages)}）" if empty_pages else ""),
            f"- 完全未使用 POM 的测试：{len(tests_without_pom)} 个",
            f"- 用例缺口：{len(gaps)} 项；raw 降级：{len(raw_steps)} 处",
            "",
            "## 1. 未被测试引用的 Page",
            "",
        ]
        lines += [f"- `{name}`" for name in unused_pages] or ["（无）"]
        lines += ["", "## 2. 用例缺口", ""]
        if gaps:
            lines += ["| Page | 方法 | 出现次数 |", "|---|---|---|"]
            lines += [f"| `{p}` | `{m}` | {c} |" for (p, m), c in gaps.most_common()]
            lines += ["", "> 这些是 AI 生成用例需要、但 POM 尚未提供的能力。优先补高频项。"]
        else:
            lines.append("（无缺口）")
        lines += ["", "## 3. raw 降级步骤", ""]
        if raw_steps:
            lines += ["| 用例 | 待补 POM |", "|---|---|"]
            lines += [f"| {i['case']} | `{i['gap']}` |" for i in raw_steps]
        else:
            lines.append("（无）")
        lines += ["", "## 4. POM 绕过热点", ""]
        by_file = manifest["stats"]["bypass_by_file"]
        if by_file:
            lines += ["| 文件 | 定位调用次数 |", "|---|---|"]
            lines += [f"| `{f}` | {c} |" for f, c in list(by_file.items())[: args.top]]
        else:
            lines.append("（无）")
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n[gap] 报告 -> {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
