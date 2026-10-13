# MCP业务语义重构：映射与验收

日期：2026-10-13。实施中，不是全部菜单验收报告。固定工作快照：`0b18ddf4468552d911eba06e`，美伊局势，全部维度，无日期过滤。

## 菜单字段映射

| 菜单 / 字段 | 业务依据 | Tool / 字段 | 验收及剩余边界 |
|---|---|---|---|
| 风险监测：升级/持续/缓和、记录数 | filtered_records、event_metadata、reviews最新版本 | workspace_summary.signal_counts/records_count/review_evidence | 本地同范围92/54/135、2301一致；全框架覆盖与主题筛选分开 |
| 风险监测：地图与事件类型 | semantic.map_projection、event_metadata、reviews | risk_map.categories/signal/region；classified_records_count/mapped_records_count/regions | 新增图层、信号、地点名称匹配及地区聚合；美伊2279/1831一致。不读取地图视口，不把地区报道数当部署或事实数量 |
| 证据台账：搜索、分类、复核 | semantic.search_evidence/evidence_semantics/apply_review、evidence.link_health | evidence_search、evidence_get.record/link_health | 原文/已有译文/来源子串匹配、人工覆盖同源；链接状态读取与面板相同的检查档案，不冒充本次实时请求或内容核实 |
| 情景推演：条件、失效条件、候选材料 | research.analysis/assessment | analysis_view.scenarios.evidence_condition_links | 明确candidate_only/not_evaluated；未建立支持/反向证据的自动核验判定，不声称反证不存在 |
| 数据源：接入、来源状态、缺时间、候选覆盖 | snapshot.projects/sources、research.coverage | source_health.projects/sources/coverage | 全快照分页，冻结状态非实时；来源失败与缺维度因果关系未建立 |
| 采集管线：批次状态及事件 | collector_monitor.load_runs/load_ledger_events、semantic.collector_comparison | collector_runs → collector_status(compare_run_id) | 指定历史批次不再受最近20条限制；结构化比较状态、导入、失败数及采集器差异；未知不补零，不代表进程心跳 |
| 智能问答：问题到业务能力 | catalog.TOPICS、工具契约 | business_catalog.questions/topics | 17工具，参数有业务description，均有只读annotations；PiChat上下文由另一Agent实施，尚未联合验收 |
| 量化指标：含义/阅读/作用/边界 | config/metric-guides.json | metric_series.metric.semantics；前端MetricGuide同源 | 规则不是因果证据；原值与亿美元换算分开 |
| 量化指标：本周变化 | research.metrics、semantic.interval_change、snapshot_provenance | metric_changes.metrics[].interval_change | 默认导入周；首末有效观测、零值、隔离、缺观测、基点分开；显式单边日期不自动套周 |
| 资产关联：五类资产、传导、反证、缺口 | research.analysis | analysis_view.assets；asset_id按需详情 | 保留原有条件研判，不将条件当事实；scenario_id可筛情景 |
| 研究简报：档案/版本差异 | briefs归档、对应snapshot权限、chinese.compare_brief_markdown | brief_list/get/compare | 看板与MCP共享中文展示正文；offset_chars/next_offset_chars支持有界续读，原始档案不修改；真实双简报113358字节分4页全文一致；结论变化仍需Agent阅读 |

## 兼容与范围

- 原11工具保留，新增6工具（含geo_risk_map）。既有对外Bearer校验不变。
- workspace_summary增加compact=true概览模式；默认false保留旧版资产/情景字段，不静默删减。返回projection/result_limits说明实际模式。
- MCP工作台选择工具时，按输入Schema预填当前看板快照/主题/维度/日期，仍允许手动覆盖。这只是工作台上下文，不等于嵌入Agent上下文。
- 初次审查PiChat旧版本时未发现GeoAgent上下文消费；随后PiChat侧新增host-view-context.md契约，用户授权双方5585联调。GeoAgent已实现独立ready/set/result桥、范围版本和回执，见07；5585运行版本及独立凭证仍待准备，联合模型消费未验收。applicationContext仍为准入用途，不充当业务范围。未修改PiChat仓库。

## 证据与验收层级

- 最新自动回归：216 passed / 29.47s。覆盖地图去重/复核/空图层/非法筛选、历史25批次定位与比较、链接档案投影、两份隔离简报差异及UTF-8分页拼接、REST/MCP展示正文一致、审计ID唯一、基点精度。真实双档案本地UI验证通过，见下；中间SDK读取超时及复测记录保留在03账本。
- 前端：npm run build通过，指标解释配置由前后端共用；Docker前端构建已补COPY config。
- 本地UI：工作台显示17工具。地图2279分类/1831定位与面板一致；概览92/54/135及2301，与全快照4384分开。来源缺发布时间7/缺采集时间144与面板一致。区间31指标中13可比较且变化、18无足够观测；10年美债5.24%到5.26%显示2基点。情景条件、反向条件、缺口及candidate_only/not_evaluated与面板一致。历史/当前批次比较成功；非法地图参数显示业务错误并进入调用记录。运行中切换工具后结果保持“尚未执行”，不串旧返回。
- 主题/维度切换：俄乌军事战线自动预填ukraine/frontline，地图611分类/432定位一致。后续用原生日期控件真实键盘事件验证09-28至09-30：自动预填完整日期，未手改JSON执行地图返回534分类/398定位、风险3/19/7，与看板一致。清除后start/end空、恢复0b18ddf4正确。上轮fill未触发React状态是验证方式局限，不作为已确认业务bug；本轮没有为此改日期代码。桌面验证，移动端未测。
- 真实双简报：页面分别从0b18ddf4和9163e282生成并归档b530cd7437c29264a7bbdb70、3d33806bd844f547bbce381a，重启后保留。修复MCP原始正文与看板中文展示正文不一致；增加续读，四次真实工作台调用拼接与面板113358字节全文逐字一致。未修改归档原文，不以文本差异证明因果。
- 本轮补充证据：/tmp/geoagent-date-brief-live-receipts-20261013.json、/tmp/geoagent-date-sync-live-20261013.png、/tmp/geoagent-real-brief-panel-20261013.png、/tmp/geoagent-real-brief-mcp-20261013.png。
- 本地真实回执：`/tmp/geoagent-semantic-live-results-20261013.json`；截图`/tmp/geoagent-semantic-map-live-20261013.png`。原始回执是工具层证据，不是Agent最终回答。
- 尚未进行本轮独立嵌入新会话逐菜单自然问答验收；不得将以上工具调用当成R4完成。

## 后续验收问题（每菜单至少三条）

| 菜单 | 自然问题 |
|---|---|
| 风险监测 | 当前升级线索有多少？主要事件类型是什么？为何地图数量与右侧记录数不同？ |
| 证据台账 | 找外交斡旋相关报道；这条为何归为升级？是独立核实还是单方声明？ |
| 情景推演 | 当前判断有哪些反证？升级条件满足了吗？候选材料能证明缓和成立吗？ |
| 数据源 | 哪些来源失败？哪些维度缺数据？这些状态是实时的吗？ |
| 采集管线 | 最近批次完成了吗？哪几个采集器失败？较上一批发生什么变化？ |
| 智能问答 | 还缺什么数据？当前使用哪个快照和筛选？读了两条能判断整个快照没有反证吗？ |
| 量化指标 | 本周哪些指标变化？美债10年变化意味着什么？零值与无数据有什么区别？ |
| 资产关联 | 对信用债有什么影响？依据哪些市场观测？什么条件会推翻这个判断？ |
| 研究简报 | 有哪些已归档简报？两版主要差异是什么？这次回答是否已经落盘？ |

每条记录输入、看板预期值/范围、实际工具及参数、输出原文、证据ID、通过/失败及修复回归。增加历史/最新切换、主题/日期切换、无两点、隔离值、limit截断、单方声明与未知反证负例。所有尚未执行的问题状态均为待验收。
