# ukraine-aid-monitor — 俄乌冲突·乌克兰西方后援监测

监测乌克兰依赖的西方后援：援助包与资金承诺、贷款与金融援助（IMF/G7/欧盟）、武器装备援助、美国情报支持，以及中国视角（新华社/中方涉乌表态，用户指定通道）。

承继 ua-front-monitor 骨架：配置驱动源 + 主题锚定（并入各桶全部词表，兼容「Pentagon announces new package」类无国名主语）+ tier 分级 + 事实/观点三分类 + 援助动能 flags + 意向守卫 + 累积档回填（cfg_ver 含代码哈希）+ 离线自检。

## 用法

```
python ukraine_aid_monitor.py                # 抓取+渲染（run.bat 含自检前置）
python ukraine_aid_monitor.py --selftest     # 离线自检（77 项，不联网）
python ukraine_aid_monitor.py --as-of YYYY-MM-DD --out-dir output/_xday-verify  # 跨天落盘验证
python ukraine_aid_monitor.py --render-only  # 仅从累积档重渲染
python ukraine_aid_monitor.py --from-file corpus.json  # 离线退路
python tools/verify_report.py                # 对账（路径可覆盖 --date/--report/--records/--archive/--out-dir）
python tools/audit_content.py                # 确定性重放审计
python tools/probe_sources.py                # 信源探测（37 候选实测）
```

## 信号桶（全部 ≥2 维匹配）

| 桶 | 维度 |
|---|---|
| aid_packages 西方援助包与资金承诺 | 援助词汇 × 乌克兰语境 |
| loans_finance 贷款与金融援助 | 贷款/机构词汇 × 乌克兰语境 |
| weapons_aid 武器装备援助 | 武器装备 × 交付/援助动作/援助方 × 乌克兰语境（三维） |
| intel_sharing 情报支持 | 情报词汇 × 分享/暂停动作或美方主体 × 乌克兰语境（三维） |
| china_xinhua 中国视角 | 中国/新华社词汇 × 乌克兰语境 |

flags：`pledge_only` 承诺未交付 / `delivered` 已交付 / `delay_or_halt` 延误暂停 / `strings_attached` 附带条件。
立场词表 = 援助动能（扩张 vs 收缩，否定翻转，启发式）。

## 核心纪律

1. **援助口径四层严禁混加**：承诺 ≠ 拨款 ≠ 交付 ≠ 实际消耗；PDA（库存现提）与 USAI（合同制）记法不可横向比较。
2. **情报支持**：官方从不披露细节，该桶几乎全为匿名官员转述（T3），只记录方向不作事实认定。
3. **信源等级**：T1（白宫总统行动/白宫新闻/美驻北约使团/UN）→ T2（ICG）→ T3（新华社/中新社官方媒体窗口、TASS/Moscow Times 俄方窗口、Kyiv Post 等、gnews 聚合层）；T4 不采用。交叉表全 T3 桶自动警告。
4. **新华社通道**：`worldrss.xml` 实测 200/rss 但**停更于 2018-01**（假活 feed），已停用留档；改用 `xinhua-home` 英文首页列表页解析（`html_listing` 解析器，img 空锚+标题锚双写去重、URL 抽日期）；涉乌新华社报道另由 `gn-xinhua` 聚合搜索通道覆盖。

## 首轮实测（2026-09-19）

- 自检 **77/77**；对账 **29/29**；重放审计零漂移；同日重跑新增 0、回填 33（词表收紧后快照 33→25，8 条宽词假阳性被排除）；跨天隔离目录 25 条对账全过；两次独立运行 25/25 id 与分类完全一致。
- 真实语料人工核对后收紧三处宽词：`targeting`→`targeting data/support/information`（制裁 targeting X 误入 intel 桶）、裸 `financial`→`financial assistance`（financial networks 误入 loans 桶）、china 桶 d2 收紧到乌克兰语境（西亚洲评论混入）。
- gnews 9 通道受代理出口影响：出口 50465 对 google 域全断（502），断连自动留档、恢复自动回补。
- Kiel 援助追踪器（T2 权威）/CSIS/Atlantic Council 本出口连接失败，留档复测；US DoD 合同 RSS 403（Akamai）。

## 产出

- `output/LATEST.md` 日报（人读）+ `output/ALL-records.jsonl` 累积总档（结构化、去重、first_seen）
- `output/<日期>/uaid-<日期>.md` + `records.jsonl`（快照=窗口内全部，非仅新增）
- `output/_filter_audit.json` 被锚定拦截候选逐条留档；`output/_verify_last.json` 对账状态（报告动态引用）
