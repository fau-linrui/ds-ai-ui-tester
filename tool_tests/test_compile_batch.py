"""The compiled suite must match the complete input set without losing user files."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def write_case(path, case_id="BATCH-001", **overrides):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"id": case_id, "title": "Batch case", "steps": [
        {"action": "goto", "url": "/"}, {"action": "expectUrl", "contains": "/"},
    ]}
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")


def run(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "render.py"), *map(str, args)],
        cwd=tmp_path, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )


def test_recursive_generation_and_check_cover_nested_cases(tmp_path):
    cases, out = tmp_path / "cases", tmp_path / "out"
    write_case(cases / "one.json")
    write_case(cases / "nested" / "two.json", "BATCH-002")
    assert run(tmp_path, "--all", cases, "--out-dir", out).returncode == 0
    assert {p.name for p in out.glob("test_*.py")} == {"test_batch_001.py", "test_batch_002.py"}
    assert run(tmp_path, "--all", cases, "--out-dir", out, "--check").returncode == 0
    write_case(cases / "nested" / "two.json", "BATCH-002", title="Changed nested case")
    checked = run(tmp_path, "--all", cases, "--out-dir", out, "--check")
    assert checked.returncode == 1
    assert "DIRTY" in checked.stdout and "test_batch_002.py" in checked.stdout


def test_check_reports_orphan_but_preserves_handwritten_tests(tmp_path):
    cases, out = tmp_path / "cases", tmp_path / "out"
    write_case(cases / "one.json")
    write_case(cases / "two.json", "BATCH-002")
    assert run(tmp_path, "--all", cases, "--out-dir", out).returncode == 0
    (out / "test_handwritten.py").write_text("def test_handwritten():\n    assert True\n", encoding="utf-8")
    assert run(tmp_path, "--all", cases, "--out-dir", out, "--check").returncode == 0
    (cases / "two.json").unlink()
    checked = run(tmp_path, "--all", cases, "--out-dir", out, "--check")
    assert checked.returncode == 1 and "ORPHAN" in checked.stdout
    assert (out / "test_batch_002.py").exists()
    assert (out / "test_handwritten.py").exists()


@pytest.mark.parametrize("invalid", ["duplicate", "malformed"])
def test_entire_batch_is_validated_before_any_output_is_written(tmp_path, invalid):
    cases, out = tmp_path / "cases", tmp_path / "out"
    write_case(cases / "a.json")
    if invalid == "duplicate":
        write_case(cases / "b.json")
    else:
        write_case(cases / "b.json", "BATCH-002", steps=[{"action": "click", "target": {"css": ".save"}}])
    result = run(tmp_path, "--all", cases, "--out-dir", out)
    assert result.returncode == 1
    assert not out.exists()


def test_never_overwrites_a_handwritten_test(tmp_path):
    cases, out = tmp_path / "cases", tmp_path / "out"
    write_case(cases / "a.json")
    write_case(cases / "b.json", "BATCH-002")
    out.mkdir()
    manual = out / "test_batch_002.py"
    manual.write_text("# user-owned\n", encoding="utf-8")
    result = run(tmp_path, "--all", cases, "--out-dir", out)
    assert result.returncode == 1 and "unmanaged" in result.stdout
    assert manual.read_text(encoding="utf-8") == "# user-owned\n"
    assert not (out / "test_batch_001.py").exists()


def test_single_case_check_never_rewrites_dirty_output(tmp_path):
    case, out = tmp_path / "case.json", tmp_path / "test_output.py"
    write_case(case)
    assert run(tmp_path, case, "--out", out).returncode == 0
    out.write_text(out.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    result = run(tmp_path, case, "--out", out, "--check")
    assert result.returncode == 1 and "DIRTY" in result.stdout
    assert out.read_text(encoding="utf-8").endswith("# changed\n")
