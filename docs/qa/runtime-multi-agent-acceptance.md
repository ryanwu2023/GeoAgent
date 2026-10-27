# GeoAgent 运行时多 Agent 首版验收清单

日期：2026-10-25

本文是本地模型多 Agent 首版的阻断验收标准。PiChat 继续使用原有独立运行时，不参与本地多 Agent 调度。

## 1. 必须固定的接口

### 1.1 计划协议

复杂问题的计划必须显式包含：

- `multi_agent.enabled`：是否启用多 Agent；
- `multi_agent.reason`：可展示的启用依据；
- `multi_agent.tasks`：事件、市场、反证子任务；
- `multi_agent.budget`：总工具调用、每个子任务调用、总时限和并发数；
- 每个任务的 `task_id`、`agent_id`、`agent_role`、`objective`、`allowed_tools`。

简单数值、单条证据和单指标查询必须保持单 Agent，不应仅因关键词“分析”就无条件启动三个子 Agent。

### 1.2 子 Agent 备忘录协议

子 Agent 只能返回结构化研究备忘录，最低字段为：

```json
{
  "task_id": "event-1",
  "agent_id": "event-agent",
  "agent_role": "event",
  "status": "completed",
  "summary": "可展示的研究结论",
  "claims": [
    {"text": "主张", "evidence_ids": ["公开证据ID"], "confidence": "limited"}
  ],
  "counter_evidence": [],
  "internal_message_ids": [],
  "gaps": [],
  "follow_up_queries": []
}
```

公开 `evidence_ids` 必须属于同一冻结快照且已经由本轮工具检索取得。华泰消息 ID 只能进入 `internal_message_ids`，不得进入公开引用映射。

### 1.3 追踪协议

`tool-trace/v1` 的 Agent 生命周期和工具事件都必须带：

- `run_id`、`sequence`、`task_id`；
- `agent_id`、`agent_role`、`parent_agent_id`；
- 工具事件继续包含 `mcp_server`、`tool`、`call_id`、参数摘要、开始/结束时间、耗时、结果数、重试、截断、Artifact 和错误。

生命周期事件使用 `kind=agent`，状态只允许 `running`、`completed`、`degraded`。不得把模型隐藏思维链写入步骤、Artifact 或流式消息。

## 2. 调度与预算验收

### 2.1 全局工具预算必须覆盖所有调用

现有 `HarnessBudget.max_tool_calls` 只约束主模型复核循环，bootstrap、初始正文读取和后续子 Agent 调用并不天然包含在这个数字内。多 Agent 首版必须引入一个运行级原子计数器，统计：

1. bootstrap 调用；
2. 初始正文读取；
3. 三个子 Agent 的全部工具调用及重试；
4. 主 Agent 后续补查。

测试必须证明在三个子 Agent 同时申请最后一个名额时，只有一个调用能执行；其余调用返回可解释的 `budget_exhausted`，且仍能生成最终回答。重试是否计入总预算要固定，建议每次真实上游尝试均计数，同时另报逻辑调用数与尝试数。

### 2.2 共享数据预算必须原子化

公开证据数、全文数、内部消息数和内部消息全文数由所有 Agent 共享。并行搜索、全文读取不得突破 `evidence`、`full_text`、`internal_messages`、`internal_full_text` 上限。

特别验证 `LocalGeoTools` 中内部消息 ID、内部正文 ID 的并行更新；这些路径当前并非全部在同一个锁内。测试至少使用两个并行子 Agent 同时检索相同消息和读取预算最后一个正文。

### 2.3 角色白名单

- 事件 Agent 不得调用角色范围外工具；
- 市场 Agent 不得把指标来源自动解释为事件事实；
- 反证 Agent 不得将“未检索到反证”写成“没有反证”；
- 任一越权请求返回 `tool_not_allowed_for_agent`，记录具体 Agent、工具和任务，不执行工具。

九个正式工具的名称、参数和返回协议保持不变；白名单属于调度层，不得复制或改写工具实现。

## 3. 并行、步骤序列和可观察性

### 3.1 真并行

用三个各延迟 200ms 的假子任务验收。总耗时应接近一个任务耗时加调度开销，而不是约 600ms；同时不得依赖完成顺序生成最终显示顺序。

### 3.2 `qa_steps.sequence`

并行 Agent 不得各自用 `len(prior_steps)+1` 生成序号。所有事件必须通过同一运行级序列分配器写入，满足：

- 同一 `run_id` 内严格递增且唯一；
- 运行事件先于该 Agent 的工具事件；
- 终态事件晚于该 Agent 的全部工具终态；
- 恢复时从数据库现有 `MAX(sequence)` 继续，不能用记录条数推导；
- 旧记录存在序号空洞时不得覆盖历史步骤。

数据库当前使用 `INSERT OR REPLACE`，如果恢复序号重复会静默覆盖。因此“空洞恢复不覆盖”必须是阻断测试。

### 3.3 错误归属

任何失败至少显示：`agent_role / mcp_server / tool / error.kind / call_id`。一个子 Agent 失败时，其他 Agent 的成功工具回执和 Artifact 必须保留；前端默认展开失败 Agent，成功 Agent 默认折叠。

## 4. Artifact 所有权与恢复

### 4.1 稳定调用身份

调用 ID 至少包含 Agent 身份和规范化参数摘要，例如：

`agent-{agent_id}-{tool}-{arguments_digest}`

同一 Agent 恢复时复用已完成调用；不同 Agent 即使工具和参数相同也不能相互覆盖 `run.tool_calls`。如要跨 Agent 去重，必须单独维护共享只读结果缓存，不能借用另一个 Agent 的调用状态冒充执行记录。

### 4.2 三重归属校验

读取完整详情必须同时匹配：

- `qa_artifacts.run_id == 请求 run_id`；
- `qa_artifacts.call_id == 请求 call_id`；
- 运行状态中的 `agent_id/task_id` 与 Artifact 元数据一致。

测试必须覆盖：跨运行 call、同运行跨 Agent call、伪造 Artifact ID、已删除 Artifact、失败 Artifact。任何不匹配返回 404 或明确的不可用状态，不能退而返回同名工具的其他结果。

### 4.3 子任务级检查点

检查点必须持久化子任务的 `pending/running/completed/degraded`、备忘录、开始结束时间、工具调用 ID 和错误。恢复测试需要模拟：

1. 一个 Agent 完成、一个运行中、一个未开始时断开；
2. 恢复后不重新调用已完成 Agent及其工具；
3. 原先运行中的调用按固定策略重试或降级；
4. 最终合并只采用最新、归属正确的终态备忘录；
5. 恢复范围变化时继续由 `HarnessScope.stable_hash()` 拒绝。

## 5. 引用、数字和快照安全

### 5.1 最终引用闭包

最终答案引用必须是以下集合的子集：

`同一 run + 同一 snapshot + 已检索公开证据 + 当前仍存在的记录`

子 Agent 备忘录不能直接扩大最终引用白名单。主 Agent合并后仍需执行现有引用映射和数值校验。测试应注入：

- 另一快照的真实记录 ID；
- 华泰内部消息 ID；
- 虚构 ID；
- 只在备忘录出现、没有工具回执的 ID；
- 引用存在但数字不在证据中的句子。

这些引用均不能进入最终公开 `citation_map`。若校验失败，应删除不支持的引用或数字句，保留可读回答并陈述降级原因，不能返回空答案。

### 5.2 快照隔离

所有子 Agent只能得到不可变的 `HarnessScope`，不能自行选择 latest 或重新加载新快照。运行期间即使工作快照切换，三个 Agent、最终答案、证据按钮和报告仍须指向启动时快照。

测试同时启动两个不同快照的运行，交错返回相同记录标题和相同工具参数，验证结果、Artifact、引用和详情没有串线。

### 5.3 内部消息边界

华泰消息可影响“待核验线索”和补查计划，但最终公开事实必须另有公开证据支撑。最终输出应把内部来源单列；没有公开证据时明确写“内部线索待外部核验”。

## 6. 失败与降级矩阵

| 场景 | 预期结果 |
|---|---|
| 一个子 Agent模型超时 | 该 Agent `degraded`，其余结果继续，最终答案非空并列出缺口 |
| 一个工具超时 | 错误归属到具体 Agent/工具，保留其他工具成果 |
| 子 Agent返回非法 JSON | 有限重试；失败后降级，不阻塞其他 Agent |
| 子 Agent请求越权工具 | 不执行，记录 `tool_not_allowed_for_agent` |
| 总工具预算耗尽 | 停止新调用，使用现有材料综合，最终答案非空 |
| 总运行超时 | 取消未开始工作，收集已完成备忘录，最终答案非空 |
| 三个子 Agent全部失败 | 回退现有单 Agent流程或确定性摘要，最终答案非空 |
| 客户端断开 | 已完成调用先落检查点；恢复不重复成功调用 |
| Artifact 缺失 | 详情明确不可用，不跨 Agent寻找替代 Artifact |
| 发布进度失败 | 不撤销已完成只读调用及其检查点 |

取消协程时必须等待已启动线程型只读工具自然结束或把其状态标成不确定；不得立即对同一稳定调用发起第二份并行副本。

## 7. PiChat 和兼容性边界

以下是阻断条件：

- `provider == pichat` 时不得进入本地多 Agent协调器；
- PiChat 嵌入身份、配置接口和 iframe 数据范围同步不增加本地 `run_id`、`task_id` 或 Agent状态；
- 九个本地正式工具的 `TOOL_NAMES` 和 `TOOL_GUIDE` 不变；
- 二十个对外 MCP 工具目录及认证、审计协议不变；
- 历史 `tool-trace/v1` 缺少 Agent字段仍可读取和展示；
- 关闭多 Agent环境开关后，现有单 Agent测试和真实问答结果保持可用。

## 8. 首版完成门槛

完成必须同时满足：

1. 复杂综合问题启用三个子 Agent，简单问题维持单 Agent；
2. 并行耗时测试、全局预算竞争测试和角色白名单测试通过；
3. 单 Agent失败、全部失败、总超时和断点恢复测试通过；
4. `qa_steps` 空洞恢复、Artifact 跨 Agent越权和双快照交错测试通过；
5. 公开引用闭包、内部消息隔离和数字句校验通过；
6. PiChat分支与九工具合同回归测试通过；
7. 后端全量测试、前端测试和生产构建通过；
8. 使用一个“最新局势＋资产影响＋反证＋观察点”的真实问题验收，前端能追溯每个 Agent及其 MCP 工具错误来源。

## 9. 2026-10-25 实际验收结果

- 真实问题：`请综合分析最新美伊局势、资产影响、反证和未来一周观察点。`
- 运行结果：事件、市场、反证三个子 Agent 均完成；协调 Agent 统一生成最终答案。
- 调用结果：27 次工具尝试全部完成，其中协调 Agent 16 次、事件 Agent 4 次、市场 Agent 4 次、反证 Agent 3 次；运行级上限为 36 次，没有超额调用。
- 可观察性：三个子任务均保存任务状态、备忘录和工具调用 ID；工具详情可显示 Agent、任务、开始结束时间、Artifact 与完整回执。
- 所有权校验：正确运行和调用返回 200；未知调用与跨运行调用均返回 404。
- 恢复验证：复用同一 `run_id` 时不再运行子 Agent 或重复工具调用，直接返回已保存结果。
- 简单问题验证：单指标值和单条证据问题不启用多 Agent；综合局势、资产、反证和观察点问题启用三个角色。
- 报告与证据：答案 HTML 报告返回 200；引用能按答案冻结快照读取证据详情并保留原始链接。
- 回归结果：后端 `587 passed, 28 skipped`；前端 `109 passed`；TypeScript 与 Vite 生产构建通过。

