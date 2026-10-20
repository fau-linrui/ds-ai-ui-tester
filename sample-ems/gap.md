# POM 覆盖与缺口报告

- 业务 Page 类：4 个（被测试引用 3 个）
- POM 方法：6 个
- 测试函数：5 个
- 定位绕过：7 次（1.40 次/函数）
- POM 内部债务：find_element=2、sleep=1、页面内断言=0

## 0. 结论摘要

- 空壳 Page：1 个（ReportPage）
- 完全未使用 POM 的测试：1 个
- 用例缺口：2 项；raw 降级：1 处

## 1. 未被测试引用的 Page

- `BasePage`

## 2. 用例缺口

| Page | 方法 | 出现次数 |
|---|---|---|
| `StationPage` | `search_by_keyword` | 1 |
| `ReportPage` | `export_as_pdf` | 1 |

> 这些是 AI 生成用例需要、但 POM 尚未提供的能力。优先补高频项。

## 3. raw 降级步骤

| 用例 | 待补 POM |
|---|---|
| EMS-ALARM-003 | `AlarmCenterPage.refresh` |

## 4. POM 绕过热点

| 文件 | 定位调用次数 |
|---|---|
| `tests/test_station_list.py` | 5 |
| `tests/test_report_export.py` | 2 |
