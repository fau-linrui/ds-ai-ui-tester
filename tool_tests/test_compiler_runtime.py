"""Execute generated code against controlled results, including known bad results."""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tools.render import render_case


def case(steps, **overrides):
    spec = dict(id="RUNTIME-001", title="Runtime contract", steps=steps)
    spec.update(overrides)
    return spec


def function(spec):
    scope = {}
    exec(compile(render_case(spec, "runtime.json"), "generated.py", "exec"), scope)
    return scope["test_runtime_001"]


def test_python_literals_survive_nested_pom_arguments_and_expected_values():
    received = {}

    class Service:
        def configure(self, **kwargs):
            received.update(kwargs)

        def result(self):
            return {"enabled": True, "values": [None, False, 2]}

    payload = {"enabled": True, "values": [None, False, 2]}
    spec = case([
        {"action": "call", "page": "service", "method": "configure", "args": {"payload": payload, "flag": False, "optional": None}},
        {"action": "expect", "page": "service", "method": "result", "value": payload},
    ], fixtures=["service"])
    function(spec)(Service())
    assert received == {"payload": payload, "flag": False, "optional": None}


class FakePage:
    def __init__(self, body, status=200):
        self.response = SimpleNamespace(status=status, json=lambda: body, url="http://app/api/save", request=SimpleNamespace(method="POST"))
        self.events = []
        self.mocks = []

    @contextmanager
    def expect_response(self, predicate):
        assert predicate(self.response)
        self.events.append("watch")
        yield SimpleNamespace(value=self.response)
        self.events.append("response")

    def get_by_role(self, *args, **kwargs):
        return self

    def click(self, **kwargs):
        self.events.append("click")

    def route(self, url, callback):
        route = SimpleNamespace(fulfill=lambda **kwargs: self.mocks.append((url, kwargs)))
        callback(route)
        self.events.append("route")


def network_spec(**overrides):
    return case([
        {"action": "captureNetwork", "name": "save", "urlContains": "/api/save", "method": "POST"},
        {"action": "click", "target": {"role": "button", "name": "Save"}},
        {"action": "expectNetwork", "name": "save", "status": 201, "equals": {"success": True}},
    ], **overrides)


def test_network_capture_brackets_trigger_and_checks_response_body():
    page = FakePage({"success": True}, status=201)
    function(network_spec())(page)
    assert page.events == ["watch", "click", "response"]


@pytest.mark.parametrize("body,status", [({"success": False}, 201), ({"success": True}, 500)])
def test_known_bad_response_must_fail(body, status):
    with pytest.raises(AssertionError):
        function(network_spec())(FakePage(body, status=status))


@pytest.mark.parametrize("payload", [None, False, {"success": True, "items": [None, False]}])
def test_routes_are_installed_before_actions_and_preserve_json_values(payload):
    page = FakePage({"success": True}, status=201)
    spec = network_spec(routes=[{"url": "**/api/save", "status": 201, "json": payload}])
    function(spec)(page)
    assert page.events == ["route", "watch", "click", "response"]
    url, fulfilled = page.mocks[0]
    assert url == "**/api/save"
    assert fulfilled["status"] == 201
    assert fulfilled["content_type"] == "application/json"
    assert json.loads(fulfilled["body"]) == payload


def test_multiline_metadata_stays_data_and_cannot_change_test_body():
    called = []
    spec = case([{"action": "call", "page": "service", "method": "open"}],
                fixtures=["service"], title='quotes """\nraise RuntimeError("injected")',
                requirement="REQ-1\nraise RuntimeError('injected')",
                sources=["DOC-1\nraise RuntimeError('injected')"])
    fn = function(spec)
    fn(SimpleNamespace(open=lambda: called.append(True)))
    assert called == [True]
    assert spec["title"] in fn.__doc__
