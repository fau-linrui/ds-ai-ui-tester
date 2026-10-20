import pytest


@pytest.mark.ems_report
@pytest.mark.p1
def test_report_export(report_page):
    """报表导出（页面对象还是空壳）。"""
    report_page.page.goto("/ems/report")
    report_page.page.get_by_role("button", name="导出 PDF").click()
    assert report_page.page.get_by_text("导出成功").is_visible()
