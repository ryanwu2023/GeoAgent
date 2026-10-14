# PiChat GeoAgent 答复质量修复移交

请只在 PiChat 的 geo-research-assistant 范围内调查和修复以下实际 Live 答复问题，不改其他 Agent、9172 或生产。不要把 Host 业务规则硬塞进通用 Harness，不为通过测试静默切 latest、重放旧答案或扩大权限。当前联合 Live 请先协调，再复测；不要重复发送同一问题。

## 已有证据（PiChat 联调任务回报，GeoAgent未独立复查Provider轨迹）

- 会话 s-871cdebaf82c。首问 run-456241ec-ad55-4f71-a4f0-76bc76bb0594：Provider宿主基线 0b18ddf4468552d911eba06e/usiran/空维度日期；MCP参数同快照，回答92/54/135/2301与看板一致。
- 周指标 run-df10d9e0-6449-4177-b745-a8597af85c62：查询09-28～09-30，13可比、18不足；一次分页ECONNRESET重试成功。不要把桥正确、重试成功等同于最终答复正确。

## 待修问题与最小验收要求

1. 数量与清单不一致：L2/L4说霍尔木兹6项，实际只列5项。GeoAgent已只读核对该快照美伊指标，只有hormuz_capacity_tanker/tanker/total/ma7/tanker_share五个唯一series，所问区间均无观测点。清单数量须按实际唯一ID/series核对；分页未读完不推断子组总数，不把没有观测当零值或没有变化。
2. 比较日期混用：L3把HY09/25→09/28的+9bp称为与09/28→09/30同区间。现有MCP comparison_basis、interval_change.requested_start/end、first/last及provenance.relative_time_basis均已说明单期/区间差异。逐项引用实际首末观测日；不同区间只能列为不同时间的观察，不能包装为同窗反证或已证实因果。
3. 未读取就断言改善：L4未读取10/9快照指标，就断言可完整一周、可比项大增。若提出换快照建议，只说待验证；只有用户允许且读取目标快照同口径geo_metric_changes.summary和覆盖信息后，才能报告实际变化。两点可比不等于逐日完整覆盖；默认“本周”是快照导入周，不保证完整七天。
4. 采集恢复与旧快照补齐混同：采集成功、导入成功、目标范围覆盖改善是不同证据。GeoAgent已在代码中向快照provenance及采集返回增加collection_snapshot_boundary：旧归档不因修采集器自动回填；复核/翻译投影更新不是新增归档材料；确认导入snapshot_id后仍需读同范围材料。**这项改动未重启、当前Live尚未载入**，请勿当作当前Provider已经收到的新字段。待联合Live结束、协调重启后再验收。

## 建议调查方式

先检查真实Provider可见工具回执和最终答复之间的最早偏差，再选择Geo专用指令、最终清点或通用证据一致性修复；不要仅靠追加提示词就宣称修好。保留原失败答案、tool arguments、各页结果、实际日期和context revision，不泄露Key/手机号/token。区分模型未遵守已有语义与工具未提供信息。

## 复测

在8010真实嵌入面板逐项自然输入问题，核对Provider上下文、MCP参数/返回及同范围看板：数量清单一致；实际日期不混用；未读目标快照不声称改善；采集恢复不声称旧快照回填。分别记录桥接、工具与最终答案结果；只收到received不是modelConsumed。运行失败/连接故障另记，不算业务通过。保留L5/L6现场，未经协调不更换筛选、重启或新建会话。

## 2026-10-14补充：chat.test嵌入语音播放阻点

用户配置腾讯TTS后，8010原嵌入会话第4轮实际geo_workspace_summary读数227/68/139/3979与公网57fb9e48看板一致，audit geo-audit-ddbff61f62688df7d8ee。speech_generate的operation speech-66289721-290d-4df5-bc42-64f08ba98009已生成M4A479.4KB/55秒，讲稿数字一致；两次播放短暂00:00后均报“播放中断”，不是生成未配置问题。请优先对同一operation检查嵌入Bearer PCM响应、Stream-Key/Sample-Rate检查、AudioWorklet错误及播放器状态，避免重新生成掩盖失败。卡片首音频标记存在，不代表完整听到；下载UI未收到事件也不能单独确定服务端下载失败。请保留用户会话/草稿/制品，不无条件刷新或切身份。

另有语义问题：答复及讲稿把unknown待研判占比解释为自动分类覆盖不足。unknown是信号待研判，不能据该数单独断言分类覆盖缺失；数值正确不等于分析正确。该项与播放阻点分别验收。GeoAgent本轮未改PiChat代码或权限，详细证据见03账本。

## 2026-10-14补充：公网三个业务问题与恢复验收

- v1.0.15、57fb9e48/usiran/全部维度/空日期，05:58–06:07在实际公网站点嵌入自然输入三个原问句。14项指标首末日期/值与看板一致；反证规则及not_evaluated/unknown解释一致；缺口在首次失败后顺序复测补齐目录31与全主题告警7（当前主题0），明确旧/新回执及有效零值。详见03账本，不重写失败历史。
- 路由效率：指标问题用了18次MCP（14次series）、72秒，未用geo_metric_changes；请核对本实例GeoAgent专用工具装配是否包含新增业务工具及模型实际可见description，再评估路由。现有证据不能断言工具未装配，不盲目扩大权限。
- 体积/稳定性：analysis_view本轮结果126221字符，调用call_00_p4DCb8xLOhHUwlOQpgT49709；缺口首次3调用30秒超时，补试no healthy upstream。顺序目录两页1.3s/1.4s及all概览21.8s恢复。请关联相同会话s-ffe599973451的工具生命周期、网关响应与服务日志，区分队列/客户端超时/实际服务端执行时间；不要凭结果大或顺序恢复就认定根因。可按查询问题减少材料体积，但不得丢失范围、时间、缺口与证据语义。
- 重新进入：看板菜单切换后一次身份确认超时、UI重试无效，页面刷新后恢复。现场/tmp/geoagent-panel-reentry-timeout-20261014.png；请区分iframe加载与auth-ready握手，并验证重试是否确实重载未完成frame。根因尚未确认，不要求关闭TLS或放宽权限。
- 反证问题仅重新读analysis/summary，新闻正文引用虽明确标记旧回执，仍不是本轮独立正文核验。若验收目标是当前事实反证，须重新读目标材料且保留候选/已核验边界；长篇规则复述及不必要历史材料应收敛。
- 本项目本轮未更改PiChat源码、部署或凭据，未确认确定业务计算缺陷；恢复通过不等于稳定性修复。语音仍按上节独立复测。

## 移交提示词：公网映射、握手及MCP超时专项

请只排查GeoAgent专用Agent，不改其他Agent或扩大权限。外网chat.lhotsetech.com映射同一内网chat.test；实际206节点Nginx两域名同server_name，代理9292。GeoAgent修复重复事件分类、同步投影阻塞及分析重要指标携带完整历史的问题，发布后的回执见03账本。

请关联实际会话s-ffe599973451：06:05:34 runner出现geo_workspace_summary及两次geo_metric_catalog的mcp.request.idle=30秒截断，06:05:40 Streamable HTTP POST no healthy upstream。GeoAgent持久审计06:05:41仍ok、执行22207ms，audit geo-audit-8c731901808d4e8a92e5cf4323cbf16b；不能把客户端超时说成后端没执行或用户缺数据。请查客户端并发复用、连接失效后的重连、deadline排队计时、网关/Ingress状态，关联请求始末，不以增加超时作为唯一修复。

身份问题请分层验证：签发embed/identity200不代表iframe加载/finance:chat:ready或WS票据通过。查公网Host/Origin/Bearer是否在映射中保留，匹配请求来源、父站域名与会话；已有其他浏览器geoagent.lhotsetech.com父站日志反复/auth/ws-ticket401，不可混作Chrome d40d同会话的根因。本项目手动重试会重建未就绪frame，不静默改已连接会话；请对比重试前后auth/me、ws-ticket及ready事件，严禁关TLS或放宽嵌入来源来掩盖失败。

路由请核对模型实际装配新增geo_metric_changes、geo_business_catalog等工具及描述刷新。17工具是GeoAgent原始目录，不等于PiChat模型全部可见；14次series有正确结果但效率低。修复后通过公网嵌入原问句、3个并发真实MCP请求及菜单重进验证，保留失败历史。日志位于206的/home/pichat/.pm2/logs/pi-chat-runner-error.log、pi-chat-server-out-0.log及/var/log/nginx/chat.access.log；不得将Token、票据或个人资料复制进移交。

补充06:27的v1.0.16实际回归：4个并行工具成功，分析结果已由126221字符缩为23480字符，后端概览仍11.6/13.5秒，GeoAgent继续修复lineage全快照解码。不要将来源体缩小当PiChat所有压缩/引用已通过。答复“usiran（含并列all）与宿主一致、无覆盖差异”措辞不严谨：all是用户明确要求的另一个比较范围，不等于宿主usiran。请每个结果标自己的scope；not_evaluated时应写“未完成反证核验”，避免“本轮无任何counter被核验为已发生”被误读为做完了核验。

### v1.0.17回归补充与本次移交范围

GeoAgent已在main发布751b0419、geoagent-prod-v1.0.17，流水线5363/部署28107通过。06:36同一公网嵌入会话4项并行均成功：概览7.9/8.4秒、目录5.1秒、情景8.1秒，全轮24秒；情景call_03_ET_292aA08JM86zDpgQLdN36272真实结果23480字符/37312字节且complete/succeeded。两次业务菜单重进正常、原会话保留，但未再次触发故障，不能据此宣布身份异常根因消失。GeoAgent未改PiChat源码或服务配置。

请在本次授权的已发布PiChat实例排查并给出修复方案（本文件开头旧联调“不要改生产”的限制属于当时场景，本次先调查、部署变更仍与用户协调）：

1. 关联同一调用者的identity签发、frame载入、ready/ack、auth/me、ws-ticket与WS建立；两个PiChat域名映射同实例，不将其他父域/浏览器401挪用为本会话根因。验证失败后重连与重试确实恢复，不清空对话、不静默切身份。
2. 按请求始末定位30秒deadline包含的排队、HTTP连接复用和上游执行时间；联合Sealos故障期Ingress/Pod日志解释no healthy upstream。GeoAgent审计不含工具前队列/认证失败，不能直接与客户端墙钟视作同一指标。不能只提高deadline。
3. 区分单次返回与累计历史上下文：本次UI显示429440缓存输入+402939非缓存输入词元，虽然情景返回已缩小，仍是约83万输入上下文。查历史工具材料是否重复展开、落盘后是否仍反复入模、压缩/引用是否真的作用于Provider。保留范围、数字、日期与证据边界，不靠删除用户会话或重放旧结果“优化”。
4. 自然问题路由核对geo_metric_changes等实际模型可见工具，改善14次series读数的效率；最终输出不要用“无覆盖差异”模糊usiran与all，也不要把not_evaluated/unknown转写成完成了反证核验。语音播放问题仍按原operation另验，未在本轮修复。

验收必须在GeoAgent公网嵌入面板自然输入三个业务原问句，逐项对照同范围看板和最新工具回执，再测试冷启动、菜单重进异常恢复与4项并发。记录实际Provider输入大小、端到端耗时、失败/重试状态及前后session/context revision，保留初次失败；只写“配置正常”或HTTP200不算闭环。

### 2026-10-14 v1.0.18发布后联合核查补充

GeoAgent 36ce052/v1.0.18已rollout，main5372与生产验证28142通过；部署28145后置Sealos initialize探针失败、该CI仍红，未把恢复改成初次通过。用户要求复用现有Key后，在206只读使用site:geoagent已有凭据，两个公网域名匿名401、认证initialize/list/geo_snapshot_list200，17工具可见；现有连接endpoint就是https://geoagentd40d.sealosgzg.site/mcp，凭据未改。

09:47–09:49 Chrome父站d40d嵌入出现“PiChat未确认身份”“智能体加载尚未完成”；显式重试先失败，随后恢复。请关联这次frame/会话的ready与ack，不用多调用者的identity200或票据200代替握手成功。09:52随后实际新geo_workspace_summary约2.6秒、全轮8秒，read_at=2026-10-14T01:52:54Z，e6f55aa20d3fdc1d0f1a1dae/usiran/空维度日期，231/60/126/3897与看板一致；恢复回执不抹掉身份初次失败。当前未读取该新会话session ID，不沿用旧s-ffe599973451标识这次回归。不要扩大权限或重新签Key，先查实际关联轨迹。

### 后置发布探针与身份问题的责任边界

GeoAgent已通过MR !9修订发布探针的初始化生命周期和首错脱敏日志，main d906125，MR流水线5379及main5380通过。下一轮生产验证沿用用户确认的一次性release-live Key，不改PiChat长期Key。旧28145日志丢失实际HTTP状态，暂不能确定网络、认证或上游根因；206节点使用现有Key、Python urllib复刻旧探针两个域名均通过，也不能代替GitLab Runner的失败时点证据。MCP发布探针由本项目继续验收，请PiChat维护Agent重点处理09:47–09:49身份未确认的同frame关联，而非重签凭据或用09:52恢复成功关闭问题。
