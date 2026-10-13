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
