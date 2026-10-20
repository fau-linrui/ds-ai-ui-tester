# text-only-e2e（pytest + Playwright）

> 项目文档 · v0.3 · 2026-10
> 技术栈：Python 3.9+ / pytest / Playwright Python
> 适用场景：内网环境、仅可使用纯文本大模型、需生成可维护的 UI 自动化用例资产

---

## 1. 摘要

本项目的目标不是"让 AI 写测试脚本"，而是**让 AI 生产可评审、可回归、可进 CI 的测试资产**。

核心判断：UI 自动化不必依赖多模态模型。把"看截图找坐标"换成"读可访问性树选引用"，纯文本模型同样能完成页面理解、元素定位与断言生成，且在业务系统这类有语义的 UI 上更稳定、更便宜、更可审计。

三条不变量贯穿整个设计：

1. 模型**不写选择器**，只写语义目标（testid / role+name）。
2. 模型**不碰生成物**（`tests/`），只写结构化用例。
3. **LLM 不进 CI**，生成期用模型，执行期用代码。

---

## 2. 背景与约束

### 2.1 为什么不用 MidScene 那类方案

MidScene.js 的范式是「截图 + 多模态模型 → 直接输出坐标或自然语言断言」。它把**感知**和**执行**耦合在一次模型调用里，带来三个问题：

| 问题 | 表现 |
|---|---|
| 不可复现 | 同一个页面两次调用可能点到不同位置，失败时无法解释"模型当时看到了什么" |
| 不可固化 | 坐标和"AI 定位"很难沉淀成稳定资产，每次都要现场表演 |
| 成本与延迟 | 图片 token 贵，运行时调模型无法进 CI |

### 2.2 本项目的约束

- 公司大模型能力绑定在自研 agent 上，只能用纯文本模型。
- 内网环境，插件安装受限，npm 走内部私服。
- 目标系统是业务后台、管理台一类**有语义**的 UI（表单、表格、按钮都有 label）。

这三条约束恰好指向同一个答案：**结构接地（structural grounding）取代视觉接地（visual grounding）**。

---

## 3. 核心思路

### 3.1 把"看见"换成"读到"

UI 自动化拆开是四件事：**感知 → 决策 → 执行 → 判定**。多模态只影响第一件。

用可访问性树（accessibility tree）做感知层，模型拿到的是这样的结构：

```yaml
- heading "订单控制台" [level=1]
- navigation "主导航":
  - link "订单" [current]
  - link "用户"
- form "按手机号查询订单":
  - textbox "手机号" [required]
  - combobox "订单状态"
  - button "查询"
  - button "重置"
```

模型输出 `ref` 或 `role+name`，由框架解析成真实定位器。模型即使幻觉，最坏结果是"点错元素"，而不是生成一个永远匹配不上的 `div:nth-child(3)`。

### 3.2 相对多模态路线的四个优势

1. **确定性**：同一页面两次快照结构一致，可 diff、可回归。
2. **可引用**：引用可回指到 DOM，断言能落到具体元素。
3. **可审计**：失败时留下的是文本树，不是"模型说它看到了"。
4. **成本低**：纯文本快照的 token 通常只有截图的 1/5 ~ 1/3。

### 3.3 一条容易被忽略的推论

视觉路线的定位结果**无法固化成测试资产**，而这正是本项目的产出目标。纯文本路线天然产出 `getByRole('button', { name: '查询' })` 这种可提交、可 review、可进 CI 的代码。这是选型的决定性理由。

---

## 4. 架构

### 4.1 数据流

```
需求 / 接口文档 / 页面快照
        │
        ▼
  spec/cases/*.json          ← 模型唯一被允许写的地方（意图级，无选择器）
        │  render.py
        ▼
  tests/test_*.py            ← 生成物，禁止手改
        │  pytest
        ▼
  report + trace             ← 失败证据
        │  triage（人工或 agent）
        ▼
  四分类结果 → 只有 case_error 回流重写
```

### 4.2 目录

| 路径 | 作用 | 谁写 |
|---|---|---|
| `tools/snapshot.py` | 稳定 key 的页面快照 + 增量 diff | 人 / 维护者 |
| `tools/render.py` | 用例 JSON → pytest + Playwright 测试函数，含 `--check` | 人 / 维护者 |
| `tools/preflight.py` | 环境自检，退出码即结论 | 人 / 维护者 |
| `tools/serve.py` | 零依赖静态服务器（演示 / 本地调试） | 人 / 维护者 |
| `conftest.py` | `base_url` / `AUTH_STATE` 两个 fixture | 人 |
| `spec/case.schema.json` | 用例契约，改这里等于改语言 | 人 |
| `spec/cases/*.json` | 用例资产 | agent → 人评审 |
| `tests/test_*.py` | 可执行测试 | **仅 render 生成** |
| `AGENTS.md` | 给 agent 的硬约束 | 人 |
| `AGENT-CAPABILITIES.md` | 换 agent 时的能力核对表 | 人 |

### 4.3 三个工具的设计要点

**snapshot.py**
产出稳定 key，优先级 `tid:<data-testid>` > `<role>:<name>`，重复时追加 `#2`。**绝不用 DOM 序号做 key**——序号每次快照都会变，模型上一轮记的引用下一轮就错位，而这种错误极其隐蔽（点了"看起来对"的另一个按钮，测试却过了）。

**render.py**
把语义 target 翻译成 Playwright Python 定位器，四级优先级：
`get_by_test_id` > `get_by_role(role, name=..., exact=...)` > `get_by_label` > `get_by_text`；
链接支持 `href` 消歧。
`--check` 模式用于检测 `tests/` 是否被手改：重新渲染到内存，与磁盘比对，不一致即退出码 1。

**preflight.py**
检查 Python 版本、playwright 包、浏览器能否真正启动、`BASE_URL` 可达、工作区可写。退出码 0/1，让 agent 读码而不是读自然语言。

---

## 5. 用例契约

### 5.1 结构

```json
{
  "id": "ORDER-001",
  "title": "有效手机号查询返回订单列表",
  "requirement": "REQ-ORDER-QUERY",
  "priority": "P1",
  "risk": "查询主流程不通，或结果条数/金额展示错误",
  "fixtures": {
    "routes": [{ "url": "**/api/orders**", "json": { "items": [] } }]
  },
  "steps": [
    { "action": "goto", "url": "/demo/login.html" },
    { "action": "fill", "target": { "label": "手机号" }, "value": "13800138000" },
    { "action": "click", "target": { "testid": "query-submit" } },
    { "action": "expect", "target": { "testid": "order-list" }, "assertion": { "type": "count", "value": 2 } },
    { "action": "expect", "target": { "text": "共 2 条订单" }, "assertion": { "type": "visible" } }
  ]
}
```

### 5.2 关键字段

| 字段 | 约束 |
|---|---|
| `requirement` | 需求 / 缺陷追溯 ID，正式用例必填 |
| `risk` | 这条用例覆盖的风险点，一句话。写不出来说明不该生成 |
| `target` | **禁止出现选择器**，只允许 `testid` / `role`+`name` / `label` / `text` |
| `assertion.type` | `visible` / `hidden` / `text` / `value` / `count` / `enabled` / `disabled` |
| `fixtures.routes` | 接口 mock，用于消除时序与数据抖动 |

### 5.3 断言分级（强制）

| 级别 | 类型 | 适用 |
|---|---|---|
| L1 | `visible` | 仅作为入口校验，不可单独使用 |
| L2 | `text` / `value` / `count` | 所有用例至少补一条 |
| L3 | `expectNetwork` | 涉及写操作（提交、删除、支付）必须补 |
| L4 | 副作用校验（DB / 消息 / 文件） | 关键链路，框架外补充 |

只写 L1 的用例等于没测——它只证明"页面没崩"，不证明"行为正确"。

---

## 6. 工作流

### 6.1 标准流程（agent 可编排）

```bash
python tools/preflight.py                              # 0. 自检
python tools/snapshot.py --url ... --out .snap/x.json  # 1. 感知
# 2. 写 spec/cases/xxx.json
python tools/render.py --all spec/cases                # 3. 生成
python -m pytest tests -q                              # 4. 执行
# 5. 失败四分类 → 只有 case_error 回流 2
```

### 6.2 多 agent 分工（工具支持分派时）

| 角色 | 产出 | 权限边界 |
|---|---|---|
| explorer | `.snap/*.json` | 只读页面，禁止写 spec / tests |
| author | `spec/cases/*.json` | 禁止执行 |
| verifier | `.snap/triage.json` | 禁止改 spec / tests |

**产物即协议**：agent 之间不靠对话传递，靠文件 + JSON Schema。这样可断点续跑、可并行、可单独重跑某一阶段。

### 6.3 单 agent 串行模式（工具不支持分派时）

不要硬拆三个角色。改成**阶段闸门**：

1. 快照完成 → 写 `.snap/stage.json` 标记 `explored` → **停**。
2. 人确认 → 写用例 → 标记 `authored` → **停**。
3. 人审 → render → 执行 → 标记 `verified`。

单 agent 最大的风险是"一口气做完"，第一步就错，后面全废且看不出错在哪。每阶段必停。

---

## 7. Agent 能力要求

详见 [AGENT-CAPABILITIES.md](AGENT-CAPABILITIES.md)。核心结论：

| 能力 | 必需性 | 缺失后果 |
|---|---|---|
| 执行 shell 命令 | **必需** | 只能把脚本包成 MCP/HTTP 服务，否则不可用 |
| 读写文件 | **必需** | 退化为人工复制粘贴 |
| 多轮工具调用 | **必需** | 流程无法自动推进 |
| 子 agent 分派 | 可选 | 降级为串行模式 |
| 工具权限白名单 | 可选 | 用 `render --check` 兜底 |
| MCP 客户端 | **不需要** | 用 `snapshot.py` 平替 |

**唯一的一票否决项是 shell 能力。** 其余缺失都有降级方案。

---

## 8. 内网落地

### 8.1 安装

```bash
pip config set global.index-url http://<内部私服>/simple
pip install -r requirements.txt
```

Python 版没有 postinstall 钩子，`pip install` 不会触发浏览器下载，装完再单独处理浏览器（见下节）。

### 8.2 浏览器二进制（内网第一道坎）

pip 私服只提供 Python 包，Chromium 来自 `cdn.playwright.dev`，通常出不去。三个办法：

```bash
# A. 有内部镜像
set PLAYWRIGHT_DOWNLOAD_HOST=http://<内部镜像>
python -m playwright install chromium

# B. 离线搬运：源机器 python -m playwright install chromium，
#    拷贝 %LOCALAPPDATA%\ms-playwright 到目标机同路径，
#    或统一设置 PLAYWRIGHT_BROWSERS_PATH=D:\browsers\ms-playwright

# C. 复用已装 Chrome / Edge（最省事）
#    pytest 直接加参数：python -m pytest --browser-channel chrome
```

### 8.3 登录态

```bash
python -m playwright codegen --save-storage=auth.json http://<内网地址>/login
python tools/snapshot.py --url ... --state auth.json
set AUTH_STATE=auth.json && python -m pytest tests -q
```

`auth.json` 含有效凭证，必须进 `.gitignore`。会话过期重录即可，不要把账号密码写进代码。

### 8.4 MCP（可选）

```bash
npm i -D @playwright/mcp
```

定位：**只用于探索期加速**。框架本身不依赖它。MCP 被禁、升级、节点不通，流程照跑。

---

## 9. 设计决策（ADR）

### ADR-1：模型不写选择器，只写语义目标
**理由**：选择器生成是幻觉重灾区，且失败模式隐蔽（能匹配到错误元素）。
**代价**：需要前端配合提供语义（label / role / testid）。
**结论**：接受。这是把风险从"不可控"转为"可控且可推动"。

### ADR-2：模型不碰 `tests/`
**理由**：生成物必须可回滚、可 diff、可评审。手改过就说不清是谁改的。
**保障**：`render --check` 在 CI 中强制校验。

### ADR-3：LLM 不进 CI
**理由**：成本、延迟、可复现性三者都不可接受。运行时调模型出 bug 时无法复现"模型当时看到了什么"。
**结论**：生成期用模型，执行期只跑固化的代码。

### ADR-4：自愈只能改定位器，不能改断言
**理由**：改断言让测试变绿，等于把 bug 写成预期——这是最恶性的失败模式。
**保障**：断言变更必须人工确认，写进 AGENTS.md 红线。

### ADR-5：快照优先于截图
**理由**：见 §3。业务系统 UI 有语义，结构信息比像素信息更完整（能看到 `required` / `disabled` / `aria-live`）。

### ADR-6：接口 mock 优先于真实数据
**理由**：纯文本 agent 对时序敏感，数据一变全局飘。**先可复现，再真实**。
**代价**：需要维护 mock，覆盖率下降。**结论**：探索期必须 mock，固化后可切真实数据跑一轮。

---

## 10. 质量与度量

### 10.1 该看的指标

| 指标 | 说明 | 参考目标 |
|---|---|---|
| 缺陷发现率 | 每 100 条用例抓到多少真 bug | 越高越好，别只看绝对值 |
| 变异杀伤率 | 注入缺陷被用例捕获的比例 | > 60% 说明断言有效 |
| Flaky 率 | 非确定性失败占比 | < 5%，超过就停下来修 |
| 定位失败率 | 因定位失效导致的失败占比 | 持续下降 |
| 人工评审耗时 | 每条用例的 review 时间 | 应随资产化下降 |
| 单条用例成本 | 模型 token + 执行时间 | 控制预算上限 |

### 10.2 不该看的指标

- **用例数量**：只会诱导大量重复、低价值用例。
- **代码覆盖率**：证明"被执行过"，不证明"能抓 bug"。用变异测试补位。

### 10.3 验收标准（v0.3）

- [x] 一条用例可从 JSON 渲染为可执行测试函数（pytest + Playwright Python）
- [x] 快照支持稳定 key 与增量 diff
- [x] 环境自检可给出明确阻塞原因
- [x] 登录态可复用
- [x] 端到端在真实站点跑通（TesterHome，5/5）
- [ ] 接入内网业务系统（待验证）
- [ ] `render --check` 接入 CI
- [ ] 失败四分类自动化

---

## 11. 风险与边界

| 风险 | 影响 | 对策 |
|---|---|---|
| 前端不配合加语义 | 框架成功率上限被锁死 | 把"可测试性"写进研发 DoD；用 axe 扫描报告推动 |
| 上下文窗口小 | 大页面快照超限 | diff 优先、分页快照、只取交互节点 |
| 自研 agent 不支持 shell | 框架不可用 | 包 MCP/HTTP server（约 80 行） |
| 模型能力弱 | 生成的用例质量低 | 缩小单次任务粒度；强制 schema 校验；人审兜底 |
| 过度工程 | 三个月后无人维护 | 先跑通一条用例，再扩展 |
| 在预发/生产探索 | 真实数据被误操作 | 全程测试账号 + 接口 mock |

### 明确做不到的

- **像素级视觉回归**：用 Playwright 自带 `toHaveScreenshot`（算法比对，不需要模型）。
- **canvas / WebGL / 无 a11y 自绘组件**：定位不了，需前端补 `role` + `aria-label`，或退回接口断言。
- **原生 App**：除非有可访问性桥接。
- **自动发现 bug**：框架产出的是用例资产，发现 bug 靠人设计的风险点与变异测试。

---

## 12. 路线图

| 版本 | 内容 | 状态 |
|---|---|---|
| v0.1 | 快照 + 渲染 + 用例契约 | 完成 |
| v0.2 | 环境自检、登录态、单 agent 降级、能力核对表 | 完成 |
| v0.3 | 迁移到 pytest + Playwright (Python)；真实站点 5/5 验证 | 完成 |
| v0.3 | 失败四分类（triage）、CI 接入 | 待做 |
| v0.4 | 覆盖率 / 变更影响面反馈，增量选靶 | 待做 |
| v1.0 | 多项目复用、指标看板、变异测试 | 规划 |

---

## 13. 附录

### 13.1 命令速查

```bash
python tools/preflight.py                                   # 环境自检
python tools/snapshot.py --url <URL> --out .snap/x.json     # 快照
python tools/snapshot.py --url <URL> --out .snap/x.json \
     --diff .snap/prev.json --state auth.json --json        # 增量 + 登录态 + 机读输出
python tools/render.py --all spec/cases                     # 生成
python tools/render.py --all spec/cases --check             # 检测手改
python tools/serve.py --root . --port 3000                  # 本地静态服务
python -m pytest tests -q                                   # 执行
python -m pytest tests -k th_ -q --browser-channel chrome   # 复用系统 Chrome
```

环境变量：`BASE_URL`、`AUTH_STATE`、`PLAYWRIGHT_BROWSERS_PATH`、`PLAYWRIGHT_DOWNLOAD_HOST`、`PYTHONUTF8`。

### 13.2 常见失败模式对照表

| 现象 | 根因 | 处理 |
|---|---|---|
| 用例全绿但线上出 bug | 断言只有 `visible` | 强制断言分级 |
| 定位器这周能用下周失效 | 用了 class / DOM 序号 | 稳定 key + testid 契约 |
| 今天绿明天红 | 数据或时序不稳 | `page.route` 固定数据 |
| 模型点了错元素但测试通过 | ref 跨轮失效 / 断言太弱 | 稳定 key + L2 断言 |
| token 爆炸、响应变慢 | 每次全量快照 | 用 `--diff`，只喂变化子树 |
| 自愈后 bug 漏过 | 模型改了断言 | ADR-4 红线，断言变更人工确认 |
| 三个月后没人维护 | 首版过度设计 | 先一条用例跑通，再扩展 |
| 前端不肯加 testid | 缺乏推动依据 | 用 axe 扫描报告量化"不可测元素" |

### 13.3 给团队的三句话

1. 这套东西的产出是**用例资产**，不是"自动测出 bug"。
2. 它能不能成，七成取决于**前端愿不愿意给语义**，三成才是模型和框架。
3. 承诺"全自动无人值守"的项目都活不过半年；承诺"AI 辅助 + 人工把关"的能活三年。

---

*本文档与代码同步维护：改设计先改这里，再改代码。*
