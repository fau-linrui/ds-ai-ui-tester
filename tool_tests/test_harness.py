"""Exercise real subprocess agents, generated pytest execution, and recovery policy."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import Event, Timer

import pytest

from tools.harness import (
    HarnessError, TaskRunner, execution_outcome, main, oracle_projection,
    prepare_inputs, run_scheduler,
)
from tools.harness_io import agent_result, atomic_json, read_json, run_lock, run_process

ROOT = Path(__file__).resolve().parents[1]
AGENT = '''import copy, json, pathlib, sys, time
scenario = sys.argv[1]
if scenario == 'opencode':
    assert sys.argv[-2] == '--file'
    request = json.loads(pathlib.Path(sys.argv[-1]).read_text(encoding='utf-8'))
else:
    request = json.load(sys.stdin)
role = request['role']
with pathlib.Path('calls.jsonl').open('a', encoding='utf-8') as log:
    log.write(json.dumps({'role': role, 'id': request['task']['id'], 'call_id': request['call_id']}) + '\\n')
if scenario == 'timeout':
    time.sleep(10)
if scenario == 'bad_json':
    print('not JSON')
    sys.exit(0)
if scenario == 'oversize':
    print('x' * 200000)
    sys.exit(0)
if role == 'explorer':
    result = {'summary': 'Observed a named Save button', 'targets': [{'testid': 'ok'}], 'warnings': []}
elif role == 'author':
    case = {
        'id': request['task']['id'], 'title': 'Save contract', 'mode': 'pom',
        'sources': ['REQ-SAVE'], 'pages': {'app': 'App'},
        'steps': [
            {'action': 'call', 'page': 'app', 'method': 'prepare'},
            {'action': 'call', 'page': 'app', 'method': 'prepare'},
            {'action': 'call', 'page': 'app', 'method': 'prepare'},
            {'action': 'click', 'target': {'testid': 'ok'}, 'pom_gap': 'test fixture semantic click'},
            {'action': 'expect', 'page': 'app', 'method': 'saved', 'value': True}
        ]
    }
    if scenario in {'repair', 'product_bug', 'flaky', 'env', 'always_wrong', 'change_oracle', 'fake_pass'}:
        case['steps'][3]['target']['testid'] = 'wrong'
    if request.get('previous_case'):
        case = copy.deepcopy(request['previous_case'])
        if scenario != 'always_wrong':
            case['steps'][3]['target']['testid'] = 'ok'
        if scenario == 'change_oracle':
            case['steps'][-1]['value'] = False
    if scenario == 'invalid_case' or (scenario == 'invalid_then_valid' and request.get('feedback') is None):
        case = {}
    result = {'case': case}
else:
    category = scenario if scenario in {'product_bug', 'flaky', 'env'} else 'case_error'
    if scenario == 'fake_pass':
        category = 'passed'
    result = {'category': category, 'summary': 'Observed the actual locator failure', 'evidence': ['junit.xml']}
if scenario == 'opencode':
    print(json.dumps({'type': 'text', 'part': {'text': json.dumps(result)}}))
else:
    print(json.dumps(result))
'''

CONFTEST = '''import pytest
class App:
    def prepare(self): pass
    def saved(self): return True
class Page:
    def get_by_test_id(self, key):
        self.key = key
        return self
    def click(self):
        if self.key != 'ok':
            raise RuntimeError('semantic target not found: ' + self.key)
@pytest.fixture
def app(): return App()
@pytest.fixture
def page(): return Page()
'''


def project(tmp_path, scenario="pass", count=1):
    (tmp_path / "agent.py").write_text(AGENT, encoding="utf-8")
    (tmp_path / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (tmp_path / "requirement.md").write_text("REQ-SAVE: Save must persist successfully; saved() is True.", encoding="utf-8")
    atomic_json(tmp_path / "snapshot.json", {"snapshotVersion": 2, "complete": True,
                "url": "http://127.0.0.1:3000/save", "ariaSnapshot": '- button "Save"', "nodes": []})
    agents = {role: {"protocol": "json", "command": [sys.executable, str(tmp_path / "agent.py"), scenario]}
              for role in ["explorer", "author", "verifier"]}
    atomic_json(tmp_path / "config.json", {"version": 1, "test_environment": True,
                "base_url": "http://127.0.0.1:3000", "agents": agents, "max_workers": 2})
    atomic_json(tmp_path / "tasks.json", {"version": 1, "tasks": [
        {"id": f"SAVE-{i + 1:03}", "goal": "Verify save", "url": "/save", "snapshot": "snapshot.json",
         "sources": [{"id": "REQ-SAVE", "path": "requirement.md"}]} for i in range(count)
    ]})
    return tmp_path


def run(root, run_id="test"):
    return main(["run", "--root", str(root), "--config", str(root / "config.json"),
                 "--tasks", str(root / "tasks.json"), "--run-id", run_id])


def task_directory(root, task="SAVE-001"):
    return root / ".snap/runs/test/tasks" / task


def calls(root, role=None):
    data = [json.loads(line) for line in (root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    return [item for item in data if role is None or item["role"] == role]


def set_config(root, **values):
    config = read_json(root / "config.json")
    config.update(values)
    atomic_json(root / "config.json", config)


def test_parallel_tasks_compile_and_execute_in_separate_directories(tmp_path):
    root = project(tmp_path, count=2)
    assert run(root) == 0
    summary = read_json(root / ".snap/runs/test/summary.json")
    assert summary["passed"] and len(summary["tasks"]) == 2
    for task in ["SAVE-001", "SAVE-002"]:
        directory = task_directory(root, task)
        state = read_json(directory / "state.json")
        assert state["stage"] == "done" and state["status"] == "passed"
        report = read_json(directory / "attempts/01/execution.json")
        assert report["counts"] == {"tests": 1, "failed": 0, "errors": 0, "skipped": 0}
        assert list((directory / "attempts/01/generated").glob("test_*.py"))
    assert len(calls(root, "explorer")) == len(calls(root, "author")) == 2
    assert not calls(root, "verifier")
    assert not (root / "tests").exists() and not (root / "spec").exists()
    # Completed runs resume without launching agents or rerunning tests.
    before = (root / "calls.jsonl").read_bytes()
    assert main(["resume", "--run", str(root / ".snap/runs/test")]) == 0
    assert (root / "calls.jsonl").read_bytes() == before


def test_only_case_error_repairs_target_then_requires_real_pytest_pass(tmp_path):
    root = project(tmp_path, "repair")
    assert run(root) == 0
    directory = task_directory(root)
    first, second = [read_json(directory / f"attempts/0{i}/case.json") for i in (1, 2)]
    assert first["steps"][3]["target"] != second["steps"][3]["target"]
    assert oracle_projection(first) == oracle_projection(second)
    assert read_json(directory / "attempts/01/execution.json")["outcome"] == "failed"
    assert read_json(directory / "attempts/02/execution.json")["outcome"] == "passed"
    assert len(calls(root, "author")) == 2 and len(calls(root, "verifier")) == 1


def test_opencode_adapter_attaches_request_and_consumes_jsonl(tmp_path):
    root = project(tmp_path, "opencode")
    config = read_json(root / "config.json")
    for adapter in config["agents"].values():
        adapter["protocol"] = "opencode"
    atomic_json(root / "config.json", config)
    assert run(root) == 0
    assert len(calls(root, "author")) == 1


@pytest.mark.parametrize("category", ["product_bug", "flaky", "env"])
def test_non_case_errors_stop_without_rewriting(tmp_path, category):
    root = project(tmp_path, category)
    assert run(root) == 1
    state = read_json(task_directory(root) / "state.json")
    assert state["status"] == "blocked" and state["category"] == category
    assert len(calls(root, "author")) == 1


@pytest.mark.parametrize("scenario", ["always_wrong", "invalid_case"])
def test_attempt_budget_caps_execution_and_static_gate_loops(tmp_path, scenario):
    root = project(tmp_path, scenario)
    assert run(root) == 1
    state = read_json(task_directory(root) / "state.json")
    assert state["status"] == "exhausted" and state["attempt"] == 3
    assert len(calls(root, "author")) == 3
    if scenario == "invalid_case":
        assert not list(task_directory(root).rglob("generated"))
        assert not calls(root, "verifier")


def test_attempted_oracle_change_is_blocked_before_second_execution(tmp_path):
    root = project(tmp_path, "change_oracle")
    assert run(root) == 1
    state = read_json(task_directory(root) / "state.json")
    assert "oracle" in state["summary"]
    assert not (task_directory(root) / "attempts/02/execution.json").exists()


def test_rejected_structural_draft_can_be_completed_before_baseline_is_locked(tmp_path):
    root = project(tmp_path, "invalid_then_valid")
    assert run(root) == 0
    directory = task_directory(root)
    second_request = read_json(directory / "attempts/02/author.request.json")
    assert second_request["previous_case"] is None and second_request["rejected_draft"] == {}
    assert not (directory / "attempts/01/execution.json").exists()
    assert read_json(directory / "attempts/02/execution.json")["outcome"] == "passed"


def test_skipped_execution_is_blocked_even_with_exit_zero(tmp_path):
    root = project(tmp_path)
    (root / "conftest.py").write_text(CONFTEST.replace("def app(): return App()", "def app(): pytest.skip('missing account')"), encoding="utf-8")
    assert run(root) == 1
    state = read_json(task_directory(root) / "state.json")
    assert state["category"] == "env" and "skipped" in state["summary"]
    assert not calls(root, "verifier")


@pytest.mark.parametrize("scenario", ["bad_json", "timeout", "oversize", "fake_pass"])
def test_invalid_agent_transport_never_certifies_pass(tmp_path, scenario):
    root = project(tmp_path, scenario)
    if scenario == "timeout":
        set_config(root, agent_timeout=0.2)
    if scenario == "oversize":
        set_config(root, max_output_bytes=100000)
    assert run(root) == 1
    state = read_json(task_directory(root) / "state.json")
    assert state["status"] == "blocked" and state["category"] == "env"


@pytest.mark.parametrize("role", ["explorer", "author", "verifier"])
def test_agent_can_abstain_without_fabricating_a_case_or_retrying(tmp_path, role):
    root = project(tmp_path, "repair" if role == "verifier" else "pass")
    script = root / "abstain.py"
    script.write_text('import json,sys\njson.load(sys.stdin)\nprint(json.dumps({"blocked":{"reason":"missing requirement evidence","needed":["approved expected value"]}}))\n', encoding="utf-8")
    config = read_json(root / "config.json")
    config["agents"][role]["command"] = [sys.executable, str(script)]
    atomic_json(root / "config.json", config)
    assert run(root) == 1
    state = read_json(task_directory(root) / "state.json")
    assert state["status"] == "blocked" and state["category"] is None
    assert "approved expected value" in state["summary"] and state["attempt"] == 1


def staged_runner(root, stage="author"):
    inputs = prepare_inputs(root, root / "config.json", root / "tasks.json")
    run_dir = root / ".snap/runs/test"
    run_dir.mkdir(parents=True)
    runner = TaskRunner(run_dir, inputs, inputs["tasks"][0], Event())
    runner.snapshot()
    runner.agent("explorer", snapshot=read_json(runner.directory / "snapshot.json"))
    runner.checkpoint("author", list(runner.directory.glob("explorer.*")))
    if stage in {"execute", "triage"}:
        runner.author()
        runner.gate()
    if stage == "triage":
        runner.execute()
    return inputs, run_dir, runner


def test_resume_starts_at_saved_stage_without_reexploring(tmp_path):
    root = project(tmp_path)
    inputs, run_dir, runner = staged_runner(root)
    assert run_scheduler(run_dir, inputs)["passed"]
    assert len(calls(root, "explorer")) == 1
    assert len(calls(root, "author")) == 1


def test_uncertain_interrupted_execution_is_never_replayed(tmp_path):
    root = project(tmp_path)
    inputs, run_dir, runner = staged_runner(root, "execute")
    runner.start_call()  # Crash after dispatch, before durable execution receipt.
    assert not run_scheduler(run_dir, inputs)["passed"]
    state = read_json(runner.state_file)
    assert state["category"] == "env" and "interrupted" in state["summary"]
    assert not list(runner.directory.rglob("junit.xml"))


def test_completed_execution_receipt_recovers_without_replay(tmp_path):
    root = project(tmp_path)
    inputs, run_dir, runner = staged_runner(root, "triage")
    report = runner.attempt_dir / "junit.xml"
    before = report.stat().st_mtime_ns
    runner.state["stage"] = "execute"
    runner.start_call()  # Simulate lost stage transition after execution.json was committed.
    assert run_scheduler(run_dir, inputs)["passed"]
    assert report.stat().st_mtime_ns == before


@pytest.mark.parametrize("changed", ["source", "artifact"])
def test_resume_refuses_changed_inputs_or_artifacts_before_any_new_call(tmp_path, changed):
    root = project(tmp_path)
    inputs, run_dir, runner = staged_runner(root)
    path = root / "requirement.md" if changed == "source" else runner.directory / "snapshot.json"
    path.write_text("modified", encoding="utf-8")
    before = len(calls(root))
    with pytest.raises(HarnessError, match="changed|checkpoint"):
        run_scheduler(run_dir, inputs)
    assert len(calls(root)) == before


def test_malformed_checkpoint_cannot_skip_stages(tmp_path):
    root = project(tmp_path)
    inputs, run_dir, runner = staged_runner(root)
    state = read_json(runner.state_file)
    state["status"] = "passed"
    atomic_json(runner.state_file, state)
    before = len(calls(root))
    with pytest.raises(HarnessError, match="stage/status mismatch"):
        run_scheduler(run_dir, inputs)
    assert len(calls(root)) == before


def test_os_run_lock_rejects_concurrent_scheduler_and_releases(tmp_path):
    with run_lock(tmp_path):
        with pytest.raises(HarnessError, match="already owned"):
            with run_lock(tmp_path):
                pytest.fail("concurrent ownership")
    with run_lock(tmp_path):
        pass


@pytest.mark.parametrize("xml,code", [
    ("<testsuites/>", 0),
    ("<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>", 0),
    ("<testsuites><testsuite><testcase><failure/></testcase></testsuite></testsuites>", 0),
    ("<testsuites><testsuite><testcase/></testsuite></testsuites>", 5),
])
def test_exit_zero_or_empty_skipped_inconsistent_reports_are_not_pass(tmp_path, xml, code):
    report = tmp_path / "junit.xml"
    report.write_text(xml, encoding="utf-8")
    assert execution_outcome({"returncode": code, "reason": None}, report, 10000)["outcome"] == "env"


def test_opencode_event_stream_is_parsed_as_strict_assistant_json():
    events = [{"type": "step_start"}, {"type": "text", "part": {"text": '{"case":'}},
              {"type": "text", "part": {"text": '{}}'}}, {"type": "step_finish"}]
    assert agent_result("\n".join(map(json.dumps, events)), "opencode") == {"case": {}}
    for raw in ['{"type":"error","error":"quota"}', '{"type":"step_finish"}',
                '{"type":"text","part":{"text":"{}","synthetic":true}}',
                '{"type":"text","part":{"text":"```json\\n{}\\n```"}}']:
        with pytest.raises(HarnessError):
            agent_result(raw, "opencode")


def test_json_protocol_rejects_duplicate_keys_and_non_finite_numbers():
    for raw in ['{"case":{},"case":{}}', '{"case":{"x":NaN}}', '{"case":{"x":1e999}}']:
        with pytest.raises(HarnessError):
            agent_result(raw, "json")


def test_cancellation_stops_an_inflight_process(tmp_path):
    request = tmp_path / "request.json"
    atomic_json(request, {})
    stop = Event()
    timer = Timer(0.2, stop.set)
    timer.start()
    try:
        receipt = run_process([sys.executable, "-c", "import time; time.sleep(10)"],
                              cwd=tmp_path, env=dict(os.environ), request=request,
                              prefix=tmp_path / "worker", timeout=5, limit=1024, stop=stop)
        assert receipt["reason"] == "cancelled" and receipt["returncode"] != 0
    finally:
        timer.cancel()


@pytest.mark.parametrize("mutation", ["production", "duplicate_id", "outside_source", "wrong_origin", "wrong_snapshot", "report_override"])
def test_invalid_jobs_are_rejected_before_agent_launch(tmp_path, mutation):
    root = project(tmp_path)
    tasks = read_json(root / "tasks.json")
    if mutation == "production":
        set_config(root, test_environment=False)
    elif mutation == "duplicate_id":
        tasks["tasks"] *= 2
    elif mutation == "outside_source":
        tasks["tasks"][0]["sources"][0]["path"] = "../outside.md"
    elif mutation == "wrong_origin":
        tasks["tasks"][0]["url"] = "https://example.com/production"
    elif mutation == "wrong_snapshot":
        tasks["tasks"][0]["url"] = "/other"
    elif mutation == "report_override":
        set_config(root, pytest_args=["--junitxml=forged.xml"])
    atomic_json(root / "tasks.json", tasks)
    assert run(root) == 2
    assert not (root / "calls.jsonl").exists()
