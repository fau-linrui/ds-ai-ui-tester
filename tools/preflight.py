#!/usr/bin/env python
"""环境自检。任何 agent 接手这个仓库的第一条命令。

退出码: 0 = 就绪, 1 = 有阻塞项。

    python tools/preflight.py
    python tools/preflight.py --json
"""
from __future__ import annotations

import json
import argparse
from importlib.metadata import PackageNotFoundError, version
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

CHECKS: list[dict] = []


def add(name: str, ok: bool, detail: str) -> None:
    CHECKS.append({"name": name, "ok": ok, "detail": detail})


def check_python() -> None:
    major, minor = sys.version_info[:2]
    add("python>=3.9", (major, minor) >= (3, 9), f"{major}.{minor}")


def check_playwright():
    try:
        import playwright
        from playwright.sync_api import sync_playwright
    except ImportError:
        add("playwright 包", False, "未安装：pip install playwright")
        return None
    add("playwright 包", True, version("playwright"))
    return sync_playwright


def check_browser(sync_playwright, channel: str = "") -> None:
    if sync_playwright is None:
        return
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
            version = browser.version
            browser.close()
        add("浏览器可启动", True, version)
    except Exception as exc:  # noqa: BLE001
        first = str(exc).splitlines()[0][:110]
        add("浏览器可启动", False, f"{first} → 跑 python -m playwright install chromium")


def check_base_url() -> None:
    base = os.environ.get("BASE_URL", "")
    if not base:
        add("BASE_URL 可达", False, "未设置环境变量 BASE_URL")
        return
    try:
        request = urllib.request.Request(base, method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            add("BASE_URL 可达", response.status < 500, f"{base} -> HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        add("BASE_URL 可达", exc.code < 500, f"{base} -> HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        add("BASE_URL 可达", False, f"{base} -> {exc}")


def check_writable() -> None:
    try:
        Path(".snap").mkdir(parents=True, exist_ok=True)
        probe = Path(".snap") / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("工作区可写", True, ".snap/")
    except Exception as exc:  # noqa: BLE001
        add("工作区可写", False, str(exc))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--channel", default="", help="与 snapshot / pytest 使用相同的 chrome 或 msedge")
    args = parser.parse_args()
    CHECKS.clear()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    check_python()
    for package in ("pytest", "pytest-playwright", "jsonschema"):
        try:
            add(package, True, version(package))
        except PackageNotFoundError:
            add(package, False, "pip install -r requirements.txt")
    sync_playwright = check_playwright()
    check_browser(sync_playwright, args.channel)
    check_base_url()
    check_writable()

    ok = all(item["ok"] for item in CHECKS)

    if args.json:
        print(json.dumps({"ok": ok, "checks": CHECKS}, ensure_ascii=False, indent=2))
    else:
        for item in CHECKS:
            flag = "PASS" if item["ok"] else "FAIL"
            print(f"{flag}  {item['name']:<14} {item['detail']}")
        print("\n环境就绪，下一步：python tools/snapshot.py --url $BASE_URL/..." if ok else "\n有阻塞项，先修完再往下走")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
