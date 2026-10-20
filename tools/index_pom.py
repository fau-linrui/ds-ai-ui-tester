#!/usr/bin/env python
"""扫描既有 pytest + Playwright 仓库，产出框架 API 清单（manifest.json）。

不做 RAG、不调模型——纯 AST 静态分析，所以永远和代码同步。

用法：
    python tools/index_pom.py --root D:\\work\\ems-autotest
    python tools/index_pom.py --root . --out manifest.json --json
"""
from __future__ import annotations

import argparse
import ast
import configparser
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".idea", ".tox", "build", "dist"}

LOCATOR_METHODS = {
    "get_by_role",
    "get_by_text",
    "get_by_label",
    "get_by_test_id",
    "get_by_placeholder",
    "get_by_title",
    "get_by_alt_text",
    "locator",
    "find_element",
    "find_elements",
    "query_selector",
    "query_selector_all",
}

LEGACY_METHODS = {"find_element", "find_elements", "query_selector", "query_selector_all"}


def iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def parse_file(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def first_line(doc: str | None) -> str:
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return ""


def decorator_name(node: ast.AST) -> str:
    """@pytest.fixture / @fixture(scope='module') -> 'fixture'"""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def call_attr(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def self_page_attr(node: ast.AST) -> str:
    """self.page.get_by_role(...) -> 'get_by_role'；不是这个形状返回空串。"""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return ""
    owner = func.value
    if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
        if owner.value.id == "self" and owner.attr in ("page", "browser", "driver"):
            return func.attr
    if isinstance(owner, ast.Name) and owner.id == "page":
        return func.attr
    return ""


def arg_names(args: ast.arguments) -> list[str]:
    names = [a.arg for a in args.args if a.arg != "self"]
    if args.vararg:
        names.append("*" + args.vararg.arg)
    names += [a.arg for a in args.kwonlyargs]
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    return names


def class_is_page(node: ast.ClassDef) -> bool:
    if node.name.endswith("Page") or "Page" in node.name:
        return True
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if "Page" in name:
            return True
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            for sub in ast.walk(item):
                if self_page_attr(sub):
                    return True
    return False


def class_fixture_return(node: ast.FunctionDef) -> str:
    """fixture 里 return AlarmCenterPage(page) -> 'AlarmCenterPage'"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Call):
            func = sub.value.func
            if isinstance(func, ast.Name):
                return func.id
            if isinstance(func, ast.Attribute):
                return func.attr
    return ""


def collect_page(node: ast.ClassDef, file_rel: str, module: str) -> dict:
    methods = []
    style = {"get_by": 0, "locator": 0, "legacy": 0, "sleep": 0, "asserts": 0}
    init_params: list[str] = []

    for item in node.body:
        if not isinstance(item, ast.FunctionDef):
            continue
        if item.name == "__init__":
            init_params = arg_names(item.args)
            continue
        methods.append(
            {
                "name": item.name,
                "params": arg_names(item.args),
                "doc": first_line(ast.get_docstring(item)),
            }
        )

    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            style["asserts"] += 1
        if not isinstance(sub, ast.Call):
            continue
        name = self_page_attr(sub) or call_attr(sub)
        if name.startswith("get_by_"):
            style["get_by"] += 1
        elif name == "locator":
            style["locator"] += 1
        elif name in LEGACY_METHODS:
            style["legacy"] += 1
        elif name == "sleep":
            style["sleep"] += 1

    return {
        "class": node.name,
        "module": module,
        "file": file_rel,
        "is_base": node.name.startswith("Base") or node.name.endswith("Base"),
        "bases": [
            (b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")) for b in node.bases
        ],
        "init_params": init_params,
        "methods": methods,
        "style": style,
    }


def read_markers(root: Path) -> list[str]:
    markers: list[str] = []

    ini = root / "pytest.ini"
    if ini.exists():
        parser = configparser.ConfigParser()
        try:
            parser.read(ini, encoding="utf-8")
            raw = parser.get("pytest", "markers", fallback="")
        except Exception:  # noqa: BLE001
            raw = ""
        markers += [line.split(":")[0].strip() for line in raw.splitlines() if line.strip()]

    for name in ("setup.cfg", "tox.ini"):
        cfg = root / name
        if cfg.exists():
            parser = configparser.ConfigParser()
            try:
                parser.read(cfg, encoding="utf-8")
                raw = parser.get("tool:pytest", "markers", fallback="")
            except Exception:  # noqa: BLE001
                raw = ""
            markers += [line.split(":")[0].strip() for line in raw.splitlines() if line.strip()]

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            raw = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
            markers += [str(line).split(":")[0].strip() for line in raw]
        except Exception:  # noqa: BLE001
            pass

    return sorted({m for m in markers if m})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="既有自动化仓库根目录")
    parser.add_argument("--out", default="manifest.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"[index] 目录不存在：{root}")
        return 2

    pages: list[dict] = []
    fixtures: list[dict] = []
    tests: list[dict] = []
    failed: list[str] = []

    for path in iter_py_files(root):
        tree = parse_file(path)
        if tree is None:
            failed.append(rel(root, path))
            continue

        file_rel = rel(root, path)
        module = path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
        is_test = path.name.startswith("test_") or "/tests/" in f"/{file_rel}"

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and class_is_page(node):
                pages.append(collect_page(node, file_rel, module))

            if isinstance(node, ast.FunctionDef):
                decos = [decorator_name(d) for d in node.decorator_list]
                if "fixture" in decos:
                    scope = "function"
                    for d in node.decorator_list:
                        if isinstance(d, ast.Call):
                            for kw in d.keywords:
                                if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                                    scope = str(kw.value.value)
                    fixtures.append(
                        {
                            "name": node.name,
                            "scope": scope,
                            "file": file_rel,
                            "params": arg_names(node.args),
                            "returns": class_fixture_return(node),
                            "doc": first_line(ast.get_docstring(node)),
                        }
                    )

                if is_test and node.name.startswith("test_"):
                    markers = [decorator_name(d) for d in node.decorator_list]
                    tests.append(
                        {
                            "name": node.name,
                            "file": file_rel,
                            "params": arg_names(node.args),
                            "markers": [m for m in markers if m not in ("parametrize",)],
                            "doc": first_line(ast.get_docstring(node)),
                        }
                    )

    # 统计测试文件里的定位调用（POM 绕过程度）
    bypass = {"get_by": 0, "locator": 0, "legacy": 0, "sleep": 0}
    per_file: dict[str, int] = {}
    for path in iter_py_files(root):
        relpath = rel(root, path)
        if not (path.name.startswith("test_") or "/tests/" in f"/{relpath}"):
            continue
        tree = parse_file(path)
        if tree is None:
            continue
        count = 0
        for sub in ast.walk(tree):
            if not isinstance(sub, ast.Call):
                continue
            name = call_attr(sub)
            if name.startswith("get_by_"):
                bypass["get_by"] += 1
                count += 1
            elif name == "locator":
                bypass["locator"] += 1
                count += 1
            elif name in LEGACY_METHODS:
                bypass["legacy"] += 1
                count += 1
            elif name == "sleep":
                bypass["sleep"] += 1
                count += 1
        if count:
            per_file[relpath] = count

    fixture_map = {f["name"]: f["returns"] for f in fixtures if f["returns"]}
    for test in tests:
        test["uses_pages"] = sorted({fixture_map[p] for p in test["params"] if p in fixture_map})

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": root.as_posix(),
        "pages": sorted(pages, key=lambda p: p["class"]),
        "fixtures": fixtures,
        "markers": read_markers(root),
        "tests": tests,
        "stats": {
            "page_classes": len(pages),
            "page_methods": sum(len(p["methods"]) for p in pages),
            "fixtures": len(fixtures),
            "test_files": len({t["file"] for t in tests}),
            "test_functions": len(tests),
            "locator_bypass": bypass,
            "bypass_by_file": dict(sorted(per_file.items(), key=lambda kv: -kv[1])),
            "unparsable_files": failed,
        },
    }

    out = Path(args.out)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps({"ok": True, "out": str(out), "stats": manifest["stats"]}, ensure_ascii=False))
        return 0

    s = manifest["stats"]
    print(f"[index] 扫描完成：{root}")
    print(f"  Page 类      {s['page_classes']} 个 / 方法 {s['page_methods']} 个")
    print(f"  fixtures     {s['fixtures']} 个")
    print(f"  测试          {s['test_files']} 个文件 / {s['test_functions']} 个函数")
    print(f"  marker       {', '.join(manifest['markers']) or '(未配置)'}")
    print(
        f"  测试内定位调用 get_by={s['locator_bypass']['get_by']} locator={s['locator_bypass']['locator']} "
        f"legacy={s['locator_bypass']['legacy']} sleep={s['locator_bypass']['sleep']}"
    )
    if failed:
        print(f"  ! 无法解析的文件 {len(failed)} 个：{', '.join(failed[:5])}")
    print(f"  -> {out}")

    top = list(s["bypass_by_file"].items())[:5]
    if top:
        print("\n  POM 绕过最多的测试文件：")
        for name, count in top:
            print(f"    {count:4d}  {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
