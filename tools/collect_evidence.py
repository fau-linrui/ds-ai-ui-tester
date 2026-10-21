#!/usr/bin/env python
"""为一个既有仓库生成 evidence.md —— 供内网 AI 做对照评估。

只抽"能判断框架成熟度"的东西，不做全量转储：
  - 目录结构（深度 3）
  - conftest.py / pytest.ini / requirements.txt / pyproject.toml
  - Page Object 清单（类名 + 方法名，AST 提取，不贴全文）
  - 2~3 个典型测试文件（截断）
  - 若存在 manifest.json，附 pages/fixtures 摘要

用法：
    python tools/collect_evidence.py --root D:\\work\\ems-autotest --out evidence.md
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", "venv", ".venv", "__pycache__", ".pytest_cache", "reports",
    "test_data", "node_modules", ".idea", "dist", "build",
}
CODE_SUFFIX = {".py", ".ini", ".txt", ".toml", ".cfg", ".yaml", ".yml"}


def build_tree(root: Path, depth: int = 3, limit: int = 200) -> str:
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(root)
        if len(rel.parts) > depth:
            continue
        if path.is_file() and path.suffix not in CODE_SUFFIX:
            continue
        indent = "  " * (len(rel.parts) - 1)
        lines.append(f"{indent}{rel.name}{'/' if path.is_dir() else ''}")
        if len(lines) >= limit:
            lines.append("... (truncated)")
            break
    return "\n".join(lines)


def dump_file(path: Path, max_lines: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "\n".join(text[:max_lines])
    suffix = "" if len(text) <= max_lines else f"\n# ... 截断，共 {len(text)} 行"
    return f"\n### {path.name}\n```python\n{body}{suffix}\n```\n"


def summarize_pages(root: Path) -> str:
    pages_dir = root / "pages"
    if not pages_dir.exists():
        return "\n## Page Object\n\n（未找到 pages/ 目录）\n"

    files = sorted(p for p in pages_dir.rglob("*.py") if p.name != "__init__.py")
    out = [f"\n## Page Object（{len(files)} 个文件）\n"]

    for path in files[:60]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            out.append(f"- `{path.relative_to(root).as_posix()}`（解析失败）")
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            methods = [
                m.name for m in node.body
                if isinstance(m, ast.FunctionDef) and not m.name.startswith("_")
            ]
            bases = ", ".join(
                b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "?")
                for b in node.bases
            )
            out.append(
                f"- `{node.name}`（{path.relative_to(root).as_posix()}）"
                + (f" ← {bases}" if bases else "")
                + f"\n  - 方法：{', '.join(methods) if methods else '（无）'}"
            )
    return "\n".join(out) + "\n"


def manifest_summary(root: Path) -> str:
    path = root / "manifest.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    stats = data.get("stats", {})
    return (
        "\n## manifest.json 摘要\n\n"
        f"- Page 类：{stats.get('page_classes')}，方法：{stats.get('page_methods')}\n"
        f"- fixtures：{stats.get('fixtures')}，测试函数：{stats.get('test_functions')}\n"
        f"- 测试内定位调用：{stats.get('locator_bypass')}\n"
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", default="evidence.md")
    parser.add_argument("--max-lines", type=int, default=120)
    parser.add_argument("--max-tests", type=int, default=3)
    parser.add_argument("--max-bytes", type=int, default=15000)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"[evidence] 目录不存在：{root}")
        return 2

    parts = ["# 仓库证据包\n", "## 目录结构\n```\n" + build_tree(root) + "\n```\n"]

    for name in ("conftest.py", "pytest.ini", "requirements.txt", "pyproject.toml"):
        path = root / name
        if path.exists():
            parts.append(dump_file(path, args.max_lines))

    parts.append(summarize_pages(root))
    parts.append(manifest_summary(root))

    tests_dir = root / "tests"
    tests = sorted(tests_dir.rglob("test_*.py")) if tests_dir.exists() else []
    body = ["\n## 典型测试文件\n"]
    for path in tests[: args.max_tests]:
        body.append(dump_file(path, args.max_lines))
    parts.append("\n".join(body))

    content = "\n".join(parts)
    size = len(content.encode("utf-8"))

    # 超预算就逐步砍测试文件
    while size > args.max_bytes and tests:
        tests = tests[:-1]
        body = ["\n## 典型测试文件\n"]
        for path in tests[: args.max_tests]:
            body.append(dump_file(path, args.max_lines))
        parts[-1] = "\n".join(body)
        content = "\n".join(parts)
        size = len(content.encode("utf-8"))

    Path(args.out).write_text(content, encoding="utf-8")
    print(f"[evidence] {args.out}  {size} 字节  {'OK' if size <= args.max_bytes else '仍超预算'}")
    if size > args.max_bytes:
        print("  建议调小 --max-lines 或 --max-tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
