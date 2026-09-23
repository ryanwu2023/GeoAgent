# mideast-monitor — 美伊冲突·中东各国态势监测

对美伊战争背景下的中东多国态势做日频监测：**麦加防务协议与区域安全架构、沙特-胡塞战线、伊拉克民兵 vs 驻伊美军、海湾国家遇袭与防务、双海峡（霍尔木兹/曼德）与红海航运、斡旋与停火外交、街头反应**。事实/观点五分类，信源 T1–T4 分级，T4（当事方宣传/自媒体）不采用。

## 运行

```bat
run.bat          # ① --selftest 离线自检 → ② 全量抓取+落盘 → ③ verify_report 独立对账
```

手动：

```bash
python mideast_monitor.py --selftest        # 70 项离线自检（不联网）
python mideast_monitor.py                   # 抓取 + 落盘 + 渲染
python tools/verify_report.py               # 从 records 独立复算报告数字
python mideast_monitor.py --as-of 2026-09-20 --out-dir output/_xday-verify   # 跨天落盘验证
python mideast_monitor.py --render-only     # 改渲染层后免重抓重渲染
```

## 产出

- `output/LATEST.md` = `output/<日期>/mideast-digest-<日期>.md`：日报（§0 阅读须知 / §2 数字证据板 / §3 麦加协议 / §4 也门战线 / §5 伊拉克战线 / §6 海湾与航运 / §7 斡旋 / §8 街头 / 附录信源清单与等级定义）
- `output/ALL-records.jsonl`：累积总档（按 id 去重、永不淘汰、带 first_seen/cfg_ver）
- `output/_filter_audit.json`：被锚定拦截的候选留档
- `output/_verify_last.json`：最近一次对账留档（报告头部引用其状态）

## 事实/观点五分类（kind）

| kind | 含义 | 完成态判定 |
|---|---|---|
| `fact_pact` | 协议已签署/加入 | signed/joined/sealed… 且前方 32 字符无意向词（will/could/should/if/考虑…） |
| `fact_attack` | 袭击已发生 | killed/struck/claimed responsibility/seized…（**声称战果≠核实战果**，单方口径只并列） |
| `fact_event` | 其他可核验事件 | 警报/军援交付/机场关闭/会晤/决议/协议意向类 |
| `opinion_statement` | 表态 | 『他说了X』是事实，X 是观点（附人物+上下文） |
| `opinion_analysis` | 分析 | 观点 |

## 信源现实（2026-09-19 实测，全部留档于 config.json `enabled:false` 条目）

- 海湾核心媒体（The National/Arab News/Al Arabiya/Gulf News）403/404 防护——**不绕过**；沙特系视角靠 Asharq Al-Awsat（实测可用）+ Google News 转述层
- ISW 每日 Iran Update（民兵袭击计数权威）RSS 403——计数靠 gnews 转述层，报告披露口径降级
- CENTCOM 403、伊拉克官方通讯社 SSL 中断、卡塔尔通讯社无 RSS——官方声明全部经 T3 转述
- Al-Masirah（胡塞官方）属 T4 当事方宣传，明确不采用；其声称仅经 T3 转述引用并标注

## 手工基线

config.json 内四组（`pact_baseline`/`houthi_baseline`/`iraq_baseline`/`gulf_baseline`），为 2026-09-19 探测期 T2/T3 检索转写，逐条带来源与等级；月份粒度日期（如 `2026-06`）表示具体日子未核实。成员国/控制线变动后须回源官方公报替换。
