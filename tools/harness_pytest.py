"""Opt-in pytest plugin: retain text browser evidence without changing test code."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from playwright.sync_api import Page

from tools.harness_io import atomic_json
from tools.snapshot import collect_snapshot


def pytest_addoption(parser):
    parser.addoption("--harness-evidence-dir", default="", help="Harness-owned browser evidence directory")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        item._harness_failed = True


@pytest.fixture(autouse=True)
def _harness_browser_evidence(request):
    directory = request.config.getoption("--harness-evidence-dir")
    if not directory or "page" not in request.fixturenames:
        yield
        return
    page = request.getfixturevalue("page")
    if not isinstance(page, Page):
        yield
        return
    messages = []

    def console(message):
        if len(messages) < 200:
            messages.append({"type": message.type, "text": message.text[:2000]})

    def error(exception):
        if len(messages) < 200:
            messages.append({"type": "pageerror", "text": str(exception)[:2000]})

    page.on("console", console)
    page.on("pageerror", error)
    yield
    # Evidence collection must never replace the actual assertion result.
    try:
        if getattr(request.node, "_harness_failed", False):
            data = {"nodeid": request.node.nodeid, "console": messages}
            try:
                data["snapshot"] = collect_snapshot(page, timeout=1000)
            except Exception as exc:
                data["snapshot_error"] = str(exc)
            key = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
            atomic_json(Path(directory) / (key + ".json"), data)
    except Exception:
        pass
    finally:
        if not page.is_closed():
            page.remove_listener("console", console)
            page.remove_listener("pageerror", error)
