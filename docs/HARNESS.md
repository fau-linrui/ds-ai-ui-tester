# 多 Agent Harness（v1）

`tools/harness.py` 负责自动调度，模型负责三个独立的文本任务。
不需要多模态，也不依赖 OpenCode 自身是否支持子 Agent 分派。
每次角色调用启动独立进程 / 新会话，任务之间通过经过检查的 JSON 产物协作。

| 阶段 | 执行者 | 产物与闸门 |
|---|---|---|
| snapshot | Harness 调用已有 Playwright 工具，或读取提供的快照 | 完整 v2 ARIA 快照；限制测试站点来源 |
| explorer | 文本 Agent | 结构摘要、语义目标、歧义说明 |
| author | 文本 Agent | 单个 case JSON；必须引用任务提供的来源 ID |
| gate | 确定性 Python 代码 | Schema、语义、lint、POM 预算、来源、步骤数、修复边界检查与 render |
| execute | Harness 启动 pytest | 退出码、JUnit、stdout/stderr、失败 trace、ARIA 与 console |
| verifier | 文本 Agent，仅分析实际测试失败 | 四分类；引用存在的执行证据 |
| decision | 确定性 Python 代码 | 仅 case_error 回到 author；其余阻塞；总尝试数最多 3 |

通过由 pytest 退出码与 JUnit 一起判定，Agent 不能把失败声明为通过。
退出 0 但没有测试、包含跳过、报告缺失 / 损坏或退出码与报告矛盾，都按环境阻塞处理。
静态闸门失败由代码记录为 case_error；进程故障、超时、协议错误按 env 停止。

## 用 OpenCode 运行本地示例

先准备依赖、浏览器和可用的纯文本模型，完成 OpenCode 的 provider / 默认模型配置。
可把 `opencode.harness.example.json` 中的 agent 字段合入现有配置，或用 `OPENCODE_CONFIG` 指向这个文件。
每个角色的 command 也可以添加 `--model provider/model`，分别选择模型。
不要把 API key 放进 Harness JSON，沿用已有 provider 登录或环境变量。

终端一：

```powershell
python tools/serve.py --root . --port 3000
```

终端二：

```powershell
$env:BASE_URL = 'http://127.0.0.1:3000'
$env:OPENCODE_CONFIG = (Resolve-Path opencode.harness.example.json).Path
python tools/preflight.py
python tools/harness.py run --config harness.example.json --tasks harness.tasks.example.json --run-id demo-001
python tools/harness.py status --run .snap/runs/demo-001
```

`run-id` 必须唯一，已有目录会拒绝覆盖。通过后的退出码是 0；阻塞 / 耗尽是 1；输入或恢复检查错误是 2；中断是 130。
OpenCode 适配使用官方 CLI 的 `run --format json --file REQUEST`，解析 JSONL 的 `text` / `part.text`。
缺少文本、错误事件、多段内容不能组成一个合法 JSON、Markdown 包裹等情况会停止，保留原始输出供排查。
适配器不复用 `--session` / `--continue`，并设置 `OPENCODE_PERMISSION={"*":"deny"}`；角色只读取已附带的文本，不需要 shell 或编辑权限。

官方接口参考：[CLI](https://opencode.ai/docs/cli/)、[Agent](https://opencode.ai/docs/agents/)、[权限](https://opencode.ai/docs/permissions/)。
不同版本或公司封装的 CLI 仍需做实际模型联调。工具回归使用受控子进程和 JSONL 协议样本，不会花费真实模型额度。

## 接 DSH 或自研 Agent

把对应角色改为 JSON 进程适配器即可，三个角色可以使用不同后端：

```json
{
  "protocol": "json",
  "command": ["python", "D:/adapters/dsh_bridge.py"]
}
```

Harness 用 UTF-8 stdin 发送一个 JSON 请求：

```json
{
  "protocol_version": 1,
  "call_id": "demo-001/AUTO-ORDER-001/author/1",
  "role": "author",
  "instructions": "阶段要求与修复约束",
  "task": {"id": "AUTO-ORDER-001", "sources": [{"id": "REQ-DEMO-ORDER", "content": "原文"}]},
  "context": [],
  "response_schema": {},
  "snapshot": {},
  "exploration": {},
  "case_schema": {},
  "previous_case": null,
  "feedback": null
}
```

上例省略了字段内容；实际请求包含完整 Schema、来源原文和已完成阶段产物。
桥接脚本调用你们内部模型 / Agent，stdout 只输出符合 response_schema 的一个 JSON 对象；日志写 stderr，故障退出非 0。
explorer 返回 `summary/targets/warnings`，author 返回 `{"case": {...}}`，verifier 返回 `category/summary/evidence`。
三个角色都可以返回 `{"blocked":{"reason":"缺少需求依据","needed":["经确认的期望值"]}}`，停止当前任务并记录需要人补充的内容，不消耗额外尝试去猜。
不接受 NaN、重复 JSON 键或额外字段。request、原始响应、进程耗时、退出码都会保留。
`call_id` 可供后端去重；恢复时未完成的文本调用可能再次投递同一 ID，不能假设外部调用恰好一次。
这里没有假定某一种 DSH 的专属命令格式，需要桥接你实际使用的版本。

## 任务与既有 POM

配置和任务契约见 `spec/harness.schema.json`。
sources / context_files / seed_case / snapshot 的路径相对于 `--root`，解析后必须在项目内。
任务的 URL、case 的 goto 和最终快照必须处于配置的测试站点来源；`test_environment: true` 是调用者对环境的声明，不能代替网络隔离。

- `context_files`：加入 POM manifest、fixture 说明与业务约定；不要放账号、密码或 token。
- `require_pom: true`：沿用现有 POM 策略和 20% 降级预算。fixture 必须已经在目标项目里可用。
- `storage_state: "auth.json"`：由快照工具与 pytest 使用；只记录文件校验值，不把登录态内容送给模型。
- task 的 `snapshot`：可指定已有完整 v2 快照，适合把可信的有状态探索结果交给调度器。
- task 的 `seed_case`：指定已评审 case，第一轮 author 也只能改它的 target。
- `pytest_args`：只允许独立 flag，带值用 `--name=value`；禁止覆盖 Harness 管理的报告 / 输出 / 根目录选项。

首次出现合法 Schema 的 case 会锁定为修复基线。
尚未通过结构校验的草稿通过 rejected_draft 和闸门反馈退回，不作为已执行用例的修复基线。
之后只允许修改每个 step 的 target（含 within），其余字段的变化会阻塞：断言、value、routes、URL、步骤顺序、POM 方法 / 参数、来源元数据都不能自动改。
修复 POM 方法或更改断言需要人评审后创建新运行。
来源 ID 检查只能证明引用存在，不能证明模型正确理解了需求；首轮用例仍须评审。

## 并发、持久化与恢复

`max_workers` 控制同时运行的任务流水线数量，默认 1；三个相互依赖的阶段在每条流水线内部按顺序执行。
只有数据和账号相互独立的任务才提高并发。独立目录、pytest 进程 / browser context 不会隔离后端业务数据。

```text
.snap/runs/<run-id>/
  inputs.json / inputs.sha256       固定配置、来源与输入文件校验值
  summary.json                     整体结果
  tasks/<ID>/
    state.json / triage.json        阶段、尝试次数、审计事件、最终结论
    snapshot.json / explorer.*     实际页面证据与 explorer 请求/响应
    baseline.json                  自动修复不能更改的基线
    attempts/01/                   每次尝试单独保留
      author.* / case.json / gate.json
      generated/test_*.py          仅 renderer 生成
      pytest.* / junit.xml / execution.json
      browser-results/ / verifier.* / triage.json
```

```powershell
python tools/harness.py resume --run .snap/runs/demo-001
```

检查点通过临时文件 + fsync + replace 写入；同一运行使用 OS 文件锁，退出后锁自动释放。
pytest 插件会为实际 page fixture 的失败留下文本 ARIA 快照与最多 200 条 console / pageerror 记录；采集失败不会覆盖原始断言结果。
trace 保留为二进制审计产物，verifier 只接收文本证据及有标识的节选。
恢复前校验输入文件与已封存产物的 SHA-256；发现变化就拒绝继续，不会把新需求接到旧阶段结果上。
已完成的阶段不会再次调用模型，完成的运行不会重跑测试。
如果执行中断，存在完整 execution.json 时可继续分类；缺少结果时停止，不自动重放可能已完成的写操作。
按 Ctrl+C 会停止分派并终止当前进程树，保留阶段与日志。

## 评审后固化

所有自动结果先写入被 gitignore 的 `.snap/runs/`，不会覆盖正式 spec/cases 或 tests。
评审 case、来源和真实执行报告后，再把选中的 case 复制到正式用例目录，通过既有 renderer 生成并提交。
CI 继续只执行工具回归和固化测试；工具回归中的 Agent 是本地受控脚本，CI 不连接真实模型。

## v1 边界

- 默认探索是现有 snapshot CLI 的单 URL 页面。弹窗、登录步骤、分页等会话探索仍需可信 POM / 外部会话工具提供快照，当前没有自动探索规划器。
- verifier 的分类是模型意见；通过结果必须有执行证据，产品缺陷、环境、flaky 均不会自动改用例或无限重跑。
- 通用命令和项目 fixture 是调用者信任的代码。cwd、JSON 协议、检查点校验与 OpenCode 工具 deny 不等于 OS 沙箱，不限制任意命令对文件 / 网络的访问。
- SHA-256 用于防止意外混用或修改，不是对恶意宿主的签名认证。外部服务版本、数据库状态、未列入 context_files 的 POM 依赖不会全部自动冻结；变化后应新建运行。
- 超时与 stdout/stderr 字节预算阻止失控调用；没有跨机器分布式队列、模型 token 计费器或自动审批发布。

回归覆盖真实子进程、生成后的 pytest、四分类分支、目标修复、断言变更阻止、3 次耗尽、JSONL、超时、输出预算、并行目录、运行锁、检查点恢复与不确定执行停止。
