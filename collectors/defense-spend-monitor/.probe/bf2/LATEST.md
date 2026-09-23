# 美军弹药库存与军费开支监测 · 2026-09-18

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

- 运行时间：**2026-09-18 02:59 UTC**（本地 2026-09-18 10:59 中国标准时间）
- 回溯窗口：6456 小时（各源可用 `window_days` 覆盖，见状态表）
- 记录：**本次新增 0 条**，当前快照 **242 条**（抓取窗口内共 242 条）
- 分类：合同 30 · 报告 125 · 法规/决定书 62 · 新闻 25

| 数据源 | 等级 | 证据层 | 状态 | 窗内条数 | 其中相关 | 源内最新 | 备注 |
|---|---|---|---|---|---|---|---|
| `treasury-mts` | `T1` 🟢 原始记录/法律文件 | 支付层 | ✅ ok | 0 | 0 | 2026-08-31（18 天前） |  |
| `usaspending-munitions` | `T1` 🟢 原始记录/法律文件 | 合同层 | ✅ ok | 30 | 30 | 2026-06-17（93 天前） |  |
| `usaspending-psc-category` | `T1` 🟢 原始记录/法律文件 | 合同层 | ✅ ok | 0 | 0 | — |  |
| `federal-register` | `T1` 🟢 原始记录/法律文件 | 拨款层 | ✅ ok | 64 | 22 | 2026-09-17（1 天前） |  |
| `gao-reports` | `T2` 🟡 机构估算与判断 | 审计层 | ✅ ok | 25 | 0 | 2026-09-17（0 天前） |  |
| `cbo-reports` | `T2` 🟡 机构估算与判断 | 审计层 | ✅ ok | 1 | 1 | 2026-09-17（0 天前） | 标题闸门过滤 29 条（全议题源需显式锚定主题） |
| `crs-reports` | `T2` 🟡 机构估算与判断 | 审计层 | ⚪ ok-empty | 0 | 0 | — | 第 1 页请求失败；使用 DEMO_KEY（限流约 30 次/小时），建议申请个人 key |
| `govinfo-plaw` | `T1` 🟢 原始记录/法律文件 | 拨款层 | ✅ ok | 99 | 0 | 2026-09-09（8 天前） |  |
| `defensenews` | `T3` 🟠 二手转述 | 新闻层 | ✅ ok | 25 | 13 | 2026-09-17（0 天前） |  |
| `dod-contracts` | `T1` 🟢 原始记录/法律文件 | 合同层 | ⏸️ disabled | 0 | — | — | 【2026-09-17 实测 403 AkamaiGHost，主站与 war.gov 镜像均被拦】此前 2026-09-17 上午经 127.0.0.1:7897 出口可通，本次出口 IP 变更后（127.0.0.1:… |
| `usni-news` | `T3` 🟠 二手转述 | 新闻层 | ⏸️ disabled | 0 | — | — | 【2026-09-17 实测 403，Server=cloudflare，cf-mitigated=challenge】出口 IP 被 Cloudflare 挑战拦截。此前经 7897 出口可通。不要做风控绕过。替代：… |

- **信源等级分布**（当前快照）：`T1` 191 条、`T2` 26 条、`T3` 25 条

> 🔍 **等级怎么读**：`T1` 官方一手（政府原始记录，可逐项回源对账）· `T2` 官方分析/审计（机构估算，权威但非披露值）· `T3` 专业/商业媒体（**二手转述**，引用前必须回溯原文）· `T4` 自媒体/社交平台（**本报告不采集**）。等级与「证据层」是两个正交维度：层回答「在战争链条的哪一环」，等级回答「这条信息有多可信」。**完整信源清单（含停用源、发布主体、回源方式）与逐项溯源矩阵见附录 D。**

## 1. 一页速览（关键事实块）

- 国防部**月度净支出**（2026-08-31）：**$67.92B**，环比 -21.1%｜来源：`treasury-mts` `T1` 🟢 原始记录/法律文件（财政部 API，可回源）
- 国防部**本财年累计净支出**：**$832.63B**｜来源：`treasury-mts` `T1` 🟢 原始记录/法律文件（财政部 API，可回源）
- 其中**采购科目**（含弹药导弹）：$14.77B，环比 +6.9%，占国防合计 21.7%｜来源：`treasury-mts` `T1` 🟢 原始记录/法律文件（财政部 API，可回源）
- 国防部弹药与导弹类（PSC 13xx/14xx）**窗口期授标金额**（合同义务，非实际支出）：**$42.11B**｜来源：`usaspending-psc-category` `T1` 🟢 原始记录/法律文件（联邦授标库，可回源重算）
- 弹药类**新签合同**：30 笔，最大一笔 $28.00M（LOCKHEED MARTIN CORPORATION）｜来源：`usaspending-munitions` `T1` 🟢 原始记录/法律文件（每条含 award 编号可点开）
- **政策/审计信号**：持续升级 13 篇（最高等级 `T1` 🟢 原始记录/法律文件）· 降级结束 1 篇（最高等级 `T1` 🟢 原始记录/法律文件）· 产能库存压力 3 篇（最高等级 `T3` 🟠 二手转述）
  > ⚠️ **「产能/库存压力」桶的证词全部为 T3 商业媒体转述**（原始出处为 CBO 报告，本报告未直连）。该桶参与净值计算，但在回溯到官方原文前，**不应作为独立佐证**。
- **走向证据净值**：**+36** → 定性标签：**明显偏向持续/升级**（算法见 §8，勿单独使用）

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

窗口：2025-09-18 ~ 2026-09-18（365 天）

- 其中**弹药与导弹类（PSC 13xx 弹药炸药 + 14xx 制导导弹）**授标金额（合同义务）：**$42.11B**（21 个代码有数据，按 PSC 精确过滤，**不受排序截断影响**）
- 国防部**金额最大的前 100 个 PSC 类别**合计：$324.77B（⚠️ 这是**下界**，不是国防部合同总额 —— 该接口按金额排序返回，未返回的类别不在此数内）

**弹药与导弹类 PSC 明细（窗口期授标金额，共 21 个代码）**

| PSC | 类别 | 金额 |
|---|---|---|
| 1410 | GUIDED MISSILES | $23.95B |
| 1425 | GUIDED MISSILE SYSTEMS, COMPLETE | $4.45B |
| 1420 | GUIDED MISSILE COMPONENTS | $3.76B |
| 1440 | LAUNCHERS, GUIDED MISSILE | $2.50B |
| 1325 | BOMBS | $2.40B |
| 1320 | AMMUNITION, OVER 125MM | $1.71B |
| 1305 | AMMUNITION, THROUGH 30MM | $942.77M |
| 1355 | TORPEDOS AND COMPONENTS, INERT | $577.39M |
| 1310 | AMMUNITION, OVER 30MM UP TO 75MM | $517.59M |
| 1315 | AMMUNITION, 75MM THROUGH 125MM | $471.14M |
| 1390 | FUZES AND PRIMERS | $314.76M |
| 1370 | PYROTECHNICS | $135.13M |
| 1375 | DEMOLITION MATERIALS | $113.50M |
| 1395 | MISCELLANEOUS AMMUNITION | $58.40M |
| 1430 | GUIDED MISSILE REMOTE CONTROL SYSTEMS | $57.26M |
| 1340 | ROCKETS, ROCKET AMMUNITION AND ROCKET COMPONENTS | $42.99M |
| 1385 | SURFACE USE EXPLOSIVE ORDNANCE DISPOSAL TOOLS AND EQUIPMENT | $34.64M |
| 1450 | GUIDED MISSILE HANDLING AND SERVICING EQUIPMENT | $29.97M |
| 1330 | GRENADES | $26.92M |
| 1345 | LAND MINES | $7.40M |
| 1350 | UNDERWATER MINE AND COMPONENTS, INERT | $4.03M |

**国防部开支前 10 的 PSC 类别（对照用）**

| PSC | 类别 | 金额 |
|---|---|---|
| 1510 | AIRCRAFT, FIXED WING | $36.88B |
| 1410 | GUIDED MISSILES 🔸 | $23.95B |
| 1905 | COMBAT SHIPS AND LANDING VESSELS | $22.83B |
| R425 | SUPPORT- PROFESSIONAL: ENGINEERING/TECHNICAL | $18.87B |
| Q201 | MEDICAL- MANAGED HEALTHCARE | $13.43B |
| AC13 | NATIONAL DEFENSE R&D SERVICES; DEPARTMENT OF DEFENSE - MILITARY; EXPERIMENTAL DEVELOPMENT | $8.62B |
| 1520 | AIRCRAFT, ROTARY WING | $7.95B |
| 1680 | MISCELLANEOUS AIRCRAFT ACCESSORIES AND COMPONENTS | $7.25B |
| 4220 | MARINE LIFESAVING AND DIVING EQUIPMENT | $7.15B |
| R499 | SUPPORT- PROFESSIONAL: OTHER | $6.70B |

**逐月合同义务（2026-02-01 → 2026-09-18）**

完整趋势与解读见 `output/history/LATEST-trend.md`。

| 月份 | 弹药/导弹 | 全部合同类 | 弹药占比 |
|---|---:|---:|---:|
| 2025-09 | $9.07B | $65.00B | 14.0% |
| 2025-10 | $493.70M | $13.02B | 3.8% |
| 2025-11 | $1.59B | $28.08B | 5.7% |
| 2025-12 | $3.34B | $48.85B | 6.8% |
| 2026-01 | $1.10B | $35.55B | 3.1% |
| 2026-02 | $1.45B | $27.08B | 5.4% |
| 2026-03 | $9.72B | $68.62B | 14.2% |
| 2026-04 | $9.61B | $52.04B | 18.5% |
| 2026-05 | $3.99B | $49.48B | 8.1% |
| 2026-06 | $1.74B | $22.87B | 7.6% |
| 2026-07 | $0.00 | $46.67M | — |
| 2026-08 | $0.00 | $22.07M | — |
| 2026-09 | $0.00 | $4.44M | — |

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

### 总统决定书—对外军援与紧急裁减授权（10 篇）

- `2026-08-26` **Presidential Determination on Provision of Atomic Information to Finland and Sweden**  
  类型：Presidential Document｜信号：持续/升级｜[原文](https://www.federalregister.gov/documents/2026/08/26/2026-17477/presidential-determination-on-provision-of-atomic-information-to-finland-and-sweden)
- `2026-08-04` **Presidential Determination Pursuant to Section 101 of the Defense Production Act of 1950, as Amended, on Recoverable Critical Minerals and Materials**  
  类型：Presidential Document｜信号：持续/升级 / 持续/升级｜[原文](https://www.federalregister.gov/documents/2026/08/04/2026-15859/presidential-determination-pursuant-to-section-101-of-the-defense-production-act-of-1950-as-amended)
- `2026-07-28` **Presidential Determination on the Proposed Agreement for Cooperation Between the Government of the United States of America and the Government of the…**  
  类型：Presidential Document｜信号：持续/升级｜[原文](https://www.federalregister.gov/documents/2026/07/28/2026-15273/presidential-determination-on-the-proposed-agreement-for-cooperation-between-the-government-of-the)
- `2026-07-06` **Presidential Determination on Assistance to Venezuela Consistent With the Trafficking Victims Protection Act of 2000**  
  类型：Presidential Document｜信号：持续/升级｜[原文](https://www.federalregister.gov/documents/2026/07/06/2026-13631/presidential-determination-on-assistance-to-venezuela-consistent-with-the-trafficking-victims)
- `2026-07-01` **Presidential Determination Concerning the Department of the Air Force's Rehabilitation and Revitalization of the Joint Base Andrews Golf Course**  
  类型：Presidential Document｜信号：持续/升级｜[原文](https://www.federalregister.gov/documents/2026/07/01/2026-13408/presidential-determination-concerning-the-department-of-the-air-forces-rehabilitation-and)
- `2026-06-17` **Presidential Determination and Delegation of Authority Under Section 708 of the Defense Production Act of 1950, as Amended**  
  类型：Presidential Document｜信号：持续/升级 / 持续/升级｜[原文](https://www.federalregister.gov/documents/2026/06/17/2026-12286/presidential-determination-and-delegation-of-authority-under-section-708-of-the-defense-production)
- `2026-05-27` **Emergency Presidential Determination on Refugee Admissions for Fiscal Year 2026**  
  类型：Presidential Document｜信号：持续/升级｜[原文](https://www.federalregister.gov/documents/2026/05/27/2026-10598/emergency-presidential-determination-on-refugee-admissions-for-fiscal-year-2026)
- `2026-05-13` **Presidential Determination Pursuant to Section 1245(d)(4)(B) and (C) of the National Defense Authorization Act for Fiscal Year 2012**  
  类型：Presidential Document｜信号：持续/升级｜[原文](https://www.federalregister.gov/documents/2026/05/13/2026-09624/presidential-determination-pursuant-to-section-1245d4b-and-c-of-the-national-defense-authorization)
- `2026-04-24` **Eligibility of the Board of Peace To Receive Defense Articles and Defense Services Under the Foreign Assistance Act of 1961 and the Arms Export Contr…**  
  类型：Presidential Document｜信号：—｜[原文](https://www.federalregister.gov/documents/2026/04/24/2026-08126/eligibility-of-the-board-of-peace-to-receive-defense-articles-and-defense-services-under-the-foreign)
- `2026-04-23` **Presidential Determination Pursuant to Section 303 of the Defense Production Act of 1950, as Amended, on Natural Gas Transmission, Processing, Storag…**  
  类型：Presidential Document｜信号：持续/升级 / 持续/升级｜[原文](https://www.federalregister.gov/documents/2026/04/23/2026-08017/presidential-determination-pursuant-to-section-303-of-the-defense-production-act-of-1950-as-amended)

_已折叠 40 条与国防军费主题相关性不足的联邦公报文献。_

## 5. 审计与产能约束（审计层 · GAO）

口径：GAO 是官方审计机构，其报告是**官方自己承认瓶颈**的地方 —— 弹药产能、库存水平、交付延迟。这是判断「还能撑多久」最重的旁证。

_窗口内 25 份 GAO 报告均**未锚定**到弹药/军费主题，已全部折叠。_

> 这是已知的源限制：GAO 只开放 `/rss/reports.xml`（最新 25 份、全主题），搜索接口返回 403，无法按关键词检索。因此审计层**多数时候为空是正常的**；一旦 GAO 发布弹药产能/库存类报告，且排在最新 25 份内，就会自动出现。

## 6. 立法与拨款（拨款层 · govinfo 公法）

_窗口内无与国防军费相关的公法（属正常，公法数量稀少）。_

## 7. 新闻线索（新闻层 · Defense News）

> ⚠️ 商业媒体仅作**线索**，引用前必须回溯到官方源确认。

- `2026-09-17` **Lockheed Martin gets first batch of Patriot interceptor parts from General Motors**｜ballistic missile、interceptor、PAC-3 MSE、munitions｜[原文](https://www.defensenews.com/industry/2026/09/17/lockheed-martin-gets-first-batch-of-patriot-interceptor-parts-from-general-motors/)
- `2026-09-17` **US Navy should target carriers in fight with China, report says**｜ballistic missile、munitions、LRASM｜信号：产能/库存压力｜[原文](https://www.defensenews.com/news/your-military/2026/09/17/us-navy-should-target-chinese-aircraft-carriers-report-says/)
- `2026-09-17` **Pentagon, Lockheed sign framework deal to boost AIM-260 production**｜AIM-120、AMRAAM｜信号：持续/升级｜[原文](https://www.defensenews.com/news/pentagon-congress/2026/09/17/pentagon-lockheed-sign-framework-deal-to-boost-aim-260-production/)
- `2026-09-17` **US Navy awards defense startup $200 million for Blackbeard hypersonic missile**｜cruise missile、air defense、munitions｜[原文](https://www.defensenews.com/news/your-navy/2026/09/17/us-navy-awards-defense-startup-200-million-for-blackbeard-hypersonic-missile/)
- `2026-09-16` **AI military targeting may move faster than humans can authenticate, critics warn**｜ballistic missile、cruise missile、Tomahawk｜[原文](https://www.defensenews.com/news/your-military/2026/09/16/ai-military-targeting-may-move-faster-than-humans-can-authenticate-critics-warn/)
- `2026-09-16` **Israel’s SPICE 1000 bombs can soon drop from F-35 fighters**｜munitions、warhead｜[原文](https://www.defensenews.com/global/mideast-africa/2026/09/16/israels-spice-1000-bombs-can-soon-drop-from-f-35-fighters/)
- `2026-09-16` **US military must adapt as formations will be ‘hunted’ by autonomous systems, Caine says**｜munitions｜[原文](https://www.defensenews.com/news/your-military/2026/09/16/us-military-must-adapt-as-formations-will-be-hunted-by-autonomous-systems-caine-says/)
- `2026-09-16` **Germany, US to expand shared production of Patriot interceptors, strike missiles**｜cruise missile、interceptor、air-defense、PAC-3 MSE｜信号：持续/升级｜[原文](https://www.defensenews.com/global/europe/2026/09/16/germany-us-to-expand-shared-production-of-patriot-interceptors-strike-missiles/)
- `2026-09-16` **China warns US space weapons could fuel arms race**｜[原文](https://www.defensenews.com/global/2026/09/16/china-warns-us-space-weapons-could-fuel-arms-race/)
- `2026-09-16` **Spain’s Indra proffers ‘Squall’ for Europe’s emerging strike missile mix**｜cruise missile、air defense、ammunition、warhead｜[原文](https://www.defensenews.com/global/europe/2026/09/16/spains-indra-proffers-squall-for-europes-emerging-strike-missile-mix/)
- `2026-09-16` **Ukraine’s Zelenskyy says new anti-drone weapon being tested**｜ballistic missile、interceptor｜[原文](https://www.defensenews.com/global/2026/09/16/ukraines-zelenskyy-says-new-anti-drone-weapon-being-tested/)
- `2026-09-15` **Iran war has cost $38 billion, depleted two-thirds of US missile interceptors, CBO says**｜ballistic missile、Standard Missile、cruise missile、interceptor｜信号：产能/库存压力、持续/升级｜[原文](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/)
- `2026-09-15` **Iranian strikes damaged hundreds of US military buildings, dozens of aircraft**｜munitions｜信号：产能/库存压力｜[原文](https://www.defensenews.com/news/pentagon-congress/2026/09/15/iranian-strikes-damaged-hundreds-of-us-military-buildings-dozens-of-aircraft/)

_已折叠 12 条与弹药/军费主题无关的新闻。_

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

### 8.1b 第二个正交维度：信源等级

「证据层」告诉你信息在战争链条的哪一环；**「信源等级」告诉你它有多可信**。两者必须一起看：一条支付层信息如果是媒体转述的，它并不比一条合同层的官方记录更硬。

```
T1 官方一手   财政部API / USAspending / 联邦公报 / govinfo  → 可逐项回源对账
T2 官方分析   GAO 审计 · CBO 估算                            → 权威，但数字是估算
T3 商业媒体   Defense News / USNI News                       → 二手转述，须回溯原文
T4 自媒体     社交平台 / 无编辑审核博客                       → 本报告不采集
```

**注意**：等级看的是**你手上这份材料由谁加工**，不是它引用了谁。「Defense News 转述 CBO 报告」这条仍然是 T3 —— 因为数字经过了媒体裁剪，且本报告并未直连 CBO 原文。要升级为 T2，必须实际取到 CBO 报告本身。

| 信号桶 | T1 官方一手 | T2 官方分析 | T3 媒体 | 桶内最高等级 | 评价 |
|---|---|---|---|---|---|
| 🔴 持续/升级 | 10 篇 | 0 篇 | 3 篇 | `T1` 🟢 | 🔸 官方与媒体混合，分项引用时注意区分 |
| 🟢 降级/结束 | 1 篇 | 0 篇 | 0 篇 | `T1` 🟢 | ✅ 全部来自官方源 |
| 🟠 产能/库存压力 | 0 篇 | 0 篇 | 3 篇 | `T3` 🟠 | ⚠️ **全部来自二手媒体，引用前须回溯原文** |

### 8.2 信号计数（窗口内）

| 信号桶 | 命中篇数 | 加权分 | 含义 |
|---|---|---|---|
| 🔴 持续/升级 | 13 | 44 | 新增拨款、扩产、裁减授权、前置部署 |
| 🟢 降级/结束 | 1 | 3 | 停火、撤军、合同终止、制裁缓解 |
| 🟠 产能/库存压力 | 3 | 9 | 库存不足、产能瓶颈、交付延迟、成本不可持续 |

**净值** = 持续 − 降级 − 0.5×产能压力 = 44 − 3 − 0.5×9 = **+36** → **明显偏向持续/升级**

> 计分口径：只统计**相关记录**（正文未被折叠者），与 §0 状态表「其中相关」列一致；已被折叠的无关文件不参与计分。

> ⚠️ **算法局限（必须知道）**：① 这是短语命中计数，**不理解语境** —— 「停火谈判破裂」也会被计成降级信号；② 各桶权重是人工设定的先验，未经回测；③ 样本以英文官方文件为主，存在发布选择偏差；④ 因子按篇封顶 5 分，仅为防止单篇长文主导。**请把它当作检索索引，不是结论。**

### 🔴 持续/升级信号（13 篇 · 加权分 44）

来源分布：`federal-register` 10 篇、`defensenews` 3 篇

**信源等级分布**：`T1` 10 篇、`T3` 3 篇

| 命中短语 | 出现篇数 | 等级 | 原文片段（可自行核验） | 出处 |
|---|---|---|---|---|
| `Presidential Determination` | 9 | `T1` 🟢 原始记录/法律文件 | Presidential Determination on Provision of Atomic Information to Finland and Sweden . . Executive Office of the President . Presidential Document | [link](https://www.federalregister.gov/documents/2026/08/26/2026-17477/presidential-determination-on-provision-of-atomic-information-to-finland-and-sweden) |
| `Defense Production Act` | 4 | `T1` 🟢 原始记录/法律文件 | Adjusting Certain Delegations Under the Defense Production Act . . Executive Office of the President . Presidential Document | [link](https://www.federalregister.gov/documents/2026/09/11/2026-18739/adjusting-certain-delegations-under-the-defense-production-act) |
| `sustainment` | 2 | `T3` 🟠 二手转述 | nce, and these gears must turn in unison,” Under Secretary of Defense for Acquisition and Sustainment Michael Duffey said in the release. “Today’s an… | [link](https://www.defensenews.com/news/pentagon-congress/2026/09/17/pentagon-lockheed-sign-framework-deal-to-boost-aim-260-production/) |
| `expand production` | 1 | `T3` 🟠 二手转述 | e U.S. Defense Department and Lockheed Martin signed a framework agreement on Thursday to expand production of the AIM-260 Joint Advanced Tactical Mi… | [link](https://www.defensenews.com/news/pentagon-congress/2026/09/17/pentagon-lockheed-sign-framework-deal-to-boost-aim-260-production/) |
| `supplemental funding` | 1 | `T3` 🟠 二手转述 | re subject to considerable uncertainty. The administration had requested $87.6 billion in supplemental funding in June, including $67.1 billion for t… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |

### 🟢 降级/结束信号（1 篇 · 加权分 3）

来源分布：`federal-register` 1 篇

**信源等级分布**：`T1` 1 篇

| 命中短语 | 出现篇数 | 等级 | 原文片段（可自行核验） | 出处 |
|---|---|---|---|---|
| `waiver of sanctions` | 1 | `T1` 🟢 原始记录/法律文件 | Waiver of Sanctions on Syria Under the Chemical and Biological Weapons Control and Warfare Elimination Act of 1991 . On June 30, 2025, the President … | [link](https://www.federalregister.gov/documents/2026/09/16/2026-18918/waiver-of-sanctions-on-syria-under-the-chemical-and-biological-weapons-control-and-warfare) |

### 🟠 产能/库存压力信号（3 篇 · 加权分 9）

来源分布：`defensenews` 3 篇

**信源等级分布**：`T3` 3 篇
> ⚠️ 本桶证据**全部来自 T3 二手转述**（无 T1/T2 原始出处直连）。引用本桶任何结论前，必须先回溯到其援引的官方原文。

| 命中短语 | 出现篇数 | 等级 | 原文片段（可自行核验） | 出处 |
|---|---|---|---|---|
| `depleted` | 2 | `T3` 🟠 二手转述 | Iran war has cost $38 billion, depleted two-thirds of US missile interceptors, CBO says . The U.S. military has incurred about $38 billion in costs f… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `stockpile` | 1 | `T3` 🟠 二手转述 | n allied bases to host naval drones and procuring more torpedoes to bolster an inadequate stockpile. Significantly, the plan calls for a change in th… | [link](https://www.defensenews.com/news/your-military/2026/09/17/us-navy-should-target-chinese-aircraft-carriers-report-says/) |
| `fewer weapons available` | 1 | `T3` 🟠 二手转述 | entory of certain missile-defense interceptors since June 2025, leaving the military with fewer weapons available for a future conflict. Rebuilding t… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `rebuilding those stocks` | 1 | `T3` 🟠 二手转述 | since June 2025, leaving the military with fewer weapons available for a future conflict. Rebuilding those stocks could take at least five years, eve… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `of the u.s. inventory` | 1 | `T3` 🟠 二手转述 | s released today. CBO estimates the conflict has consumed between one-half and two-thirds of the U.S. inventory of certain missile-defense intercepto… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `depletion` | 1 | `T3` 🟠 二手转述 | U.S. forces defended against Iranian ballistic missile and drone attacks. The interceptor depletion is particularly significant because the weapons a… | [link](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |

### 8.3 下一步该看什么（可检验的观察点）

- **弹药产能与库存瓶颈**：GAO 报告中出现 `stockpile` / `production rate` / `lead time` 的条目。若产能扩张授权（DPA §101）持续出现但 GAO 仍报产能不足，说明消耗速度仍高于补充速度。
- **新钱与扩产授权**：联邦公报中的 Presidential Determination / 紧急追加拨款 / DPA 优先权命令。这类文件是「还能继续打」最直接的法律与资金开关。
- **采购支出的环比拐点**：财政部 MTS 中 `采购` 科目月度净支出的环比。连续两个月回落通常先于政策转向；单月跳升多为大额合同集中出账。
- **弹药类合同金额分布**：USAspending 中 PSC 1410/1420/1340 类新签合同。注意该数据有 1–3 个月发布滞后，不能把「本周无新合同」读成「停止采购」。
- **降级信号需验证约束力**：区分「正式协议/法律文件」与「单方表态/媒体转述」。只有前者才真正改变资金流的走向。
- **证伪条件**：若出现①紧急追加拨款被否决或撤销；②采购科目连续两季度下滑；③合约终止/缩减公告增多；④兵力回撤与基地收缩 —— 则应下调「持续」判断。

## 附录 A. 数据源与合规

全部为**公开**来源：政府数据 API（财政部 fiscaldata、USAspending、联邦公报）、政府机构 RSS（GAO、govinfo）、公共媒体 RSS。**每个源都在附录 D.2 列出，并标注等级与发布主体** —— 报告不隐藏它的信源构成。

**信源分级**：本报告区分 T1 官方一手 / T2 官方分析 / T3 商业媒体 / T4 自媒体（**不采集**）。同一张表里的数字可能来自不同等级，引用时务必看等级列，不要把「可回源的官方数字」和「媒体转述的估算」当同一回事。
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
12. **新闻层整体是 T3，不是事实依据**：`defensenews`（商业媒体）与 `usni-news`（学会媒体）都属二手转述层。§7 的新闻列表**只作线索索引**，其中的金额、比例、库存表述在回溯到官方原文前不得引用。注意这**不代表**「官方源就够用」—— 官方源无人转述时也可能漏掉关键事件。
13. **同一事实可能同时存在高低等级两个版本**：例如「战争成本 380 亿美元、拦截弹消耗三分之二」目前只有 T3（Defense News 转述 CBO）版本，没有 T2 直连版本。这种「关键结论只有二手来源」的情形已在附录 D.3 / D.4 中显式标注，读者可优先补抓原始文件来升级该条证据。

## 附录 C. 抓取时间与溯源

- 本文件由 `spend_monitor.py` v1.2.0 生成于 2026-09-18 02:59 UTC
- 结构化记录：`output/2026-09-18/records-2026-09-18.jsonl`（含每条记录的来源链接、金额、弹药词、信号）
- 指标快照：`output/2026-09-18/metrics-2026-09-18.json`
- 每条记录均保留原始链接、发布时间与 **`tier` 信源等级**，可逐条回溯。

## 附录 D. 信源等级与数据溯源

### D.1 等级定义

| 等级 | 含义 | 是什么 | 怎么用 |
|---|---|---|---|
| `T1` 🟢 | **官方一手** | 政府机构直接发布的原始数据或具法律效力的文件。数字可逐项回源对账。 | 可直接引用为事实，并注明 API 端点/文件编号 |
| `T2` 🟡 | **官方分析/审计** | 政府机构的审计报告、成本估算、分析结论。权威，但含机构假设、口径选择与发布滞后，数字多为估算而非原始记录。 | 可作为「官方观点/估算」引用，不得当作披露的精确值 |
| `T3` 🟠 | **专业/商业媒体** | 商业防务媒体或非政府专业学会。内容通常转述 T1/T2，但已被加工裁剪，且存在选题偏好与流量动机。 | 仅作线索索引，引用前必须回溯到其援引的原始出处 |
| `T4` 🔴 | **自媒体/社交平台** | 无编辑审核的博客、自媒体账号、社交平台帖文、匿名或不具名转述。 | 本报告明确不采集、不引用 |

> **等级判定原则（最容易搞错的两条）**：
> ① **「官方机构」≠ T1**。GAO / CBO 发布的是**分析与估算**，不是原始记录 —— 数字是算出来的，带假设与口径，所以是 T2。
> ② **转述官方结论的媒体仍是 T3**。等级看的是**你手上这份材料由谁加工**，不是它引用了谁。「Defense News 转述 CBO 报告」不因源头是官方就升级；要升到 T2，必须实际取到 CBO 报告本身。
> ③ 等级与「证据层」（拨款/合同/支付）是**正交维度**，不要混为一谈。

### D.2 完整信源清单（含停用源）

> 停用源也列出：等级不变，只是因为出口 IP 被官方风控拦截而暂停采集（**不做任何绕过**），日后出口恢复即可改回启用。

| 等级 | 数据源 | 发布主体（谁发布的） | 证据层 | 当前状态 | 可回源程度 |
|---|---|---|---|---|---|
| `T1` 🟢 官方一手 | `dod-contracts` | 美国国防部（DoD 官网每日合同公告） | 合同层 | ⏸️ disabled | ✅ 可逐项回源对账 |
| `T1` 🟢 官方一手 | `federal-register` | 美国联邦公报办公室（OFR / 国家档案与文件署 NARA） | 拨款层 | ✅ ok | ✅ 可逐项回源对账 |
| `T1` 🟢 官方一手 | `govinfo-plaw` | 美国政府出版局（GPO） | 拨款层 | ✅ ok | ✅ 可逐项回源对账 |
| `T1` 🟢 官方一手 | `treasury-mts` | 美国财政部 · 财政服务局（Bureau of the Fiscal Service） | 支付层 | ✅ ok | ✅ 可逐项回源对账 |
| `T1` 🟢 官方一手 | `usaspending-munitions` | USAspending.gov（美国联邦政府官方支出数据库，OMB 主持） | 合同层 | ✅ ok | ✅ 可逐项回源对账 |
| `T1` 🟢 官方一手 | `usaspending-psc-category` | USAspending.gov（美国联邦政府官方支出数据库，OMB 主持） | 合同层 | ✅ ok | ✅ 可逐项回源对账 |
| `T2` 🟡 官方分析/审计 | `cbo-reports` | 美国国会预算办公室（CBO，立法分支） | 审计层 | ✅ ok | 🟡 可读原文，数字为估算 |
| `T2` 🟡 官方分析/审计 | `crs-reports` | 美国国会研究服务局（CRS，国会图书馆） | 审计层 | ⚪ ok-empty | 🟡 可读原文，数字为估算 |
| `T2` 🟡 官方分析/审计 | `gao-reports` | 美国政府问责局（GAO，国会立法分支） | 审计层 | ✅ ok | 🟡 可读原文，数字为估算 |
| `T3` 🟠 专业/商业媒体 | `defensenews` | Defense News（Sightline Media Group，商业防务媒体） | 新闻层 | ✅ ok | ⚠️ 需回溯其援引的原文 |
| `T3` 🟠 专业/商业媒体 | `usni-news` | USNI News（美国海军学会，非政府专业学会） | 新闻层 | ⏸️ disabled | ⚠️ 需回溯其援引的原文 |

### D.3 关键事实 → 溯源路径

下表把报告里的每个关键数字/结论映射回它的原始出处。**标 T1 的可自行回源复算；标 T3 的，本报告替不了你对账。**

| 报告中的事实 | 本报告取值 | 来源源 | 等级 | 回源方式（可自行复核） |
|---|---|---|---|---|
| 国防部月度净支出 | $67.92B | `treasury-mts` | `T1` 🟢 原始记录/法律文件 | GET `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_5` 取表5中 classification_desc=`Total--Department of Defense--Military Programs` 的行逐项比对；或直接跑 `tools/verify_numbers.py`（离线复算 + 回源对账） |
| 国防部本财年累计净支出 | $832.63B | `treasury-mts` | `T1` 🟢 原始记录/法律文件 | 同上，取同一行的 `current_fytd_net_outly_amt` 列 |
| 采购科目（含弹药导弹）净支出 | $14.77B | `treasury-mts` | `T1` 🟢 原始记录/法律文件 | 同上，classification_desc=`Total--Procurement` |
| 弹药与导弹类授标金额（窗口期，合同义务） | $42.11B | `usaspending-psc-category` | `T1` 🟢 原始记录/法律文件 | POST `https://api.usaspending.gov/api/v2/search/spending_by_category/psc/`，**必须显式传 `filters.psc_codes`（共 40 个 13xx/14xx 代码）**；不传就会被按金额排序截断 —— 实测截断会少算 2.42B（6.1%）。或跑 `tools/verify_numbers.py` |
| 弹药逐月义务额序列（战前 → 至今） | 13 个月 | `usaspending-psc-category` | `T1` 🟢 原始记录/法律文件 | POST `https://api.usaspending.gov/api/v2/search/spending_over_time/`（group=month，同一 PSC 过滤）；月度之和必须等于上行的窗口总额（本报告已用**两个不同接口**交叉验证） |
| 弹药类新签合同 30 笔（含金额与承包商） | $78.78M | `usaspending-munitions` | `T1` 🟢 原始记录/法律文件 | POST `https://api.usaspending.gov/api/v2/search/spending_by_award/`；每条记录内含 award 唯一编号，拼 `usaspending.gov/award/<id>` 即可点开原始授标页 |
| 政策/授权文件（联邦公报，相关 22 篇） | 22 篇 | `federal-register` | `T1` 🟢 原始记录/法律文件 | 每篇含 federalregister.gov 原文 URL 与 FR 文号，可直接打开核对全文 |
| 公法原文（相关 0 篇） | 0 篇 | `govinfo-plaw` | `T1` 🟢 原始记录/法律文件 | govinfo.gov 公法原文 URL，法律文本本身 |
| GAO 审计结论（相关 0 篇） | 0 篇 | `gao-reports` | `T2` 🟡 机构估算与判断 | gao.gov/products/<报告号> 可读全文；但其中的产能/库存数字是**机构估算**，不能当披露值引用 |
| CBO 成本估算（相关 1 篇） | 1 篇 | `cbo-reports` | `T2` 🟡 机构估算与判断 | **已直连 CBO 官方 RSS**（标题/摘要/链接/日期）。⚠️ 但 CBO **正文页对数据中心 IP 返回 403（Akamai）**，本报告**不做风控绕过**，因此只能取到官方摘要层，具体数字需人工点开链接读原文 —— 摘要里没有的数字，本报告不写 |
| CRS 研究报告（相关 0 篇） | 0 篇 | `crs-reports` | `T2` 🟡 机构估算与判断 | congress.gov API 每条含报告编号；原文可拼 `congress.gov/crs-product/<编号>` 打开（PDF/HTML 双格式） |
| 弹药库存/消耗比例类表述（如「拦截弹已消耗三分之二」） | 3 篇（T1 0 / T2 0 / T3 3） | `见下条说明` | `T2/T3 混合` | **公开渠道没有库存绝对数量**。这类数字只能来自官方报告的相对表述 —— CBO（T2）为原始出处，商业媒体（T3）为转述。引用时**必须**标明用的是哪一级，且不得与 T1 金额并列排放 |

### D.4 本报告不采用的来源（T4）

明确排除、且**不设采集器**的来源类型：自媒体账号、社交平台帖文（X / Telegram 等）、无编辑审核的博客、以及任何匿名或「据不愿具名人士」的转述。理由不是「不可信」三个字，而是**不可核验** —— 这类来源没有可回溯的原始文件，一旦进入证据板就会把整张表的可信度拉平，这是本报告最需要避免的失效模式。

> ⚠️ **当前实例**：本报告「产能/库存压力」桶（加权 9 分，**直接参与净值计算**）全部来自 T3 商业媒体，无 T1/T2 直连出处。回溯到 CBO/GAO 原文之前，该桶不应被当作独立佐证使用。

### D.5 当前快照的等级分布

| 等级 | 含义 | 条数 | 占比 | 引用方式 |
|---|---|---|---|---|
| `T1` 🟢 | 官方一手 | 191 | 78.9% | 可直接引用为事实 |
| `T2` 🟡 | 官方分析/审计 | 26 | 10.7% | 可引用为机构估算 |
| `T3` 🟠 | 专业/商业媒体 | 25 | 10.3% | 仅作线索，引用前须回溯原文 |
| `T4` 🔴 | 自媒体/社交平台 | 0 | 0.0% | 本报告不采集 |

> 分母为当前快照全部 242 条记录（含相关性不足、未进正文但已归档者）。**T1 占比高 ≠ 结论可靠** —— 它只说明这份报告的数字大多可回源，不说明这些数字能回答「战争会延续还是收束」。
