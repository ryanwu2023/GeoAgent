#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_buckets.py — 真实语料核对后的词表收紧（2026-09-19 首轮）
① 军费桶加第三维俄罗斯语境（捷克军费假阳性）
② china 桶加第三维经贸/合作语境（VK 旅游、西亚评论假阳性）
③ ruble_fx d1 收紧 валют（CBR 加密货币假阳性）
④ labor d2 补 зарплат（普京谈工资漏检）"""
import json, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(BASE, "config.json")
cfg = json.load(open(p, encoding="utf-8"))
B = cfg["buckets"]

# ① military_spending 三维化
d2_mil = B["military_spending"]["require_all"][1]
B["military_spending"]["require_all"] = [
    B["military_spending"]["require_all"][0],
    d2_mil,
    ["russia", "russian", "moscow", "kremlin", "putin", "russian federation",
     "rx:\\bросси\\w*", "rx:\\bпутин\\w*", "rx:\\bкремл\\w*",
     "rx:\\bфедеральн\\w*", "rx:\\bминобороны\\w*", "rx:\\bгосбюджет\\w*"]
]

# ② china_xinhua 三维化（d2 保留俄中实体，d3 经贸/合作/谈判语境）
d2_cn = B["china_xinhua"]["require_all"][1]
B["china_xinhua"]["require_all"] = [
    B["china_xinhua"]["require_all"][0],
    d2_cn,
    ["trade", "energy", "gas pipeline", "sanctions", "cooperation",
     "negotiation", "summit", "talks", "power of siberia",
     "rx:\\bторгов\\w*", "rx:\\bэнергетик\\w*", "rx:\\bгазопровод\\w*",
     "rx:\\bсанкци\\w*", "rx:\\bсотрудничеств\\w*", "rx:\\bпереговор\\w*",
     "rx:\\bсаммит\\w*", "rx:\\bвстреч\\w*", "rx:\\bсила сибири\\w*"]
]

# ③ ruble_fx d1：валют 单词 → 短语（加密货币监管假阳性）
d1_fx = B["ruble_fx"]["require_all"][0]
d1_fx[:] = [t for t in d1_fx if t != "rx:\\bвалют\\w*"]
for t in ["rx:\\bкурс валют\\w*", "rx:\\bвалют\\w* интервенц\\w*"]:
    if t not in d1_fx:
        d1_fx.append(t)

# ④ labor_shortage d2 补 зарплат（工资动态=劳动力市场伴生信号）
d2_lab = B["labor_shortage"]["require_all"][1]
if "rx:\\bзарплат\\w*" not in d2_lab:
    d2_lab.append("rx:\\bзарплат\\w*")

json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("military dims:", len(B["military_spending"]["require_all"]),
      "| china dims:", len(B["china_xinhua"]["require_all"]),
      "| fx d1 tail:", d1_fx[-2:],
      "| labor d2 tail:", d2_lab[-2:])
