# iran-domestic-monitor — 美伊冲突·伊朗内政监测

抓取美伊战争背景下**伊朗国内态势**：里亚尔汇率、物价（食品/药品/工资）、
就业与青年失业、原油出口规模、燃料短缺与限电、文官政府 vs 革命卫队权力结构、
最高领袖公开露面与权威重塑/继承。

骨架承继 mideast-monitor（表态/事件类）：配置驱动源 + 主题锚定 + tier 分级 +
事实/观点分类 + 数值机械抽取（汇率 fx / 百分比 pct / 桶量 bbl）+ 累积档 + 离线自检。

## 用法

```
python iran_domestic_monitor.py --selftest      # 离线自检（70 项，不联网）
python iran_domestic_monitor.py                 # 抓取+渲染（写 output/LATEST.md）
python tools/verify_report.py                   # 独立对账（18+ 项，写 _verify_last.json）
python iran_domestic_monitor.py --render-only   # 仅从累积档重渲染
python iran_domestic_monitor.py --as-of 2026-09-20 --out-dir output/_xday-verify
                                                # 跨天落盘验证（隔离目录）
python iran_domestic_monitor.py --from-file corpus.json   # 离线退路
run.bat                                         # 抓取 + 对账一条龙
```

## 信号桶（8）

| 桶 | 主题 | 说明 |
|---|---|---|
| rial_fx | 里亚尔汇率 | 官方价 vs 自由市场价两套口径禁止混用；Omani/Qatari rial 排除 |
| prices_food | 物价与民生 | 通胀/食品/药品/工资/生计篮子 |
| unemployment | 就业与失业 | 总失业率/青年失业/毕业生失业/参与率 |
| oil_exports | 原油出口 | 二维桶（油类词×出口类词）；纯军事打击不误入 |
| fuel_power | 燃料与限电 | 汽油配给/加油排队/停电/电网 |
| irgc_power | 革命卫队权势结构 | 二维桶（IRGC 词×权势词）；纯军事新闻不误入 |
| khamenei_leader | 最高领袖 | 子信号：appearance（露面）/ succession（继承权威） |
| protest | 街头与抗议 | March 月份守卫 + 辞职抗议排除（承继 mideast） |

## kind 四分类

- `fact_data` 官方统计/数据发布（须数值在场）
- `fact_event` 完成态事件（涨价执行/停电/被捕/会见/配给）
- `opinion_statement` 表态（说了 X 是事实，X 是观点）
- `opinion_analysis` 分析解读

## 数值机械抽取

- **fx**：`2.3 million rials` / `230,600 toman` + ±60 字符内须有 dollar/USD；
  归一为 里亚尔/USD（托曼×10）+ 合理性区间兜底 [1e4, 1e10]；海湾里亚尔排除
- **pct**：百分比 + 最近主题词（inflation/unemployment/food/…）；上限 300（食品通胀 127.5% 是真信号）
- **bbl**：桶量（per day 标注）；排除 per second/per capita 等非日流量

## 信源现状（探测 2026-09-19，证据 tools/_probe_round1.json）

- **T1 直接源为零**：khamenei.ir / IRNA / president.ir / CBI / SCI（amar.org.ir）
  经当前网络出口连接失败或仅 JS 壳——全部停用留档（note 含实测状态），不做绕过
- T2：Atlantic Council IranSource（唯一可用）
- T3：Tehran Times（官媒窗口）、Al Jazeera、BBC、Guardian(伊朗频道)、NYT +
  16 条 Google News 关键词通道（T3 转述层，工作主力）
- 报告含**信号桶 × 信源等级交叉表**：本源结构下所有桶全 T3，自动警告——
  读任何数字前先意识到这是转写层

## 已知关键局限

1. 官方数字（CPI/失业率/汇率）全部经 T3 转述，转写链在报告逐条披露
2. 里亚尔官方价 vs 自由市场价、年度 vs 点对点通胀——口径纪律见报告 §0
3. 原油出口『归零』（美方）vs『未中断』（伊朗官媒）并列披露不采信
4. 『汽油库存仅剩 2 个月』为 Reuters 单一匿名信源
5. IRGC 未领饷为美方官员单方口径

## 输出

```
output/
├── LATEST.md               # 最新日报（滚动窗口快照，含全部窗口内记录）
├── ALL-records.jsonl       # 累积总档（按 id 去重、永不淘汰、带 first_seen）
├── YYYY-MM-DD/             # 按日快照：报告 + records.jsonl
├── _filter_audit.json      # 被锚定拦截的候选（可复核）
├── _fetch_stats.json       # 每源抓取状态
└── _verify_last.json       # 对账留档（报告头动态引用）
```
