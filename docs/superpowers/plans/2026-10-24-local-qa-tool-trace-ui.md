# GeoAgent Local QA Tool Trace UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为本地智能问答增加可追溯的 MCP 工具调用时间线、按需完整详情和可定位的错误来源，同时保持 PiChat 与九个正式工具协议不变。

**Architecture:** Harness 为每个工具调用发布版本化的开始与终态事件，并将完整脱敏结果继续保存在 `qa_artifacts`。流式接口只传紧凑审计摘要；前端按阶段分组展示调用卡片，并通过运行与调用 ID 按需读取详情抽屉。

**Tech Stack:** Python 3、FastAPI、SQLite、React、TypeScript、NDJSON、pytest、Node test runner、Vite。

**Spec:** `docs/superpowers/specs/2026-10-24-local-qa-tool-trace-ui-design.md`

## Global Constraints

- 不修改 PiChat provider、嵌入页和 PiChat 自身工具循环。
- 不修改九个本地正式工具的名称、参数和业务语义。
- 不修改对外 GeoAgent MCP 的二十个工具。
- 不展示模型隐藏思维链，不增加语音回复。
- 工具参数、错误和 Artifact 必须脱敏，不返回凭据、内部上游身份或本地绝对路径。
- 流式响应保持紧凑，完整结果仅通过详情接口按需读取。
- 历史回答没有 `tool-trace/v1` 字段时继续使用旧展示方式。

## Review Focus

- 同一 `call_id` 的 running 与终态事件必须在前端合并，不能重复显示成两个调用。
- 并行批次交错完成时，序号必须稳定且每个调用状态独立。
- 超时、参数错误和服务不可用必须准确归属到 MCP 与工具，同时保留其他成功结果。
- Artifact 详情不得跨运行读取，也不得泄露凭据或本地路径。
- 长参数、长 JSON 和窄屏不得让整个问答面板产生横向滚动。

---

### Task 1: 版本化工具追踪事件

**Files:**
- Modify: `backend/qa_harness_state.py`
- Modify: `backend/qa_scheduler.py`
- Modify: `backend/qa_harness.py`
- Modify: `backend/qa_harness_store.py`
- Test: `tests/test_qa_harness_state.py`
- Test: `tests/test_qa_scheduler.py`
- Test: `tests/test_qa_harness.py`
- Test: `tests/test_qa_harness_store.py`

**Interfaces:**
- Produces: `HarnessEvent` 的 `event_version`, `run_id`, `mcp_server`, `attempt`, `started_at`, `finished_at`, `arguments_summary`, `result_summary`, `artifact_id`, `truncated`, `error` 字段。
- Produces: `HarnessStore.get_tool_call_detail(run_id: str, call_id: str) -> dict | None` 所需的完整持久化数据。

- [ ] **Step 1: 写失败测试，固定 `tool-trace/v1` 事件字段与脱敏规则**

测试断言同一调用先发布 `running`，后发布 `completed`；两者 `call_id` 相同，终态包含耗时、结果数量、Artifact 与脱敏参数。

- [ ] **Step 2: 运行事件与存储测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_state.py tests/test_qa_scheduler.py tests/test_qa_harness.py tests/test_qa_harness_store.py -q`

Expected: 新追踪字段和开始事件断言失败。

- [ ] **Step 3: 扩展 `HarnessEvent` 和 `ToolCallState`**

新增规范字段，旧记录反序列化时使用安全默认值；事件结构版本固定为 `tool-trace/v1`。

- [ ] **Step 4: 在 `run_local_harness.execute()` 发布开始与终态事件**

开始事件在调度前写入；终态事件使用调度结果和保存后的 Artifact；重试使用相同 `call_id` 并更新 `attempt`。

- [ ] **Step 5: 对参数、错误和结果摘要统一脱敏**

复用存储层脱敏规则，增加本地绝对路径模式；流式事件不得包含完整正文。

- [ ] **Step 6: 运行 Task 1 测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_state.py tests/test_qa_scheduler.py tests/test_qa_harness.py tests/test_qa_harness_store.py -q`

Expected: PASS。

### Task 2: 工具调用详情接口

**Files:**
- Modify: `backend/qa_harness_store.py`
- Modify: `backend/app.py`
- Create: `tests/test_qa_tool_trace_api.py`

**Interfaces:**
- Consumes: Task 1 的 `ToolCallState`、审计事件与 Artifact。
- Produces: `GET /api/qa-runs/{run_id}/tool-calls/{call_id}`。

- [ ] **Step 1: 写接口失败测试**

覆盖成功详情、未知运行 404、未知调用 404、跨运行调用 404、脱敏参数、失败错误对象和 Artifact 截断状态。

- [ ] **Step 2: 运行接口测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_tool_trace_api.py -q`

Expected: 路由不存在或详情读取方法不存在。

- [ ] **Step 3: 实现 `HarnessStore.get_tool_call_detail`**

签名：`get_tool_call_detail(self, run_id: str, call_id: str) -> dict[str, Any] | None`。只从该运行的 `ToolCallState` 和所属 Artifact 组装结果。

- [ ] **Step 4: 实现 FastAPI 详情路由**

返回运行、快照、阶段、MCP、工具、脱敏参数、时间线、状态、错误、结果数量、截断信息、Artifact 和证据 ID。

- [ ] **Step 5: 运行 Task 2 测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_tool_trace_api.py tests/test_backend.py -q`

Expected: PASS。

### Task 3: 前端阶段分组与工具调用卡片

**Files:**
- Create: `src/QaTrace.tsx`
- Modify: `src/App.tsx`
- Modify: `src/types.ts`
- Modify: `src/qa_ui.ts`
- Modify: `src/style.css`
- Modify: `tests/qa_ui.test.cjs`
- Modify: `tests/research_layout.test.cjs`

**Interfaces:**
- Consumes: Task 1 的 `AnalysisRound` 追踪字段。
- Produces: `AnalysisStageGroup`, `ToolCallCard` 与新 `AnalysisProcess`。

- [ ] **Step 1: 写失败测试，固定追踪合并和默认展开规则**

测试 running→completed 合并、并行调用保留、失败自动展开、成功折叠、历史旧结构兼容及中文工具映射。

- [ ] **Step 2: 运行前端单元测试并确认失败**

Run: `node --test tests/qa_ui.test.cjs tests/research_layout.test.cjs`

Expected: 新组件、映射或展开规则断言失败。

- [ ] **Step 3: 扩展 TypeScript 类型与纯函数**

新增 `ToolTraceError`、`ToolTraceDetail` 和追踪字段；在 `qa_ui.ts` 增加阶段分组、中文工具名称及默认展开判定。

- [ ] **Step 4: 创建 `src/QaTrace.tsx`**

组件只负责阶段和卡片渲染；成功默认折叠，运行中、降级和失败自动展开；显示 MCP、工具、耗时、数量、重试、恢复和截断。

- [ ] **Step 5: 在 `App.tsx` 替换内联 `AnalysisProcess`**

最终回答、证据按钮和 PiChat 分支保持原样；删除语音相关入口的风险通过现有 UI 断言固定。

- [ ] **Step 6: 增加响应式样式**

详情键值允许换行，JSON 代码块局部滚动，窄屏抽屉全宽，整个聊天面板不产生横向滚动。

- [ ] **Step 7: 运行 Task 3 测试和构建**

Run: `npm test -- --runInBand && npm run build`

Expected: 全部测试和生产构建通过。

### Task 4: 完整详情抽屉与并发保护

**Files:**
- Modify: `src/api.ts`
- Modify: `src/QaTrace.tsx`
- Modify: `src/App.tsx`
- Modify: `src/types.ts`
- Modify: `src/style.css`
- Create: `tests/qa_trace_detail.test.cjs`

**Interfaces:**
- Consumes: Task 2 的详情接口。
- Produces: `ToolCallDrawer`，以及 `api<ToolTraceDetail>()` 的按需调用。

- [ ] **Step 1: 写失败测试，覆盖详情加载生命周期**

覆盖打开、加载、成功、接口错误、关闭、快速切换调用和迟到响应不得覆盖当前抽屉。

- [ ] **Step 2: 运行详情测试并确认失败**

Run: `node --test tests/qa_trace_detail.test.cjs`

Expected: 抽屉状态与并发保护尚不存在。

- [ ] **Step 3: 实现详情请求状态与取消令牌**

调用键由 `run_id + call_id` 组成；每次打开或关闭递增请求代次，只有当前代次可更新状态。

- [ ] **Step 4: 实现技术详情和完整详情交互**

技术详情显示流式脱敏摘要；完整详情显示归属、时间线、参数、错误、结果预览和 Artifact，不执行其中任何文本。

- [ ] **Step 5: 运行 Task 4 测试和构建**

Run: `node --test tests/qa_trace_detail.test.cjs tests/qa_ui.test.cjs && npm run build`

Expected: PASS。

### Task 5: 兼容、失败注入与真实验收

**Files:**
- Modify: `tests/test_qa_harness_compatibility.py`
- Create: `tests/test_qa_harness_observability.py`
- Modify: `tests/api_stream.test.cjs`
- Modify: `docs/qa/local-qa-harness-acceptance.md`

**Interfaces:**
- Consumes: Tasks 1–4 的事件、接口和 UI。
- Produces: 可重复运行的兼容与验收证据。

- [ ] **Step 1: 增加失败注入与兼容测试**

覆盖工具超时、参数错误、服务不可用、模型格式重试、断点恢复、PiChat 不进入本地 Harness、九工具集合不变和流式响应不含完整 Artifact。

- [ ] **Step 2: 运行组合测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_qa_harness_observability.py tests/test_qa_harness_compatibility.py tests/test_qa_tool_trace_api.py tests/test_backend.py -q`

Run: `npm test -- --runInBand && npm run build`

Expected: PASS。

- [ ] **Step 3: 运行全量后端测试**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: 无新增失败；外部环境跳过项单独记录。

- [ ] **Step 4: 重启服务并执行真实问题**

问题至少覆盖最新美伊进展、资产与指标综合、华泰消息补充。记录运行 ID、工具成功率、调用耗时、错误归属、引用打开、HTML 报告和恢复耗时。

- [ ] **Step 5: 页面验收**

验证成功折叠、失败展开、技术详情、完整详情、窄屏无整页横向滚动、最终回答与过程分离、无语音入口。

- [ ] **Step 6: 更新验收文档**

在 `docs/qa/local-qa-harness-acceptance.md` 记录脱敏后的运行结果、已知外部限制和回归命令。

