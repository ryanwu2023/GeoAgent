# 线上定时采集（Sealos）

## 背景

macOS / Windows 部署由系统后台任务（LaunchAgent / 任务计划）按 `interval_sec` 定时采集；
Linux 容器里只运行 Web 服务，**没有任何定时采集**。2026-11-05 核查：Sealos 历史 38 次采集全部是手动触发，
间隔从数小时到两天不等。直连 RSS 只保留最近一段（Al Jazeera 约 9 小时、BBC World 约 28 小时、多数 1–3 天），
不定期采集会永久漏掉这些文章，只能靠谷歌新闻回溯补，这是数据依赖谷歌新闻的主因。

## 机制（`backend/collector_scheduler.py`）

| 批次 | 频率（默认） | 采集器 | 导入 |
|---|---|---|---|
| hourly | 每 1 小时 | `collectors.json` 中 `"cadence": "hourly"` 的 14 个新闻类采集器 | 不导入，只写入各采集器累积档 |
| full | 每 6 小时 | 全部启用的采集器 | 导入新快照；允许部分成功导入 |

- 每个快照是完整副本（线上约 15–20 MB），所以每小时只采集不导入；新闻在累积档里不会丢，下次全量导入时一并入库。
- 部分导入：失败的采集器不会发布新产物，线上数据目录保留它上一份成功产物，导入时照常带上；其余采集器正常入库。
- 与手动采集、HFT 远端采集共用同一把服务锁：管线被占用时本轮跳过，下一分钟再试。
- 运行时间记录在 `data/service-config/collect-schedule.json`（PVC），Pod 重启不会重复触发；启动后默认等 5 分钟再开始。
- 远端采集模式（`GEO_COLLECTION_MODE=remote`，HFT 内网）不启用，由 Sealos 侧采集。
- 批次 run id 前缀为 `scheduled-full-` / `scheduled-hourly-`，在采集记录里可与 `manual-` 区分。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `GEO_COLLECT_SCHEDULE_ENABLED` | `false` | 设为 `true` 才启用 |
| `GEO_COLLECT_HOURLY_INTERVAL_SEC` | `3600` | 新闻类采集间隔（最小 600） |
| `GEO_COLLECT_FULL_INTERVAL_SEC` | `21600` | 全量采集+导入间隔（最小 3600） |
| `GEO_COLLECT_SCHEDULE_PARTIAL_IMPORT` | `true` | 全量批次部分成功时是否导入 |
| `GEO_COLLECT_SCHEDULE_START_DELAY_SEC` | `300` | 进程启动后首次检查前的等待 |

## 上线步骤

1. 按 `deploy/GITLAB-RELEASE.md` 走 GitLab 发布（快进 main、打 tag、手动 deploy job）。CI 只更新镜像。
2. 发布后设置环境变量（与 `GEO_REMOTE_COLLECT_PRINCIPALS` 一样由 `kubectl set env` 管理，后续 CI 发布会保留）：
   `kubectl set env deploy/geoagent -c geoagent GEO_COLLECT_SCHEDULE_ENABLED=true`
3. 验证：5 分钟后 `data/collection-runs/` 出现 `scheduled-full-*`，`data/service-config/collect-schedule.json` 记录时间；
   一小时后出现 `scheduled-hourly-*`。关闭：设为 `false`（或 `kubectl set env ... GEO_COLLECT_SCHEDULE_ENABLED-`）。

## 容量

全量每 6 小时导入一次 ≈ 每天 4 个快照，约 60–80 MB/天（当前库 491 MB，含 23 个快照）。快照保留/清理策略尚未实现，
需要另行规划。
