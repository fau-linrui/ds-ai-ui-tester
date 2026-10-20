from pages.base_page import BasePage


class StationPage(BasePage):
    """电站列表。"""

    def open(self) -> None:
        self.page.goto("/ems/station")

    def search_by_name(self, name: str) -> None:
        self.page.find_element("css selector", "input[name=keyword]").send_keys(name)
        self.page.find_element("css selector", "button.search").click()
