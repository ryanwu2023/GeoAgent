# GeoAgent 本地智能问答 Harness 可靠性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 GeoAgent 本地 OpenAI-compatible 智能问答改造成可恢复、可审计、可压缩、可降级的单 Agent Harness，并保证每次运行最终都有非空中文答案。

**Architecture:** 保留 `backend/qa_agent.py` 中九个只读工具的名称、参数、Schema 和业务语义，以新的状态机 Runner 协调有界并发调度器、上下文管理器、SQLite 检查点、最终校验及降级回答。PiChat 路径继续使用现有协议与 MCP 工具，不经过新 Harness。

**Tech Stack:** Python 3.11、FastAPI、SQLite、asyncio、React、TypeScript、NDJSON 流、Node test、pytest。

**Spec:** [GeoAgent 本地智能问答 Harness 可靠性设计](../specs/2026-10-24-local-qa-harness-reliability-design.md)

## Global Constraints

- 只修改本地 OpenAI-compatible 智能问答路径；PiChat provider、请求协议、工具循环和返回结构保持不变。
- 九个本地工具的名称、参数、Schema、业务语义和只读权限保持不变：`geo_workspace_summary`、`geo_evidence_search`、`geo_evidence_get`、`geo_metric_catalog`、`geo_metric_series`、`geo_analysis_view`、`geo_internal_message_stats`、`geo_internal_message_search`、`geo_internal_message_get`。
- 本阶段只建设单 Agent Harness，不增加子 Agent，不允许模型直接访问互联网、文件系统或数据库。
- 快照、主题、维度、日期范围共同定义运行边界；恢复运行必须验证完整边界，不能混用旧证据。
- 公开证据与华泰内部消息分别存储、分别引用，内部消息不能伪装成公开信源。
- 不在日志、检查点、接口响应或前端状态中写入模型密钥、华泰凭据、访问令牌或内部上游认证信息。
- 任意模型、工具、引用校验或流式连接异常都必须产生非空中文答案，并说明有效信息与缺口。
- 保留当前工作区中其他未提交改动；每个任务只提交本任务明确涉及的文件。

## Review Focus

1. 服务在工具批次完成后重启：同一 `run_id` 和同一范围能够恢复，已成功调用不重复执行。
2. 工具返回包含多字节字符、换行和超大 JSON：模型预览仍是合法结构，完整结果可按句柄读取，同一 `call_id` 的替换稳定。
3. 并行批次部分失败：成功结果保留，失败以结构化结果返回，最终排序与输入调用顺序一致。
4. 恢复请求范围变化或运行过期：后端返回明确冲突，不加载旧证据，不静默创建错误续跑。
5. 综合阶段模型超时或引用校验失败：确定性降级返回非空中文答案，公开证据和内部消息边界清楚。

---

### Task 1: 定义 Harness 状态对象与 SQLite 检查点

**Files:**
- Create: `backend/qa_harness_state.py`
- Create: `backend/qa_harness_store.py`
- Modify: `backend/store.py`
- Create: `tests/test_qa_harness_state.py`
- Create: `tests/test_qa_harness_store.py`

- [ ] **Step 1: 为状态迁移和范围隔离写失败测试**

  在 `tests/test_qa_harness_state.py` 覆盖：

  - 合法阶段为 `planning -> discovering -> reading -> reviewing -> synthesizing -> validating -> completed|degraded`；
  - 非法回退和跳跃抛出 `InvalidHarnessTransition`；
  - `HarnessScope` 的稳定哈希包含快照、主题、维度、开始和结束日期；
  - 相同范围哈希稳定，不同快照或筛选产生不同哈希。

- [ ] **Step 2: 运行测试并确认模块尚不存在而失败**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_state.py -q`

  Expected: FAIL，提示 `backend.qa_harness_state` 不存在。

- [ ] **Step 3: 实现状态、预算和事件类型**

  在 `backend/qa_harness_state.py` 定义：

  ```python
  class HarnessPhase(str, Enum): ...

  @dataclass(frozen=True)
  class HarnessScope:
      snapshot_id: str
      topic: str
      dimension: str
      start: str
      end: str
      def stable_hash(self) -> str: ...

  @dataclass
  class HarnessBudget:
      max_tool_calls: int
      max_retries: int
      max_context_tokens: int
      soft_context_tokens: int
      output_reserve_tokens: int

  @dataclass
  class ToolCallState: ...

  @dataclass
  class HarnessEvent: ...

  @dataclass
  class HarnessRun: ...

  def transition(run: HarnessRun, next_phase: HarnessPhase) -> HarnessRun: ...
  ```

  `HarnessRun` 至少保存 `run_id`、`answer_id`、原始问题、范围、阶段、状态、研究计划、预算、工具调用状态、公开证据 ID、内部消息 ID、研究记忆、最后有效草稿、模型名称、错误摘要和版本号。

- [ ] **Step 4: 为运行、步骤、结果工件持久化写失败测试**

  在 `tests/test_qa_harness_store.py` 使用临时 SQLite 覆盖：创建运行、追加步骤、保存检查点、保存完整工具工件、恢复运行、完成运行、拒绝范围不一致、服务重建 Store 后仍可恢复。

- [ ] **Step 5: 扩展数据库并实现 Store**

  在 `backend/store.py` 初始化：

  ```sql
  CREATE TABLE IF NOT EXISTS qa_runs (
      run_id TEXT PRIMARY KEY,
      answer_id TEXT,
      snapshot_id TEXT NOT NULL,
      scope_hash TEXT NOT NULL,
      phase TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      payload TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS qa_steps (
      run_id TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      payload TEXT NOT NULL,
      PRIMARY KEY (run_id, sequence)
  );

  CREATE TABLE IF NOT EXISTS qa_artifacts (
      artifact_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      call_id TEXT NOT NULL,
      created_at TEXT NOT NULL,
      byte_count INTEGER NOT NULL,
      token_estimate INTEGER NOT NULL,
      preview TEXT NOT NULL,
      payload TEXT NOT NULL
  );
  ```

  在 `backend/qa_harness_store.py` 实现：

  ```python
  def create_run(*, run_id, answer_id, scope, question, plan, budget, model) -> HarnessRun: ...
  def append_step(run_id: str, event: HarnessEvent) -> None: ...
  def save_checkpoint(run: HarnessRun) -> None: ...
  def save_artifact(run_id: str, call_id: str, payload: Any,
                    preview: Any, token_estimate: int) -> str: ...
  def load_run(run_id: str) -> HarnessRun | None: ...
  def find_resumable_run(run_id: str, scope: HarnessScope) -> HarnessRun | None: ...
  def complete_run(run_id: str, status: str, answer_id: str) -> None: ...
  ```

  Store 写入前调用现有敏感字段清理函数或新增局部清理器，过滤 `authorization`、`token`、`password`、`api_key` 等字段。

- [ ] **Step 6: 运行状态与存储测试**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_state.py tests/test_qa_harness_store.py -q`

  Expected: PASS。

- [ ] **Step 7: 提交本任务**

  ```powershell
  git add backend/qa_harness_state.py backend/qa_harness_store.py backend/store.py tests/test_qa_harness_state.py tests/test_qa_harness_store.py
  git commit -m "feat: add resumable QA harness state"
  ```

### Task 2: 建立结构化上下文管理与完整结果工件

**Files:**
- Create: `backend/qa_context.py`
- Modify: `.env.example`
- Create: `tests/test_qa_context.py`

- [ ] **Step 1: 为中文、多行和超大工具结果写失败测试**

  测试至少覆盖：

  - `estimate_tokens()` 对中文、英文和混合文本产生稳定正整数；
  - `build_preview()` 输出仍为 `dict/list/scalar`，经过 `json.dumps/json.loads` 后结构有效；
  - 预览优先保留记录 ID、标题、来源、发布时间、观测日期、单位、结果数量和截断标记；
  - 完整结果写入 `qa_artifacts`，模型消息只保留预览及 `artifact_id`；
  - 同一 `call_id` 多次渲染使用相同替换，不因后续上下文变化而漂移；
  - 超过软阈值时先移除可重取旧预览，再生成研究记忆；超过硬阈值时设置 `stop_discovery=True` 并保留最终输出预算。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_context.py -q`

  Expected: FAIL，提示 `backend.qa_context` 不存在。

- [ ] **Step 3: 实现上下文管理器**

  在 `backend/qa_context.py` 定义：

  ```python
  def estimate_tokens(value: Any) -> int: ...
  def build_preview(payload: Any, max_tokens: int) -> Any: ...

  @dataclass
  class ToolEnvelope:
      call_id: str
      tool: str
      ok: bool
      preview: Any
      artifact_id: str | None
      record_count: int | None
      duration_ms: int
      error: dict[str, Any] | None

  class ContextManager:
      def add_tool_result(self, envelope: ToolEnvelope, full_payload: Any) -> None: ...
      def render_messages(self, *, stage: str) -> list[dict[str, str]]: ...
      def compact_if_needed(self) -> CompactionEvent | None: ...
      def research_memory(self) -> dict[str, Any]: ...
  ```

  禁止通过字符串切片截断 JSON。研究记忆固定包含：已确认事实、相互矛盾说法、指标及日期、反证、公开证据 ID、内部消息 ID、缺口和待复核问题。

- [ ] **Step 4: 增加可配置预算**

  在 `.env.example` 增加并注释：

  ```dotenv
  QA_CONTEXT_MAX_TOKENS=32000
  QA_CONTEXT_SOFT_TOKENS=24000
  QA_CONTEXT_OUTPUT_RESERVE=6000
  QA_TOOL_PREVIEW_TOKENS=1800
  ```

- [ ] **Step 5: 运行上下文和存储回归测试**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_context.py tests/test_qa_harness_store.py -q`

  Expected: PASS；超大中文 JSON 用例不出现损坏 JSON。

- [ ] **Step 6: 提交本任务**

  ```powershell
  git add backend/qa_context.py .env.example tests/test_qa_context.py
  git commit -m "feat: add structured QA context management"
  ```

### Task 3: 加入有界并发调度与结构化错误回执

**Files:**
- Create: `backend/qa_scheduler.py`
- Modify: `backend/qa_agent.py`
- Create: `tests/test_qa_scheduler.py`
- Modify: `tests/test_qa_agent.py`

- [ ] **Step 1: 为并发上限、稳定排序和部分失败写失败测试**

  使用假的九工具容器覆盖：

  - 最多同时执行 `max_concurrency` 个调用；
  - 完成顺序乱序时，返回值仍按请求输入顺序排列；
  - 一个调用超时或抛错时，同批其他成功结果保留；
  - 参数错误和范围错误转换为结构化 `HarnessError`；
  - 可重试错误最多重试两次，不可重试错误不重试；
  - `call_id` 在重试和恢复中保持稳定。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_scheduler.py -q`

  Expected: FAIL，提示 `backend.qa_scheduler` 不存在。

- [ ] **Step 3: 实现调度器**

  在 `backend/qa_scheduler.py` 定义：

  ```python
  @dataclass(frozen=True)
  class ToolRequest:
      call_id: str
      tool: str
      arguments: dict[str, Any]
      timeout_seconds: float

  @dataclass
  class HarnessError:
      kind: str
      message: str
      retryable: bool

  @dataclass
  class ToolExecutionResult: ...

  async def execute_one(request: ToolRequest, tools: LocalGeoTools,
                        *, retry_attempts: int = 2) -> ToolExecutionResult: ...
  async def execute_batch(requests: Sequence[ToolRequest], tools: LocalGeoTools,
                          *, max_concurrency: int = 4) -> list[ToolExecutionResult]: ...
  ```

  同步工具通过 `asyncio.to_thread()` 执行，使用 `asyncio.Semaphore` 控制并发，超时用 `asyncio.timeout()`。所有异常转换成结果对象，不从批次向外抛出。

- [ ] **Step 4: 保护 `LocalGeoTools` 的运行期可变状态**

  在 `backend/qa_agent.py` 只为调用计数、已读取 ID 和缓存等可变运行状态增加锁或改为每次运行独立实例。不得更改 `TOOL_NAMES`、`TOOL_GUIDE`、工具参数解析和返回业务字段。

- [ ] **Step 5: 运行调度器、工具协议和 MCP 兼容测试**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_scheduler.py tests/test_qa_agent.py tests/test_internal_message_integration.py -q`

  Expected: PASS；九工具名称和参数快照不变。

- [ ] **Step 6: 提交本任务**

  ```powershell
  git add backend/qa_scheduler.py backend/qa_agent.py tests/test_qa_scheduler.py tests/test_qa_agent.py
  git commit -m "feat: schedule read-only QA tools safely"
  ```

### Task 4: 实现状态机 Runner、检查点恢复和确定性降级

**Files:**
- Create: `backend/qa_finalizer.py`
- Create: `backend/qa_harness.py`
- Modify: `backend/qa_agent.py`
- Modify: `backend/qa_planner.py`
- Modify: `backend/qa_retrieval.py`
- Create: `tests/test_qa_finalizer.py`
- Create: `tests/test_qa_harness.py`

- [ ] **Step 1: 为最终降级顺序写失败测试**

  在 `tests/test_qa_finalizer.py` 验证：

  1. 优先返回通过引用与数字校验的最终稿；
  2. 最终稿失败时清理最后有效草稿中的无效句；
  3. 无有效草稿时由研究记忆、公开证据和内部消息生成确定性结构化回答；
  4. 信息极少时仍返回“当前可确认内容、来源范围、数据缺口、后续观察”四段中文内容；
  5. 内部消息使用单独标题和内部 ID，不生成外部链接；
  6. 任意路径均返回非空文本、完成状态和降级原因。

- [ ] **Step 2: 为完整状态机和恢复写失败测试**

  在 `tests/test_qa_harness.py` 使用假模型和假工具覆盖：

  - 正常运行依次进入七阶段并保存检查点；
  - 第一轮宽检索、模型选择全文、复核补查和最终综合形成闭环；
  - 工具批次后模拟进程中断，恢复时跳过相同 `call_id` 的成功调用；
  - 模型综合首次超时后从最近检查点重试一次；
  - 第二次超时进入确定性降级；
  - 引用校验失败时删除问题引用或问题句，而不是丢弃整份回答；
  - 动态证据预算随问题复杂度增加，不固定为 15、16 或 36 条；
  - 到达上下文硬阈值后停止扩展检索并进入综合。

- [ ] **Step 3: 运行测试并确认失败**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_finalizer.py tests/test_qa_harness.py -q`

  Expected: FAIL，提示新模块或入口不存在。

- [ ] **Step 4: 实现最终器**

  在 `backend/qa_finalizer.py` 定义：

  ```python
  def finalize_validated(*, draft, citation_map, evidence, metrics,
                         internal_messages, scope) -> FinalAnswer: ...
  def deterministic_fallback(*, last_valid_draft, research_memory,
                             evidence, metrics, internal_messages,
                             scope, failure) -> FinalAnswer: ...
  ```

  将正文 Markdown 与引用映射分开校验。单个数字或引用失败仅移除相关句或引用，不得把整个回答替换为错误提示。

- [ ] **Step 5: 实现单 Agent Harness Runner**

  在 `backend/qa_harness.py` 定义：

  ```python
  async def run_local_harness(*, question, scope, llm, tools, store,
                              run_id=None, progress=None) -> HarnessOutcome: ...
  ```

  Runner 负责：

  - 调用现有问题规划器拆解时间、事件主线、军事、外交、市场、反证和观察点；
  - 启动阶段并行读取工作区摘要、指标目录和内部消息统计；
  - 执行关键词、实体、时间和来源等级混合宽检索；
  - 让模型选择关键公开证据与内部消息 ID，再读取完整正文；
  - 基于上一轮草稿自动生成补查问题并调用工具；
  - 每个阶段、每个工具批次和每次压缩后保存检查点；
  - 使用现有 JSON 工具动作协议，不重新注册九个工具；
  - 模型调用超时重试一次，仍失败则调用 `deterministic_fallback()`；
  - 发送不含隐藏思维链的计划、工具、复核、压缩和降级进度事件。

- [ ] **Step 6: 保留兼容入口并迁移规划和检索预算**

  在 `backend/qa_agent.py` 保留现有 `run_agent()` 签名，对本地路径转调 `run_local_harness()`。在 `backend/qa_planner.py` 和 `backend/qa_retrieval.py` 增加按问题复杂度计算的证据预算与补查词，但保持检索的快照和范围约束。

- [ ] **Step 7: 运行 Harness 和现有问答测试**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_finalizer.py tests/test_qa_harness.py tests/test_qa_agent.py tests/test_backend.py -q`

  Expected: PASS；所有失败注入用例都有非空中文答案。

- [ ] **Step 8: 提交本任务**

  ```powershell
  git add backend/qa_finalizer.py backend/qa_harness.py backend/qa_agent.py backend/qa_planner.py backend/qa_retrieval.py tests/test_qa_finalizer.py tests/test_qa_harness.py
  git commit -m "feat: run local QA through resilient harness"
  ```

### Task 5: 接入本地问答 API、续跑协议并保护 PiChat

**Files:**
- Modify: `backend/research.py`
- Modify: `backend/app.py`
- Modify: `tests/test_backend.py`
- Create: `tests/test_qa_harness_api.py`
- Modify: `tests/test_llm_transport.py`

- [ ] **Step 1: 为流式运行 ID、续跑和 PiChat 隔离写失败测试**

  覆盖：

  - 本地 `/api/ask/stream` 第一条 NDJSON 事件包含 `type=run`、`run_id` 和当前阶段；
  - 请求可携带 `run_id` 续跑未完成运行；
  - 同一 `run_id` 但范围哈希不同返回 HTTP 409；
  - 已完成运行重复提交返回已保存答案，不重复调用工具；
  - `GET /api/qa-runs/{run_id}` 返回阶段、状态、审计步骤和是否可恢复，不返回完整敏感工件；
  - 流式连接中断不取消后端已开始的检查点保存；
  - provider 为 PiChat 时不创建 `qa_runs`、不经过 `run_local_harness()`，原请求及返回结构不变。

- [ ] **Step 2: 运行测试并确认失败**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_api.py tests/test_llm_transport.py -q`

  Expected: FAIL，提示 `run_id` 或运行状态接口尚未实现。

- [ ] **Step 3: 接入 Research 层但保持 PiChat 分支原样**

  在 `backend/research.py` 仅当 `provider != "pichat"` 且本地 `agent_mode == "tools"` 时调用 Harness。增加可选 `run_id` 和进度回调传递；PiChat 条件分支和现有函数调用保持原样。

- [ ] **Step 4: 扩展 FastAPI 请求与流事件**

  在 `backend/app.py`：

  - 为 `Ask` 增加 `run_id: str = ""`；
  - 本地流首先发送 `{ "type": "run", "run_id": ..., "phase": ... }`；
  - `progress` 事件携带阶段、调用 ID、工具、耗时、结果数、重试数、上下文规模和是否压缩；
  - 新增 `GET /api/qa-runs/{run_id}`；
  - 范围不匹配用 409 和中文错误说明；
  - 最终 `answer` 事件无论成功或降级都包含正文。

- [ ] **Step 5: 运行 API、PiChat 和 MCP 回归测试**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_api.py tests/test_backend.py tests/test_llm_transport.py tests/test_internal_message_integration.py -q`

  Expected: PASS；PiChat 用例没有新增 Harness 调用。

- [ ] **Step 6: 提交本任务**

  ```powershell
  git add backend/research.py backend/app.py tests/test_backend.py tests/test_qa_harness_api.py tests/test_llm_transport.py
  git commit -m "feat: expose resumable local QA runs"
  ```

### Task 6: 在前端展示真实运行过程并自动恢复流式问答

**Files:**
- Modify: `src/types.ts`
- Modify: `src/api.ts`
- Modify: `src/App.tsx`
- Modify: `src/style.css`
- Create: `tests/qa_harness_ui.test.cjs`
- Modify: `tests/research_details.test.cjs`
- Modify: `tests/research_layout.test.cjs`

- [ ] **Step 1: 为运行事件解析、范围键和恢复 UI 写失败测试**

  在 Node 测试中验证：

  - NDJSON `run`、`progress`、`answer` 和 `error` 事件均能解析；
  - 活跃运行键由快照、主题、维度、日期和问题哈希组成；
  - 同范围断线后再次提交携带原 `run_id`；
  - 范围变化不复用旧运行；
  - 分析过程显示阶段、工具名、耗时、结果数、重试、上下文压缩和恢复标记；
  - 不显示模型隐藏思维链；
  - 旧 `Answer` 没有 Harness 字段时继续使用现有布局；
  - 降级回答仍显示完整正文和原因。

- [ ] **Step 2: 运行前端测试并确认失败**

  Run: `node --test tests/qa_harness_ui.test.cjs tests/research_details.test.cjs tests/research_layout.test.cjs`

  Expected: FAIL，提示运行事件或新状态组件尚不存在。

- [ ] **Step 3: 扩展前端类型和 NDJSON 客户端**

  在 `src/types.ts` 增加：

  ```typescript
  export type QaRunStatus = "running" | "completed" | "degraded" | "failed";

  export interface AnalysisRound {
    run_id?: string;
    phase?: string;
    call_id?: string;
    tool?: string;
    duration_ms?: number;
    result_count?: number;
    retry_count?: number;
    context_tokens?: number;
    compacted?: boolean;
    recovered?: boolean;
    // 保留现有字段
  }
  ```

  在 `src/api.ts` 扩展流客户端支持 `run` 事件，并将 `run_id` 返回给调用方；不要改变其他接口的通用请求行为。

- [ ] **Step 4: 更新问答状态和过程面板**

  在 `src/App.tsx`：

  - 收到 `run` 事件后立即按范围键保存 `run_id` 到 `localStorage`；
  - 流中断且运行未结束时，从运行状态接口检查并续跑；
  - 完成后删除活跃运行键；
  - 过程面板按阶段分组展示计划、检索、全文读取、复核、综合、校验；
  - 并行调用分别显示，不把内部过程伪装成模型思维；
  - 保留现有提交后清空输入框行为。

  在 `src/style.css` 增加恢复、压缩、重试和降级状态样式，复用现有暗色设计变量。

- [ ] **Step 5: 运行前端测试和生产构建**

  Run: `node --test tests/qa_harness_ui.test.cjs tests/research_details.test.cjs tests/research_layout.test.cjs`

  Expected: PASS。

  Run: `npm run build`

  Expected: PASS，无 TypeScript 错误。

- [ ] **Step 6: 提交本任务**

  ```powershell
  git add src/types.ts src/api.ts src/App.tsx src/style.css tests/qa_harness_ui.test.cjs tests/research_details.test.cjs tests/research_layout.test.cjs
  git commit -m "feat: show and resume local QA harness runs"
  ```

### Task 7: 加入运行观测、固定问题评测和兼容性验收

**Files:**
- Modify: `scripts/evaluate_qa.py`
- Modify: `config/qa-benchmark.json`
- Modify: `README.md`
- Create: `tests/test_qa_harness_observability.py`
- Create: `tests/test_qa_harness_compatibility.py`

- [ ] **Step 1: 为可观测字段和敏感信息过滤写失败测试**

  验证运行统计包含阶段耗时、模型请求数、工具成功率、重试、压缩次数、上下文峰值、候选数、全文读取数、最终引用数和降级原因；同时验证日志和接口序列化结果不包含 `Authorization`、`Bearer`、密码、API key 或 access token。

- [ ] **Step 2: 为 PiChat 和九工具兼容性写冻结测试**

  在 `tests/test_qa_harness_compatibility.py` 固化：

  - 九个工具名称集合；
  - 每个工具必填参数和默认值；
  - PiChat 请求构造、工具暴露和答案解析；
  - MCP 对外工具仍可调用；
  - 本地 Harness 不向 PiChat 运行写入 `qa_runs`。

- [ ] **Step 3: 运行测试并确认缺少字段而失败**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_observability.py tests/test_qa_harness_compatibility.py -q`

  Expected: FAIL，提示评测结果或运行摘要字段缺失。

- [ ] **Step 4: 扩展固定问题评测**

  在 `scripts/evaluate_qa.py` 记录：

  - `run_id`、完成状态和降级原因；
  - 各阶段耗时、工具成功率与重试次数；
  - 上下文峰值、压缩次数、证据候选数、全文读取数和最终引用数；
  - 最新事实覆盖、引用可打开率、引用支持度、无来源数字率、反证、缺口和资产传导解释评分。

  在 `config/qa-benchmark.json` 保留现有问题，并增加至少四种用例：最新美伊进展、最新俄乌进展、跨主题资产传导、华泰消息与公开证据交叉核验。每个用例明确期望时间窗、必须覆盖维度和禁止断言。

- [ ] **Step 5: 更新运行与故障恢复文档**

  在 `README.md` 增加本地 Harness 配置、运行阶段、断点恢复、前端过程展示、评测命令和 PiChat 不受影响的说明。不得把本地密钥、华泰凭据或实际内部地址写入文档。

- [ ] **Step 6: 运行全量相关测试**

  Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_state.py tests/test_qa_harness_store.py tests/test_qa_context.py tests/test_qa_scheduler.py tests/test_qa_finalizer.py tests/test_qa_harness.py tests/test_qa_harness_api.py tests/test_qa_harness_observability.py tests/test_qa_harness_compatibility.py tests/test_qa_agent.py tests/test_backend.py tests/test_llm_transport.py tests/test_internal_message_integration.py -q`

  Expected: PASS。

  Run: `node --test tests/qa_harness_ui.test.cjs tests/research_details.test.cjs tests/research_layout.test.cjs`

  Expected: PASS。

  Run: `npm run build`

  Expected: PASS。

- [ ] **Step 7: 运行固定问题评测并检查降级路径**

  Run: `.venv\Scripts\python.exe scripts/evaluate_qa.py --config config/qa-benchmark.json --output output/qa-harness-benchmark.json`

  Expected: 输出包含本地 Harness 和 PiChat 的分项指标；本地失败注入样本均有非空中文答案，PiChat 请求协议无变化。

- [ ] **Step 8: 提交本任务**

  ```powershell
  git add scripts/evaluate_qa.py config/qa-benchmark.json README.md tests/test_qa_harness_observability.py tests/test_qa_harness_compatibility.py
  git commit -m "test: benchmark resilient local QA harness"
  ```

### Task 8: 进行浏览器验收和最终回归

**Files:**
- Modify if defects are found: `backend/qa_harness.py`, `backend/qa_finalizer.py`, `backend/app.py`, `src/App.tsx`, `src/style.css`
- Modify if defects are found: corresponding files under `tests/`
- Create: `docs/qa/local-qa-harness-acceptance.md`

- [ ] **Step 1: 启动服务并检查健康状态**

  Run: `start.bat`

  Expected: 主站、MCP 和本地数据服务正常启动；启动日志不包含凭据。

- [ ] **Step 2: 浏览器验收正常回答**

  在 `http://127.0.0.1:8000/` 依次询问最新美伊进展、最新俄乌进展和资产传导问题。确认：过程阶段实时更新、工具调用可展开、引用可打开、华泰消息单独标识、最终回答非检索列表而是完整中文分析。

- [ ] **Step 3: 浏览器验收恢复与降级**

  使用测试开关或依赖注入分别模拟：流式连接断开、一个工具超时、综合模型超时、引用数字无效。确认恢复后不重复成功工具，部分失败仍保留成功结果，任何场景都有最终回答。

- [ ] **Step 4: 记录验收结果**

  在 `docs/qa/local-qa-harness-acceptance.md` 记录环境、问题、运行 ID、阶段耗时、引用结果、恢复行为、降级行为和仍存在的限制。只记录脱敏摘要。

- [ ] **Step 5: 修复验收缺陷并补回归测试**

  每个发现的缺陷先添加最小失败测试，再修改对应实现；重复 Task 7 的全量相关测试和构建命令，直到全部通过。

- [ ] **Step 6: 提交最终验收改动**

  ```powershell
  git add backend src tests docs/qa/local-qa-harness-acceptance.md
  git commit -m "test: complete local QA harness acceptance"
  ```

