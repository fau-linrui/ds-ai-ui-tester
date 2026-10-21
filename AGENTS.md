# AGENTS.md —— 给 AI agent 的硬约束

你是这套 UI 自动化框架的协作者，不是它的主人。下面每一条都是**硬约束**，违反即视为失败。

技术栈：**pytest + Playwright (Python)**。不要引入 Node 工具链，也不要生成 `.spec.ts`。

以下产物限制适用于 explorer / author / verifier 生产业务用例的流程。
用户明确要求维护框架工具时，维护者可以修改 tools、schema、tool_tests、配置与文档；
`tests/**` 仍须通过 renderer 更新。维护者应运行工具回归，并按实际证据报告验证结果。
当前工具契约与限制见 `docs/COMPILER-CONTRACT.md`。

## 你能产出什么

只有三个位置，别的一律不许写：

| 产物 | 路径 | 谁写 |
|---|---|---|
| 页面地图 | `.snap/*.json` | explorer |
| 用例 JSON | `spec/cases/*.json` | author |
| 执行结论 | `.snap/triage.json` | verifier |

`tests/**` 是**生成产物**，永远不要手改。改了就下次生成时被覆盖，而且 CI 里没人知道谁改的。

## 硬规则

1. **禁止输出 CSS / XPath 选择器。** target 只能是 `testid` / `role+name` / `href` / `label` / `text`，优先级从高到低。
2. **禁止发明期望值。** 期望只能来自：需求文档、接口契约、数据准备脚本、已确认的缺陷报告。找不到依据就停下来问人。
3. **禁止修改断言来让测试变绿。** 定位器失效可以修，断言变更必须人工确认。自愈改断言等于把 bug 写成预期。
4. **断言必须分级。** 只写 `visible` 属于弱断言，至少再补一条 `text` / `value` / `count`；涉及写操作的必须补 `captureNetwork` + `expectNetwork`。
5. **一条用例只验证一个意图。** 步骤超过 12 步就拆开。
6. **探索必须用测试环境。** 不允许在预发/生产上自由点击。
7. **失败必须分类**：`product_bug` / `case_error` / `env` / `flaky`。只有 `case_error` 允许回流给 author 重写。
8. **重试上限 3 次。** 同一用例连续 3 次仍失败，停下，把现场（snapshot + trace + console）写进 `triage.json`，等人。
9. **不许声称"已修复"。** 你只能声称"已重新生成，等待执行验证"。
10. **POM 优先于定位器。** POM 模式下，`raw` 与直接 click/fill 等动作均须带 `pom_gap`，每条用例及全局占比不得超过 20%。接入成熟框架时用 `lint --require-pom` 强制策略。

## 工作顺序

```bash
# 0. 先自检，别在环境不全的时候写用例
python tools/preflight.py

# 1. 看页面到底有什么（先快照，后动手，永远如此）
python tools/snapshot.py --url %BASE_URL%/your/page --out .snap/page.json --wait "[data-testid=root]"

# 2. 基于快照写 spec/cases/xxx.json（只写意图，不写选择器）

# 2.5 静态闸门：不通过就不要渲染
python tools/lint.py --cases spec/cases

# 3. 渲染成可执行代码
python tools/render.py --all spec/cases

# 4. 执行
python -m pytest tests/test_xxx.py -q
```

**没有快照就不要写用例。** 凭记忆或凭想象写出来的 target，成功率接近零。

## 同名元素消歧（真实项目里一定会遇到）

`get_by_role("link", name="问答")` 可能同时命中顶部导航和正文节点。
按这个顺序处理，**不要用 `.first()` / `.nth()` 蒙混**：

1. 加 `exact: true` —— 解决子串误匹配（`社区` vs `社区学堂`）
2. 加 `href` —— 链接类元素用它区分最可靠
3. 用 `within` 指定弹窗、行或导航区域；仍有歧义时推动前端补 `data-testid`

Playwright 的 strict mode 报"resolved to N elements"是**正确行为**，不是 bug。

## 没有子 agent 时（单 agent 串行模式）

不要把三个角色硬塞进一个上下文。改成**阶段闸门**：

1. 快照完成 → 写 `.snap/stage.json` 标记 `{"stage":"explored"}`，**停**。
2. 人确认 → 写用例 → 标记 `authored`，**停**。
3. 人审 → render → 执行 → 标记 `verified`。

单 agent 最大的风险是"一口气做完"，第一步就错，后面全废且看不出错在哪。

## 没有 MCP / 没有浏览器工具时

`tools/snapshot.py` 可采集单个 URL 的 ARIA 树与 DOM 辅助信息：

- 看页面 → `python tools/snapshot.py --url ... --out .snap/x.json`
- 看变化 → 加 `--diff .snap/上一份.json`
- 要登录态 → `--state auth.json`

每次命令新建浏览器，不保留上一条命令打开的弹窗、标签页或分页状态。
复杂流程需由持有浏览器会话的工具或可信 POM 探索，不能声称静态快照已覆盖全流程。
重名 key 的 `#2` 仅限本次快照，不可当成跨快照的稳定身份。

不要因为没有 MCP 就退回"让模型猜选择器"，那是死路。

## 卡住时

停下来，输出一段结构化说明：你尝试了什么、卡在哪、需要人提供什么（一个 testid？一份接口文档？一个测试账号？）。**不要用更多猜测来掩盖卡住。**
