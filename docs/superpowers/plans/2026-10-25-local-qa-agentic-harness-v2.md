# GeoAgent Local QA Agentic Harness V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本地模型问答升级为有证据台账、覆盖门禁和自适应子 Agent 工具循环的研究系统，使美伊、俄乌和资产问题的回答质量接近 PiChat，同时保持 PiChat 路径和现有 9 个 MCP 工具协议兼容。

**Architecture:** 协调器冻结研究范围并维护共享 ResearchLedger；事件、市场和反证子 Agent 在角色白名单和预算内执行有限工具循环；ReadinessGate 根据研究分面覆盖状态决定继续补查或进入综合。检索、全文、指标和引用均写入可恢复检查点，最终数值块由程序渲染，模型仅负责解释与有证据的判断。

**Tech Stack:** Python 3.11、FastAPI、SQLite、asyncio、React、TypeScript、Node test runner、pytest。

**Spec:** `docs/superpowers/specs/2026-10-25-local-qa-agentic-harness-v2-design.md`

## Global Constraints

- PiChat 的嵌入、认证和后台调用路径保持不变。
- 现有 9 个本地问答工具的公开名称和参数协议保持不变。
- 不将报道数量解释为概率或冲突强度。
- 不展示模型隐藏思维链，只展示工具、参数摘要、结果、耗时、覆盖状态和错误。
- 关键新闻主张必须至少绑定一条已读取全文的公开证据；内部消息不得进入公开引用映射。
- 数字、单位、日期和变化由程序生成或核验；校验失败不得导致最终回答为空或 Markdown 表格残缺。
- 所有 Agent、引用和导出报告必须使用同一冻结快照和筛选范围。
- 保留当前脏工作区中的用户改动；每次提交只暂存本任务明确列出的文件。

## Review Focus

- 相同查询被多个 Agent 调用时，必须复用结果且不能把“新增 0 条”显示成“无证据”；Task 3 的缓存和回执测试覆盖。
- 最新问题在最近 72 小时没有高可信材料时，必须显示陈旧性提示；Task 2 与 Task 4 的时间测试覆盖。
- 子 Agent 超时或模型返回非法动作时，其他 Agent 继续，最终仍返回自包含答案；Task 4 与 Task 7 的降级测试覆盖。
- 数字或引用局部失败时只能降级对应主张，不能删除整表或整篇答案；Task 6 的表格与引用修复测试覆盖。
- 俄乌问题出现俄罗斯—伊朗或泛中东材料时只能进入跨主题补充，不得进入核心事实；Task 2 的主题污染测试覆盖。

---

### Task 1: ResearchLedger 与持久化研究记忆

**Files:**
- Create: `backend/qa_ledger.py`
- Modify: `backend/qa_context.py`
- Modify: `backend/qa_harness_state.py`
- Modify: `backend/qa_harness_store.py`
- Test: `tests/test_qa_ledger.py`
- Test: `tests/test_qa_context.py`
- Test: `tests/test_qa_harness_store.py`

**Interfaces:**
- Produces: `LedgerEntry`, `ResearchLedger`, `ResearchLedger.merge_entry()`, `ResearchLedger.to_dict()`, `ResearchLedger.from_dict()`。
- Produces: `ContextManager.apply_ledger_updates(updates: list[dict[str, Any]]) -> None`。
- Consumes: 现有 `HarnessRun.research_memory` 和 SQLite 检查点机制。

- [ ] **Step 1: 写 ResearchLedger 的失败测试**

测试主张类型校验、稳定 ID、支持与冲突证据合并、全文 ID 合并、独立来源与转载来源分离，以及同一主张重复写入不重复计数。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_qa_ledger.py -q`  
Expected: FAIL，原因是 `backend.qa_ledger` 不存在。

- [ ] **Step 3: 实现 `backend/qa_ledger.py`**

定义 `LedgerEntry` 数据类和 `ResearchLedger`；状态仅允许 `verified`、`reported`、`single_party`、`inference`、`conflicted`、`gap`，主张类型使用设计文档第 5.2 节枚举。

- [ ] **Step 4: 写上下文与检查点恢复失败测试**

断言上下文压缩前后 ledger 内容一致，恢复运行能从 `research_memory["ledger"]` 重建对象，历史检查点没有 ledger 时使用空台账兼容启动。

- [ ] **Step 5: 接入 ContextManager 与 HarnessStore**

在 `ContextManager` 中持有 ledger，只向模型输入精简台账；完整结构保存在检查点。不得改变现有 artifact 表格式。

- [ ] **Step 6: 运行 Task 1 回归**

Run: `python -m pytest tests/test_qa_ledger.py tests/test_qa_context.py tests/test_qa_harness_store.py -q`  
Expected: PASS。

- [ ] **Step 7: 提交 Task 1**

```bash
git add backend/qa_ledger.py backend/qa_context.py backend/qa_harness_state.py backend/qa_harness_store.py tests/test_qa_ledger.py tests/test_qa_context.py tests/test_qa_harness_store.py
git commit -m "feat: add persistent QA research ledger"
```

### Task 2: 结构化查询、主题门控与时间新鲜度

**Files:**
- Create: `backend/qa_query.py`
- Modify: `backend/qa_planner.py`
- Modify: `backend/qa_retrieval.py`
- Modify: `backend/qa_agent.py`
- Test: `tests/test_qa_query.py`
- Test: `tests/test_qa_agent.py`

**Interfaces:**
- Consumes: Task 1 的 `ResearchLedger` 用于记录 rejected/cross-topic 原因。
- Produces: `FacetQuery`, `compile_query()`, `classify_topic_lane()`, `freshness_window()`。
- Produces: `hybrid_retrieve(..., query_spec: FacetQuery | None)` 向后兼容原字符串查询。

- [ ] **Step 1: 写结构化查询与主题污染失败测试**

覆盖美伊、俄乌核心准入、跨主题 lane、排除实体、`event_at`/`published_at` 双时间轴，以及俄乌问题中“俄罗斯斡旋伊朗”不能成为核心事实。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_qa_query.py -q`  
Expected: FAIL，原因是结构化查询接口不存在。

- [ ] **Step 3: 实现 `FacetQuery` 与主题门控**

字段固定为 `facet`、`query`、`must_entities`、`should_terms`、`exclude_entities`、`start`、`end`、`preferred_tiers`、`expected_role`、`lane`。`compile_query()` 只负责编译现有工具参数，不改变 MCP 公开协议。

- [ ] **Step 4: 改造规划器输出分面查询**

保留现有 `search_queries` 兼容字段，同时新增 `query_specs`。最新问题生成 48 小时、7 天和背景三层计划；用户显式日期优先。

- [ ] **Step 5: 改造混合检索与全文优先级**

先执行实体/排除/时间门控，再计算相关性；使用 `event_at` 判断事件新鲜度，`published_at` 判断发现新鲜度；输出 `latest_high_confidence_at`、`latest_any_source_at` 和 `stale_warning`。

- [ ] **Step 6: 运行 Task 2 回归**

Run: `python -m pytest tests/test_qa_query.py tests/test_qa_agent.py -q`  
Expected: PASS，且历史字符串查询测试继续通过。

- [ ] **Step 7: 提交 Task 2**

```bash
git add backend/qa_query.py backend/qa_planner.py backend/qa_retrieval.py backend/qa_agent.py tests/test_qa_query.py tests/test_qa_agent.py
git commit -m "feat: add facet-aware evidence retrieval"
```

### Task 3: 共享查询缓存与明确的检索回执

**Files:**
- Create: `backend/qa_tool_cache.py`
- Modify: `backend/qa_agent.py`
- Modify: `backend/qa_harness.py`
- Modify: `backend/qa_scheduler.py`
- Test: `tests/test_qa_tool_cache.py`
- Test: `tests/test_qa_harness.py`
- Test: `tests/test_qa_harness_observability.py`

**Interfaces:**
- Consumes: Task 2 的规范化查询参数。
- Produces: `SharedToolCache.get_or_execute(key, executor)`。
- Produces: 搜索回执字段 `matched_total`、`matched_already_seen`、`returned_new`、`budget_exhausted`、`top_existing_ids`、`cache_reused`。

- [ ] **Step 1: 写共享缓存与重复搜索失败测试**

断言相同 scope、工具和参数只执行一次；不同 Agent 都获得同一排序视图；第二次调用显示命中数和新增 0，而不是结果 0。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_qa_tool_cache.py -q`  
Expected: FAIL，原因是缓存接口不存在。

- [ ] **Step 3: 实现缓存键和并发去重**

缓存键固定包含 snapshot、topic、dimension、start、end、tool、canonical arguments；并发相同请求共享同一个 future，失败结果不永久缓存。

- [ ] **Step 4: 接入 Harness 执行路径和检索回执**

底层执行与缓存复用都写入 trace；只对首次发现的唯一证据计发现预算，缓存复用不重复计费。

- [ ] **Step 5: 运行 Task 3 回归**

Run: `python -m pytest tests/test_qa_tool_cache.py tests/test_qa_harness.py tests/test_qa_harness_observability.py -q`  
Expected: PASS。

- [ ] **Step 6: 提交 Task 3**

```bash
git add backend/qa_tool_cache.py backend/qa_agent.py backend/qa_harness.py backend/qa_scheduler.py tests/test_qa_tool_cache.py tests/test_qa_harness.py tests/test_qa_harness_observability.py
git commit -m "feat: share QA tool results across agents"
```

### Task 4: ReadinessGate 与分面全文配额

**Files:**
- Create: `backend/qa_readiness.py`
- Modify: `backend/qa_harness.py`
- Modify: `backend/qa_context.py`
- Test: `tests/test_qa_readiness.py`
- Test: `tests/test_qa_harness.py`

**Interfaces:**
- Consumes: Task 1 的 `ResearchLedger`、Task 2 的 `FacetQuery`、Task 3 的检索回执。
- Produces: `CoverageState`, `ReadinessReport`, `evaluate_readiness(plan, ledger, tool_state, budget) -> ReadinessReport`。
- Produces: `select_faceted_reads(...) -> list[str]`。

- [ ] **Step 1: 写覆盖门禁失败测试**

覆盖模型首次返回 final 但军事、反证或最新时间层未完成时不得结束；预算耗尽时允许降级收敛；最近 72 小时无高可信证据时必须生成 stale warning。

- [ ] **Step 2: 写分面全文配额失败测试**

断言复杂局势问题至少选取最新事件、外交/军事和反证材料；某一高频分面不能挤掉其他必需分面；关键引用默认来自 `full_text_ids`。

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_qa_readiness.py tests/test_qa_harness.py -q`  
Expected: FAIL。

- [ ] **Step 4: 实现 ReadinessGate 和全文选择器**

覆盖状态只允许 `unstarted`、`searched`、`full_text_supported`、`conflicted`、`gap_confirmed`、`budget_exhausted`。模型提前 final 保存为 `candidate_draft`，未通过门禁时根据缺口继续研究。

- [ ] **Step 5: 接入主协调循环**

每次工具结果和子 Agent memo 后重新计算门禁；trace 发布每个 facet 的状态和未满足原因；预算耗尽时把未完成项写入最终 gaps。

- [ ] **Step 6: 运行 Task 4 回归**

Run: `python -m pytest tests/test_qa_readiness.py tests/test_qa_harness.py tests/test_qa_context.py -q`  
Expected: PASS。

- [ ] **Step 7: 提交 Task 4**

```bash
git add backend/qa_readiness.py backend/qa_harness.py backend/qa_context.py tests/test_qa_readiness.py tests/test_qa_harness.py
git commit -m "feat: gate QA synthesis on evidence coverage"
```

### Task 5: 子 Agent 自适应有限工具循环

**Files:**
- Modify: `backend/qa_multiagent.py`
- Modify: `backend/qa_harness.py`
- Modify: `backend/qa_harness_state.py`
- Test: `tests/test_qa_multiagent.py`
- Test: `tests/test_qa_multiagent_safety_contract.py`
- Test: `tests/test_qa_harness_observability.py`

**Interfaces:**
- Consumes: Task 1 的 ledger、Task 3 的共享缓存、Task 4 的 coverage 状态。
- Produces: `SpecialistAction`，动作类型为 `tool`、`ledger_update`、`memo`。
- Produces: `run_specialist_loop(...) -> SpecialistMemo`，每个角色 2 至 4 个模型决策轮次。

- [ ] **Step 1: 写子 Agent 自适应循环失败测试**

事件 Agent 首轮搜索后由模型选择具体 `geo_evidence_get`；反证 Agent 使用已有主张生成补查；市场 Agent选择问题匹配的多个 series；非法工具动作被白名单拒绝。

- [ ] **Step 2: 写并行降级与恢复失败测试**

一个子 Agent 超时，其他 Agent继续；已完成工具调用在恢复时不重复；相同 Agent trace 显示搜索、全文、补查、memo 序列。

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_qa_multiagent.py tests/test_qa_multiagent_safety_contract.py -q`  
Expected: FAIL，因为当前仍是固定调用批次。

- [ ] **Step 4: 实现 SpecialistAction 解析和角色循环**

子 Agent每轮只输出一个动作；工具动作必须在角色白名单内；`ledger_update` 必须只引用该 Agent 已取得的记录；`memo` 前执行角色局部门禁。

- [ ] **Step 5: 接入共享主张和后续查询**

事件和市场主张写入 ledger 后，反证 Agent可以读取；`follow_up_queries` 在剩余预算内真正执行，不再只写入 memo。

- [ ] **Step 6: 运行 Task 5 回归**

Run: `python -m pytest tests/test_qa_multiagent.py tests/test_qa_multiagent_safety_contract.py tests/test_qa_harness_observability.py -q`  
Expected: PASS。

- [ ] **Step 7: 提交 Task 5**

```bash
git add backend/qa_multiagent.py backend/qa_harness.py backend/qa_harness_state.py tests/test_qa_multiagent.py tests/test_qa_multiagent_safety_contract.py tests/test_qa_harness_observability.py
git commit -m "feat: run bounded adaptive QA specialists"
```

### Task 6: 问题驱动指标、程序化数字块与逐主张引用

**Files:**
- Create: `backend/qa_metrics.py`
- Modify: `backend/qa_agent.py`
- Modify: `backend/qa_finalizer.py`
- Modify: `backend/research.py`
- Modify: `backend/answer_report.py`
- Test: `tests/test_qa_metrics.py`
- Test: `tests/test_qa_finalizer.py`
- Test: `tests/test_answer_report.py`

**Interfaces:**
- Consumes: Task 1 ledger 和已读取 `geo_metric_series` 结果。
- Produces: `select_metric_series(question, topic, catalog, limit=6)`。
- Produces: `build_metric_block(series_results) -> dict[str, Any]`，含期初、期末、峰谷、观测数、频率、滞后和边界。
- Produces: `validate_claim_citations(answer, ledger) -> ValidationResult`。

- [ ] **Step 1: 写指标选择和路径统计失败测试**

“俄罗斯财政压力”必须优先匹配卢布、Brent、财政和利率；“援乌拨款”不得只读天然气；所有输出值、单位和日期与工具结果一致。

- [ ] **Step 2: 写逐主张引用和局部数字修复失败测试**

断言一个引用不能笼统覆盖全文；无效数字只移除对应单元或句子；Markdown 表格仍完整；引用异常时最终答案仍含结论、依据、限制和观察点。

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_qa_metrics.py tests/test_qa_finalizer.py tests/test_answer_report.py -q`  
Expected: FAIL。

- [ ] **Step 4: 实现问题驱动指标与数字块**

指标别名和主题基础篮子放在 `qa_metrics.py`；程序计算采用原始工具值，不由模型换算单位；最终 Markdown 表由程序渲染。

- [ ] **Step 5: 实现逐主张引用验证和局部修复**

引用只接受 ledger 中的公开 evidence IDs；内部消息单列；失败信息作为修复 observation 回灌一次，仍失败时执行局部降级。

- [ ] **Step 6: 运行 Task 6 回归**

Run: `python -m pytest tests/test_qa_metrics.py tests/test_qa_finalizer.py tests/test_answer_report.py tests/test_answer_projection.py -q`  
Expected: PASS。

- [ ] **Step 7: 提交 Task 6**

```bash
git add backend/qa_metrics.py backend/qa_agent.py backend/qa_finalizer.py backend/research.py backend/answer_report.py tests/test_qa_metrics.py tests/test_qa_finalizer.py tests/test_answer_report.py tests/test_answer_projection.py
git commit -m "feat: validate QA claims and render metric blocks"
```

### Task 7: 华泰内部消息去污染与最终降级保证

**Files:**
- Modify: `backend/qa_agent.py`
- Modify: `backend/internal_messages.py`
- Modify: `backend/qa_harness.py`
- Modify: `backend/qa_finalizer.py`
- Test: `tests/test_internal_messages.py`
- Test: `tests/test_internal_message_integration.py`
- Test: `tests/test_qa_harness.py`

**Interfaces:**
- Consumes: Task 2 的 topic lane、Task 4 的门禁、Task 6 的 finalizer。
- Produces: 内部消息检索结果的 `core`、`cross_topic`、`rejected` 分类。
- Produces: 所有失败路径共享的 `deterministic_fallback(...)` 完整四段输出。

- [ ] **Step 1: 写内部消息污染失败测试**

删除 `topic_bridge` 后，真正包含伊朗/霍尔木兹/胡塞的消息仍能命中；只含“中东+原油”的消息只能成为市场补充；公开 citation map 不含内部消息 ID。

- [ ] **Step 2: 写模型、工具和引用连续失败测试**

断言任意失败组合下最终回答非空，不是“见上”，并包含结论、依据、限制和观察点；错误信息可定位到 Agent、MCP 和工具。

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_internal_messages.py tests/test_internal_message_integration.py tests/test_qa_harness.py -q`  
Expected: 至少内部消息污染测试 FAIL。

- [ ] **Step 4: 改造内部消息排序和降级路径**

只使用消息真实文本和数据库已有标签；长查询拆成实体、事件和资产词后合并重排；失败路径使用 ledger、指标块和已读证据生成自包含回答。

- [ ] **Step 5: 运行 Task 7 回归**

Run: `python -m pytest tests/test_internal_messages.py tests/test_internal_message_integration.py tests/test_qa_harness.py tests/test_qa_finalizer.py -q`  
Expected: PASS。

- [ ] **Step 6: 提交 Task 7**

```bash
git add backend/qa_agent.py backend/internal_messages.py backend/qa_harness.py backend/qa_finalizer.py tests/test_internal_messages.py tests/test_internal_message_integration.py tests/test_qa_harness.py
git commit -m "fix: isolate internal messages in QA research"
```

### Task 8: 前端研究过程与覆盖状态

**Files:**
- Modify: `src/QaTrace.tsx`
- Modify: `src/qa_ui.ts`
- Modify: `src/types.ts`
- Modify: `src/style.css`
- Test: `tests/qa_trace_detail.test.cjs`
- Test: `tests/qa_ui.test.cjs`

**Interfaces:**
- Consumes: Task 3 的检索回执、Task 4 的 readiness trace、Task 5 的 Agent trace。
- Produces: 分阶段 Agent 卡片、覆盖状态矩阵、缓存/重试/错误说明和证据详情入口。

- [ ] **Step 1: 写前端显示失败测试**

断言摘要对象不显示“0 条结果”；重复检索显示“命中 N 条，本轮新增 0 条”；长 MCP 名称和调用 ID正常换行；无参数显示“无需参数”；证据按钮可打开对应记录。

- [ ] **Step 2: 写覆盖状态与 Agent 序列失败测试**

断言界面显示每个 facet 的状态、Agent任务、工具序列、耗时、缓存复用、降级原因，而不显示隐藏思维链。

- [ ] **Step 3: 运行测试确认失败**

Run: `node --test tests/qa_trace_detail.test.cjs tests/qa_ui.test.cjs`  
Expected: FAIL。

- [ ] **Step 4: 实现研究过程 UI**

使用现有深色设计系统；桌面和窄屏都采用键值网格和可折叠卡片，不使用会把单词逐字符挤窄的列布局。

- [ ] **Step 5: 运行 Task 8 回归和构建**

Run: `node --test tests/qa_trace_detail.test.cjs tests/qa_ui.test.cjs`  
Run: `npm run build`  
Expected: PASS。

- [ ] **Step 6: 提交 Task 8**

```bash
git add src/QaTrace.tsx src/qa_ui.ts src/types.ts src/style.css tests/qa_trace_detail.test.cjs tests/qa_ui.test.cjs
git commit -m "feat: show auditable QA research coverage"
```

### Task 9: 质量评测、真实问题回归与兼容验收

**Files:**
- Modify: `config/qa-benchmark.json`
- Modify: `scripts/evaluate_qa.py`
- Create: `tests/fixtures/qa_gold.json`
- Modify: `tests/test_qa_agent.py`
- Modify: `tests/test_qa_harness_compatibility.py`
- Modify: `tests/test_backend.py`
- Create: `docs/qa/agentic-harness-v2-validation.md`

**Interfaces:**
- Consumes: Tasks 1—8 的最终问答、trace、ledger、引用和指标块。
- Produces: 100 分质量评分、硬性失败项、当前本地基线和优化后结果对比。

- [ ] **Step 1: 扩充固定问题与金标**

加入设计文档第 13.2 节的问题，保存固定快照、必需分面、金标事件、允许指标、不可接受污染和关键事实边界。

- [ ] **Step 2: 写评分器失败测试**

评分至少包含最新事件召回率、最近三日证据占比、逐主张引用支持率、来源分层准确率、指标一致率、主题污染率、反证覆盖率、Markdown 完整性和无效工具率。

- [ ] **Step 3: 实现评分器并运行单元测试**

Run: `python -m pytest tests/test_qa_agent.py tests/test_qa_harness_compatibility.py tests/test_backend.py -q`  
Expected: PASS，PiChat 路径仍为单次既有调用，9 工具合同不变。

- [ ] **Step 4: 运行完整后端、前端与构建回归**

Run: `python -m pytest tests -q`  
Run: `node --test tests/*.test.cjs`  
Run: `npm run build`  
Expected: 全部 PASS。

- [ ] **Step 5: 使用真实快照运行固定问题集**

Run: `python scripts/evaluate_qa.py --config config/qa-benchmark.json --label agentic-v2`  
Expected: 所有问题均生成非空、自包含中文答案；硬性失败项为 0；结果写入 `data/qa-benchmarks/`。

- [ ] **Step 6: 浏览器视觉与交互验收**

启动服务后测试美伊、俄乌、霍尔木兹、资产和内部消息问题；检查过程卡片、覆盖状态、证据按钮、最终回答、HTML 导出、桌面和窄屏布局。

- [ ] **Step 7: 写验收报告**

在 `docs/qa/agentic-harness-v2-validation.md` 记录版本、快照、问题、分数、延迟、工具调用、已知缺口和 PiChat 对比结论，不把一次运行结果写成普遍结论。

- [ ] **Step 8: 提交 Task 9**

```bash
git add config/qa-benchmark.json scripts/evaluate_qa.py tests/fixtures/qa_gold.json tests/test_qa_agent.py tests/test_qa_harness_compatibility.py tests/test_backend.py docs/qa/agentic-harness-v2-validation.md
git commit -m "test: benchmark agentic QA research quality"
```

## Self-Review Result

- Spec coverage：设计文档第 1—15 节均已映射到 Task 1—9；PiChat 和 9 工具兼容由 Task 9 固化。
- Step scan：每个任务均包含失败测试、最小实现、回归和提交边界；没有把代码正文复制进计划。
- Type consistency：ledger、query、cache、readiness、specialist action、metrics 和 validation 接口在首次定义后保持一致。
- Review focus：重复查询、陈旧数据、子 Agent失败、局部引用/数字异常、主题污染均有明确测试任务。
- Proportion：计划按九个可独立审查的交付物拆分；核心 Harness 依赖顺序为 Task 1 → 2/3 → 4 → 5 → 6/7 → 8/9。
