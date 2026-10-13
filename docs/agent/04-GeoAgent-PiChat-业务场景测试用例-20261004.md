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

## 7. 2026-10-13 外网逐项验收（未完成）

仍使用相同生产嵌入面板、主题与工作快照。问题不带预期数字；先读业务菜单作为独立基线，再输入问题、查实际回执与输出。

| 用例 | 本轮实际结果 | 待验收项 |
| --- | --- | --- |
| risk-overview + 无证据归因 | v1.0.8 核心计数/覆盖匹配；实际复核 0，回答改为具体原因未证实 | 仍误推版本字段出现与数值变化同时发生，v1.0.9 边界修复后需复测 |
| snapshot-change | 四项数值/前值/变化/日期与面板一致 | 本周错误按今天计算；v1.0.9 返回所选快照周 10-05→10-09，需复测 |
| counterevidence | 规则表与面板一致；检索并读取四条新闻，正确列出待核验和来源 | 正文“坐实”超出证据；新增 not_evaluated 边界后需复测 |
| missing-data | 四类缺口有实际取数、覆盖数字正确 | 零值≠缺失，隔离≠无原始数据，topic=null≠未绑定，排除补充报告≠缺失；中段截断需精简字段修复后复测 |
| brief-archive | **核心业务通过**：面板无档案，工具指定快照及全部可见两次 total=0；不编造正文，不把即时回答当存档 | 无正文分支通过，有正文分支未测；取数时间只应引用服务端 read_at |
| evidence-verification | 反证用例读过四条；面板独立核对了普京报道 c3cf269b55b27ed7e2da1032（新华网，10-09 12:40+08，待核验） | 两条证据的独立用例未完成，不并入反证用例假定通过 |
| pipeline-live / asset-impact | 已读取资产矩阵基线，未新增独立问答通过 | Mac 锁屏后暂停 Chrome 操作，待解锁继续 |

追加断言：本周默认锚定所选快照导入日期；单期变化不得冒充整周变化。表态/预测/未披露细节不证明条件已发生。字段首次出现在对话中不是实际部署时间。有效 0 值不自动变成缺失。模型自写时点不能代替回执 read_at；缓存不能冒充重新取数。详见实施账本本日各轮记录。

### 7.1 解锁后真实嵌入问答增量

| 轮次 / 用例 | 已确认 | 未通过或待复测 |
| --- | --- | --- |
| 10 snapshot-change | 四次序列调用；快照周 10-05→10-09、最近一期数值与面板一致、read_at 与回执相符 | 周内首值与完整图表逐点交叉核对未做，不扩为全部指标通过 |
| 11 counterevidence | 三情景规则/失效条件与面板一致，表态不再称坐实 | not_evaluated 误作否定依据；抽样泛化整个快照、旧正文未标旧回执；追加结构化边界 |
| 12 missing-data | 零值/隔离/topic=null/supplemental 纠正，五类资产缺口完整 | 全主题 warnings 与当前美伊范围混合；目录未出现误推无法取原始数据；追加范围与边界 |
| 13 pipeline-live | 一次 live 工具，批次/4 采集器/导入快照匹配；批次成功≠所有源成功 | 有限 events 推断此后无新事件，账本读取≠心跳；追加 interpretation_basis |
| 14 asset-impact | 三次分析 + 一次摘要，方向/条件/反证/缺口及禁止冲突因果归因正确 | 自加日期/维度导致 credit 无线索与面板有近期候选不符；加 query_scope/asset_overview 及同筛选要求 |
| 15 evidence-verification | 两次正文 get，ID/来源/发布时间/链接/待核验匹配，不证明停火或持续撤军 | MCP 漏采集时间（面板有），已补 fetched_at 等字段；需发布复测 |

新负向断言：未评估不是否定判定；单方通报不是独立行动核验；有限检索不证明整个快照没有；不同战区和筛选结果不可混用；证据发布时间/采集时间/事件时间/取数时间四者分开。生产仍不标全套通过，逐轮问题与截图见 03 实施账本。

### 7.2 v1.0.13 外网嵌入复测

| 轮次 / 用例 | 核对结果 | 剩余问题 |
| --- | --- | --- |
| 16 risk-attribution | 核心计数一致，原因未证实，版本标签不是部署时间 | 附带沿用管线无后续动作，不能标整轮全通过 |
| 17 evidence-verification | 重新 get 两条，采集时间等字段匹配面板 | 单方报道称事实、有限材料泛化整个快照无数据；补证据工具实际路径的边界 |
| 18 asset-impact | 同筛选五资产方向、条件、缺口、近期候选状态均匹配 | 差异错误归因 limit；limit 不参与匹配计算，补返回口径 |
| 20 pipeline-live | 最新可见批次、四采集器、导入匹配，心跳未知 | 开头仍断言此后无更新；补 activity_assessment |
| 21 counterevidence | 规则/候选/旧正文层次正确 | 未评估写成没有，补 available=unknown；待发布复测 |
| 22 missing-data | 当前主题0与全主题7告警分开，零/隔离/目录未列出边界正确，覆盖与时效匹配 | 核心通过；正文旧回执不能计为本轮新读 |

轮次19输入乱码后立即停止，不计业务验收。新增负向断言：即使回答末尾有免责声明，正文/标题的确定性错误仍判失败；不能用 limit、快照年龄或末条日志时间编造差异原因或无更新事实。

### 7.3 v1.0.14 外网嵌入复测（仍未全部通过）

发布 main `78a90c1a`，pipeline 5294，verify 27746 / deploy 27747 通过。协议探针成功不是问答验收通过。

| 轮次 / 用例 | 核对结果 | 剩余问题 |
| --- | --- | --- |
| 23 counterevidence | 新读取明确未评估/未知，规则与候选分层正确 | 附带错误解释 limit；不能标整轮通过 |
| 24 pipeline-live | 批次、四采集器、来源失败与导入匹配；进程/后续事件未知，不再以末条时间推无更新 | 主用例核心通过，不证明采集进程实时健康 |
| 25 evidence-verification | 两条正文重新读取，发布/采集/核验/链接字段匹配，不泛化全快照 | 单方返港通报仍有写成独立事实的措辞风险 |
| 26 asset-attribution | 同筛选 limit=1/5 实读后撤回 limit 因果归因，具体原因未知 | 提醒后的纠错通过，不等于自然问题稳定通过 |
| 27 counterevidence 原始问题 | 两次新读取；正文正确引用未评估/未知，情景规则与资产反证匹配 | 标题“尚无已核验反证”仍可能误导为已核查的否定判断；稳定性未通过 |

管理台未见专用 Agent 系统提示词编辑入口，未修改描述、模型或权限来替代指令修复。以上沿用既有长会话，不是新会话独立验收。周内首值逐点图表核对、有正文简报分支及剩余边界矩阵仍待测；截图和逐轮证据见 03 实施账本。
