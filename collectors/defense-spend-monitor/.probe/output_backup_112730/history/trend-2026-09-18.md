# 美军弹药与军费动态变化（2026-02-01 → 2026-09-18）

- 序列区间：**2026-02-01 → 2026-09-18**（起点＝2026-02（美伊冲突起始月））
- 生成时间：2026-09-18 03:14 UTC
- 序列条数：56 条 / 7 个指标

> **读之前必须知道的两件事**
> 1. **口径不可混用**：财政部是 `actual net outlays`（实际净支出），
>    USAspending 是 `Contract_Obligations`（合同义务/授标）。
>    后者是「签了合同的钱」，不是「花出去的钱」，**两者不可相加、不可直接比大小**。
> 2. **可信度分级**：带 🟢T1 的序列可逐项回源对账（差 0.00）；
>    标「启发式」的序列来自本监测自身收录量，**不是官方统计**，只能看方向不能当数据用。

## 3. 政策与立法活动（联邦公报文档数 · 🟢T1）

口径：该月联邦公报中匹配检索词的文档**条数**（API 顶层 `count` 字段，官方计数）。条数变化反映「政策/授权动作的密集度」，不反映金额大小。

| 月份 | 国防生产法(DPA) | 弹药/军贸 | 紧急追加拨款 |
|---|---|---|---|
| 2026-02 | 42 | 19 | 17 |
| 2026-03 | 29 | 10 | 10 |
| 2026-04 | 36 | 9 | 12 |
| 2026-05 | 93 | 48 | 17 |
| 2026-06 | 39 | 9 | 16 |
| 2026-07 | 42 | 10 | 21 |
| 2026-08 | 42 | 17 | 20 |
| 2026-09 | 11 | 4 | 6 |

- **国防生产法(DPA)**：区间合计 334 份，峰值 2026-05（93 份）
- **弹药/军贸**：区间合计 126 份，峰值 2026-05（48 份）
- **紧急追加拨款**：区间合计 119 份，峰值 2026-07（21 份）

## 4. 信号计数（本监测收录 · ⚠️ 启发式，非官方统计）

**这一节的数字不能当官方数据引用。** 它统计的是「本监测该月收录的记录中命中各类信号的条数」，因此**收录覆盖度一变、数字就变**
（例如某源中途接入或失效，会造成序列跳变）。它只能用于看方向。

| 月份 | 收录记录 | 持续/升级 | 降级/结束 | 产能库存压力 | 净值(持续-结束) |
|---|---:|---:|---:|---:|---:|
| 2026-02 | 23 | 0 | 0 | 0 | **+0** |
| 2026-03 | 1 | 0 | 0 | 0 | **+0** |
| 2026-04 | 4 | 2 | 0 | 0 | **+2** |
| 2026-05 | 11 | 2 | 0 | 0 | **+2** |
| 2026-06 | 77 | 2 | 0 | 0 | **+2** |
| 2026-07 | 22 | 3 | 0 | 0 | **+3** |
| 2026-08 | 27 | 3 | 0 | 0 | **+3** |
| 2026-09 | 93 | 7 | 4 | 11 | **+3** |

## 5. 官方事件时间线（仅 T1/T2，按官方发布日）

只收录**官方一手/官方分析**层的事件。二手媒体转述不进时间线 —— 否则时间点会变成「媒体哪天报道」而不是「哪天真的发生」。

| 日期 | 事件类型 | 标题 | 等级 | 来源 |
|---|---|---|---|---|
| 2026-04-23 | 国防生产法(DPA)授权 | [Presidential Determination Pursuant to Section 303 of the Defense Production Act of 195…](https://www.federalregister.gov/documents/2026/04/23/2026-08017/presidential-determination-pursuant-to-section-303-of-the-defense-production-act-of-1950-as-amended) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-05-13 | 总统决定书（对外军援/裁减） | [Presidential Determination Pursuant to Section 1245(d)(4)(B) and (C) of the National De…](https://www.federalregister.gov/documents/2026/05/13/2026-09624/presidential-determination-pursuant-to-section-1245d4b-and-c-of-the-national-defense-authorization) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-05-27 | 总统决定书（对外军援/裁减） | [Emergency Presidential Determination on Refugee Admissions for Fiscal Year 2026](https://www.federalregister.gov/documents/2026/05/27/2026-10598/emergency-presidential-determination-on-refugee-admissions-for-fiscal-year-2026) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-06-17 | 国防生产法(DPA)授权 | [Presidential Determination and Delegation of Authority Under Section 708 of the Defense…](https://www.federalregister.gov/documents/2026/06/17/2026-12286/presidential-determination-and-delegation-of-authority-under-section-708-of-the-defense-production) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-07-01 | 总统决定书（对外军援/裁减） | [Presidential Determination Concerning the Department of the Air Force's Rehabilitation …](https://www.federalregister.gov/documents/2026/07/01/2026-13408/presidential-determination-concerning-the-department-of-the-air-forces-rehabilitation-and) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-07-06 | 总统决定书（对外军援/裁减） | [Presidential Determination on Assistance to Venezuela Consistent With the Trafficking V…](https://www.federalregister.gov/documents/2026/07/06/2026-13631/presidential-determination-on-assistance-to-venezuela-consistent-with-the-trafficking-victims) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-07-28 | 总统决定书（对外军援/裁减） | [Presidential Determination on the Proposed Agreement for Cooperation Between the Govern…](https://www.federalregister.gov/documents/2026/07/28/2026-15273/presidential-determination-on-the-proposed-agreement-for-cooperation-between-the-government-of-the) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-08-04 | 国防生产法(DPA)授权 | [Presidential Determination Pursuant to Section 101 of the Defense Production Act of 195…](https://www.federalregister.gov/documents/2026/08/04/2026-15859/presidential-determination-pursuant-to-section-101-of-the-defense-production-act-of-1950-as-amended) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-08-26 | 总统决定书（对外军援/裁减） | [Presidential Determination on Provision of Atomic Information to Finland and Sweden](https://www.federalregister.gov/documents/2026/08/26/2026-17477/presidential-determination-on-provision-of-atomic-information-to-finland-and-sweden) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-09-11 | 国防生产法(DPA)授权 | [Adjusting Certain Delegations Under the Defense Production Act](https://www.federalregister.gov/documents/2026/09/11/2026-18739/adjusting-certain-delegations-under-the-defense-production-act) | `T1` 🟢 原始记录/法律文件 | `federal-register` |
| 2026-09-15 | CBO/官方作战成本估算 | [Estimating the Cost of Combat Operations Against Iran](https://www.cbo.gov/publication/62756) | `T2` 🟡 机构估算与判断 | `cbo-reports` |

## 6. 加总一致性自检与已知偏差

**已知偏差与解读边界**

1. **9 月财年末效应（最容易踩的坑）**：美国财年 9 月 30 日结束，9 月的合同义务额会因「不用就作废」而例行暴涨。**拿 9 月与其他月比会把财年效应误读成战争强度。**
   本报告已把 9 月从「战前基线」中剔除；跨年比较请同月对同月。
2. **发布滞后**：USAspending 新签合同有 1–3 个月滞后，最近月份的义务额偏低甚至为 0 属正常。**不得**把最新月读成「补货停了」。
3. **口径不同不可加总**：第 1 节（实际支出 outlay）与第 2 节（合同义务 obligation）不可相加，也不可直接比大小 —— 一个月的义务可能在未来数个季度才出账。
4. **监测指标是启发式**：第 4 节的计数来自本监测自身收录，**收录覆盖度变化会造成跳变**，某月数字下降可能只是某源当月无更新。
5. **库存无绝对数量**：公开渠道**不存在**弹药库存的绝对数量披露。库存只能从官方报告的相对表述（如「已消耗三分之二」）间接推断，与第 1、2 节的一手权威金额不在同一可信层级。见日报附录 D。
6. **月度序列可加总核对**：第 1、2 节的月度值来自与日报头条**同一接口、同一口径**的月度分组视图，两者必须逐分相等（见上表）；
   本报告特意用**两个不同接口**互相验证，而不是用同一个接口自证。
7. **历史序列在首次运行时靠 `--backfill` 全量建立**；之后每日运行只刷新最近月份。若某月从未被拉取过（例如中途才接入某个源），该月会显示为空而不是 0 —— **空 ≠ 零**，不要当成「没有发生」。

---

## 附录：序列清单与溯源

| 指标 | 口径 | 单位 | 来源源 id | 等级 | 可回源程度 | 性质 |
|---|---|---|---|---|---|---|
| `fr::Defense Production Act` | 联邦公报该月匹配「国防生产法(DPA)」的文档数（官方 count） | count | `federal-register` | `T1` 🟢 原始记录/法律文件 | full | 官方数据 |
| `fr::munitions` | 联邦公报该月匹配「弹药/军贸」的文档数（官方 count） | count | `federal-register` | `T1` 🟢 原始记录/法律文件 | full | 官方数据 |
| `fr::emergency supplemental appropriations` | 联邦公报该月匹配「紧急追加拨款」的文档数（官方 count） | count | `federal-register` | `T1` 🟢 原始记录/法律文件 | full | 官方数据 |
| `monitor::signal_sustain` | 本监测收录记录中命中该信号桶的条数（**启发式**：受收录覆盖度影响，非官方统计） | count | `monitor` | — | — | 启发式指标 |
| `monitor::signal_terminate` | 本监测收录记录中命中该信号桶的条数（**启发式**：受收录覆盖度影响，非官方统计） | count | `monitor` | — | — | 启发式指标 |
| `monitor::signal_stress` | 本监测收录记录中命中该信号桶的条数（**启发式**：受收录覆盖度影响，非官方统计） | count | `monitor` | — | — | 启发式指标 |
| `monitor::records_total` | 本监测该月收录的记录总数（**启发式**：覆盖度指标，非官方统计） | count | `monitor` | — | — | 启发式指标 |
