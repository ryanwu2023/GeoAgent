# 美军弹药库存与军费开支监测 · 2026-09-17

> 面向大模型阅读的结构化日报。**所有数字均标注来源与口径**；
> 本文件只做公开证据汇总与机械计算，**不构成结论、不构成预测**。
> 分析目标：为「美伊冲突等持续冲突会延续还是收束」提供可核查的佐证。
> **快照语义**：本文件是**滚动窗口快照** —— 收录抓取窗口内的**全部**记录，
> 而不只是「当天新增」；第 0 节分别标注「本次新增」与「当前快照」条数。
> 掉出时间窗的历史不会丢：跨天累积的完整档案见 `output/ALL-records.jsonl`。

> 🔴 **关于「库存」的口径（务必先读）**：本报告**不包含弹药/拦截弹库存的绝对数量**。
> 公开渠道**不存在**美军弹药库存的实时或定期数量披露。标题里的「库存」一律指
> **间接佐证**，只有三类来源：① CBO / GAO 等官方分析中「已消耗库存的百分之多少」
> 这类**相对表述**；② 承包商扩产、交付延迟的公开报道；③ 补货合同与对外军售通知。
> **任何具体库存条数都是推断，不是披露值。** 需要准确库存量只能依赖 GAO 受限报告
> 或国防部内部数据。相比之下，**军费数字是权威一手数据**（财政部 MTS / USAspending），
> 已逐项回源核对。

## 0. 本次运行

- 运行时间：**2026-09-17 12:31 UTC**（本地 2026-09-17 20:31 中国标准时间）
- 回溯窗口：720 小时（各源可用 `window_days` 覆盖，见状态表）
- 记录：**本次新增 0 条**，当前快照 **127 条**（抓取窗口内共 127 条）
- 分类：合同 30 · 报告 33 · 法规/决定书 39 · 新闻 25

| 数据源 | 证据层 | 状态 | 窗内条数 | 其中相关 | 源内最新 | 备注 |
|---|---|---|---|---|---|---|
| `treasury-mts` | 支付层 | ✅ ok | 0 | 0 | 2026-08-31（17 天前） |  |
| `usaspending-munitions` | 合同层 | ✅ ok | 30 | 30 | 2026-06-17（92 天前） |  |
| `usaspending-psc-category` | 合同层 | ✅ ok | 0 | 0 | — |  |
| `federal-register` | 拨款层 | ✅ ok | 41 | 13 | 2026-09-17（0 天前） |  |
| `gao-reports` | 审计层 | ✅ ok | 25 | 0 | 2026-09-17（0 天前） |  |
| `govinfo-plaw` | 拨款层 | ✅ ok | 8 | 0 | 2026-09-09（7 天前） |  |
| `defensenews` | 新闻层 | ✅ ok | 25 | 10 | 2026-09-17（0 天前） |  |
| `dod-contracts` | 合同层 | ⏸️ disabled | 0 | — | — | 【2026-09-17 实测 403 AkamaiGHost，主站与 war.gov 镜像均被拦】此前 2026-09-17 上午经 127.0.0.1:7897 出口可通，本次出口 IP 变更后（127.0.0.1:… |
| `usni-news` | 新闻层 | ⏸️ disabled | 0 | — | — | 【2026-09-17 实测 403，Server=cloudflare，cf-mitigated=challenge】出口 IP 被 Cloudflare 挑战拦截。此前经 7897 出口可通。不要做风控绕过。替代：… |

## 1. 一页速览（关键事实块）

- 国防部**月度净支出**（2026-08-31）：**$67.92B**，环比 -21.1%
- 国防部**本财年累计净支出**：**$832.63B**
- 其中**采购科目**（含弹药导弹）：$14.77B，环比 +6.9%，占国防合计 21.7%
- 国防部弹药与导弹类（PSC 13xx/14xx）**窗口期授标金额**（合同义务，非实际支出）：**$40.06B**
- 弹药类**新签合同**：30 笔，最大一笔 $28.00M（LOCKHEED MARTIN CORPORATION）
- **政策/审计信号**：持续升级 4 篇 · 降级结束 1 篇 · 产能库存压力 2 篇
- **走向证据净值**：**+3** → 定性标签：**多空交织，无明确方向**（算法见 §8，勿单独使用）

## 2. 军费开支仪表盘（支付层 · 财政部 MTS 表5）

口径：**政府实际净支出（net outlay）**，即钱真正从国库流出的金额，**不是预算申请数、不是授权数**。用于观察战争开支的真实节奏。

最新月份：**2026-08-31**（月度数据，发布滞后约 1 个月属正常）

| 科目 | 本月净支出 | 上月 | 环比 | 近3月均值 | 前3月均值 | 本财年累计 |
|---|---|---|---|---|---|---|
| 国防部军事项目合计 | $67.92B | $86.05B | -21.1% | $77.43B | $69.00B | $832.63B |
| 采购(含弹药导弹) | $14.77B | $13.82B | +6.9% | $15.00B | $13.30B | $158.25B |
| 运行与维护 | $28.12B | $31.30B | -10.2% | $29.54B | $28.75B | $303.98B |
| 军事人员 | $9.78B | $22.42B | -56.4% | $15.98B | $13.21B | $206.42B |
| 军事建设 | $1.41B | $1.20B | +17.9% | $1.27B | $1.10B | $13.13B |
| 对外军事融资(FMF) | $51.12M | $131.95M | -61.3% | $91.65M | $956.13M | $7.87B |
| 对外军售信托基金(FMS) | $4.84B | $5.15B | -6.0% | $5.60B | $4.76B | $54.27B |

- **采购科目占国防合计比重**：21.7%（该比重上行 = 装备弹药投入相对加大）
- **采购支出趋势**：近 3 月均值 vs 前 3 月均值 → **加速**（+12.8%）

**国防合计逐月（净支出）**

| 月份 | 金额 |
|---|---|
| 2026-08-31 | $67.92B |
| 2026-07-31 | $86.05B |
| 2026-06-30 | $78.33B |
| 2026-05-31 | $69.08B |
| 2026-04-30 | $72.82B |
| 2026-03-31 | $65.10B |
| 2026-02-28 | $66.87B |
| 2026-01-31 | $70.75B |
| 2025-12-31 | $98.30B |
| 2025-11-30 | $61.86B |
| 2025-10-31 | $95.55B |
| 2025-09-30 | $71.45B |
| 2025-08-31 | $78.02B |

## 3. 弹药与装备合同动向（合同层 · USAspending）

口径：**授标（award）层级**，反映弹药补货订单的规模与承包商结构。比支出层领先，是「未来能否打」的先行观察点。

窗口：2025-09-17 ~ 2026-09-17（365 天）

- 国防部 PSC 分类**授标金额（合同义务）合计**（返回的前 100 个类别）：**$329.00B**
- 其中**弹药与导弹类（PSC 13xx 弹药炸药 + 14xx 制导导弹）**：**$40.06B**，占前 100 类合计的 12.2%

**弹药与导弹类 PSC 明细（窗口期授标金额）**

| PSC | 类别 | 金额 |
|---|---|---|
| 1410 | GUIDED MISSILES | $23.95B |
| 1425 | GUIDED MISSILE SYSTEMS, COMPLETE | $4.48B |
| 1420 | GUIDED MISSILE COMPONENTS | $3.79B |
| 1325 | BOMBS | $2.55B |
| 1440 | LAUNCHERS, GUIDED MISSILE | $2.50B |
| 1320 | AMMUNITION, OVER 125MM | $1.71B |
| 1305 | AMMUNITION, THROUGH 30MM | $1.08B |

**国防部开支前 10 的 PSC 类别（对照用）**

| PSC | 类别 | 金额 |
|---|---|---|
| 1510 | AIRCRAFT, FIXED WING | $38.85B |
| 1410 | GUIDED MISSILES 🔸 | $23.95B |
| 1905 | COMBAT SHIPS AND LANDING VESSELS | $22.85B |
| R425 | SUPPORT- PROFESSIONAL: ENGINEERING/TECHNICAL | $19.00B |
| Q201 | MEDICAL- MANAGED HEALTHCARE | $13.46B |
| AC13 | NATIONAL DEFENSE R&D SERVICES; DEPARTMENT OF DEFENSE - MILITARY; EXPERIMENTAL DEVELOPMENT | $8.68B |
| 1520 | AIRCRAFT, ROTARY WING | $7.96B |
| 1680 | MISCELLANEOUS AIRCRAFT ACCESSORIES AND COMPONENTS | $7.25B |
| 4220 | MARINE LIFESAVING AND DIVING EQUIPMENT | $7.18B |
| R499 | SUPPORT- PROFESSIONAL: OTHER | $7.04B |

### 弹药/导弹类新签合同（窗口内 30 笔，合计 $78.78M）

> 其中 **≥$50M 的显著合同 0 笔**（窗口内无大额订单）。

> 下表只列 **≥$1.00M 的 9 笔**；另有 21 笔低于该门槛（多为国防后勤局 DLA 的零备件小额订单，合计 $1.47M），对「补货速度」无信息量，已折叠但仍保留在 JSONL 归档中。

> ⚠️ `合同金额` 为授标总额（含期权的累计潜在金额），**不等于本期实际支出**，与财政部支出数**不可相加**。USAspending 新签合同数据约有 1–3 个月发布滞后。

| 签订日 | 承包商 | 合同金额 | 弹药型号 | 说明 | 链接 |
|---|---|---|---|---|---|
| 2026-06-08 | LOCKHEED MARTIN CORPORATION | $28.00M | PrSM | M-CODE GUIDANCE SET - PRECISION STRIKE MISSILE (PRSM) | [link](https://www.usaspending.gov/award/CONT_AWD_W31P4Q26F0115_9700_W31P4Q25D0010_9700) |
| 2026-06-11 | CASTELION CORPORATION | $23.35M | — | PER SOW, CDRLS AND TECHNICAL PROPOSAL | [link](https://www.usaspending.gov/award/CONT_AWD_N6833526F1138_9700_N6833526G0006_9700) |
| 2026-06-11 | NORTHROP GRUMMAN SYSTEMS CORPORAT… | $6.89M | — | DELIVERY ORDER FOR THE XM343 SAVO | [link](https://www.usaspending.gov/award/CONT_AWD_W15QKN26F0174_9700_W15QKN24D0013_9700) |
| 2026-06-17 | NAMMO DEFENSE SYSTEMS INC. | $4.77M | rocket motor | ROCKET MOTOR NATIONAL STOCK NUMBER: 1377-01-517-5493ES NATIONAL STOCK NUMBER: 1377-01-517… | [link](https://www.usaspending.gov/award/CONT_AWD_FA821326C0002_9700_-NONE-_-NONE-) |
| 2026-05-28 | CORONET MACHINERY CORP. | $3.31M | — | BASES AND CASES OPTION YEAR 2 DELIVERY ORDER | [link](https://www.usaspending.gov/award/CONT_AWD_N0017426F1110_9700_N0017424D0003_9700) |
| 2026-05-27 | ROCKWELL COLLINS, INC. | $3.21M | — | WA98 & WA99, WB01-WB05, WB36-WB39 | [link](https://www.usaspending.gov/award/CONT_AWD_N0010426FYB05_9700_N0010422DYB01_9700) |
| 2026-06-08 | TELEDYNE FLIR DEFENSE, LLC | $3.11M | — | USSOCOM - DELIVERY ORDER #8 | [link](https://www.usaspending.gov/award/CONT_AWD_M6785426F1045_9700_M6785424D1028_9700) |
| 2026-06-11 | KRATOS UNMANNED AERIAL SYSTEMS, I… | $3.00M | — | PROCUREMENT OF MISSION KITS AND PARTS | [link](https://www.usaspending.gov/award/CONT_AWD_N0001926F1000_9700_N0001922G0004_9700) |
| 2026-06-01 | LOCKHEED MARTIN CORPORATION | $1.68M | — | PRODUCTION ACCEPTANCE TESTING AND EVALUATION (PATE) | [link](https://www.usaspending.gov/award/CONT_AWD_N0001926F1010_9700_N0001923G0002_9700) |

## 4. 政策与授权信号（拨款层 · 美国联邦公报）

口径：联邦公报是**法律效力文件**的发布地。总统决定书、DPA 授权、军贸通知都在此发布 —— 这是「有没有新钱、有没有新的扩产/动武授权」的直接开关。

### 弹药与军贸出口许可(DDTC)通知（11 篇）

- `2026-09-16` **Waiver of Sanctions on Syria Under the Chemical and Biological Weapons Control and Warfare Elimination Act of 1991**  
  类型：Notice｜信号：降级/结束｜[原文](https://www.federalregister.gov/documents/2026/09/16/2026-18918/waiver-of-sanctions-on-syria-under-the-chemical-and-biological-weapons-control-and-warfare)
- `2026-09-02` **Arms Sales Notification**  
  类型：Notice｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/09/02/2026-17980/arms-sales-notification)
- `2026-09-01` **Bureau of Political-Military Affairs, Directorate of Defense Trade Controls: Notifications to the Congress of Proposed Commercial Export Licenses**  
  类型：Notice｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/09/01/2026-17813/bureau-of-political-military-affairs-directorate-of-defense-trade-controls-notifications-to-the)
- `2026-08-31` **30-Day Notice of Proposed Information Collection: Application for Permanent/Temporary Export or Temporary Import of Classified Defense Articles and C…**  
  类型：Notice｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/08/31/2026-17748/30-day-notice-of-proposed-information-collection-application-for-permanenttemporary-export-or)
- `2026-08-28` **International Traffic in Arms Regulations: Modification of Civil Aircraft To Incorporate Aircraft Survivability Equipment**  
  类型：Rule｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/08/28/2026-17660/international-traffic-in-arms-regulations-modification-of-civil-aircraft-to-incorporate-aircraft)
- `2026-08-28` **International Traffic in Arms Regulations: Extension of Temporary Modification of Category XI(b) of the U.S. Munitions List**  
  类型：Rule｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/08/28/2026-17576/international-traffic-in-arms-regulations-extension-of-temporary-modification-of-category-xib-of-the)
- `2026-08-28` **Arms Sales Notification**  
  类型：Notice｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/08/28/2026-17568/arms-sales-notification)
- `2026-08-28` **Arms Sales Notification**  
  类型：Notice｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/08/28/2026-17567/arms-sales-notification)
- `2026-08-28` **Arms Sales Notification**  
  类型：Notice｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/08/28/2026-17564/arms-sales-notification)
- `2026-08-28` **Arms Sales Notification**  
  类型：Notice｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/08/28/2026-17562/arms-sales-notification)
- `2026-08-28` **Arms Sales Notification**  
  类型：Notice｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/08/28/2026-17561/arms-sales-notification)

### 国防生产法(DPA)产能与优先权授权（1 篇）

- `2026-09-11` **Adjusting Certain Delegations Under the Defense Production Act**  
  类型：Presidential Document｜信号：持续/升级｜[原文](https://www.federalregister.gov/documents/2026/09/11/2026-18739/adjusting-certain-delegations-under-the-defense-production-act)

### 总统决定书—对外军援与紧急裁减授权（1 篇）

- `2026-08-26` **Presidential Determination on Provision of Atomic Information to Finland and Sweden**  
  类型：Presidential Document｜信号：持续/升级｜[原文](https://www.federalregister.gov/documents/2026/08/26/2026-17477/presidential-determination-on-provision-of-atomic-information-to-finland-and-sweden)

_已折叠 26 条与国防军费主题相关性不足的联邦公报文献。_

## 5. 审计与产能约束（审计层 · GAO）

口径：GAO 是官方审计机构，其报告是**官方自己承认瓶颈**的地方 —— 弹药产能、库存水平、交付延迟。这是判断「还能撑多久」最重的旁证。

_窗口内 25 份 GAO 报告均**未锚定**到弹药/军费主题，已全部折叠。_

> 这是已知的源限制：GAO 只开放 `/rss/reports.xml`（最新 25 份、全主题），搜索接口返回 403，无法按关键词检索。因此审计层**多数时候为空是正常的**；一旦 GAO 发布弹药产能/库存类报告，且排在最新 25 份内，就会自动出现。

## 6. 立法与拨款（拨款层 · govinfo 公法）

_窗口内无与国防军费相关的公法（属正常，公法数量稀少）。_

## 7. 新闻线索（新闻层 · Defense News）

> ⚠️ 商业媒体仅作**线索**，引用前必须回溯到官方源确认。

- `2026-09-16` **AI military targeting may move faster than humans can authenticate, critics warn**｜ballistic missile、cruise missile、Tomahawk｜[原文](https://www.defensenews.com/news/your-military/2026/09/16/ai-military-targeting-may-move-faster-than-humans-can-authenticate-critics-warn/)
- `2026-09-16` **Israel’s SPICE 1000 bombs can soon drop from F-35 fighters**｜munitions、warhead｜[原文](https://www.defensenews.com/global/mideast-africa/2026/09/16/israels-spice-1000-bombs-can-soon-drop-from-f-35-fighters/)
- `2026-09-16` **US military must adapt as formations will be ‘hunted’ by autonomous systems, Caine says**｜munitions｜[原文](https://www.defensenews.com/news/your-military/2026/09/16/us-military-must-adapt-as-formations-will-be-hunted-by-autonomous-systems-caine-says/)
- `2026-09-16` **Germany, US to expand shared production of Patriot interceptors, strike missiles**｜cruise missile、interceptor、air-defense、PAC-3 MSE｜信号：持续/升级｜[原文](https://www.defensenews.com/global/europe/2026/09/16/germany-us-to-expand-shared-production-of-patriot-interceptors-strike-missiles/)
- `2026-09-16` **China warns US space weapons could fuel arms race**｜[原文](https://www.defensenews.com/global/2026/09/16/china-warns-us-space-weapons-could-fuel-arms-race/)
- `2026-09-16` **Spain’s Indra proffers ‘Squall’ for Europe’s emerging strike missile mix**｜cruise missile、air defense、ammunition、warhead｜[原文](https://www.defensenews.com/global/europe/2026/09/16/spains-indra-proffers-squall-for-europes-emerging-strike-missile-mix/)
- `2026-09-16` **Ukraine’s Zelenskyy says new anti-drone weapon being tested**｜ballistic missile、interceptor｜[原文](https://www.defensenews.com/global/2026/09/16/ukraines-zelenskyy-says-new-anti-drone-weapon-being-tested/)
- `2026-09-15` **Iran war has cost $38 billion, depleted two-thirds of US missile interceptors, CBO says**｜ballistic missile、Standard Missile、cruise missile、interceptor｜信号：持续/升级、产能/库存压力｜[原文](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/)
- `2026-09-15` **Iranian strikes damaged hundreds of US military buildings, dozens of aircraft**｜munitions｜信号：产能/库存压力｜[原文](https://www.defensenews.com/news/pentagon-congress/2026/09/15/iranian-strikes-damaged-hundreds-of-us-military-buildings-dozens-of-aircraft/)
- `2026-09-15` **Russia and Iran deepen weapons partnership amid wars in Ukraine, Middle East**｜cruise missile、ammunition、munitions｜[原文](https://www.defensenews.com/news/your-military/2026/09/15/russia-and-iran-deepen-weapons-partnership-amid-wars-in-ukraine-middle-east/)

_已折叠 15 条与弹药/军费主题无关的新闻。_

## 8. 战争走向证据板

### 8.1 三级证据链（怎么用这份报告）

```
拨款层  公法 / 总统决定书 / DPA授权   → 有没有新钱、有没有新的扩产或动武授权
            ↓（领先）
合同层  弹药导弹类新签合同 (PSC 13xx/14xx)  → 弹药补货速度 = 打得起的物质基础
            ↓（滞后 1–3 个月）
支付层  财政部 MTS 国防科目实际净支出      → 真金白银出账节奏（最硬、最慢）

旁证：审计层 GAO（官方承认的产能/库存瓶颈） · 新闻层（线索，需回溯官方源）
```

**读法**：拨款层出现新钱/新授权 → 合同层订单放量 → 支付层支出上行，三段同向即为「持续/升级」的强证据；反之三段同向走弱，是「降级/结束」的强证据。**若拨款层放量而支付层停滞**，说明钱批了但没花出去（产能或流程卡点）。

### 8.2 信号计数（窗口内）

| 信号桶 | 命中篇数 | 加权分 | 含义 |
|---|---|---|---|
| 🔴 持续/升级 | 4 | 10 | 新增拨款、扩产、裁减授权、前置部署 |
| 🟢 降级/结束 | 1 | 3 | 停火、撤军、合同终止、制裁缓解 |
| 🟠 产能/库存压力 | 2 | 8 | 库存不足、产能瓶颈、交付延迟、成本不可持续 |

**净值** = 持续 − 降级 − 0.5×产能压力 = 10 − 3 − 0.5×8 = **+3** → **多空交织，无明确方向**

> 计分口径：只统计**相关记录**（正文未被折叠者），与 §0 状态表「其中相关」列一致；已被折叠的无关文件不参与计分。

> ⚠️ **算法局限（必须知道）**：① 这是短语命中计数，**不理解语境** —— 「停火谈判破裂」也会被计成降级信号；② 各桶权重是人工设定的先验，未经回测；③ 样本以英文官方文件为主，存在发布选择偏差；④ 因子按篇封顶 5 分，仅为防止单篇长文主导。**请把它当作检索索引，不是结论。**

### 🔴 持续/升级信号（4 篇 · 加权分 10）

来源分布：`defensenews` 2 篇、`federal-register` 2 篇

| 命中短语 | 出现篇数 | 原文片段（可自行核验） | 出处 |
|---|---|---|---|
| `sustainment` | 1 | es for joint development, production, licensed manufacturing and expanded maintenance and sustainment capacities — exactly what allied countries shou… | [link](https://www.defensenews.com/global/europe/2026/09/16/germany-us-to-expand-shared-production-of-patriot-interceptors-strike-missiles/) |
| `supplemental funding` | 1 | re subject to considerable uncertainty. The administration had requested $87.6 billion in supplemental funding in June, including $67.1 billion for t… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `Defense Production Act` | 1 | Adjusting Certain Delegations Under the Defense Production Act . . Executive Office of the President . Presidential Document | [link](https://www.federalregister.gov/documents/2026/09/11/2026-18739/adjusting-certain-delegations-under-the-defense-production-act) |
| `Presidential Determination` | 1 | Presidential Determination on Provision of Atomic Information to Finland and Sweden . . Executive Office of the President . Presidential Document | [link](https://www.federalregister.gov/documents/2026/08/26/2026-17477/presidential-determination-on-provision-of-atomic-information-to-finland-and-sweden) |

### 🟢 降级/结束信号（1 篇 · 加权分 3）

来源分布：`federal-register` 1 篇

| 命中短语 | 出现篇数 | 原文片段（可自行核验） | 出处 |
|---|---|---|---|
| `waiver of sanctions` | 1 | Waiver of Sanctions on Syria Under the Chemical and Biological Weapons Control and Warfare Elimination Act of 1991 . On June 30, 2025, the President … | [link](https://www.federalregister.gov/documents/2026/09/16/2026-18918/waiver-of-sanctions-on-syria-under-the-chemical-and-biological-weapons-control-and-warfare) |

### 🟠 产能/库存压力信号（2 篇 · 加权分 8）

来源分布：`defensenews` 2 篇

| 命中短语 | 出现篇数 | 原文片段（可自行核验） | 出处 |
|---|---|---|---|
| `depleted` | 2 | Iran war has cost $38 billion, depleted two-thirds of US missile interceptors, CBO says . The U.S. military has incurred about $38 billion in costs f… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `fewer weapons available` | 1 | entory of certain missile-defense interceptors since June 2025, leaving the military with fewer weapons available for a future conflict. Rebuilding t… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `rebuilding those stocks` | 1 | since June 2025, leaving the military with fewer weapons available for a future conflict. Rebuilding those stocks could take at least five years, eve… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `of the u.s. inventory` | 1 | s released today. CBO estimates the conflict has consumed between one-half and two-thirds of the U.S. inventory of certain missile-defense intercepto… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `depletion` | 1 | U.S. forces defended against Iranian ballistic missile and drone attacks. The interceptor depletion is particularly significant because the weapons a… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |

### 8.3 下一步该看什么（可检验的观察点）

- **弹药产能与库存瓶颈**：GAO 报告中出现 `stockpile` / `production rate` / `lead time` 的条目。若产能扩张授权（DPA §101）持续出现但 GAO 仍报产能不足，说明消耗速度仍高于补充速度。
- **新钱与扩产授权**：联邦公报中的 Presidential Determination / 紧急追加拨款 / DPA 优先权命令。这类文件是「还能继续打」最直接的法律与资金开关。
- **采购支出的环比拐点**：财政部 MTS 中 `采购` 科目月度净支出的环比。连续两个月回落通常先于政策转向；单月跳升多为大额合同集中出账。
- **弹药类合同金额分布**：USAspending 中 PSC 1410/1420/1340 类新签合同。注意该数据有 1–3 个月发布滞后，不能把「本周无新合同」读成「停止采购」。
- **降级信号需验证约束力**：区分「正式协议/法律文件」与「单方表态/媒体转述」。只有前者才真正改变资金流的走向。
- **证伪条件**：若出现①紧急追加拨款被否决或撤销；②采购科目连续两季度下滑；③合约终止/缩减公告增多；④兵力回撤与基地收缩 —— 则应下调「持续」判断。

## 附录 A. 数据源与合规

全部为**公开**来源：政府数据 API（财政部 fiscaldata、USAspending、联邦公报）、政府机构 RSS（GAO、govinfo）、公共媒体 RSS。
遵守 robots.txt；不抓取需登录内容；**不做任何风控绕过**；不抓取实时船舶/航空定位类受限数据。

## 附录 B. 已知偏差与口径提醒

0. **「库存」无绝对数量**：公开渠道不存在美军弹药库存的数量披露，本报告的库存判断全部来自 CBO/GAO 的相对表述与新闻转述，属**间接推断**。军费数字则是权威一手数据，两者可信度不在一个层级，请勿等同看待。
1. **财政部 MTS 滞后约 1 个月**：最新月份金额可能后续被修订。
2. **USAspending 新签合同滞后 1–3 个月**：窗口内无新合同 ≠ 停止采购。
3. **合同金额是授标总额**（含期权与潜在金额），非本期实际支出；与财政部支出数**不可直接相加**。
4. 财政部 MTS 的 `采购` 科目包含所有装备采购（飞机、舰船、车辆等），**不只弹药**；弹药占比需用 USAspending 的 PSC 13xx/14xx 单独观察。
5. 预算申请（budget request）≠ 授权（authorization）≠ 拨款（appropriation）≠ 合同（obligation）≠ 支出（outlay）。本报告主要覆盖后三者。
6. **GAO 审计层多数时候为空属正常**：GAO 只开放全主题 RSS（最新 25 份），搜索接口 403，无法按关键词检索。不要因为该节为空就断定「没有产能问题」。
7. **相关性折叠会损失召回**：为压低噪声，正文按标题强主题词锚定做过滤，可能漏掉用词特殊的相关文件。需要全量审阅时加 `--show-all`。
8. **`relevance` 字段已写入 JSONL**：可自行按该字段重排序或改阈值（配置项 `relevance_min`）。
9. **信号在完整正文上计算，但 `summary` 被截断到 1200 字** —— 因此每条信号都额外保存 `context`（原文片段），证据板表格直接展示。核验信号请以`context` 为准，不要只看 `summary`。
10. **数字可信度分级**：财政部 MTS 与 USAspending 金额属**一手权威数据**，已逐项回源对账（`tools/verify_numbers.py`，26 项差 0.00）；而「库存水平/消耗比例」只能靠 CBO/GAO/新闻的**相对表述间接推断**，无法回源核对。两者不可等同看待。
11. **PSC 13xx/14xx 含大量零备件**：弹药类代码会带出国防后勤局(DLA)的备件小额订单（实测 30 笔中 21 笔低于 $1M，多为「挡圈」「排气管罩」这类），对「补货速度」无信息量。正文只列 ≥$1M 的订单并标注折叠笔数。

## 附录 C. 抓取时间与溯源

- 本文件由 `spend_monitor.py` v1.0.0 生成于 2026-09-17 12:31 UTC
- 结构化记录：`output/2026-09-17/records-2026-09-17.jsonl`（含每条记录的来源链接、金额、弹药词、信号）
- 指标快照：`output/2026-09-17/metrics-2026-09-17.json`
- 每条记录均保留原始链接与发布时间，可逐条回溯。
