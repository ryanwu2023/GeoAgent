# 战争走向证据板 · 2026-09-18

> 本文件是 `spend_monitor.py` 输出的**证据索引**，不是预测。
> 用法：把每桶的原文读一遍，人工判断语境，再形成自己的结论。

**净值 +6 → 温和偏向持续**

> **信源等级**：`T1` 官方一手（可回源对账）· `T2` 官方分析/审计（估算）· `T3` 商业媒体（二手转述，须回溯原文）。等级看的是**这份材料由谁加工**，不是它引用了谁 —— 转述 CBO 的媒体报道仍是 T3。

### 信号桶 × 信源等级

| 信号桶 | T1 官方一手 | T2 官方分析 | T3 媒体 | 桶内最高等级 | 评价 |
|---|---|---|---|---|---|
| 🔴 持续/升级 | 2 篇 | 0 篇 | 3 篇 | `T1` 🟢 | 🔸 官方与媒体混合，分项引用时注意区分 |
| 🟢 降级/结束 | 1 篇 | 0 篇 | 0 篇 | `T1` 🟢 | ✅ 全部来自官方源 |
| 🟠 产能/库存压力 | 0 篇 | 0 篇 | 3 篇 | `T3` 🟠 | ⚠️ **全部来自二手媒体，引用前须回溯原文** |

## 🔴 持续/升级（5 篇 / 加权 14）

信源等级分布：`T1` 2 篇、`T3` 3 篇

| 短语 | 篇数 | 等级 | 例证 |
|---|---|---|---|
| `sustainment` | 2 | `T3` 🟠 二手转述 | [Pentagon, Lockheed sign framework deal to boost AIM-260 production](https://www.defensenews.com/news/pentagon-congress/2026/09/17/pentagon-lockheed-sign-framework-deal-to-boost-aim-260-production/) |
| `expand production` | 1 | `T3` 🟠 二手转述 | [Pentagon, Lockheed sign framework deal to boost AIM-260 production](https://www.defensenews.com/news/pentagon-congress/2026/09/17/pentagon-lockheed-sign-framework-deal-to-boost-aim-260-production/) |
| `supplemental funding` | 1 | `T3` 🟠 二手转述 | [Iran war has cost $38 billion, depleted two-thirds of US missile inte…](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `Defense Production Act` | 1 | `T1` 🟢 原始记录/法律文件 | [Adjusting Certain Delegations Under the Defense Production Act](https://www.federalregister.gov/documents/2026/09/11/2026-18739/adjusting-certain-delegations-under-the-defense-production-act) |
| `Presidential Determination` | 1 | `T1` 🟢 原始记录/法律文件 | [Presidential Determination on Provision of Atomic Information to Finl…](https://www.federalregister.gov/documents/2026/08/26/2026-17477/presidential-determination-on-provision-of-atomic-information-to-finland-and-sweden) |

## 🟢 降级/结束（1 篇 / 加权 3）

信源等级分布：`T1` 1 篇

| 短语 | 篇数 | 等级 | 例证 |
|---|---|---|---|
| `waiver of sanctions` | 1 | `T1` 🟢 原始记录/法律文件 | [Waiver of Sanctions on Syria Under the Chemical and Biological Weapon…](https://www.federalregister.gov/documents/2026/09/16/2026-18918/waiver-of-sanctions-on-syria-under-the-chemical-and-biological-weapons-control-and-warfare) |

## 🟠 产能/库存压力（3 篇 / 加权 9）

信源等级分布：`T3` 3 篇

> ⚠️ 本桶**全部来自 T3 二手转述**，无官方原始出处直连 —— 引用前必须回溯其援引的原文。

| 短语 | 篇数 | 等级 | 例证 |
|---|---|---|---|
| `depleted` | 2 | `T3` 🟠 二手转述 | [Iran war has cost $38 billion, depleted two-thirds of US missile inte…](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `stockpile` | 1 | `T3` 🟠 二手转述 | [US Navy should target carriers in fight with China, report says](https://www.defensenews.com/news/your-military/2026/09/17/us-navy-should-target-chinese-aircraft-carriers-report-says/) |
| `fewer weapons available` | 1 | `T3` 🟠 二手转述 | [Iran war has cost $38 billion, depleted two-thirds of US missile inte…](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `rebuilding those stocks` | 1 | `T3` 🟠 二手转述 | [Iran war has cost $38 billion, depleted two-thirds of US missile inte…](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `of the u.s. inventory` | 1 | `T3` 🟠 二手转述 | [Iran war has cost $38 billion, depleted two-thirds of US missile inte…](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |
| `depletion` | 1 | `T3` 🟠 二手转述 | [Iran war has cost $38 billion, depleted two-thirds of US missile inte…](https://www.defensenews.com/news/your-military/2026/09/15/iran-war-has-cost-38-billion-depleted-two-thirds-of-us-missile-interceptors-cbo-says/) |

## 观察点

- **弹药产能与库存瓶颈**：GAO 报告中出现 `stockpile` / `production rate` / `lead time` 的条目。若产能扩张授权（DPA §101）持续出现但 GAO 仍报产能不足，说明消耗速度仍高于补充速度。
- **新钱与扩产授权**：联邦公报中的 Presidential Determination / 紧急追加拨款 / DPA 优先权命令。这类文件是「还能继续打」最直接的法律与资金开关。
- **采购支出的环比拐点**：财政部 MTS 中 `采购` 科目月度净支出的环比。连续两个月回落通常先于政策转向；单月跳升多为大额合同集中出账。
- **弹药类合同金额分布**：USAspending 中 PSC 1410/1420/1340 类新签合同。注意该数据有 1–3 个月发布滞后，不能把「本周无新合同」读成「停止采购」。
- **降级信号需验证约束力**：区分「正式协议/法律文件」与「单方表态/媒体转述」。只有前者才真正改变资金流的走向。
- **证伪条件**：若出现①紧急追加拨款被否决或撤销；②采购科目连续两季度下滑；③合约终止/缩减公告增多；④兵力回撤与基地收缩 —— 则应下调「持续」判断。
