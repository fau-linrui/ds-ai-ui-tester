# text-only-e2e

纯文本大模型驱动的 UI 自动化用例框架。不依赖多模态、不依赖 DSH 插件，`npm i` 之后就能跑。

> 完整项目文档（背景、设计决策、契约、验收、路线图）见 [PROJECT.md](PROJECT.md)。
> 换 agent 前先过一遍 [AGENT-CAPABILITIES.md](AGENT-CAPABILITIES.md)。

```
spec/cases/*.json   ──render──>   tests/*.spec.ts   ──playwright──>   CI
       ↑                                  ↑
    模型只写这里                     人评审 + 固化，不要再让模型碰
```

## 内网安装（三件事）

**1. 装包**

```bash
npm config set registry http://<你们的私服地址>/
npm i -D @playwright/test playwright
```

**2. 浏览器二进制**——这是内网最容易踩的坑。npm 私服只给你 JS 包，Chromium 是从 `cdn.playwright.dev` 下的，通常出不去。三个办法，任选：

```bash
# A. 有内部镜像
set PLAYWRIGHT_DOWNLOAD_HOST=http://<内部镜像>
npx playwright install chromium

# B. 离线搬：在能上网的机器上下好，整个目录拷过来
#    源机器: npx playwright install chromium
#    拷贝 %LOCALAPPDATA%\ms-playwright 到目标机器同路径
#    或者设置 PLAYWRIGHT_BROWSERS_PATH=D:\browsers\ms-playwright

# C. 复用内网已装的 Chrome（最省事）
#    playwright.config.ts 的 project 里加 channel
#    use: { ...devices['Desktop Chrome'], channel: 'chrome' }
```

装包时建议加 `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`，避免 postinstall 卡在网络上十分钟然后失败。

**3. MCP（可选，只用于探索期）**

```bash
npm i -D @playwright/mcp
# opencode.json 里配置，参考 opencode.example.json
```

MCP 只是让 agent 在探索时手快一点。**框架本身不依赖它**——MCP 被禁、升级、内网节点连不上，你的流程照跑。

## 适配任意 agent（包括自研 harness）

这套东西**不依赖 opencode，也不依赖 MCP**。它和 agent 之间的契约只有三条：

1. **agent 能跑 shell 命令** → 所有能力都从 `node tools/*.mjs` 走
2. **agent 能读写文件** → 产物落盘，人可评审可回滚
3. **退出码是语义的** → `0` 成功 / `1` 失败 / `2` 用法错。agent 读退出码，不读自然语言输出

换任何一个 agent，只要这三条成立，框架原样跑。工具清单和降级方案见
[AGENT-CAPABILITIES.md](AGENT-CAPABILITIES.md)——对着你的自研工具核一遍，缺哪项补哪个降级方案。

> 有多 agent 分派就用多 agent；没有就按 AGENTS.md 的「单 agent 串行模式」走。
> **不要因为工具不支持分角色，就硬拆三个 agent 装样子**——我见过最多的翻车就是这么来的。

### 登录态（内网系统基本都绕不开）

```bash
# 录一次登录，存 cookie/localStorage
npx playwright codegen --save-storage=auth.json http://<内网地址>/login

# 之后快照带登录态
node tools/snapshot.mjs --url http://<内网地址>/orders --out .snap/orders.json --state auth.json

# 跑测试时
set AUTH_STATE=auth.json
npx playwright test
```

`auth.json` 里有有效凭证，进 `.gitignore`，别提交。

## 跑起来

```bash
# 1. 起本地演示页（零依赖静态服务器）
node tools/serve.mjs --root . --port 3000

# 2. 看一眼页面结构（永远先快照，后动手）
node tools/snapshot.mjs --url http://127.0.0.1:3000/demo/login.html --out .snap/login.json
node tools/snapshot.mjs --url http://127.0.0.1:3000/demo/login.html --out .snap/login2.json --diff .snap/login.json

# 3. 生成用例代码
node tools/render.mjs --all spec/cases

# 4. 跑
npx playwright test
```

## 目录

| 路径 | 作用 |
|---|---|
| `tools/snapshot.mjs` | 稳定 key 的快照 + 增量 diff |
| `tools/render.mjs` | 用例 JSON → Playwright spec |
| `tools/serve.mjs` | 零依赖静态服务器 |
| `spec/case.schema.json` | 用例契约（改这里等于改语言） |
| `spec/cases/` | 模型唯一被允许写的地方 |
| `tests/` | **生成产物**，禁止手改 |
| `AGENTS.md` | 给 agent 的硬约束，比 prompt 管用 |

## 三个设计决策，别改

1. **模型不写选择器，只写语义 target。** 选择器由 renderer 生成，模型最多点错元素，不会产出永远匹配不上的 `div:nth-child(3)`。
2. **模型不碰 `tests/`。** 生成物可回滚、可 diff、可评审；手改过就再也说不清是谁改的。
3. **LLM 不进 CI。** 生成期用 agent，CI 只跑固化好的代码。成本、稳定性、可复现性都靠这条撑着。

## 已知边界

- 像素级视觉回归用 Playwright 自带 `toHaveScreenshot`，不需要模型，别用 AI 做。
- canvas / 图表 / 无 a11y 的自绘组件：定位不了，也断言不了。要么让前端补 `role`+`aria-label`，要么退回接口断言。
- 这套东西的输出是**用例资产**，不是"自动发现 bug"。发现 bug 靠人设计的风险点 + 变异测试，不靠生成量。
