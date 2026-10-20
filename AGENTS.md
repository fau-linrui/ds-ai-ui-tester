# AGENTS.md —— 给 AI agent 的硬约束

你是这套 UI 自动化框架的协作者，不是它的主人。下面每一条都是**硬约束**，违反即视为失败。

## 你能产出什么

只有三个位置，别的一律不许写：

| 产物 | 路径 | 谁写 |
|---|---|---|
| 页面地图 | `.snap/*.json` | explorer |
| 用例 JSON | `spec/cases/*.json` | author |
| 执行结论 | `.snap/triage.json` | verifier |

`tests/**` 是**生成产物**，永远不要手改。改了就下次生成时被覆盖，而且 CI 里没人知道谁改的。

## 硬规则

1. **禁止输出 CSS / XPath 选择器。** target 只能是 `testid` / `role+name` / `label` / `text`，优先级从高到低。
2. **禁止发明期望值。** 期望只能来自：需求文档、接口契约、数据准备脚本、已确认的缺陷报告。找不到依据就停下来问人，不要"合理推测"。
3. **禁止修改断言来让测试变绿。** 定位器失效可以修，断言变更必须人工确认。这是红线——自愈改断言等于把 bug 写成预期。
4. **断言必须分级。** 只写 `visible` 属于弱断言，至少再补一条 `text` / `value` / `count`，涉及写操作的必须补 `expectNetwork`。
5. **一条用例只验证一个意图。** 步骤超过 12 步就拆开。
6. **探索必须用测试环境。** 不允许在预发/生产上自由点击——你真的会点到"删除"。
7. **失败必须分类**：`product_bug` / `case_error` / `env` / `flaky`。只有 `case_error` 允许回流给 author 重写。
8. **重试上限 3 次。** 同一用例连续 3 次仍失败，停下，把现场（snapshot + trace + console）写进 `triage.json`，等人。
9. **不许声称"已修复"。** 你只能声称"已重新生成，等待执行验证"。

## 工作顺序

```bash
# 0. 先自检，别在环境不全的时候写用例
node tools/preflight.mjs

# 1. 看看页面到底有什么（先快照，后动手，永远如此）
node tools/snapshot.mjs --url $BASE_URL/your/page --out .snap/page.json --wait "[data-testid=root]"

# 2. 基于快照写 spec/cases/xxx.json（只写意图，不写选择器）

# 3. 渲染成可执行代码
node tools/render.mjs --all spec/cases

# 4. 执行
npx playwright test tests/xxx.spec.ts
```

**没有快照就不要写用例。** 凭记忆或凭想象写出来的 target，成功率接近零。

## 没有子 agent 时（单 agent 串行模式）

不要把三个角色硬塞进一个上下文。改成**阶段闸门**：

1. 跑完 `snapshot` → 写 `.snap/stage.json` 记录 `{"stage":"explored","page":"..."}`，**停下来**。
2. 人确认页面地图没问题 → 继续写用例 → 更新 stage 为 `"authored"`，**停下来**。
3. 人审用例 → render → 跑测试 → 更新 stage 为 `"verified"`。

每个阶段结束就停，不要自己往下冲。单 agent 最大的风险是"一口气做完"，然后错在第一步、
后面全废，而你看不出错在哪一步。

## 没有 MCP / 没有浏览器工具时

`tools/snapshot.mjs` 就是 MCP 的平替，功能更窄但更可控：

- 要"看页面" → `node tools/snapshot.mjs --url ... --out .snap/x.json`
- 要看变化 → 加 `--diff .snap/上一份.json`
- 要登录态 → 先 `npx playwright codegen --save-storage=auth.json <登录页>`，
  之后所有命令加 `--state auth.json`

不要因为没有 MCP 就退回"让模型猜选择器"，那是死路。

## 卡住时

停下来，输出一段结构化说明：你尝试了什么、卡在哪、需要人提供什么（一个 testid？一份接口文档？一个测试账号？）。**不要用更多猜测来掩盖卡住。**
