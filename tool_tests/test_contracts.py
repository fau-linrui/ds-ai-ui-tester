"""Invalid or unsupported case intent must fail before generating executable code."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tools.contracts import CaseValidationError, validate_case
from tools.lint import check_case
from tools.render import render_case


def valid_case(**overrides):
    case = {
        "id": "CONTRACT-001", "title": "Contract test", "priority": "P1",
        "requirement": "REQ-1",
        "steps": [{"action": "goto", "url": "/"}, {"action": "expectUrl", "contains": "/"}],
    }
    case.update(overrides)
    return case


@pytest.mark.parametrize("step", [
    {"action": "fill", "target": {"label": "Name"}, "value": {"wrong": True}},
    {"action": "fill", "target": {"label": "Name"}},
    {"action": "click", "target": {}},
    {"action": "click", "target": {"css": ".save"}},
    {"action": "click", "target": {"role": "button", "testid": "save"}},
    {"action": "click", "target": {"testid": "save", "exact": False}},
    {"action": "click", "target": {"role": "button"}, "ignored": True},
    {"action": "click", "target": {"label": "Name", "within": {"selector": "div"}}},
    {"action": "expectUrl"},
    {"action": "expectUrl", "contains": "/", "equals": "http://localhost/"},
    {"action": "expect", "target": {"role": "row"}, "assertion": {"type": "count"}},
    {"action": "expect", "target": {"role": "row"}, "assertion": {"type": "count", "value": True}},
    {"action": "expect", "page": "p", "method": "count"},
    {"action": "raw", "target": {"role": "button"}},
    {"action": "raw", "operation": "fill", "target": {"label": "Name"}, "pom_gap": "fill_name"},
    {"action": "expectNetwork", "name": "missing"},
    {"action": "captureNetwork", "url_contains": "/api/save"},
])
def test_linter_and_renderer_reject_invalid_steps(step):
    case = valid_case(steps=[step, {"action": "expectUrl", "contains": "/"}])
    assert check_case(case, 0.2)[0]
    with pytest.raises(CaseValidationError):
        render_case(case, "case.json")


@pytest.mark.parametrize("field,value", [
    ("unsupported", True), ("fixtures", ["class"]), ("fixtures", ["bad-name"]),
    ("routes", [{"url": "**/api", "status": 200, "ignored": True}]),
    ("sources", [""]), ("risk", float("nan")),
    ("id", "CONTRACT-001\n"),
])
def test_rejects_invalid_top_level_data(field, value):
    case = valid_case(**{field: value})
    assert check_case(case, 0.2)[0]
    with pytest.raises(CaseValidationError):
        validate_case(case)


def test_network_capture_must_have_trigger_and_unambiguous_names():
    trigger = {"action": "click", "target": {"role": "button", "name": "Save"}}
    cap = {"action": "captureNetwork", "name": "save", "urlContains": "/api/save"}
    for steps in ([cap], [cap, cap, trigger], [cap, trigger, cap, trigger]):
        with pytest.raises(CaseValidationError):
            validate_case(valid_case(steps=deepcopy(steps)))
    second = dict(cap, name="second")
    with pytest.raises(CaseValidationError):
        validate_case(valid_case(steps=[cap, trigger, second, trigger, {"action": "expectNetwork"}]))


def test_declared_pom_and_python_identifiers_are_checked():
    for step in (
        {"action": "call", "page": "ghost", "method": "open"},
        {"action": "call", "page": "p", "method": "open", "args": {"class": "x"}},
        {"action": "call", "page": "p", "method": "open", "args": {"bad-name": "x"}},
        {"action": "call", "page": "p", "method": "open\n"},
    ):
        with pytest.raises(CaseValidationError):
            validate_case(valid_case(fixtures=["p"], steps=[step]))


def test_direct_pom_actions_cannot_bypass_raw_budget():
    case = valid_case(pages={"p": "ExistingPage"}, steps=[
        {"action": "click", "target": {"role": "button", "name": "Save"}},
        {"action": "expectUrl", "contains": "/"},
    ])
    errors, stats = check_case(case, 0.2)
    assert stats["raw"] == 1
    assert any("pom_gap" in e for e in errors)
    assert any("预算" in e for e in errors)
    # Project policy also applies when an author omits the POM declaration.
    case.pop("pages")
    assert check_case(case, 0.2, require_pom=True)[0]


def test_absolute_href_and_scoped_targets_remain_valid():
    case = valid_case(steps=[
        {"action": "click", "target": {"href": "https://example.test/x", "name": "Details",
         "within": {"role": "navigation", "name": "Main"}}},
        {"action": "expectUrl", "contains": "/x"},
    ])
    assert check_case(case, 0.2)[0] == []


def test_ui_mode_cannot_disguise_pom_calls():
    with pytest.raises(CaseValidationError):
        validate_case(valid_case(mode="ui", pages={"p": "ExistingPage"}))
