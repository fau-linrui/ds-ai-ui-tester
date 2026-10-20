import pytest
from playwright.sync_api import Page

from pages.ems.alarm_center_page import AlarmCenterPage
from pages.ems.report_page import ReportPage
from pages.ems.station_page import StationPage


@pytest.fixture(scope="session")
def logged_in(page: Page) -> Page:
    page.goto("/login")
    page.get_by_role("textbox", name="用户名").fill("ems_auto")
    page.get_by_role("textbox", name="密码").fill("***")
    page.get_by_role("button", name="登录").click()
    page.wait_for_url("**/dashboard")
    return page


@pytest.fixture
def alarm_center(logged_in: Page) -> AlarmCenterPage:
    return AlarmCenterPage(logged_in)


@pytest.fixture
def station_page(logged_in: Page) -> StationPage:
    return StationPage(logged_in)


@pytest.fixture
def report_page(logged_in: Page) -> ReportPage:
    return ReportPage(logged_in)
