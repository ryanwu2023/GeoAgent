# Collector Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 避免重复采集与缓存丢失，保留数据范围，验证并安全上线。
**Architecture:** 持久请求缓存和历史缓存、批次内续跑、来源级并发与限速；现有产物原子发布不变。
**Tech Stack:** Python urllib/threading、PostgreSQL 测试隔离、GitLab CI、Sealos。
**Spec:** docs/superpowers/specs/2026-10-31-collector-performance.md

## Global Constraints
不减少来源、候选或时间窗口；不调整导入审批；不中断正在运行的批次；不关闭 TLS/robots/限速。

## Review Focus
- 跨批缓存陈旧/同批失败缓存；请求体变化不能混用。
- 进程超时后重启必须复用已完成响应，失败必须重请求。
- staged 失败不覆盖生产输出，旧记录应保留但不冒充新增。
- 同一域名多来源并发仍共享限速，排序确定。
- 发布检查运行状态失败时必须阻止变更。

### Task 1: 缓存与重试
**Files:** scripts/collector_cache.py, scripts/collect.py, collectors/defense-spend-monitor/spend_monitor.py, tests/test_collector_performance.py
**Interfaces:** ResponseCache(directory, run_id).get(key)/put(key, response), run identity and previous output environment.
- [x] 写失败用例：同批进程重启重复请求、错误不缓存、请求体隔离、历史缓存迁移、近期月份刷新。
- [x] 运行失败测试，确认真实缺陷。
- [x] 实现原子缓存、军费路径兼容/历史缓存恢复、同批 HTTP 成功响应复用；先保留完整原始请求语义。
- [x] 定向测试通过，记录证据并提交。

### Task 2: 来源并发与进度
**Files:** collectors/geopolitical-source-monitor/web.py, geopolitical_source_monitor.py, config.json, tests/test_collector_performance.py
**Interfaces:** PolicyHttpClient persistent cache + per-host lock; run_collection stable source results.
- [x] 写失败用例：跨实例条件缓存、同批续跑、并发/串行结果等价、来源执行重叠、同域串行、完整候选保留。
- [x] 实现来源并发 3、主机锁、逐源日志和历史结果加载；记录旧产物来源，不伪造成功。
- [x] 全量回归、构建、可重复 fixture 性能对比与独立审查。

### Task 3: 安全发布与真实计时
**Files:** scripts/ci/assert_collectors_idle.py, scripts/ci/sealos-release.sh, tests/test_collector_performance.py, docs validation evidence
**Interfaces:** production idle check nonzero on busy/unknown; old Pod holds shared collector lock during rollout.
- [x] 测试 running、lock、查询失败阻止发布，idle 放行。
- [x] 当前采集自然结束后，合并 GitLab、标签发布（现有 CI），验证健康/MCP。
- [x] 在线运行全范围批次，记录总秒数、每源耗时、失败项和导入结果，不将 partial 称为全成功。

### Task 4: 实测追加的两个串行瓶颈
**依据：** v1.0.30 全量验证超过旧批次 2477 秒；以色列、中东仍在执行。源码确认分别 26/40 个来源逐个抓取，超时重试没有复用已成功 HTTP 响应。
**Files:** scripts/collector_http_runtime.py, collectors/israel-monitor/israel_monitor.py, collectors/mideast-monitor/mideast_monitor.py, tests/test_legacy_collector_performance.py
**Design:** 复用已有 ResponseCache，只在同一 run_id 复用成功响应；每个采集器来源并发 3，同主机互斥保持原限速/重试，代理池加锁、opener 按线程隔离；来源结果仍按配置顺序归并。托管输出使用 staging 并接续上轮输出；保留全部来源、候选、窗口及失败语义，不改变代理候选和超时配置。
- [x] RED：同批重试缓存/新批刷新/错误不缓存、来源数与顺序不变、跨主机重叠/同主机串行、历史输出保留。
- [x] 实现并定向与全量回归；审查并修复空/无效 RSS 响应缓存。
- [x] 等 v1.0.30 验证自然结束后发布v1.0.31；全范围2086.36秒，26成功2缺key，未中断。

### Task 5: 补齐相同 RSS 采集器家族
**依据：** v1.0.31 已发布并在全量验证；未改的资产传导/欧洲内政接近900秒。源码比对确认另9个采集器复用了同型 live_proxies、http_get、fetch_source 与顺序来源循环。统一接入已测共享运行器，避免遗漏同根问题。
**Scope:** asset-transmission, diplomacy, eu-domestic, iran-domestic, opinion, ru-diplomacy, russia-domestic, ua-front, ukraine-aid。不改原请求/TLS/代理候选/业务解析筛选/窗口/来源；只包裹检查点、主机锁、代理池锁、线程本地 opener、来源并发3、托管输出接续。
- [x] 将现有参数化契约扩展到全部11个同族采集器，先验证新增成员失败（27 failed，40 passed）。
- [x] 逐个检查网络实现差异，再接入共享运行器；全部参数化测试和全量回归通过，独立审查无阻断项。
- [ ] v1.0.31 当前批次自然结束后才发布最终补齐版本，完整计时并核对来源与产物。

### Task 6: 重试诊断的批次归属
**依据：** 托管输出接续复制了旧批次根目录_fetch_stats/_source_status；若本批超时尚未写回执，_source_diagnostics会将旧成功回执归档到本批。历史记录应保留，单批回执不得沿用。
- [x] 真实提取函数RED用例复现旧成功回执误归属；prepare_output仅跳过这两份旧根目录文件，保留记录、原文、累计健康及本批已写文件。
- [x] 定向82项通过，独立审查68项通过无阻断。
- [ ] 全量回归与CI通过，当前v33批次自然结束后发布v34；v33性能实测保持原版本标识，v34不改网络/缓存/并发逻辑。
