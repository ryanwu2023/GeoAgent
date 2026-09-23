# russia-domestic-monitor — 俄罗斯内政监测

监测俄罗斯内政七维度：**杜马选举（2026-09-20 单一投票日）/ 油品出口与石油收入 /
财政赤字与预算执行 / 军费开支 / 卢布汇率与货币政策 / 劳动力短缺 /
中国视角（新华社·中俄经贸）**。

## 日常运行

双击 `run.bat`（自检 → 抓取 → 对账 → 审计 一条龙），或：

```bash
python russia_domestic_monitor.py             # 抓取+渲染
python russia_domestic_monitor.py --selftest  # 离线自检
python tools/verify_report.py                 # 独立对账
python tools/audit_content.py                 # 确定性重放审计
python russia_domestic_monitor.py --render-only  # 只重渲染
```

产出：`output/LATEST.md`（日报）+ `output/<日期>/records.jsonl`（结构化快照）+
`output/ALL-records.jsonl`（累积总档，按 id 去重永不淘汰）。

## 骨架要点（承继 ukraine-aid-monitor，新增能力）

- **俄语前缀词干匹配**：俄语屈折丰富，词表用前缀匹配（`рубл` →
  рубля/рублю/рубль…），每个词干带 `\w*`；多词短语各词独立带词尾。
- **`rx:` 原生正则出口**：前缀匹配不够精确时（如 `rx:\bдум[аыуе]\b` 只匹配
  Дума/Думы/Думу/Думе 而不吃动词 думать）写原生正则。
- **flags 编译走 `flag_re`**：含正则语法（`\w*`、`(?:...)`）的 flags 词条原生编译，
  纯词组才走 term_re——用 term_re 统一编译会把 `\w*` escape 成字面量（踩过的坑）。
- **内政四 flags**：官方数据语境 / 预测估算 / 缓解措施 / 收紧限制。
- **事件动词双语**：英语完成态 + 俄语完成态（опубликовал/повысил/вырос…）。
- **锚定**：俄罗斯国别词 + 俄语官方机构词（Росстат/Минфин/ЦБ/Правительство——
  俄语机构词天然内政）+ 各桶全部词表；英语泛词（government）不入锚定。
- **html_listing 解析器**：新华社英文首页（worldrss 停更 2018-01 假活 feed）。

## 信源现实（2026-09-19 实测）

| 层 | 状态 |
|---|---|
| T1 | 仅 `en.kremlin.ru` 可达。CBR（JS 壳）/Rosstat 404/Minfin 404/Duma、CEC 502 全部留档 |
| T2 | 暂缺（Carnegie 返回 HTML 壳、CSIS 出口不可达） |
| T3 | 俄语主力 6 家（Interfax/Vedomosti/Kommersant/Iz/Lenta/RIA）+ TASS/Moscow Times/Meduza + 新华社/中新社 + gnews×8 |
| 出口依赖 | gnews 8 通道依赖代理出口可达 google（实测 502 全断，断连留档、恢复自动回补） |

官方统计（汇率/赤字/失业率）当前全部经媒体转述（T3 单一来源口径），出口恢复后
优先回补 CBR/Rosstat/Minfin 官方一手。

## 口径纪律（报告 §0 全文）

- 计划(план) ≠ 执行(факт) ≠ 预测(прогноз)；卢布/美元计价禁混加
- CBR 官方汇率 / MOEX 市场价 / 平行汇率三层禁混读
- 油品出口：出口量与出口收入分开读（禁运语境下「量稳收入降」是常态）
- 2026-09-20 前后选举报道尖峰属周期效应，票数以 CEC 为准

## 验证闭环（建成时全绿）

自检 78/78（含 5 条真实语料回归负例）；对账 26/26（正式+跨天隔离目录双跑）；
重放审计零漂移；同日重跑全量回填正确排除 4 条人工核实的假阳性（捷克军费/
VK 旅游/CBR 加密监管/西亚评论）；两次独立运行交集分类 100% 一致
（id 差异=滚动 feed 采集时差，选举日报道更替快属预期）。
