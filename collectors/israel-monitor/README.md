# israel-monitor — 美伊冲突·以色列动向监测

美伊冲突背景下对**以色列侧**的监测：2026-10-27 第 26 届议会大选与组阁、内塔尼亚胡司法/政治处境、
黎巴嫩·叙利亚·约旦河西岸·加沙四域军事动向、对伊第二战线、国内抗议。

承继 `opinion-monitor`（表态/事件类）骨架：配置驱动源、事实/观点四分类、tier 分级、
锚定+桶二维匹配、累积档+全量回填（cfg_ver 含代码哈希）、离线自检、独立对账、跨天落盘验证。

## 日常运行

```
run.bat   # 等价于: --selftest → 全量抓取 → verify_report 对账
```

产出：`output/LATEST.md`（最新日报）、`output/<日期>/israel-digest-<日期>.md`、
`output/ALL-records.jsonl`（累积档，按 id 去重永不淘汰）、`_filter_audit.json`（锚定拦截留档）。

## 信号桶（9）

| 桶 | 语义 | 判定 |
|---|---|---|
| election_polls | 大选民调/席位预测 | poll 词 × (election/knesset/政党词)，二维 |
| election_campaign | 大选与组阁动向 | 竞选人物实体 + 选举词 |
| netanyahu_trial | 内塔尼亚胡司法 | Netanyahu 实体 + trial/court/ICC 等词 |
| lebanon / syria / westbank / gaza | 四域军事 | 地名/组织锚词（维度单层，靠源关键词通道覆盖） |
| iran_front | 对伊第二战线 | iran/irgc/houthi/yemen 词 |
| protest | 国内街头行动 | 集会动词（march 带月份语境排除守卫） |

## 事实/观点四分类

- `fact_poll` 民调/席位预测发布值=事实（10-27 开票前不存在"结果"，只有预测）
- `fact_vote` 开票/组阁结果=事实（results_re：exit poll/results/ballots counted/mandate/coalition talks）
- `fact_event` 可核验事件=事实（军事打击/突袭/逮捕/听证/集会，event_re 含军事动词族）
- `opinion_statement` 「他说了X」是事实、X 是观点（附人物归属+上下文）
- `opinion_analysis` 分析/转述=观点

## 信源（25 启用 / 12 停用留档）

- T1：UN press（其余官方站探测不可用：IDF 官网 JS 渲染壳、PMO 403、UNIFIL/OCHA/WAFA 无 RSS——留档见 config）
- T3：以媒（TOI/JPost/JNS）+ 区域对照（Al Jazeera/MEE/Asharq Al-Awsat/Al-Monitor）+ 国际（BBC/Guardian/NYT/RS）+ Google News 关键词通道 13 条
- 伤亡数字为**当事方口径**（以方声明 vs 黎卫生部长/哈马斯卫生部），报告并列不核实

## 关键基线（config 内，T3 转写快照）

- `election_baseline`：10-27 投票规则、10 家民调均值（反对派 52 vs 执政 52，均不过 61）、
  Channel 12/13 关键民调、支持率轨迹
- `netanyahu_baseline`：作证完毕（98 场）→ 9-6 复庭 → 10-4 起每周 5 天；Case 4000 受贿指控存疑；
  总统拒绝赦免；ICC 逮捕令 + 土耳其红色通报请求；联大 9-23/24 旋风行程
- `military_baseline`：黎（3 月参战、6 月框架协议、安全区 10km、Ali al-Taher 高地、Grey Line 计划）、
  加沙（正式停火 + IDF 控制 60%+ 缓冲区、人质线 2026-01 闭环）、叙（西南缓冲区、9-9 戈兰访问遭谴责）、
  西岸（9-4 纳布卢斯造枪工坊清剿、赎罪日封锁）

## 验证

- 离线自检 60 项：词形族、实体陷阱（Yair Golan≠Golan Heights、Eisenkot 双拼写）、日期六坑、
  民调抽取方向、九类分类、桶结构不变量、基线字段、march 月份守卫、回填 excluded 语义
- `tools/verify_report.py` 18+ 项独立对账（快照条数/桶计数/kind 计数/数字表/基线行数/累积档闭环），
  全路径可覆盖（--out-dir/--date/--report/--records/--archive），实际核对路径打印首行
- 跨天验证：`--as-of 2026-09-20 --out-dir output/_xday-verify`（隔离目录名非日期形态，防误选）
