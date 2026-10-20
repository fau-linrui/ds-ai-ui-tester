class BasePage:
    """所有页面的基类。"""

    def __init__(self, page):
        self.page = page

    def wait_ready(self) -> None:
        """等待页面主区域渲染完成。"""
        self.page.wait_for_load_state("networkidle")
