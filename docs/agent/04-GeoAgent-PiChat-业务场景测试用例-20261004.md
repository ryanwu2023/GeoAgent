# GeoAgent × PiChat 业务场景测试用例（2026-10-04）

## 1. 目的与验收边界

本文件把 GeoAgent 的菜单业务数据转成可重复的 Agent 验收用例。每个用例必须同时验证三层：

1. **数据层**：GeoAgent MCP 能提供所需的结构化字段、快照口径和 provenance。
2. **路由层**：用户自然语言能被 PiChat Agent 映射到正确的业务工具，不以网页搜索或猜测代替 MCP。
3. **回答层**：答案包含事实、来源、时间口径和缺口；工具失败、无历史值、空简报不能被改写成“没有变化”或“没有风险”。

当前范围是本地只读 MCP。验证对象是 `site:geoagent` 和显式装配该站点的 `geo-research-assistant`，不推断为任意 Agent 已自动拥有能力。

## 2. 菜单到业务工具的基线用例

| ID | 菜单 | 用户问题 | 预期工具 | 答案必须包含 |
| --- | --- | --- | --- | --- |
| snapshot-change | 量化指标 | 本周哪些指标发生变化？请说明当前值、前值、变化幅度、日期和数据缺口。 | `geo_snapshot_list` → `geo_metric_catalog` → `geo_metric_series` | current、previous、delta、日期、provenance；无可比前值要明确标记 |
| counterevidence | 情景推演 | 当前判断有哪些反证？请分别区分情景规则中的反证和新闻证据中的反证。 | `geo_analysis_view` + `geo_evidence_search/get` | `counter` 与 `evidence_ids` 分层，附来源和核验边界 |
| missing-data | 数据源 | 还缺什么数据？请按指标、证据、覆盖范围和时效分别列出缺口。 | `geo_workspace_summary` + `geo_metric_catalog` + `geo_analysis_view` | coverage、metrics、missing_data、freshness；不能用空结果冒充完整覆盖 |
| evidence-verification | 证据台账 | 找出当前主题最相关的两条证据，并说明来源、发布时间、摘要和核验边界。 | `geo_evidence_search` → `geo_evidence_get` | records、record、来源、发布时间、摘要、provenance |
| risk-overview | 风险监测 | 当前快照的风险态势和证据覆盖如何？请说明各主题覆盖量，不要把报道数量当成风险概率。 | `geo_snapshot_get` + `geo_workspace_summary` + `geo_analysis_view` | coverage、assets、scenarios；明确“覆盖量不等于概率” |
| pipeline-live | 采集管线监控 | 当前采集管线是否完成？哪些采集器失败或仍在运行？请区分 live 运行态和冻结快照。 | `geo_collector_status` + `geo_snapshot_list` | run_status、collectors、`live=true`、快照时间；不能以冻结快照代替实时状态 |
| asset-impact | 资产关联 | 当前主题对权益、利率债、信用债、商品和外汇分别有什么条件影响？请附反证和待补数据。 | `geo_analysis_view` + `geo_metric_catalog` + `geo_evidence_search` | assets、counter、missing_data、证据来源和条件限定 |
| brief-archive | 研究简报 | 当前快照是否有已落盘研究简报？如果没有，请明确说明不能用空结果替代简报正文。 | `geo_brief_list` + `geo_snapshot_list` | briefs、total、快照口径；空列表必须明确“没有已落盘正文” |

机器可读版本位于 `tests/fixtures/geoagent_business_cases.json`。数据侧自动验证器为 `scripts/verify_business_cases.py`。

## 3. 当前验证结果

### 3.1 MCP 数据侧

2026-10-04 在本地 `http://127.0.0.1:8401/mcp` 执行：

```bash
GEOAGENT_MCP_VERIFY_TOKEN=geoagent-local-dev \
  .venv/bin/python scripts/verify_business_cases.py
```

结果：**8/8 passed**。验证覆盖 11 个只读工具、working snapshot、指标目录/时序、证据检索/详情、分析视图、简报列表和 live collector provenance。

### 3.2 PiChat Agent 语义侧

| 用例 | 状态 | 证据 |
| --- | --- | --- |
| snapshot-change | 已 live 通过 | 嵌入面板实际调用 `geo_snapshot_list`、`geo_metric_catalog`、`geo_metric_series`；回答区分可判定变化、无可比历史和 working/latest 时间口径 |
| counterevidence | 已 live 通过（部分取数） | Agent 实际调用 `geo_analysis_view`、`geo_evidence_search`、`geo_evidence_get`；明确区分规则层 `counter` 与新闻证据，标注 `待核验`、来源和缺口；后续正文调用触发平台无进展熔断，Agent 如实标记未取到而非编造 |
| missing-data | 已 live 通过（部分取数） | Agent 实际调用分析、指标、证据和 collector 路径，按指标/证据/覆盖/时效四维组织缺口；明确“未取到”不等于“无数据”，并识别 working/latest 落差和逐源失败 |
| 其余 5 项 | 待 live | 已有 MCP 数据侧通过；按同一验收模板继续执行，不能用直连 MCP 结果替代 Agent 语义验收 |

“待 live”不是 MCP 失败，而是尚未取得对应 PiChat Agent 的真实工具回执；不能用直接 MCP 通过替代 Agent 语义验收。

## 4. 统一通过/失败规则

- **通过**：工具选择与用例映射一致；答案引用实际返回字段；时间、快照和 live/historical 口径正确；明确数据缺口；无 secret、无本地路径、无越权数据。
- **失败**：把 `working snapshot` 当成自动等于最新；把无历史序列说成“无变化”；把 `counter` 和新闻证据混为一谈；把报道数量解释成风险概率；把空简报列表写成简报结论；把 collector 的冻结状态写成实时状态。
- **工具异常**：Agent 必须报告工具失败和已确认部分，修正参数后重试；不能填充臆测数字。平台无进展熔断时，应停止扩张查询并交付部分结果与缺口。

## 5. 后续执行顺序

1. 在已打开的 GeoAgent 嵌入面板 live 执行 `counterevidence` 和 `missing-data`，记录实际工具 trace 与回答。
2. 逐一执行其余五项；每项保存问题、工具名/参数、快照 ID、回答和失败原因。
3. 对每个主问题增加边界变体：指定历史快照、无可比序列、空证据、采集器运行中、无已落盘简报、未授权快照。
4. 只有 8 个主用例和边界变体均通过，才把 PiChat 语义验收标为完成。

## 6. 2026-10-12 生产外网验收增量

生产地址 `https://geoagentd40d.sealosgzg.site/`，嵌入生产 `geo-research-assistant`，指定快照 `57fb9e48f6a0fa1f10f8502d`。只在浏览器输入自然问题，未提供预期答案数字；以右侧实际面板为独立数值基线，不以工具调用成功代替答案正确。

| 项目 | 本轮结果 |
| --- | --- |
| 专用连接/权限 | `site:geoagent`，11 只读工具，仅 GeoAgent；外网嵌入实际调用成功 |
| risk-overview：分类数 | 初次误答各 5 条；首次修复后历史快照误归全部 unknown；复用页面分类函数并发布 v1.0.7 后，回答 227 / 68 / 139 / unknown 3545，与面板相符 |
| risk-overview：范围/覆盖 | 3979，含补充报告 22；全框架候选维度 13/13，美伊单主题 8 个；候选覆盖不是数据完整 |
| risk-overview：分析解释 | **未完全通过**：将代码修复引起的数值差异臆断为人工复核；将 coverage.count 称为维度合计。核心计数正确不能抵消这些解释错误 |
| 其他主用例 | 本轮未新增生产通过记录；前文既有 local live 不能自动等同当前生产验收 |

新增负向断言：工具描述“人工复核可以改变字段”不证明某次变化来自人工复核；需真实复核记录/版本证据，否则只能说原因未确认。`coverage[].count` 是主题去补充报告后的记录数，不是 `dimensions[].count` 之和。情景 `evidence_ids` 是有限候选样本，不可作为风险监测三类报道总数。完整过程、发布流水线及截图路径见实施账本末尾。
