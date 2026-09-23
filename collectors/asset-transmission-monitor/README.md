# asset-transmission-monitor · 冲突冲击的资产传导证据监测

> 面向「冲突如何传导到资产价格」的**证据收集**，不是行情终端，也不做因果断言。
> 五条证据链各自独立成节：供应损失 / 运费与保险（战争险）/ 欧洲天然气 / 粮食 / 地区资产。

## 1. 这个项目解决什么

冲突类新闻容易读成「油价要涨」这类结论，但**结论需要证据链支撑**。本项目把证据按五条链分开收集，
每条记录带 `kind`（事实/表态/分析）、`flags`（宣布/已执行/价格上行/价格下行）与信源等级
（T1 一手 / T2 机构估算 / T3 转述），让下游（人或大模型）自己判断链条走到了哪一步。

**不做的三件事**：① 不写「因为 A 所以 B」；② 不给点位预测；③ 不把承诺当交付、不把宣布当已发生。

## 2. 五条证据链（信号桶，全部 ≥2 维匹配）

| 桶 | 含义 | 典型证据 |
|---|---|---|
| `supply_loss` | 供应损失与产能中断 | 炼厂/管道/油田遭袭停产、出口禁令、不可抗力 |
| `freight_insurance` | 运费与保险（战争险） | 战争险附加费、租船/油轮运价、绕行与运河通行 |
| `eu_gas` | 欧洲天然气（TTF/库存/LNG） | TTF 价格、注采与库存水平、LNG 到港、对俄气依赖 |
| `grain_food` | 粮食与农产品（黑海/出口） | 黑海走廊、小麦玉米出口、粮价、化肥与收成 |
| `regional_assets` | 地区资产（汇率/股债） | 卢布/格里夫纳、地区股指、主权债收益率、资本外流 |

**二维匹配纪律**：每个桶要求「信号词族 × 主题锚定」同时命中，避免 `cut`、`fall`、`record high`
这类通用词在任意新闻里都命中（本项目桶里所有通用动词都进二维）。

## 3. flags：声明与行动必须分开

| flag | 含义 |
|---|---|
| `announced_only` | ★ 只是宣布/计划/拟议，**未执行** |
| `confirmed_action` | 已执行的动作（打击/停产/断供/恢复/装船） |
| `price_spike` | 价格或运价上行（surge/record high…） |
| `price_fall` | 价格或运价下行（ease/lowest since…） |

`announced_only` 与 `confirmed_action` 可能同时命中（先宣布后执行），**二者不可相加**，
计数时只说明「有多少条被标了某个旗标」。

## 4. 信源（25 条配置，启用 23 / 停用留档 2）

- **海运/运费/保险**：gCaptain、Splash247、The Loadstar、Hellenic Shipping News、Shipping Italy
- **能源**：OilPrice.com、Natural Gas World（当前 200 但 0 条，留待复测）
- **粮食**：World Grain（当前 200 但 0 条，留待复测）
- **地区/宏观**：Kyiv Post、Euromaidan Press、TASS(EN)、Moscow Times、Vedomosti、Interfax
- **一手/机构**：UN 新闻稿（T1）、International Crisis Group（T2）
- **中国官媒**：新华社英文首页（html_listing 解析，仅标题）、CGTN（RSS）
- **聚合检索**：Google News 5 通道（TTF 气价 / 战争险 / 黑海粮食 / 卢布 / 供应中断）

**停用留档**：`xinhua-worldrss`（末条 2018-01，假活 feed）、`gie-agsi`（欧洲储气为交互页，
无开放 RSS/JSON，需 API key，未接入）。

★ **代理纪律**：端口会漂移（2026-09-19 的 50465 已死、2026-09-20 环境代理为 7897），
代码**不写死端口** —— 运行时探测存活出口，隧道失败立即摘除并切换下一个；
403 视为出口 IP 信誉问题，换出口再试一次（**不做风控绕过**，全出口 403 即放弃并留档）。
★ **gnews 限流**：2026-09-20 08:40 前后该出口对 google 返回过 502，属临时限流，
断连期窗口覆盖不完整，出口恢复后自动回补（报告「信源健康」节会如实告警）。

## 5. 用法

```bat
run.bat                                             :: 抓取 + 渲染
python asset_transmission_monitor.py --selftest     :: 离线自检（不联网，76 项）
python asset_transmission_monitor.py --render-only  :: 仅从累积档重渲染
python asset_transmission_monitor.py --as-of 2026-09-20 --out-dir output/_xday-verify
python asset_transmission_monitor.py --from-file corpus.json   :: 离线退路
python tools/verify_report.py --save                :: 回源对账（30 项）
```

## 6. 输出

- `output/LATEST.md` —— 滚动窗口快照（收录窗内**全部**记录，不只是当天新增）
- `output/<日期>/asset-<日期>.md`、`records.jsonl`
- `output/ALL-records.jsonl` —— 跨天累积档（去重 + `first_seen`）
- `output/_fetch_stats.json` —— 本轮每个信源的成功/失败与原因
- `output/_source_health.json` —— 跨运行的连续失败计数（≥3 次在报告里告警）
- `output/_filter_audit.json` —— 被闸门拦截的候选（可复核，不静默丢弃）

报告末尾固定追加两节：**信源健康**（失败与连续失败告警）与
**事件核验**（跨源印证 / 声明与行动 / 术语对照）。
跨源印证的判定：同一事件被 ≥2 个**不同发布方**报道，或出自 T1 原始出处 → 「已印证」；
单源且非 T1 一律「待证」。

## 7. 已知偏差与误读边界

1. **只列证据，不写因果**：跨节的「因为…所以…」一律不出现（相关 ≠ 因果）。
2. **战争险费率是个案报价**（随船/航次/船旗而异），公开报道只能给区间或个案，不是市场均价。
3. **运价 ≠ 运量**：BDI/运费上涨可能来自绕行拉长航程，不代表贸易中断或运量增长。
4. **单位不可混**：$/桶、€/MWh、$/吨、指数点、bp 各自独立，不可相减、不可相加。
5. **T1 一手源在本主题普遍稀缺**（官方能源/粮食数据多为 API 需 key 或被风控），
   多数桶的交叉表会显示「全 T3」警告——这是**信源结构的事实**，不是程序缺陷。
6. **新华社首页解析只有标题**（无摘要），该源只按标题判定。
7. 数值序列（TTF/小麦/玉米/BDI/卢布/格里夫纳/地区股指）在 `finance-front-monitor` 的配置里，
   2026-09-20 因出口对 Yahoo/stooq 返回 403 而**无值**，按「无数据」显式列示，出口恢复后自动补取。

## 8. 合规

只抓公开内容；守 robots.txt；不登录、不绕风控、不抓受限数据；403 全部留档不复试绕过。
俄/乌/中国官媒来源均标注立场框架，只作窗口使用。
