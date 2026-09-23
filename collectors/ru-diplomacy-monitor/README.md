# ru-diplomacy-monitor — 俄乌冲突外交斡旋监测

监测俄乌冲突中的外交斡旋动态：**美国特使斡旋意愿与动向、谈判进程与停火安排、
欧盟与乌克兰对和谈的态度、安克雷奇框架（是否回到该框架）、中国视角（新华社）**。

## 运行

双击 `run.bat`（自检 → 抓取+渲染 → 对账一条龙），或手动：

```bat
python ru_diplomacy_monitor.py             # 抓取+渲染
python ru_diplomacy_monitor.py --selftest  # 离线自检（不联网）
python tools\verify_report.py              # 独立对账（写 _verify_last.json，报告头部动态引用）
python tools\audit_content.py              # 确定性重放审计（零漂移/可疑/矛盾扫描）
```

产出：`output/LATEST.md`（= `output/<日期>/rudip-<日期>.md`）、
`output/ALL-records.jsonl`（累积总档，按 id 去重带 first_seen）、
`output/_filter_audit.json`（锚定拦截留档）、`output/_fetch_stats.json`。

## 六个信号桶（全部 ≥2 维，核心桶 3 维）

| 桶 | 维度设计 |
|---|---|
| `us_envoy` 美国特使与斡旋动向 | 特使层词（envoy/Witkoff/Kellogg/Rubio/спецпредставител）× 斡旋动作 × 俄乌语境（三维） |
| `talks_process` 谈判进程与停火 | 和谈词（переговор/перемири 词干）× 俄乌双方 |
| `eu_stance` 欧盟对和谈态度 | 欧盟机构/人物 × 和谈/制裁语境 |
| `ua_stance` 乌克兰对和谈态度 | 乌克兰层（Zelensky/Yermak/Umerov）× 和谈/让步词 |
| `anchorage` 安克雷奇框架 | `Anchorage` × framework/terms/memorandum/return…（回摆旗标 anchorage_return） |
| `china_xinhua` 中国视角 | 中国词 × 欧洲语境 × 俄乌对象（三维门控，防中方经贸新闻混入） |

flags：`envoy_travel` 特使出行 / `willingness_positive` 愿谈 / `willingness_negative` 拒谈 /
`anchorage_return` 安克雷奇回摆 / `memorandum_draft` 备忘录 / `ceasefire_call` 停火呼吁。
立场词表语义 = **和谈倒退/强硬 vs 和谈推进/妥协**（否定翻转）。

## 信源（20 启用 / 18 停用留档）

- **T1 四个一手**：白宫 presidential-actions/news、UN News、克里姆林宫英文。
  国务院（feed 为 HTML 壳/404）、乌克兰总统府与外交部（403 疑 WAF）、EEAS（JS 壳）、NATO（404）均留档。
- **T2**：International Crisis Group。
- **T3 俄方框架**：TASS/Interfax(RU)/Moscow Times/Meduza(RU)；**乌方框架**：Kyiv Post/Euromaidan Press；
  **协调方窗口**：Anadolu（土耳其）；**中文窗口**：新华社英文首页（html_listing 解析，仅标题）+ 中新社；
  **聚合层**：Google News 6 通道（出口依赖，断连自动留档、恢复自动回补）。

## 阅读纪律（详见日报 §0）

- **意愿≠行动**：愿谈表态与特使出行分层阅读；
- **安克雷奇框架全文未公开**：只记录「是否回到该框架」的方向性报道，条款数字均为转述；
- **双方口径对立**：俄方（TASS/Interfax/kremlin）与乌方（Kyiv Post/Euromaidan）逐条标 publisher，禁平均；
- **斡旋多轨**：伊斯坦布尔直谈/美特使穿梭/第三方并行记录，不归并。

## 验证基线（2026-09-19 首轮）

自检 **90/90**（含真实语料回归正例 2 + 负例 5）；对账 **26/26**（正式+跨天隔离目录双跑）；
重放审计零漂移；同日重跑全量回填 36→21→24→26（假阳性排除与真信号补收均经人工核对）；
两次独立运行交集分类 **100% 一致**（id 差=滚动 feed 更替）。

## 已知局限

见日报 §10 与 `config.json` → `report.known_limitations`（10 条），要点：
gnews 6 通道依赖代理出口可达 google；新华社窗口仅标题级；俄语前缀词干为启发式；
乌克兰/美国国务院/EEAS 一手缺失经 T3 覆盖，出口变更后应复测停用源。
