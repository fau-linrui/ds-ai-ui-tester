import time

from pages.base_page import BasePage


class AlarmCenterPage(BasePage):
    """告警中心。"""

    def open(self) -> None:
        """进入告警中心。"""
        self.page.goto("/ems/alarm")

    def filter_by_level(self, level: str) -> None:
        """按告警等级筛选。"""
        self.page.get_by_role("combobox", name="告警等级").select_option(level)
        self.page.get_by_role("button", name="查询").click()

    def row_count(self) -> int:
        """当前列表行数。"""
        return self.page.get_by_role("row").count()

    def export(self) -> None:
        """导出报表（老写法，等接口稳定后改）。"""
        self.page.get_by_role("button", name="导出").click()
        time.sleep(3)
