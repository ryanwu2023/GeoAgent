# ua-front-monitor — 俄乌冲突·军事战线监测

配置驱动爬虫 + 落盘累积 + 事实/观点分类，骨架承继 diplomacy-monitor（表态/事件类）。

## 覆盖域（8 桶）

| 桶 | 覆盖 | 词表结构 |
|---|---|---|
| donbas_front | 顿巴斯/堡垒带（波克罗夫斯克为主、顿涅茨克州各城） | 地名 × 军事动作词（二维） |
| kharkiv_front | 哈尔科夫方向（库皮扬斯克/沃夫昌斯克/奥斯科尔河） | 地名 × 军事动作词（二维） |
| other_fronts | 其他方向（扎波罗热/赫尔松/苏梅-库尔斯克边境带） | 地名 × 军事动作词（二维） |
| drone_war | 无人机战（Shahed/Geran/FPV/拦截/蜂群） | 装备词 × 动作词（二维） |
| long_range | 导弹/滑翔弹远程打击（Iskander/KN-23/KAB/ATACMS/Storm Shadow） | 武器词 × 打击词（二维） |
| black_sea | 黑海/亚速海（海军无人机/舰队/敖德萨港口/航运） | 地理词 × 海事词（二维） |
| air_defense | 乌克兰防空能力（拦截率/Patriot/NASAMS/机动火力组） | 系统词 × 目标词（二维） |
| claims_intel | 双方声称与核实（信息战线） | 声称/核实词 × 主语词（二维） |

flags（机械词表命中，逐条附原文核对）：`ru_claim` 俄方声称 / `ua_claim` 乌方声称 /
`unverified` 未经核实 / `encirclement_alert` 包围表述。

## 分类

- `fact_attack` 完成态军事事件=事实（打击/交战/推进/拦截；**战果数字仍是单一来源声称**）
- `opinion_statement` 表态（『他说了X』是事实，X 是观点/声称）
- `opinion_analysis` 分析解读=观点
- 完成态判定带意向守卫 `_event_done`：事件动词前 32 字符含意向词 → 非完成态
- 锚定词自动并入各桶第一维地理专名（战线报道常只有地名主语）

## 信源（19 启用 / 12 停用留档）

- **T1**：UN Press。乌总参战报无直接 RSS（Ukrinform 404 留档），经 T3 转述层覆盖。
- **T2**：ICG。
- **T3**：Kyiv Post、Euromaidan Press、TASS（俄方官方立场窗口，标注使用）、Moscow Times、
  13 条 Google News 关键词通道。
- gnews 通道依赖代理出口可达 google：2026-09-19 晚出口 50465 对 google 域 502/全断
  （下午 7897 出口可用），断连期窗口覆盖不完整，出口恢复后自动回补。
- 全部停用源在 `config.json` 带 `note`（实测日期+状态码+原因+替代覆盖）。

## 运行

```
python ua_front_monitor.py --selftest     # 离线自检（59 项）
python ua_front_monitor.py                # 抓取+渲染
python ua_front_monitor.py --render-only  # 仅从累积档重渲染
python tools/verify_report.py             # 独立对账（31 项）
python tools/audit_content.py             # 重放分类审计
run.bat                                   # 自检→抓取→对账一条龙
```

- 产出：`output/LATEST.md`（人读）、`output/ALL-records.jsonl`（累积档，按 id 去重永不淘汰）、
  `_filter_audit.json`（拦截审计）、`_verify_last.json`（对账留档）。
- cfg_ver = sha(config + 词表指纹 + 代码哈希)：词表或代码改动自动全量回填。
- 修剪超龄前强制备份 `_pruned-backup.jsonl`。

## 已知偏差（报告 §8 完整版）

1. 战线战报全部来自交战方或其转述，推进/包围类表述几乎必然未经独立核实。
2. 拦截率数字分母不一（总体 ~90% vs 喷气式 Geran ~60%），禁止相加平均。
3. 每日交战次数/伤亡数为乌总参单一口径，俄方无对应公开对照。
4. 军事温度（升温/降温）词表为启发式，只看方向不看强度。
