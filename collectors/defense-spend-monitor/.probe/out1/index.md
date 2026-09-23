# 监测归档索引

累计档案（跨天不淘汰，按 id 去重）：`ALL-records.jsonl` — **10 条**

> `LATEST.md` 与各日 `spending-digest-*.md` 均为**滚动窗口快照**（含窗口内全部记录，
> 非仅当日新增）；真正长期不丢的是 `ALL-records.jsonl`。

## 动态变化（跨月时间序列）

- 趋势报告（人读）：`LATEST-trend.md`（= `history/trend-<日期>.md`）
- 序列长表（机读）：`history/series.jsonl`
  —— 每行含 `metric / period / value / unit / caliber / source_id / tier`，
  可直接做透视；`kind=monitor-index` 的行是**启发式**监测指标，非官方统计。

## 每日快照

| 日期 | 快照条数 | 日报 | 证据板 |
|---|---|---|---|
| 2026-09-18 | 10 | 2026-09-18/spending-digest-2026-09-18.md | 2026-09-18/war-trajectory-2026-09-18.md |
