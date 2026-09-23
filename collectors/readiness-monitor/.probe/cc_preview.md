# 交叉验证明细

> 生成时间：2026-09-18 14:06:37 中国标准时间　·　版本 `v1.4.0`

## 配对口径

- 配对条件：同一**议题锚点** + 发布周相差不超过 7 天（按 ISO 周归组）
- 判定为机械规则，**不做语义一致性判断**：
  - `contradiction` 🔴 一方宣称行动效果、另一方在同周否认
  - `mutual_assert` 🟠 双方同周均宣称行动效果（**不等于**说的是同一件事）
  - `mutual_denial` 🟠 双方同周均是否认
  - `both_mentioned` 🟡 双方同周都提到该议题（**不等于**说法一致）
  - `us_only` / `iran_only` 仅单方提及 —— 单方声明在获印证前不可作为事实

## 全部同期对照组

### [MQ-9 / 无人机] 2026-W37 · `both_mentioned`

**判定依据**（程序按词表机械抽取，请逐条核对原文）：

- _伊方宣称有行动效果_
  - T1🟢 Pars Today（伊朗国家广播 IRIB 对外台） · `action_claim` — [US drone destroyed by IRGC Navy fire in Strait of Hormuz + Video](https://parstoday.ir/en/news/iran-i246024-us_drone_destroyed_by_irgc_navy_fire_in_strait_of_hormuz_video)

**美方**

- T1🟢 · 美国国防部（五角大楼）
  - [DOW Hosts 2nd Interagency Summit to Strengthen Counter-Drone Defense](https://www.war.gov/News/News-Stories/Article/Article/4594128/dow-hosts-2nd-interagency-summit-to-strengthen-counter-drone-defense/)
  - 原文片段（`counter-drone defense`）：DOW Hosts 2nd Interagency Summit to Strengthen Counter-Drone Defense . Joint Interagency Task Force 401 recently hosted its second interag…

**伊方**

- T1🟢 · Pars Today（伊朗国家广播 IRIB 对外台）
  - [US drone destroyed by IRGC Navy fire in Strait of Hormuz + Video](https://parstoday.ir/en/news/iran-i246024-us_drone_destroyed_by_irgc_navy_fire_in_strait_of_hormuz_video)
  - 原文片段（`destroy`）：US drone destroyed by IRGC Navy fire in Strait of Hormuz + Video . Pars Today- The Islam…

**第三方旁证**

- T3🟠 · 美国海军学会新闻（USNI News）
  - [Marines Help Philippine Armed Forces Develop Strike Plans for BrahMos Anti-Ship Missile](https://news.usni.org/2026/09/08/marines-help-philippine-armed-forces-develop-strike-plans-for-brahmos-anti-ship-missile)
  - 原文片段（`drill/strike`）：…s learned. According to images recently released by the Pentagon, the drills practiced the process of forming a kill chain using notional Armed Fo…

### [军舰] 2026-W37 · `contradiction`

**判定依据**（程序按词表机械抽取，请逐条核对原文）：

- _美方宣称有行动效果_
  - T1🟢 美国中央司令部公共事务办公室（DVIDS） · `action_claim` — [George Washington Conducts Flight Operations](https://www.dvidshub.net/video/1021582/george-washington-conducts-flight-operations)
  - T1🟢 美国中央司令部公共事务办公室（DVIDS） · `action_claim` — [USS Boxer, VMFA 122 Conduct Flight Operations](https://www.dvidshub.net/video/1021551/uss-boxer-vmfa-122-conduct-flight-operations)
  - T1🟢 美国中央司令部公共事务办公室（DVIDS） · `action_claim` — [U.S. Destroys 5 IRGC Tankers After Iran Targets Another American Warship](https://www.dvidshub.net/video/1022155/us-destroys-5-irgc-tankers-after-iran-targets-another-american-warship)
- _第三方转述的**替美方否认伊方**_
  - T3🟠 伊朗流亡/反对派媒体 · `action_claim,denial` — [CENTCOM Denies IRGC Claims of Striking Two U.S. Destroyers](https://news.google.com/rss/articles/CBMimAFBVV95cUxNcVU2RkNCNHpIZWJCLTMxM2p1NTAtcXhvS29WREc5QWp1bG0wajZaa1prQTRTc05SczE3LW8yaUhUVkg0dHJkcEpGWXNYUVcxdHNMajg0NE5ZVUxVOXRsMmRqQ3F6QUNRWkFHb0ptVXhTbTNXbERzU3FJMktyOG1WaUFsZnVHM1FuaHZoenFUSW1GMk9EdWlnMw?oc=5)
  - T3🟠 AzerNews · `action_claim,denial` — [CENTCOM rejects Iranian claim that IRGC struck two US Navy destroyers](https://news.google.com/rss/articles/CBMiVEFVX3lxTE9rbmpZWC1WX3RnODNPY0g4X3QtV1BxbWZILUhpRmJjcWNqQVcxWFlaZzdBTGdINUNTUWhnYW9BQ3lyVFpZZXIzZU1OdUxjWjFENjkxSA?oc=5)
- _伊方宣称有行动效果_
  - T3🟠 Tasnim News Agency（تسنیم，革命卫队系） · `action_claim` — [American Warships Struck with Powerfull Ballistic Missiles: IRGC](https://news.google.com/rss/articles/CBMitwFBVV95cUxOOTdnR2hxdVNaSXRCRmFuampFQ19yelRCVEt3b1hDMmRuMXk3THJycjh6aW9WQmp3dWFGSjNodzdYZWt4c2lSLXd6R1NCX2owVVlOOC1XdzVjdElEZUZwQXZocmhOZUpDZWhjclVCT0RHek82Q0FkOWJtM2owSGhwMkJtYmdTeVdwVV9YaEZCWjBxY0pPMkxsM1Z6eXgyZFd0Z0dNbDZ6RE1Pd0ZPYmd4bDFmdHgyREXSAbwBQVVfeXFMT1lESzQtVzFkRjBONDVfckhPVFZrbFp2ZnFMT1l3d0RoUldnMnQwcm9DYzJZTVVObkhxQzdOSlZQT1RJVlVVUGV6N0hyTG9xQ3g1N3phWE9kS0wwanZ5eDJiTFBfY09iTXVMeUJPaFRudzlVRkJ3MlNhajRSbGhpSW90dGhHeFVLSndQa245Y1A5SE9BSjB2QktzdEpvSmxaQ00zcFpYZDEyclBYTm1wUUtoMzJrQjdHVzNadTc?oc=5)

**美方**

- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [George Washington Conducts Flight Operations](https://www.dvidshub.net/video/1021582/george-washington-conducts-flight-operations)
  - 原文片段（`deployed to/strike`）：…ier USS George Washington (CVN 73), Sep 2, 2026. George Washington is deployed to the U.S. 5th Fleet area of operations to support maritime security an…
- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [USS Boxer, VMFA 122 Conduct Flight Operations](https://www.dvidshub.net/video/1021551/uss-boxer-vmfa-122-conduct-flight-operations)
  - 原文片段（`deployed to`）：…p USS Boxer (LHD 4) during flight operations, Sept. 1, 2026. Boxer is deployed to the U.S. 5th Fleet area of operations to support maritime security an…
- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [U.S. Destroys 5 IRGC Tankers After Iran Targets Another American Warship](https://www.dvidshub.net/video/1022155/us-destroys-5-irgc-tankers-after-iran-targets-another-american-warship)
  - 原文片段（`destroy`）：U.S. Destroys 5 IRGC Tankers After Iran Targets Another American Warship . U.S. Cen…

**伊方**

- T3🟠 · Tasnim News Agency（تسنیم，革命卫队系）
  - [American Warships Struck with Powerfull Ballistic Missiles: IRGC](https://news.google.com/rss/articles/CBMitwFBVV95cUxOOTdnR2hxdVNaSXRCRmFuampFQ19yelRCVEt3b1hDMmRuMXk3THJycjh6aW9WQmp3dWFGSjNodzdYZWt4c2lSLXd6R1NCX2owVVlOOC1XdzVjdElEZUZwQXZocmhOZUpDZWhjclVCT0RHek82Q0FkOWJtM2owSGhwMkJtYmdTeVdwVV9YaEZCWjBxY0pPMkxsM1Z6eXgyZFd0Z0dNbDZ6RE1Pd0ZPYmd4bDFmdHgyREXSAbwBQVVfeXFMT1lESzQtVzFkRjBONDVfckhPVFZrbFp2ZnFMT1l3d0RoUldnMnQwcm9DYzJZTVVObkhxQzdOSlZQT1RJVlVVUGV6N0hyTG9xQ3g1N3phWE9kS0wwanZ5eDJiTFBfY09iTXVMeUJPaFRudzlVRkJ3MlNhajRSbGhpSW90dGhHeFVLSndQa245Y1A5SE9BSjB2QktzdEpvSmxaQ00zcFpYZDEyclBYTm1wUUtoMzJrQjdHVzNadTc?oc=5)
  - 原文片段（`struck`）：American Warships Struck with Powerfull Ballistic Missiles: IRGC . <a href="https://news.googl…

**第三方旁证**

- T3🟠 · 美国海军学会新闻（USNI News）
  - [Houthis Make Moves On Red Sea, U.S., Iran Go Tanker for Warship](https://news.usni.org/2026/09/11/houthis-make-moves-on-red-sea-u-s-iran-go-tanker-for-warship)
  - 原文片段（`strike`）：…ngton establishing that for any warship to come under attack, it will strike an Iranian tanker. It is not immediately clear what the Houthis’ move…
- T3🟠 · 美国海军学会新闻（USNI News）
  - [USNI News Western Pacific Pulse: Sept. 11, 2026](https://news.usni.org/2026/09/11/usni-news-western-pacific-pulse-sept-11-2026)
  - 原文片段（`exercise`）：…pt. 11, 2026 . The following is a summary of major ship movements and exercises in the Western Pacific over the last week. In the South China Sea Air…
- T3🟠 · 美国海军学会新闻（USNI News）
  - [Combat Patrol – Scarborough Shoal](https://news.usni.org/2026/09/11/combat-patrol-scarborough-shoal)
  - 原文片段（`forward-deployed/drill`）：…yan Dao,” the People’s Liberation Army Southern Theater Command’s has forward-deployed new light combat aircraft and advanced air defense frigates to the So…

### [军舰] 2026-W38 · `both_mentioned`

**判定依据**（程序按词表机械抽取，请逐条核对原文）：

- _伊方宣称有行动效果_
  - T3🟠 Fars News Agency（فارس，革命卫队系） · `action_claim` — [Farsnews | Armed Forces Deputy Chief: Iranian Missiles Struck, Seriously Damaged US Warships](https://news.google.com/rss/articles/CBMiygFBVV95cUxPdUk3cE9JT296ZTZrUi02NEY0aWw3NndrWlRoaFZ0U3ZST2d2THBsUWVVVTRZeE1yVmVreFl0T082b0xTNG1JX3d3cElKclZsYWJ0Vm8tMmhoTXNuZ1Bkd05yc3ZoTTdqSnhjQWFsWEV0WFZoOXVjbEp6UFdldEFTbmROVzVubXZsMG8yVEFrbEJTQzJ1ZlFIZDlDeFNLeFBJUUtES0pSQkdwQmxvUjNtR1dEU0dOcWhnaFJGaEpWbHF6a2FsYnhrQkp3?oc=5)

**美方**

- T1🟢 · 美国国防部（五角大楼）
  - [Hegseth, German Counterpart Agree to Advance Defense Industrial Cooperation](https://www.war.gov/News/News-Stories/Article/Article/4602094/hegseth-german-counterpart-agree-to-advance-defense-industrial-cooperation/)
  - 原文片段（`defense industrial`）：Hegseth, German Counterpart Agree to Advance Defense Industrial Cooperation . Secretary of War Pete Hegseth and Boris Pistorius, Germ…

**伊方**

- T3🟠 · Fars News Agency（فارس，革命卫队系）
  - [Farsnews | Armed Forces Deputy Chief: Iranian Missiles Struck, Seriously Damaged US Warships](https://news.google.com/rss/articles/CBMiygFBVV95cUxPdUk3cE9JT296ZTZrUi02NEY0aWw3NndrWlRoaFZ0U3ZST2d2THBsUWVVVTRZeE1yVmVreFl0T082b0xTNG1JX3d3cElKclZsYWJ0Vm8tMmhoTXNuZ1Bkd05yc3ZoTTdqSnhjQWFsWEV0WFZoOXVjbEp6UFdldEFTbmROVzVubXZsMG8yVEFrbEJTQzJ1ZlFIZDlDeFNLeFBJUUtES0pSQkdwQmxvUjNtR1dEU0dOcWhnaFJGaEpWbHF6a2FsYnhrQkp3?oc=5)
  - 原文片段（`struck`）：Farsnews | Armed Forces Deputy Chief: Iranian Missiles Struck, Seriously Damaged US Warships . <a href="https://news.google.com/rss…

**第三方旁证**

- T3🟠 · Al Jazeera English
  - [Italy to deploy warships to protect shipping through Bab al-Mandeb](https://www.aljazeera.com/economy/2026/9/18/italy-to-deploy-warships-to-protect-shipping-through-bab-al-mandeb?traffic_source=rss)
  - 原文片段（`deploy`）：Italy to deploy warships to protect shipping through Bab al-Mandeb . Italy's defence…
- T3🟠 · 美国海军学会新闻（USNI News）
  - [Navy Wants Containerized, Low-cost Interceptors to Defeat Cruise Missiles, Drones](https://news.usni.org/2026/09/17/navy-wants-containerized-low-cost-interceptors-to-defeat-cruise-missiles-drones)
  - 原文片段（`deploy/interceptor`）：…terceptors to Defeat Cruise Missiles, Drones . The U.S. Navy wants to deploy containerized low-cost interceptors to defeat cruise missiles and att…
- T3🟠 · 美国海军学会新闻（USNI News）
  - [Carrier USS Abraham Lincoln Operates with Australians in the South China Sea](https://news.usni.org/2026/09/15/carrier-uss-abraham-lincoln-operates-with-australians-in-the-south-china-sea)
  - 原文片段（`carrier strike group/strike`）：…Perth (FFH157) and HMAS Stuart (FFH153) supported the Abraham Lincoln Carrier Strike Group during its transit of the South China Sea in accordance with internat…

### [导弹] 2026-W37 · `mutual_assert`

**判定依据**（程序按词表机械抽取，请逐条核对原文）：

- _美方宣称有行动效果_
  - T1🟢 美国中央司令部公共事务办公室（DVIDS） · `action_claim` — [U.S. Destroys 5 IRGC Tankers After Iran Targets Another American Warship](https://www.dvidshub.net/video/1022155/us-destroys-5-irgc-tankers-after-iran-targets-another-american-warship)
- _伊方宣称有行动效果_
  - T3🟠 Tasnim News Agency（تسنیم，革命卫队系） · `action_claim` — [American Warships Struck with Powerfull Ballistic Missiles: IRGC](https://news.google.com/rss/articles/CBMitwFBVV95cUxOOTdnR2hxdVNaSXRCRmFuampFQ19yelRCVEt3b1hDMmRuMXk3THJycjh6aW9WQmp3dWFGSjNodzdYZWt4c2lSLXd6R1NCX2owVVlOOC1XdzVjdElEZUZwQXZocmhOZUpDZWhjclVCT0RHek82Q0FkOWJtM2owSGhwMkJtYmdTeVdwVV9YaEZCWjBxY0pPMkxsM1Z6eXgyZFd0Z0dNbDZ6RE1Pd0ZPYmd4bDFmdHgyREXSAbwBQVVfeXFMT1lESzQtVzFkRjBONDVfckhPVFZrbFp2ZnFMT1l3d0RoUldnMnQwcm9DYzJZTVVObkhxQzdOSlZQT1RJVlVVUGV6N0hyTG9xQ3g1N3phWE9kS0wwanZ5eDJiTFBfY09iTXVMeUJPaFRudzlVRkJ3MlNhajRSbGhpSW90dGhHeFVLSndQa245Y1A5SE9BSjB2QktzdEpvSmxaQ00zcFpYZDEyclBYTm1wUUtoMzJrQjdHVzNadTc?oc=5)
  - T1🟢 Pars Today（伊朗国家广播 IRIB 对外台） · `action_claim,threat_warning` — [IRGC carries out retaliatory strike on U.S. fighter jet hangars](https://parstoday.ir/en/news/iran-i245980-irgc_carries_out_retaliatory_strike_on_u.s._fighter_jet_hangars)

**美方**

- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [U.S. Destroys 5 IRGC Tankers After Iran Targets Another American Warship](https://www.dvidshub.net/video/1022155/us-destroys-5-irgc-tankers-after-iran-targets-another-american-warship)
  - 原文片段（`destroy`）：U.S. Destroys 5 IRGC Tankers After Iran Targets Another American Warship . U.S. Cen…

**伊方**

- T3🟠 · Tasnim News Agency（تسنیم，革命卫队系）
  - [American Warships Struck with Powerfull Ballistic Missiles: IRGC](https://news.google.com/rss/articles/CBMitwFBVV95cUxOOTdnR2hxdVNaSXRCRmFuampFQ19yelRCVEt3b1hDMmRuMXk3THJycjh6aW9WQmp3dWFGSjNodzdYZWt4c2lSLXd6R1NCX2owVVlOOC1XdzVjdElEZUZwQXZocmhOZUpDZWhjclVCT0RHek82Q0FkOWJtM2owSGhwMkJtYmdTeVdwVV9YaEZCWjBxY0pPMkxsM1Z6eXgyZFd0Z0dNbDZ6RE1Pd0ZPYmd4bDFmdHgyREXSAbwBQVVfeXFMT1lESzQtVzFkRjBONDVfckhPVFZrbFp2ZnFMT1l3d0RoUldnMnQwcm9DYzJZTVVObkhxQzdOSlZQT1RJVlVVUGV6N0hyTG9xQ3g1N3phWE9kS0wwanZ5eDJiTFBfY09iTXVMeUJPaFRudzlVRkJ3MlNhajRSbGhpSW90dGhHeFVLSndQa245Y1A5SE9BSjB2QktzdEpvSmxaQ00zcFpYZDEyclBYTm1wUUtoMzJrQjdHVzNadTc?oc=5)
  - 原文片段（`struck`）：American Warships Struck with Powerfull Ballistic Missiles: IRGC . <a href="https://news.googl…
- T1🟢 · Pars Today（伊朗国家广播 IRIB 对外台）
  - [IRGC carries out retaliatory strike on U.S. fighter jet hangars](https://parstoday.ir/en/news/iran-i245980-irgc_carries_out_retaliatory_strike_on_u.s._fighter_jet_hangars)
  - 原文片段（`strike`）：IRGC carries out retaliatory strike on U.S. fighter jet hangars . Pars Today- The Public Relations Depart…

**第三方旁证**

- T3🟠 · 美国海军学会新闻（USNI News）
  - [Sailors, Marines Deploy Long-range Missile Systems to Norway for Drills](https://news.usni.org/2026/09/10/sailors-marines-deploy-long-range-missile-systems-to-norway-for-drills)
  - 原文片段（`deploy/drill/strike`）：Sailors, Marines Deploy Long-range Missile Systems to Norway for Drills . The U.S. Navy and M…
- T3🟠 · 美国海军学会新闻（USNI News）
  - [Marines Help Philippine Armed Forces Develop Strike Plans for BrahMos Anti-Ship Missile](https://news.usni.org/2026/09/08/marines-help-philippine-armed-forces-develop-strike-plans-for-brahmos-anti-ship-missile)
  - 原文片段（`drill/strike`）：…s learned. According to images recently released by the Pentagon, the drills practiced the process of forming a kill chain using notional Armed Fo…

### [导弹] 2026-W38 · `both_mentioned`

**判定依据**（程序按词表机械抽取，请逐条核对原文）：

- _第三方转述的**替美方否认伊方**_
  - T3🟠 France 24（中东） · `action_claim,denial` — ['Axis of Iranian centrality' remains "one of more destabilising forces in the region", expert s…](https://www.france24.com/en/axis-of-iranian-centrality-remains-one-of-more-destabilising-forces-in-the-region-expert-says)
- _伊方宣称有行动效果_
  - T3🟠 Fars News Agency（فارس，革命卫队系） · `action_claim` — [Farsnews | Armed Forces Deputy Chief: Iranian Missiles Struck, Seriously Damaged US Warships](https://news.google.com/rss/articles/CBMiygFBVV95cUxPdUk3cE9JT296ZTZrUi02NEY0aWw3NndrWlRoaFZ0U3ZST2d2THBsUWVVVTRZeE1yVmVreFl0T082b0xTNG1JX3d3cElKclZsYWJ0Vm8tMmhoTXNuZ1Bkd05yc3ZoTTdqSnhjQWFsWEV0WFZoOXVjbEp6UFdldEFTbmROVzVubXZsMG8yVEFrbEJTQzJ1ZlFIZDlDeFNLeFBJUUtES0pSQkdwQmxvUjNtR1dEU0dOcWhnaFJGaEpWbHF6a2FsYnhrQkp3?oc=5)
  - T2🟡 Tehran Times（伊朗官方英文日报） · `action_claim` — [Leaked photos and munition depletion expose devastating toll of Iranian strikes on US bases](https://www.tehrantimes.com/news/530104/Leaked-photos-and-munition-depletion-expose-devastating-toll)
  - T2🟡 ISNA（伊朗学生通讯社） · `action_claim,threat_warning` — [New photos reveal Iranian missile, drone strikes inflicted extensive damage to US targets acros…](https://en.isna.ir/news/1405062517058/New-photos-reveal-Iranian-missile-drone-strikes-inflicted-extensive)

**美方**

- T1🟢 · 美国国防部（五角大楼）
  - [Department of War Signs Framework Agreement With Lockheed Martin to Increase Production of AIM-260 …](https://www.war.gov/News/Releases/Release/Article/4603380/department-of-war-signs-framework-agreement-with-lockheed-martin-to-increase-pr/)
  - 原文片段（`sale + missile/increase production`）：…ith Lockheed Martin to Increase Production of AIM-260 JATM, Boost FMS Sales . The War Department announced a new framework agreement to expand production capacity of critical next generation air-to-air missiles, the AIM-260 Joint Advanced…

**伊方**

- T3🟠 · Fars News Agency（فارس，革命卫队系）
  - [Farsnews | Armed Forces Deputy Chief: Iranian Missiles Struck, Seriously Damaged US Warships](https://news.google.com/rss/articles/CBMiygFBVV95cUxPdUk3cE9JT296ZTZrUi02NEY0aWw3NndrWlRoaFZ0U3ZST2d2THBsUWVVVTRZeE1yVmVreFl0T082b0xTNG1JX3d3cElKclZsYWJ0Vm8tMmhoTXNuZ1Bkd05yc3ZoTTdqSnhjQWFsWEV0WFZoOXVjbEp6UFdldEFTbmROVzVubXZsMG8yVEFrbEJTQzJ1ZlFIZDlDeFNLeFBJUUtES0pSQkdwQmxvUjNtR1dEU0dOcWhnaFJGaEpWbHF6a2FsYnhrQkp3?oc=5)
  - 原文片段（`struck`）：Farsnews | Armed Forces Deputy Chief: Iranian Missiles Struck, Seriously Damaged US Warships . <a href="https://news.google.com/rss…
- T2🟡 · Tehran Times（伊朗官方英文日报）
  - [Leaked photos and munition depletion expose devastating toll of Iranian strikes on US bases](https://www.tehrantimes.com/news/530104/Leaked-photos-and-munition-depletion-expose-devastating-toll)
  - 原文片段（`strike`）：…aked photos and munition depletion expose devastating toll of Iranian strikes on US bases . TEHRAN – A mounting body of leaked evidence and admissi…
- T1🟢 · IRNA（伊朗伊斯兰共和国通讯社，官方）
  - [Army Ground Force chief hails Iran’s strategic weapons capabilities](https://en.irna.ir/news/86265802/Army-Ground-Force-chief-hails-Iran-s-strategic-weapons-capabilities)
  - 原文片段（`combat readiness`）：…’s Armed Forces are currently more prepared in terms of capabilities, combat readiness, and operational reserves than they were before the US-Israeli war on…
- T2🟡 · ISNA（伊朗学生通讯社）
  - [New photos reveal Iranian missile, drone strikes inflicted extensive damage to US targets across We…](https://en.isna.ir/news/1405062517058/New-photos-reveal-Iranian-missile-drone-strikes-inflicted-extensive)
  - 原文片段（`strike`）：New photos reveal Iranian missile, drone strikes inflicted extensive damage to US targets across West Asia . Newly rev…

**第三方旁证**

- T3🟠 · Air & Space Forces Magazine
  - [Pentagon, Lockheed to Expand Production of Secretive New Joint Advanced Tactical Missile](https://www.airandspaceforces.com/pentagon-lockheed-martin-joint-advanced-tactical-missile-production/)
  - 原文片段（`expand production`）：Pentagon, Lockheed to Expand Production of Secretive New Joint Advanced Tactical Missile . The Pentagon annou…
- T3🟠 · 美国海军学会新闻（USNI News）
  - [Navy Wants Containerized, Low-cost Interceptors to Defeat Cruise Missiles, Drones](https://news.usni.org/2026/09/17/navy-wants-containerized-low-cost-interceptors-to-defeat-cruise-missiles-drones)
  - 原文片段（`deploy/interceptor`）：…terceptors to Defeat Cruise Missiles, Drones . The U.S. Navy wants to deploy containerized low-cost interceptors to defeat cruise missiles and att…
- T3🟠 · Breaking Defense
  - [Lockheed reaches deal to boost production of secretive JATM missile](https://breakingdefense.com/2026/09/lockheed-reaches-deal-to-boost-production-of-secretive-jatm-missile/)
  - 原文片段（`boost production`）：Lockheed reaches deal to boost production of secretive JATM missile . The announcement did not include informat…

### [演习] 2026-W38 · `both_mentioned`

**美方**

- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [Eager Lion 26](https://www.dvidshub.net/image/9944650/eager-lion-26)
  - 原文片段（`exercise`）：…l and unconventional scenarios during an Eager Lion 2026 command post exercise at Ft. Carson, Colo., Sept. 17, 2026. Eager Lion 2026, a biennial, bi…
- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [Eager Lion 2026 Command Post Exercise Training [Image 2 of 2]](https://www.dvidshub.net/image/9941227/eager-lion-2026-command-post-exercise-training)
  - 原文片段（`exercise`）：Eager Lion 2026 Command Post Exercise Training [Image 2 of 2] . Instructors provide training to U.S. milita…
- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [Eager Lion 2026 Cyber Training Exercise](https://www.dvidshub.net/image/9941222/eager-lion-2026-cyber-training-exercise)
  - 原文片段（`training exercise`）：Eager Lion 2026 Cyber Training Exercise . U.S. Military and Jordanian Armed Forces (JAF) personnel take part…
- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [CENTCOM Hosts Jordan for Exercise Eager Lion in Colorado](https://www.dvidshub.net/news/574840/centcom-hosts-jordan-exercise-eager-lion-colorado)
  - 原文片段（`exercise`）：CENTCOM Hosts Jordan for Exercise Eager Lion in Colorado . On Sept. 15, U.S. Central Command (CENTCOM)…

**伊方**

- T1🟢 · Mashregh News（مشرق، 与革命卫队关系密切）
  - [رزمایش ۳۱۳هزار نفری جان‌فدایان ایران آغاز شد](https://www.mashreghnews.ir/news/1839868/%D8%B1%D8%B2%D9%85%D8%A7%DB%8C%D8%B4-%DB%B3%DB%B1%DB%B3%D9%87%D8%B2%D8%A7%D8%B1-%D9%86%D9%81%D8%B1%DB%8C-%D8%AC%D8%A7%D9%86-%D9%81%D8%AF%D8%A7%DB%8C%D8%A7%D9%86-%D8%A7%DB%8C%D8%B1%D8%A7%D9%86-%D8%A2%D8%BA%D8%A7%D8%B2-%D8%B4%D8%AF)
  - 原文片段（`رزمایش`）：رزمایش 313هزار نفری جان فدایان ایران آغاز شد . رزمایش 313 هزار جانفدای ایران…
- T1🟢 · Defa Press（دفاع مقدس，伊朗军事/国防新闻社）
  - [رزمایش بزرگ جان‌فدای ایران آغاز شد](https://defapress.ir/fa/news/863163/رزمایش-بزرگ-جان-فدای-ایران-آغاز-شد)
  - 原文片段（`رزمایش`）：رزمایش بزرگ جان فدای ایران آغاز شد . با حضور سردار حسن زاده فرمانده سپاه محم…
- T1🟢 · Sepah News（伊朗伊斯兰革命卫队官方新闻社）
  - [دعوت مسئول دفتر نمایندگی ولی فقیه در سپاه تهران از مردم برای حضور در اجتماع بزرگ مردم جان فدای ایران](https://sepahnews.ir/fa/news/37750/دعوت-مسئول-دفتر-نمایندگی-ولی-فقیه-در-سپاه-تهران-از-مردم-برای-حضور-در-اجتماع-بزرگ-مردم-جان-فدای-ایران)
  - 原文片段（`رزمایش`）：…قیه در سپاه تهران بزرگ از عموم مردم و اقشار مختلف جامعه دعوت کردتا در رزمایش و اجتماع بزرگ مردمی «جان فدایان ایران» که روز جمعه 27 شهریور ماه جاری…
- T1🟢 · Sepah News（伊朗伊斯兰革命卫队官方新闻社）
  - [اجرای ۵۶ عنوان برنامه و بیش از ۱۳ هزار برنامه اجرایی در هفته دفاع مقدس/ خوزستان در آستانه رزمایش بز…](https://sepahnews.ir/fa/news/37741/اجرای-56-عنوان-برنامه-و-بیش-از-13-هزار-برنامه-اجرایی-در-هفته-دفاع-مقدس-خوزستان-در-آستانه-رزمایش-بزرگ-70-هزار-نفری)
  - 原文片段（`رزمایش`）：…ه و بیش از 13 هزار برنامه اجرایی در هفته دفاع مقدس/ خوزستان در آستانه رزمایش بزرگ 70 هزار نفری . فرمانده سپاه حضرت ولیعصر(عج) خوزستان از اجرای 56…

**第三方旁证**

- T3🟠 · Air & Space Forces Magazine
  - [F-16 Crashes During Training Exercise in Michigan; Pilot Ejects](https://www.airandspaceforces.com/f-16-crashes-during-training-exercise-in-michigan-pilot-ejects/)
  - 原文片段（`training exercise`）：F-16 Crashes During Training Exercise in Michigan; Pilot Ejects . An F-16 Fighting Falcon assigned to the T…
- T3🟠 · Air & Space Forces Magazine
  - [CCA Drones to Fly at Large-Scale Air Force Exercise in December](https://www.airandspaceforces.com/air-force-cca-drones-large-scale-exercise-fly-emerald-flag/)
  - 原文片段（`exercise`）：CCA Drones to Fly at Large-Scale Air Force Exercise in December . The Air Force’s new Collaborative Combat Aircraft will…
- T3🟠 · 美国海军学会新闻（USNI News）
  - [Defense Primer: Control and Command of the U.S. Military](https://news.usni.org/2026/09/17/defense-primer-control-and-command-of-the-u-s-military)
  - 原文片段（`exercise`）：…ontrol and Command of the U.S. Military. From the Report Congress has exercised oversight and a measure of control over the U.S. military, including…

### [战斗机] 2026-W37 · `mutual_assert`

**判定依据**（程序按词表机械抽取，请逐条核对原文）：

- _美方宣称有行动效果_
  - T1🟢 美国中央司令部公共事务办公室（DVIDS） · `action_claim` — [USS Boxer, VMFA 122 Conduct Flight Operations](https://www.dvidshub.net/video/1021551/uss-boxer-vmfa-122-conduct-flight-operations)
- _伊方宣称有行动效果_
  - T1🟢 Pars Today（伊朗国家广播 IRIB 对外台） · `action_claim,threat_warning` — [IRGC carries out retaliatory strike on U.S. fighter jet hangars](https://parstoday.ir/en/news/iran-i245980-irgc_carries_out_retaliatory_strike_on_u.s._fighter_jet_hangars)

**美方**

- T1🟢 · 美国中央司令部公共事务办公室（DVIDS）
  - [USS Boxer, VMFA 122 Conduct Flight Operations](https://www.dvidshub.net/video/1021551/uss-boxer-vmfa-122-conduct-flight-operations)
  - 原文片段（`deployed to`）：…p USS Boxer (LHD 4) during flight operations, Sept. 1, 2026. Boxer is deployed to the U.S. 5th Fleet area of operations to support maritime security an…

**伊方**

- T1🟢 · Pars Today（伊朗国家广播 IRIB 对外台）
  - [IRGC carries out retaliatory strike on U.S. fighter jet hangars](https://parstoday.ir/en/news/iran-i245980-irgc_carries_out_retaliatory_strike_on_u.s._fighter_jet_hangars)
  - 原文片段（`strike`）：IRGC carries out retaliatory strike on U.S. fighter jet hangars . Pars Today- The Public Relations Depart…

### [战斗机] 2026-W38 · `both_mentioned`

**判定依据**（程序按词表机械抽取，请逐条核对原文）：

- _伊方否认_
  - T2🟡 Tehran Times（伊朗官方英文日报） · `action_claim,denial` — [IRGC dismisses US account of F-15 pilot rescue as ‘Hollywood-style’ scenario](https://www.tehrantimes.com/news/530102/IRGC-dismisses-US-account-of-F-15-pilot-rescue-as-Hollywood-style)
- _伊方宣称有行动效果_
  - T2🟡 Tehran Times（伊朗官方英文日报） · `action_claim,denial` — [IRGC dismisses US account of F-15 pilot rescue as ‘Hollywood-style’ scenario](https://www.tehrantimes.com/news/530102/IRGC-dismisses-US-account-of-F-15-pilot-rescue-as-Hollywood-style)
  - T2🟡 Mehr News Agency（مهر） · `action_claim` — [VIDEO: Yemenis release footage of downed F-15 aircraft](https://en.mehrnews.com/news/247821/VIDEO-Yemenis-release-footage-of-downed-F-15-aircraft)
  - T3🟠 Tasnim News Agency（تسنیم，革命卫队系） · `action_claim` — [IRGC Ridicules Hollywood-Style US Account of Downed F-15 Rescue](https://news.google.com/rss/articles/CBMitwFBVV95cUxOMjNabjQzV2VtZGhWa0dUZXhNdFFwZk5tNHNJMnBZM0ZWTl9LMVhIWGpGSm9va1R5Wng5a1EwdzFTdkNjTlRPQkpDb1NNeHVrejNnek5OQlRmMUgwRXBvbGU1ejFnam1UX3NDSFFhY3U5ZjcyUWxNN2U0Q0Jta3JlbG1WVENmRzg4c2MxTEtqbHZIOGEyZld3MUljX3hsdGNMUnNBTnlUSk5mWWZaUFRSaHRCREdnN3PSAbwBQVVfeXFMTkxhZjZQMFhVcVJabVZ2ajJJRS1VVXB3M1FiamFXZGdhT2xwaVROSHhXcE1laWV6SDNhQk1oakZNNXFVQVQ3V2E3cGpQb3Z3eVg0TlF3cE9uREFSdldoeDF2SDljMnlVanZHM1U1Tnp4d2xDZVZMYmkxR1JMSVZzbC1JcldncS1FUzhiYWR2c0RJdUhCMjZSWlBMR19HSkM5ZWFMYmdOSEtObFFqRTZCbmVCUFBUdEJMdDYtZUc?oc=5)

**美方**

- T1🟢 · 美国国防部（五角大楼）
  - [NORAD F-16s Deploy to High North Under Operation Arctic Vanguard](https://www.war.gov/News/News-Stories/Article/Article/4604174/norad-f-16s-deploy-to-high-north-under-operation-arctic-vanguard/)
  - 原文片段（`deploy`）：NORAD F-16s Deploy to High North Under Operation Arctic Vanguard . The Alaskan North Ame…

**伊方**

- T2🟡 · ISNA（伊朗学生通讯社）
  - [US approves $24.3bn sale of 48 F-35 fighter jets to Saudi Arabia](https://en.isna.ir/news/1405062618017/US-approves-24-3bn-sale-of-48-F-35-fighter-jets-to-Saudi-Arabia)
  - 原文片段（`sale + jet`）：US approves $24.3bn sale of 48 F-35 fighter jets to Saudi Arabia . The US State Department has announced the approval…
- T2🟡 · Tehran Times（伊朗官方英文日报）
  - [IRGC dismisses US account of F-15 pilot rescue as ‘Hollywood-style’ scenario](https://www.tehrantimes.com/news/530102/IRGC-dismisses-US-account-of-F-15-pilot-rescue-as-Hollywood-style)
  - 原文片段（`downed`）：…Mohebi has dismissed the recent US account regarding the rescue of a downed F-15 fighter pilot in Iran as a “Hollywood-style” narrative, calling…
- T2🟡 · Mehr News Agency（مهر）
  - [VIDEO: Yemenis release footage of downed F-15 aircraft](https://en.mehrnews.com/news/247821/VIDEO-Yemenis-release-footage-of-downed-F-15-aircraft)
  - 原文片段（`downed`）：VIDEO: Yemenis release footage of downed F-15 aircraft . TEHRAN, Sep. 16 (MNA) – Yemeni military forces releas…
- T3🟠 · Tasnim News Agency（تسنیم，革命卫队系）
  - [IRGC Ridicules Hollywood-Style US Account of Downed F-15 Rescue](https://news.google.com/rss/articles/CBMitwFBVV95cUxOMjNabjQzV2VtZGhWa0dUZXhNdFFwZk5tNHNJMnBZM0ZWTl9LMVhIWGpGSm9va1R5Wng5a1EwdzFTdkNjTlRPQkpDb1NNeHVrejNnek5OQlRmMUgwRXBvbGU1ejFnam1UX3NDSFFhY3U5ZjcyUWxNN2U0Q0Jta3JlbG1WVENmRzg4c2MxTEtqbHZIOGEyZld3MUljX3hsdGNMUnNBTnlUSk5mWWZaUFRSaHRCREdnN3PSAbwBQVVfeXFMTkxhZjZQMFhVcVJabVZ2ajJJRS1VVXB3M1FiamFXZGdhT2xwaVROSHhXcE1laWV6SDNhQk1oakZNNXFVQVQ3V2E3cGpQb3Z3eVg0TlF3cE9uREFSdldoeDF2SDljMnlVanZHM1U1Tnp4d2xDZVZMYmkxR1JMSVZzbC1JcldncS1FUzhiYWR2c0RJdUhCMjZSWlBMR19HSkM5ZWFMYmdOSEtObFFqRTZCbmVCUFBUdEJMdDYtZUc?oc=5)
  - 原文片段（`downed`）：IRGC Ridicules Hollywood-Style US Account of Downed F-15 Rescue . <a href="https://news.google.com/rss/articles/CBMitwFBV…

**第三方旁证**

- T3🟠 · Air & Space Forces Magazine
  - [F-16 Crashes During Training Exercise in Michigan; Pilot Ejects](https://www.airandspaceforces.com/f-16-crashes-during-training-exercise-in-michigan-pilot-ejects/)
  - 原文片段（`training exercise`）：F-16 Crashes During Training Exercise in Michigan; Pilot Ejects . An F-16 Fighting Falcon assigned to the T…
- T3🟠 · Al Jazeera English
  - [Trump administration approves sale of F-35 jets to Saudi Arabia](https://www.aljazeera.com/news/2026/9/17/trump-administration-approves-sale-of-f-35-jets-to-saudi-arabia?traffic_source=rss)
  - 原文片段（`sale + jet`）：Trump administration approves sale of F-35 jets to Saudi Arabia . The deal, which needs approval from Congress, comes…
- T3🟠 · Breaking Defense
  - [US clears $24B F-35 sale for Saudi Arabia](https://breakingdefense.com/2026/09/us-clears-24b-f-35-sale-for-saudi-arabia/)
  - 原文片段（`sale + fighter`）：US clears $24B F-35 sale for Saudi Arabia . The approval comes a year after President Donald Trump announced Riyadh would be permitted to buy the highly advanced stealth fighters, though lawmakers could still block the sale.

## 局限

本文件由程序按「议题词 + 周」机械配对生成，只负责把材料摆到一起。**任何一致性/矛盾性结论都必须由人读原文后给出。**
