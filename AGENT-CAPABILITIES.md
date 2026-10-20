# AGENT-CAPABILITIES.md —— 换任何 agent 都要过这张表

这套框架不绑定 opencode，也不绑定 DSH。它只要求 agent 提供下面 6 项能力中的**前 3 项**。
把你的自研 agent 对着这张表核一遍，缺哪项就看到对应的降级方案。

## 必需（缺了就跑不起来）

| # | 能力 | 在框架里用在哪 | 缺了怎么办 |
|---|---|---|---|
| 1 | **执行 shell 命令** | 跑 `python tools/*.py`、`python -m pytest` | 无解。只能把这几个脚本包成 HTTP 服务，让 agent 走 MCP/HTTP 调 |
| 2 | **读写文件** | 读 `spec/cases/*.json`，写 `spec/cases/*.json` | 降级：脚本输出 JSON 到 stdout，人工粘贴回文件。能跑，但没法自动化 |
| 3 | **多轮工具调用**（能连续调 5+ 次不丢上下文） | 快照 → 读结果 → 写用例 → 渲染 → 执行 → 读失败 | 降级：拆成"人驱动的一步一停"，每个阶段人工确认后再继续 |

## 强烈建议（没有也能跑，但体验差很多）

| # | 能力 | 用在哪 | 降级方案 |
|---|---|---|---|
| 4 | **子 agent / 任务分派** | explorer / author / verifier 分工 | 单 agent 串行 + `stage-gate` 文件：每阶段结束写 `.snap/stage.json`，人确认后进下一阶段。见 AGENTS.md「无 subagent 模式」 |
| 5 | **工具权限白名单** | 物理禁止 verifier 改代码 | 用脚本兜底：`python tools/render.py --all spec/cases --check`，`tests/` 被手改直接 fail |
| 6 | **MCP 客户端** | 探索期用 `@playwright/mcp` 快速摸页面 | **完全不需要**。`tools/snapshot.py` 就是 MCP 的替代品，而且更可控 |

## 关键判断

**第 6 项（MCP）没有，几乎不影响。** 我给的骨架从第一天就是 CLI-first：
agent 只要能跑 `python tools/snapshot.py`，就已经"看见"页面了。
MCP 的价值只是让探索快一点，以及省掉一次 shell 调用。

**真正会卡住你的是第 1、2、3 项。** 只要这三项在，MCP、子 agent、权限白名单全都可以用脚本和人补上。

## 核完之后

对着你的 agent 配置，把下面这段改写成它认识的格式（opencode 是 `opencode.json`，
DSH 是 profile 的 `cordis.patch.yml`，你的自研工具大概是一份 YAML/JSON）：

```yaml
tools:
  explorer: [read, write, shell]        # 不许改源码
  author:   [read, write]               # 不许执行
  verifier: [read, write, shell]        # 只许写 .snap/triage.json
instructions: AGENTS.md
```

如果它根本不支持分角色，那就别装样子做多 agent——**一个人按 AGENTS.md 的顺序串行做，
效果比强行拆三个角色更好**。这是我见过的最多的一类翻车：工具不支持，硬拆角色，
结果三个 agent 互相等对方产物，最后死在死锁上。
