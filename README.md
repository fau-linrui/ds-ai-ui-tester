# text-only-e2e

纯文本大模型驱动的 UI 自动化用例框架，基于 **pytest + Playwright (Python)**。不依赖多模态、不依赖任何特定 agent，装完就能跑。

> 完整项目文档（背景、设计决策、契约、验收、路线图）见 [PROJECT.md](PROJECT.md)。
> 设计理念、实测数据与自我剖析见 [docs/DESIGN-PHILOSOPHY.md](docs/DESIGN-PHILOSOPHY.md)。
> 一次完整用例的逐步走读见 [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)。
> 换 agent 前先过一遍 [AGENT-CAPABILITIES.md](AGENT-CAPABILITIES.md)。
> 当前编译契约、迁移方式和已实现边界见 [docs/COMPILER-CONTRACT.md](docs/COMPILER-CONTRACT.md)（v0.5）。

```
spec/cases/*.json   ──render──>   tests/test_*.py   ──pytest──>   CI
       ↑                                  ↑
    模型只写这里                     人评审 + 固化，不要再让模型碰
```

## 环境要求

- Python ≥ 3.9（开发验证用 3.14）
- `playwright`、`pytest`、`pytest-playwright`

## 内网安装（三件事）

**1. 装包**

```bash
pip config set global.index-url http://<你们的私服>/simple
pip install -r requirements.txt
```

**2. 浏览器二进制**——内网最容易踩的坑。pip 私服只给你 Python 包，Chromium 是从 CDN 下的，通常出不去。三个办法：

```bash
# A. 有内部镜像
set PLAYWRIGHT_DOWNLOAD_HOST=http://<内部镜像>
python -m playwright install chromium

# B. 离线搬：源机器装好后，整个目录拷过来
#    默认在 %LOCALAPPDATA%\ms-playwright
#    或统一设置 PLAYWRIGHT_BROWSERS_PATH=D:\browsers\ms-playwright

# C. 复用内网已装的 Chrome / Edge（最省事）
#    conftest.py 里给 browser_type_launch_args 加 channel，或跑用例时
#    用 --browser-channel chrome（pytest-playwright 自带该参数）
```

**3. MCP（可选，只用于探索期）**

```bash
npm i -D @playwright/mcp
```

框架本身不依赖它。MCP 被禁、升级、节点不通，流程照跑。

## 跑起来

```bash
# 1. 自检
set BASE_URL=https://testerhome.com
python tools/preflight.py

# 2. 看一眼页面结构（永远先快照，后动手）
python tools/snapshot.py --url %BASE_URL%/ --out .snap/home.json

# 3. 生成测试代码
python tools/render.py --all spec/cases

# 4. 跑
python -m pytest tests -k th_ -q
```

本地演示（不联网）：

```bash
python tools/serve.py --root . --port 3000
set BASE_URL=http://127.0.0.1:3000
python -m pytest tests -k order_ -q
```

## P0 工具：先量现状，再谈生成

对接既有框架前，先跑这两个（都不调模型）：

```bash
# 1. 扫既有仓库，产出框架 API 清单
python tools/index_pom.py --root D:\work\ems-autotest --out manifest.json

# 2. 覆盖率 + 缺口 + 技术债报告
python tools/gap_report.py --manifest manifest.json --cases spec/cases --out gap.md

# 3. 用例静态闸门（结构 / 断言强度 / sources / raw 预算 / 越界）
python tools/lint.py --cases spec/cases --boundary

# 4. 工具链自身的测试
python -m pytest tool_tests -q
```

输出四类信息：Page 覆盖率、POM 内部债务（`find_element` / `sleep` / 页面内断言）、
测试绕过 POM 的热点、AI 用例需要的 POM 缺口。

`sample-ems/` 是一个刻意做"脏"的仿真仓库，用来验证这两个工具的口径。

## 把设计文档喂给内网 AI

如果要把 [docs/DESIGN-PHILOSOPHY.md](docs/DESIGN-PHILOSOPHY.md) 交给另一个 AI 做框架对比：

```bash
python tools/collect_evidence.py --root <你的仓库> --out evidence.md   # 生成证据包（≤15KB）
```

然后按 [prompts/](prompts/) 里的两轮提示词执行：第一轮只校准理解，第二轮打分与给建议。
详见 [prompts/README.md](prompts/README.md)。

## 目录

| 路径 | 作用 |
|---|---|
| `tools/snapshot.py` | 浏览器 ARIA 树 + DOM 辅助信息 + 歧义感知 diff |
| `tools/contracts.py` | lint 与 renderer 共用的严格契约与语义检查 |
| `tools/render.py` | 用例 JSON → pytest 测试函数 |
| `tools/preflight.py` | 环境自检，退出码即结论 |
| `tools/index_pom.py` | AST 扫描既有仓库，产出 POM/manifest 清单 |
| `tools/gap_report.py` | 覆盖率、缺口、技术债报告 |
| `tools/lint.py` | 用例静态闸门：结构、断言强度、sources、raw 预算、越界 |
| `tools/collect_evidence.py` | 生成给外部 AI 的仓库证据包 |
| `tools/serve.py` | 零依赖静态服务器 |
| `prompts/` | 两轮对照评估的提示词 |
| `conftest.py` | `base_url` / `AUTH_STATE` 两个 fixture |
| `spec/case.schema.json` | 用例契约（改这里等于改语言） |
| `spec/cases/` | 模型唯一被允许写的地方 |
| `tests/` | **生成产物**，禁止手改 |
| `AGENTS.md` | 给 agent 的硬约束，比 prompt 管用 |

## 登录态

```bash
# 录一次登录（也可以用 playwright codegen，Python 版同样可用）
python -c "from playwright.sync_api import sync_playwright; ..."
# 更简单：先用 --save-storage 的等价写法
#   python -m playwright codegen --save-storage=auth.json http://<内网地址>/login

set AUTH_STATE=auth.json
python -m pytest tests -q
```

`auth.json` 含有效凭证，已加入 `.gitignore`。

## 三个设计决策，别改

1. **模型只写受约束的语义 target。** renderer 生成定位代码；target 仍可能错误，必须结合真实页面验证唯一性和业务结果。重名元素用 `within` 指定区域。
2. **模型不碰 `tests/`。** 生成物可回滚、可 diff、可评审；手改过就再也说不清是谁改的。
3. **LLM 不进 CI。** 生成期用 agent，CI 运行契约检查、工具测试和固化代码。

`lint` 与 `render` 现在都会执行 Schema / 语义校验。未知字段不会被静默忽略。
网络 `routes` 会实际注册 mock；`expectNetwork.equals` 检查响应 JSON 的完整相等。
批量生成递归扫描用例，`--check` 会报告缺失、变化及过期生成文件；单个子目录请使用独立 `--out-dir`。
工具不自动删除过期测试，也不覆盖人工测试文件。

真实浏览器工具回归默认跳过；准备 Chromium 后设置 `RUN_BROWSER_TESTS=1` 执行，CI 会单独运行。
多 Agent 调度与自动失败分类仍处于后续实现范围，角色提示词不等于权限隔离。

## 已知边界

- 像素级视觉回归用 Playwright 自带 `to_have_screenshot`，不需要模型，别用 AI 做。
- canvas / 图表 / 无 a11y 的自绘组件：定位不了，也断言不了。要么让前端补 `role`+`aria-label`，要么退回接口断言。
- 这套东西的输出是**用例资产**，不是"自动发现 bug"。
