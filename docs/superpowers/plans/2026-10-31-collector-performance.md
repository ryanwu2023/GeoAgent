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
- [ ] 当前采集自然结束后，合并 GitLab、标签发布（现有 CI），验证健康/MCP。
- [ ] 在线运行全范围批次，记录总秒数、每源耗时、失败项和导入结果，不将 partial 称为全成功。
