import pytest


@pytest.mark.ems_alarm
@pytest.mark.p0
def test_filter_alarm_by_level(alarm_center):
    """按等级筛选告警。"""
    alarm_center.filter_by_level("严重")
    assert alarm_center.row_count() > 0


@pytest.mark.ems_alarm
@pytest.mark.p1
def test_export_alarm(alarm_center):
    """导出告警。"""
    alarm_center.export()
