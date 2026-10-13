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
