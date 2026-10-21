"""Opt-in real-browser checks; CI installs Chromium and enables these tests."""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading

import pytest

from tools.render import render_case
from tools.harness import main as harness_main
from tools.harness_io import atomic_json, read_json
from tools.snapshot import collect_snapshot

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="set RUN_BROWSER_TESTS=1 after installing Chromium"),
]

HTML = b'''<!doctype html><html><body>
<button onclick="document.querySelector('output').textContent='wrong'">Save</button>
<div role="dialog" aria-label="Edit">
  <button onclick="fetch('/api/save',{method:'POST'}).then(r=>r.json()).then(v=>document.querySelector('output').textContent=v.saved?'saved':'failed')">Save</button>
</div><output data-testid="status"></output>
</body></html>'''


@pytest.fixture(scope="module")
def app_url():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML)

        def do_POST(self):
            # The real endpoint is deliberately wrong. The mock must be installed.
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"saved":false}')

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def spec(payload, *, ui_assertion=True):
    steps = [
        {"action": "goto", "url": "/"},
        {"action": "captureNetwork", "name": "save", "urlContains": "/api/save", "method": "POST"},
        {"action": "click", "target": {"role": "button", "name": "Save", "exact": True,
         "within": {"role": "dialog", "name": "Edit", "exact": True}}},
        {"action": "expectNetwork", "name": "save", "status": 201, "equals": {"saved": True}},
    ]
    if ui_assertion:
        steps.append({"action": "expect", "target": {"testid": "status"},
                      "assertion": {"type": "text", "value": "saved", "exact": True}})
    return {"id": "BROWSER-001", "title": "Mocked save in scoped dialog",
            "routes": [{"url": "**/api/save", "status": 201, "json": payload}], "steps": steps}


def execute(case, page, app_url):
    scope = {}
    exec(compile(render_case(case, "browser.json"), "generated.py", "exec"), scope)
    scope["test_browser_001"](page, app_url)


def test_mocked_save_and_scoped_duplicate_buttons(page, app_url):
    execute(spec({"saved": True}), page, app_url)


def test_bad_json_response_fails_even_when_status_is_correct(page, app_url):
    with pytest.raises(AssertionError):
        execute(spec({"saved": False}, ui_assertion=False), page, app_url)


def test_snapshot_uses_browser_accessible_names_and_keeps_ambiguity(page):
    page.set_content('''<label>Wrapped Label<input id="wrapped"></label>
      <label for="quantity">Quantity</label><input id="quantity" type="number">
      <button>Save</button><div role="dialog" aria-label="Edit"><button>Save</button></div>''')
    data = collect_snapshot(page)
    assert data["complete"]
    assert 'spinbutton "Quantity"' in data["ariaSnapshot"]
    assert 'textbox "Wrapped Label"' in data["ariaSnapshot"]
    saves = [n for n in data["nodes"] if n["name"] == "Save" and n["role"] == "button"]
    assert len(saves) == 2
    assert all(n["ambiguous"] and n["keyStability"] == "snapshot-only" for n in saves)


def test_snapshot_includes_iframe_context(page):
    page.set_content('''<iframe title="Child" srcdoc="<button>Frame action</button>"></iframe>''')
    page.frame_locator("iframe").get_by_role("button", name="Frame action").wait_for()
    data = collect_snapshot(page)
    assert data["complete"]
    assert any("Frame action" in f["ariaSnapshot"] for f in data["frames"])


def test_harness_collects_real_snapshot_repairs_target_and_reruns_browser(tmp_path, app_url):
    """Model is controlled; browser, strict locator failure, trace and repair are real."""
    case = spec({"saved": True})
    case["sources"] = ["REQ-BROWSER"]
    del case["steps"][2]["target"]["within"]  # First execution must fail on duplicate Save buttons.
    script = '''import json, sys
request = json.load(sys.stdin)
role = request['role']
case = CASE
if role == 'explorer':
    assert 'button "Save"' in request['snapshot']['ariaSnapshot']
    result = {'summary': 'Two Save buttons; one in Edit dialog', 'targets': [], 'warnings': ['scope required']}
elif role == 'author':
    if request.get('previous_case'):
        case = request['previous_case']
        case['steps'][2]['target']['within'] = {'role': 'dialog', 'name': 'Edit', 'exact': True}
    result = {'case': case}
else:
    assert request['execution']['outcome'] == 'failed'
    assert any(key.startswith('browser-results/harness-evidence/') for key in request['evidence'])
    result = {'category': 'case_error', 'summary': 'Actual strict mode duplicate locator', 'evidence': ['junit.xml']}
print(json.dumps(result))
'''.replace("CASE", repr(case))
    (tmp_path / "agent.py").write_text(script, encoding="utf-8")
    (tmp_path / "source.md").write_text("Save in Edit must POST /api/save and display saved; fixture contract status 201 and JSON saved true.", encoding="utf-8")
    (tmp_path / "conftest.py").write_text('import os, pytest\n@pytest.fixture\ndef base_url(): return os.environ["BASE_URL"]\n', encoding="utf-8")
    agents = {role: {"protocol": "json", "command": [sys.executable, str(tmp_path / "agent.py")]}
              for role in ["explorer", "author", "verifier"]}
    atomic_json(tmp_path / "config.json", {"version": 1, "test_environment": True, "base_url": app_url, "agents": agents})
    atomic_json(tmp_path / "tasks.json", {"version": 1, "tasks": [
        {"id": case["id"], "goal": "Save in Edit dialog", "url": "/",
         "sources": [{"id": "REQ-BROWSER", "path": "source.md"}]}
    ]})
    assert harness_main(["run", "--config", str(tmp_path / "config.json"), "--tasks", str(tmp_path / "tasks.json"),
                         "--root", str(tmp_path), "--run-id", "browser"]) == 0
    directory = tmp_path / ".snap/runs/browser/tasks/BROWSER-001"
    assert 'dialog "Edit"' in read_json(directory / "snapshot.json")["ariaSnapshot"]
    assert read_json(directory / "attempts/01/execution.json")["outcome"] == "failed"
    assert list((directory / "attempts/01/browser-results").rglob("trace.zip"))
    evidence = list((directory / "attempts/01/browser-results/harness-evidence").glob("*.json"))
    assert evidence and read_json(evidence[0])["snapshot"]["complete"]
    assert read_json(directory / "attempts/02/execution.json")["outcome"] == "passed"
