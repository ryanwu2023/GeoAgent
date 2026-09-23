# eu-domestic-monitor — 欧盟内政×俄乌战争监测

俄乌战争视角下的欧盟内政监测：法国 2027 总统大选预备（勒庞/国民联盟民调）、意大利大选（2027-12 前）、
各国对乌军事援助态度、各国对俄政策立场、欧盟机构运作与成员国博弈、中国视角（新华社）。

## 运行

```
run.bat          # 自检 → 抓取+渲染 → 对账+审计 一条龙
python eu_domestic_monitor.py --selftest     # 离线自检（不联网）
python eu_domestic_monitor.py --render-only  # 仅从累积档重渲染
python eu_domestic_monitor.py --as-of 2026-09-21 --out-dir output/_xday-verify
```

依赖：Python 3.x（`run.bat` 自动解析：`RM_PY` → `PYTHON` → 托管运行时 → PATH，
不再写死版本路径，升级运行时不会连带把项目弄挂）；代理自动探测（`urllib.request.getproxies()`），
绝不写死端口。

## 结构

| 文件 | 说明 |
|---|---|
| `eu_domestic_monitor.py` | 主脚本（Engine 分类/抓取/渲染/自检 82 项） |
| `config.json` | 45 源全留档（启用+停用）、六桶词表、flags、立场词表、基线 |
| `tools/probe_sources.py` `probe_round2.py` | 候选源探测（实测日期+状态码+原因） |
| `tools/verify_report.py` | 独立对账（19 项；路径全覆盖参数 `--out-dir/--date/--report/--records/--archive`） |
| `tools/audit_content.py` | 确定性重放审计（桶/kind/stance/flags 零漂移）+ 完成态可疑 + flags 矛盾提示 |
| `output/LATEST.md` | 最新日报 |
| `output/ALL-records.jsonl` | 累积总档（按 id 去重、永不淘汰、带 first_seen） |
| `output/_filter_audit.json` | 被锚定拦截候选留档 |

## 六桶（全 ≥2 维）

1. **france_2027** 法国2027总统大选预备（选举/民调词维 × 勒庞/RN/马克龙/爱丽舍宫人物党派维）
2. **italy_election** 意大利大选与执政联盟（选举/民调/组阁语境 × Meloni/Lega/FdI/M5S/联盟）
3. **ua_aid_attitude** 各国对乌军事援助态度（援助词 × 乌克兰 × 态度动词三维）
4. **russia_policy** 各国对俄政策与战争立场（俄国词 × 制裁/外交/和平/接触政策词）
5. **eu_politics** 欧盟机构运作与成员国博弈（机构词 × 政治动作词）
6. **china_xinhua** 中国视角（中国词 × 欧洲/战争语境 × 俄/乌对象三维）

flags：`polling_data`（民调）/`campaign_prep`（竞选筹备）/`aid_commitment`（援助承诺）/
`aid_restriction`（援助限制）/`russia_engagement`（对俄接触/制裁松动）。
立场词表：收缩/软化 vs 扩张/强化（否定翻转，只看方向）。

## 阅读纪律（报告 §0 同步）

- 事实/观点三分类：`fact_event`（完成态）≠ `opinion_statement`（他说了X，X 是立场）≠ `opinion_analysis`
- 民调投票意向≠实际投票；2022 民调普遍低估 RN；机构间禁横向平均
- 选举→政府→援助政策是**间接链条**：本监测记录表态，不预测选举结果
- 法国援助权在**总统**（半总统制），意大利在**内阁**；EU 共同外交需 27 国一致

## 词表语言

英语（SUF 词形族）+ 法语（élection→élections / sondage→sondages 等 + `rx:` 精确正则如
`rx:\ble pen\b`）+ 意大利语（`rx:\belezion\w*` / `rx:\bsondagg\w*` 前缀词干，**注意意语
Ucraina≠ukraine**，已双词干）。

## 已知局限（报告 §10 完整版）

gnews 6 通道依赖代理出口可达 google（2026-09-19 出口 50465 对 google 全断，断连自动留档、
恢复自动回补）；Le Monde/Politico EU/Euronews/EURACTIV/欧盟理事会 本出口不可达留档复测；
新华社首页仅标题无摘要；delors 源活动日期含未来日期落「无日期」分节。

## 验证基线（2026-09-19 建成时）

自检 82/82；对账 19/19（正式+跨天隔离目录双跑）；重放零漂移；同日重跑全量回填正确排除
9 条真实语料假阳性；两次独立运行交集分类 100% 一致（id 差=滚动 feed 更替，预期内）。
