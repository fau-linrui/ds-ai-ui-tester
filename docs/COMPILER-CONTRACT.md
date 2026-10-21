# 用例编译与验证契约（v0.5）

本轮保留 JSON → pytest + Playwright Python 的主线，修复输入契约与执行语义之间的缺口。
当前可执行行为以 `spec/case.schema.json`、`tools/contracts.py` 和回归测试为准。

## 验证顺序

1. `lint` 与 `render` 共用严格 JSON Schema 和语义检查；未知字段、非法类型、未知网络捕获、重复捕获名直接失败。
2. `lint` 另外检查断言种类、来源字段和 POM 降级预算。来源非空不等于来源可信，仍须核对原始需求。
3. `render` 先校验并编译整批输入，再写输出。重复 ID、非法用例和人工文件冲突不会产生半批生成物。
4. `render --check` 检查缺失、内容变化及多余的生成测试；只识别工具头标记，不删除或覆盖人工测试。
5. 运行生成代码，验证实际业务预期；仅通过静态检查不表示用例正确。

```powershell
python tools/lint.py --cases spec/cases
python tools/render.py --all spec/cases
python tools/render.py --all spec/cases --check
python -m pytest tests -k order_ -q
```

`--all` 递归扫描子目录。一组输入目录对应一个完整生成输出集合。
只生成子目录时，应指定独立的 `--out-dir`，否则其他组的生成文件会被报告为 ORPHAN。
删除用例后，需要人工确认并删除对应的过期生成文件，再运行检查；工具不会自动删除文件。
单文件 `CASE --out FILE --check` 只检查该文件。

## 网络与 mock

```json
{
  "id": "SAVE-001",
  "title": "保存成功",
  "priority": "P1",
  "sources": ["REQ-SAVE-1"],
  "routes": [{"url": "**/api/save", "status": 201, "json": {"saved": true}}],
  "steps": [
    {"action": "goto", "url": "/settings"},
    {"action": "captureNetwork", "name": "save", "urlContains": "/api/save", "method": "POST"},
    {"action": "click", "target": {"role": "button", "name": "保存", "exact": true}},
    {"action": "expectNetwork", "name": "save", "status": 201, "equals": {"saved": true}}
  ]
}
```

- `routes` 在测试步骤前注册；`status` 默认 200，`json` 默认 `{}`，支持 JSON 的 `null`、布尔值和嵌套数据。
- mock 在 fixture setup 完成后生效。登录或 POM fixture 初始化期间的请求，需要由原有 fixture 管理。
- `captureNetwork` 使用 `urlContains`，可指定 HTTP `method`；必须紧跟一个实际触发动作，不支持嵌套捕获步骤。
- `expectNetwork.status` 默认 200；`equals` 是响应 JSON 的完整相等校验，不是部分字段匹配。
- 只有一个已声明捕获时，可省略 `expectNetwork.name`。多个捕获必须明确引用名称。
- 写操作涉及的业务副作用、请求体与数据库状态，并不会因为状态码正确而自动得到验证。

## 区域定位与 POM 策略

```json
{
  "action": "click",
  "target": {
    "role": "button", "name": "保存", "exact": true,
    "within": {"role": "dialog", "name": "编辑通道", "exact": true}
  }
}
```

`within` 可以递归嵌套；不会通过 `.first()` 或 DOM 序号消除歧义。
一个 target 使用一种定位策略，链接可以组合 `href` 与 `role=link` / `name`。
`exact` 对 role、label、placeholder、text 和链接名称生效。

普通 demo 默认为 `ui` 模式；包含 `pages` 或 POM 调用时自动采用 `pom` 策略，也可显式声明 `mode: pom`。
POM 模式下，直接 `click/fill/select/check/press/waitFor` 与 `raw` 都计入降级预算，并须带 `pom_gap`。
每条用例和整个 POM 用例集合均执行预算检查。
接入已有框架时，使用 `lint --require-pom` 强制该策略，避免靠漏写 `pages` 绕过。

`pages` 仅声明现有 fixture 的类型信息，不会自动生成 fixture 或实例化 Page 类。
POM 方法、fixture 实际作用域及返回语义仍需结合现有框架验证，AST 缺口报告是辅助信息。
复杂逻辑应沉淀为已评审的 POM / fixture，避免不断扩张低级动作 DSL。

## 快照 v2 与探索边界

`ariaSnapshot` 来自 Playwright / 浏览器计算的 ARIA 树，保留区域层次与可访问名称。
`nodes` 是补充 DOM 元数据，不保证完整实现 ARIA 名称计算规则。
重名节点标记 `ambiguous: true`、`keyStability: snapshot-only`；其 `#2` 编号不是跨快照身份。
diff 把此类目标列入 `ambiguous`，不会按序号猜测实体变化。

快照包含 iframe 的独立 ARIA 文本；当前 target DSL 不提供跨 iframe 定位，需使用现有 POM 或后续显式适配。
快照 CLI 每次新建页面，只负责当前 URL 的采集，不是具有持续浏览器会话的 MCP 替代品。
弹窗、切页等状态的探索，应由持有该页面会话的工具或可信 POM 完成。

## 验证与未实现能力

```powershell
python -m pytest tool_tests -q
# 已准备 Chromium 的环境中执行实际浏览器测试：
$env:RUN_BROWSER_TESTS = '1'
python -m pytest tool_tests/test_browser_contracts.py -q
```

CI 分别执行 Python 兼容性、契约 / 生成集合检查和真实 Chromium 回归。
浏览器回归覆盖同名按钮的区域定位、mock、生效时机、错误响应体与 ARIA / iframe 快照。

多 Agent 调度、持久任务状态、模型四分类与跨会话恢复现由 [Harness v1](HARNESS.md) 实现。
它以独立进程调用 explorer / author / verifier，并由代码负责闸门、执行结果与重试决策。
原 OpenCode 示例仍是人工流程的角色配置；自动模式使用单独的 Harness 示例。
提示词不等于文件权限，`--check` 与 Harness 的协议检查也不提供 OS 工具权限隔离。
这轮没有验证真实 EMS 的业务链路，没有增加运行时模型依赖。
