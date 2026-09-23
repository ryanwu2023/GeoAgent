# opinion-monitor — 美伊战争·美国民意监测

监测美国国内对伊朗战争的民意态势：**民调数字 / 意见领袖动向 / 国会战争权力动向 / 街头行动 / 中期选举压力** 五个信号桶。

## 运行

```
run.bat                 # 自检 -> 抓取 -> 对账 三步，任一失败即中断
python opinion_monitor.py --selftest     # 离线自检（50 项）
python tools\verify_report.py            # 对账（独立复算报告数字，24 项）
python opinion_monitor.py --render-only  # 仅重渲染（免重抓）
python opinion_monitor.py --as-of 2026-09-20 --out-dir output/_xday-verify   # 跨天验证
```

产出：`output/LATEST.md`（最新快照）、`output/<日期>/`（日报+records.jsonl）、
`output/ALL-records.jsonl`（累积档，永不淘汰）、`output/_filter_audit.json`（被锚定拦截候选留档）。

## 事实/观点分类（本项目的核心约定）

每条记录标 `kind`：

| kind | 含义 | 性质 |
|---|---|---|
| fact_poll | 民调数字（机构+数值+口径句） | 事实（发布值） |
| fact_vote | 表决/动议记录 | 事实（可回源） |
| fact_event | 辞职/集会/立案等 | 事实 |
| opinion_statement | 人物表态 | 『他说了X』是事实，X 是观点 |
| opinion_analysis | 分析/专栏/转述解读 | 观点 |

立场词表（antiwar/pro_war）是**启发式**，只看方向，每条附上下文可人工复核。
信源等级 T1（民调机构/议员官网一手）与 T3（媒体转述）分开排布；T4 社交平台不采集。

## 已知关键坑（新增条目见 .workbuddy 记忆）

- **月份 March ≠ 动词 march**：已加月份语境排除（in/on/by/of March 2026…），自检锁定
- **gnews summary=标题复读**：同一百分比会双抽 → 按值+方向去重
- **回填必须整条换口径**：词表收紧后掉桶的记录显式置 `kind=excluded`，绝不保留旧桶只刷 cfg_ver
- **cfg_ver 必须含主脚本代码哈希**：否则代码行为变更不触发全量回填（混龄档案）
- 参议员新闻稿 RSS 大多 404/410（Paul 官网 /feed/ 例外）；参院表决 RSS 端点已下线——
  战争权力四次表决以 T3 转写基线收录（config.warpowers_baseline），待回源 senate.gov LIS XML
