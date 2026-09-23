# diplomacy-monitor — 美伊冲突·外交斡旋监测

美伊战争（2026-02-28 开打）背景下的**外交斡旋专线监测**：协调方动向、特朗普谈判意愿与
能力（含 TACO 市场叙事）、军事降温/再升级对照、伊斯兰堡备忘录动向、谈判渠道状态、联大周外交窗口。

## 快速开始

```bash
python diplomacy_monitor.py --selftest   # 离线自检（70 项）
python diplomacy_monitor.py              # 抓取+渲染
python tools/verify_report.py            # 独立对账（36 项）
run.bat                                  # 一条龙：自检→抓取→对账
```

其他参数：`--as-of YYYY-MM-DD --out-dir DIR`（跨天落盘验证）、`--render-only`（改渲染层后免重抓）、
`--from-file corpus.json`（离线退路）。

## 监测域（10 桶）

| 桶 | 内容 | 维度 |
|---|---|---|
| qatar_mediation | 卡塔尔（首相访伊、安萨里吹风、交换草案） | 协调方词 × 外交动作词（二维） |
| oman_mediation | 阿曼（临时航道框架、扫雷、幕后渠道） | 同上 |
| pakistan_mediation | 巴基斯坦（穆尼尔访伊、OIC 背书、后通道） | 同上 |
| other_mediators | 中国/土耳其/瑞士/沙特/UN/OIC/E3 | 同上 |
| trump_signals | 特朗普愿谈/拒谈/重大决定/TACO | 特朗普词 × 伊朗语境词（二维） |
| memo_track | 伊斯兰堡备忘录（重回/破坏/条件） | 备忘录词 × 履约语境词（二维） |
| deescalation | 军事降温（停火/暂停/收兵） | 降温词 × 美/伊主体词（二维） |
| reescalation_mil | 军事再升级（对照：降温是否被逆转） | 美方词 × 打击词（二维） |
| talks_channels | 谈判渠道状态（直接/间接/否认并列） | 谈判词 × 美伊/阿曼语境词（二维） |
| unga_diplomacy | 联大周外交窗口（签证/演讲/场边会晤） | 场馆词 × 伊朗方词（二维） |

子信号 flags（机械词表命中，逐条附原文核对）：愿谈/拒谈、重回备忘录/破坏违约、会晤访问。

## 信源

- **T1**：APP 巴基斯坦联合通讯社、UN Press（新闻稿）、UN Iran（国别页）。QNA/ONA/Radio Pakistan/
  卡塔尔·巴基斯坦·伊朗·中国 MOFA/state.gov(407)/KUNA/WAM/SPA 等停用留档（404/JS 壳/连不上）。
- **T2**：ICG、Atlantic Council IranSource、Responsible Statecraft（Quincy）。
- **T3**：Al Jazeera、BBC、Guardian、NYT、MEE、Dawn、Doha News、Tehran Times（伊朗官媒窗口）、
  TASS（俄方官方立场窗口）、Anadolu、LobeLog + 15 条 Google News 关键词通道。
- 全部停用源在 `config.json` 带 `note`（实测日期+状态码+原因+替代覆盖）；探测证据
  `tools/_probe_round*.json`。

## 已知偏差（报告 §9 完整版）

1. 斡旋大量发生在幕后：『谈判在进行』与『否认谈判』经常同时为真，只并列不裁决。
2. 『意愿』类表态不是承诺；TACO 是市场叙事（Signum 模型），不构成降温证据本身。
3. 备忘录文本未公开，条款（30 天解除封锁等）全部来自转述。
4. 军事温度词表是启发式，只看方向不看强度；沙特-胡塞外溢事件可能混入。

## 工程纪律（承继系列项目）

- 事实（完成态事件）/ 观点（表态/分析）三分类；每条记录带原文链接可溯源。
- cfg_ver = sha(config + 词表指纹 + 代码哈希)：词表或代码改动自动全量回填累积档。
- 累积档 `ALL-records.jsonl` 按 id 去重永不淘汰；修剪超龄前强制备份 `_pruned-backup.jsonl`。
- 排除项只作用于**标题区间**（正文提及不误杀真信号）。
- 拦截候选留档 `_filter_audit.json` 供复核『没命中』是真无关还是删过头。
- 验证闭环：70 自检 + 36 对账 + 同日重跑（新增 0）+ 跨天隔离目录 36/36 + 两次独立运行窗口 445/445 一致。
