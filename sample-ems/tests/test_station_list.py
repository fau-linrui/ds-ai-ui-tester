import pytest


@pytest.mark.ems_station
@pytest.mark.p1
def test_station_search(station_page):
    """电站搜索（历史写法：直接操作页面，未走 POM）。"""
    station_page.page.goto("/ems/station")
    station_page.page.locator("input[name=keyword]").fill("华东")
    station_page.page.get_by_role("button", name="搜索").click()
    assert station_page.page.locator(".station-card").count() > 0


@pytest.mark.ems_station
@pytest.mark.p1
def test_station_detail(page):
    """电站详情（完全没走 POM）。"""
    page.goto("/ems/station")
    page.locator("tr.station-row").first.click()
    assert page.get_by_text("电站详情").is_visible()
