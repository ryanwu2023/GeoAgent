# 单采集器补采、智能问答预算与核心结论优化实施计划

> **执行要求：** 按任务逐项实施和验证，使用复选框跟踪进度。

**目标：** 提供显式单采集器补采入口，为本地多 Agent 问答增加可热更新的超时与人民币费用预算，并让核心结论优先直接回答用户问题。

**架构：** 复用现有单采集器后端请求契约，只调整前端入口与请求参数；新增独立的 Agent 运行策略存储和 API，由问答开始时冻结为本轮预算；费用控制在专业子 Agent 调度层进行并发预留和完成结算；最终回答通过提示词与确定性复核共同保证主题聚焦和非空输出。

**技术栈：** Python 3、FastAPI、Pydantic、React、TypeScript、现有 GeoAgent 问答 Harness 与测试框架。

**设计文档：** `docs/superpowers/specs/2026-10-27-collector-agent-budget-and-answer-focus-design.md`

## 全局约束

- 所有新增文档和用户可见文案使用中文。
- PiChat 嵌入问答保持现状；预算只作用于本地模型问答。
- Agent 配置不得包含模型密钥，不提交运行配置文件。
- 未配置费用上限时保持无限制行为。
- 超时、超预算、模型异常或引用复核失败时仍必须返回非空最终回答。
- 单采集器补采只运行显式选中的稳定 ID，不扩展到主题或全量采集。

## 重点复核风险

- 费用上限已填写但任一价格缺失时必须拒绝保存，不能静默按零计算。
- 并发任务必须先预留费用，避免多个子 Agent 同时越过上限。
- 总超时小于单任务超时时必须拒绝保存。
- 全量批次运行期间点击单采集器补采必须保留当前批次并显示冲突原因。
- 核心结论不能因移除内部统计而丢失必要的不确定性和证据边界。

---

### 任务一：Agent 运行策略存储与接口

**文件：**

- 新增 `backend/qa_agent_settings.py`
- 新增 `backend/qa_agent_routes.py`
- 修改 `backend/app.py`
- 新增 `tests/test_qa_agent_settings.py`
- 新增 `tests/test_qa_agent_routes.py`

**接口：**

- `default_settings() -> dict`
- `validate_settings(payload: dict) -> dict`
- `effective_settings() -> dict`
- `save_settings(payload: dict) -> dict`
- `GET /api/qa-agent/settings`
- `PUT /api/qa-agent/settings`

- [ ] 编写默认值、费用无限制、价格完整性、两个超时关系、字段范围和原子保存测试。
- [ ] 实现独立运行策略文件与校验；文件缺失时使用默认值，损坏时返回明确错误。
- [ ] 实现受现有本地管理员保护的读写接口，并在应用中注册路由。
- [ ] 运行设置和路由测试。
- [ ] 提交本任务。

### 任务二：Agent 配置页面

**文件：**

- 新增 `src/agent_settings.ts`
- 新增 `src/AgentSettings.tsx`
- 修改 `src/IntegrationSettings.tsx`
- 修改 `src/style.css`
- 新增 `tests/agent_settings.test.cjs`

**接口：**

- `AgentRuntimeConfig`
- `agentDraftError(draft: AgentRuntimeConfig) -> string`
- “智能问答 Agent”页签通过 `/api/qa-agent/settings` 读取和保存配置。

- [ ] 编写两个超时、并发数、价格与费用上限的前端校验测试。
- [ ] 实现配置类型、默认草稿和校验函数。
- [ ] 实现配置表单，空费用上限表示无限制，并展示“仅影响本地模型问答”。
- [ ] 接入配置对话框页签与中文状态文案。
- [ ] 运行前端测试和生产构建。
- [ ] 提交本任务。

### 任务三：子 Agent 超时和费用预算执行

**文件：**

- 修改 `backend/qa_multiagent.py`
- 修改 `backend/qa_harness.py`
- 修改 `backend/qa_harness_state.py`
- 新增 `backend/qa_cost.py`
- 修改 `tests/test_qa_multiagent.py`
- 修改 `tests/test_qa_harness.py`
- 修改 `tests/test_qa_harness_observability.py`

**接口：**

- `SpecialistBudget.from_settings(settings: dict) -> SpecialistBudget`
- `QaCostLedger.reserve(...)`、`settle(...)` 和 `snapshot()` 统一管理本轮主 Agent 与子 Agent 的估算费用。
- 专业 Agent 备忘录增加估算输入 Token、输出 Token、估算费用和降级原因。
- 多 Agent 汇总增加本轮费用限制状态、估算费用和预算剩余值。

- [ ] 编写配置覆盖默认超时、单任务超时、总超时和无限费用测试。
- [ ] 编写并发费用预留、完成结算、费用不足不启动及其他任务继续的测试。
- [ ] 问答开始时只读取一次运行策略，并将其转换为冻结预算。
- [ ] 在问答模型调用包装器和专业 Agent 调度器中实现预算预留、实际估算结算和“费用上限降级”。
- [ ] 主 Agent 与最终综合调用也记入单次问答估算费用；费用不足时跳过新的模型调用并转入确定性最终回答。
- [ ] 将估算用量与降级原因写入现有可审计过程，不暴露隐藏思维链。
- [ ] 确保所有专业 Agent 降级时主 Agent 仍继续最终综合。
- [ ] 运行多 Agent、Harness 与可观测性测试。
- [ ] 提交本任务。

### 任务四：核心结论围绕原始问题

**文件：**

- 修改 `backend/qa_harness.py`
- 修改 `backend/qa_finalizer.py`
- 修改 `backend/qa_agent.py`
- 修改 `tests/test_qa_finalizer.py`
- 修改 `tests/test_qa_harness.py`
- 修改 `tests/test_qa_agent.py`

**接口：**

- 最终提示词明确当前状态、最新变化、判断、后续条件和适用时的资产含义顺序。
- 最终复核检测主题错位、内部统计主导和空答案，并使用确定性降级模板恢复。

- [ ] 编写美伊问题不得以俄乌开头、俄乌问题不得以美伊开头的测试。
- [ ] 编写核心结论不得以记录数、线索分布、覆盖维度或工具次数开头的测试。
- [ ] 编写超时、超预算和引用错误后仍返回完整最终答案的测试。
- [ ] 调整规划、综合和最终复核提示词，将内部统计移至“口径与限制”。
- [ ] 实现主题与问题聚焦复核；不合格时重建核心结论而非返回空结果。
- [ ] 运行问答与最终复核回归测试。
- [ ] 提交本任务。

### 任务五：显式单采集器补采入口

**文件：**

- 修改 `src/CollectorMonitor.tsx`
- 修改 `src/prototype.css`
- 修改 `backend/app.py`
- 修改 `tests/test_collector_jobs.py`
- 新增 `tests/collector_single_run.test.cjs`

**接口：**

- 列表行“单独采集”调用现有 `POST /api/collector/runs`，请求包含一个 ID、`timeout=300`、`retry_attempts=1`。
- 详情“补采”使用相同请求参数。

- [ ] 编写后端单 ID 不扩展范围，以及前端请求参数固定为单 ID、300 秒、1 次尝试的测试。
- [ ] 在采集器列表项增加不触发行选择冲突的“单独采集”按钮。
- [ ] 统一列表与详情补采方法，运行中禁用并显示当前批次冲突原因。
- [ ] 保持现有日志、来源状态、门禁和快照链路不变。
- [ ] 运行采集器任务、前端源代码契约和生产构建测试。
- [ ] 提交本任务。

### 任务六：端到端回归与交付

- [ ] 通过 API 保存和重读 Agent 配置，确认费用上限留空时为无限制。
- [ ] 使用极低费用上限触发专业 Agent 费用降级，确认仍返回最终答案。
- [ ] 使用短单任务超时和短总超时分别触发降级，确认页面显示原因。
- [ ] 提交一个单采集器补采请求，确认运行报告中只有该 ID。
- [ ] 运行 Python 问答、配置、采集器测试，Node 前端测试和 Vite 生产构建。
- [ ] 执行 `git diff --check`，确认运行配置、密钥和用户 Excel 文件未进入提交。
- [ ] 提交最终集成修改。

