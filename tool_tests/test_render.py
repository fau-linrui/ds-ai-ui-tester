"""工具链自身的测试。渲染器是单点故障，必须有黄金文件级断言。

跑法：python -m pytest tool_tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lint import check_case  # noqa: E402
from tools.render import render_case  # noqa: E402


def make_case(**overrides) -> dict:
    case = {"id": "X-001", "title": "示例", "requirement": "REQ-1", "steps": []}
    case.update(overrides)
    return case


def test_goto_and_role_exact() -> None:
    case = make_case(steps=[
        {"action": "goto", "url": "/"},
        {"action": "click", "target": {"role": "link", "name": "社区", "exact": True}},
    ])
    out = render_case(case, "spec/cases/x-001.json")
    assert 'page.goto(base_url + "/")' in out
    assert 'page.get_by_role("link", name="社区", exact=True).click()' in out
    assert "def test_x_001(page: Page, base_url: str) -> None:" in out
    assert "改 spec/cases/x-001.json 后重新生成" in out


def test_call_injects_fixture_and_asserts() -> None:
    case = make_case(
        fixtures=["alarm_center"],
        pages={"alarm_center": "AlarmCenterPage"},
        steps=[
            {"action": "call", "page": "alarm_center", "method": "filter_by_level", "args": {"level": "严重"}},
            {"action": "expect", "page": "alarm_center", "method": "row_count", "matcher": "gt", "value": 0},
        ],
    )
    out = render_case(case, "spec/cases/x-001.json")
    assert "def test_x_001(alarm_center) -> None:" in out
    assert 'alarm_center.filter_by_level(level="严重")' in out
    assert "assert alarm_center.row_count() > 0" in out
    # 没有 page 相关步骤，不应出现 page 参数
    assert "(page: Page" not in out


def test_raw_emits_todo_and_operation() -> None:
    case = make_case(steps=[
        {"action": "raw", "target": {"role": "button", "name": "刷新"}, "pom_gap": "AlarmCenterPage.refresh"},
        {"action": "raw", "target": {"role": "textbox", "name": "关键字"},
         "operation": "fill", "value": "华东", "pom_gap": "StationPage.fill_keyword"},
    ])
    out = render_case(case, "spec/cases/x-001.json")
    assert "# TODO(pom-gap): AlarmCenterPage.refresh" in out
    assert 'page.get_by_role("button", name="刷新").click()' in out
    assert 'page.get_by_role("textbox", name="关键字").fill("华东")' in out


def test_sources_rendered_as_header() -> None:
    case = make_case(sources=["REQ-1", "TC-9"], steps=[
        {"action": "goto", "url": "/x"},
        {"action": "expectUrl", "contains": "/x"},
    ])
    out = render_case(case, "spec/cases/x-001.json")
    assert "# sources: REQ-1, TC-9" in out
    assert 'to_have_url(re.compile("/x"))' in out


def test_lint_rejects_selector_target() -> None:
    case = make_case(steps=[
        {"action": "click", "target": {"testid": "div:nth-child(3) > button"}},
        {"action": "expect", "target": {"role": "button"}, "assertion": {"type": "visible"}},
    ])
    errors, _ = check_case(case, 0.2)
    assert any("疑似选择器" in e for e in errors)


def test_lint_rejects_weak_assertion() -> None:
    case = make_case(steps=[
        {"action": "goto", "url": "/"},
        {"action": "expect", "target": {"role": "button"}, "assertion": {"type": "visible"}},
    ])
    errors, stats = check_case(case, 0.2)
    assert stats["assertions"] == 0
    assert any("缺少有效断言" in e for e in errors)


def test_lint_rejects_undeclared_page() -> None:
    case = make_case(steps=[{"action": "call", "page": "ghost_page", "method": "open"}])
    errors, _ = check_case(case, 0.2)
    assert any("未在 pages/fixtures 中声明" in e for e in errors)
