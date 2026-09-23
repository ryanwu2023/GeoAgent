#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_widewords.py — 真实语料核对后的词表收紧（2026-09-19 首轮）
四处假阳性根因：
1) eu_stance d1 裸 europ/european/europ\\w* → ICG 全球文章提到 European 即过 d1；
2) talks_process/ua_stance/eu_stance d2 裸 deal/settlement（俄语『定居点』义！）；
3) china_xinhua d2/d3 裸 war → ICG 缅甸/锡矿、TASS 西亚评论进桶；
4) flags willingness_positive 的 agree\\w* to（agreed to triple capacity）、
   envoy_travel 裸 meeting/visit（Venezuela 石油协议）。"""
import json

PATH = "config.json"
cfg = json.load(open(PATH, encoding="utf-8"))
B = cfg["buckets"]

# 1) eu_stance d1：删裸词干，改机构短语
d1 = B["eu_stance"]["require_all"][0]
for t in ["eu", "european", "europe"]:
    if t in d1:
        d1.remove(t)
for t in ["european union", "european council", "european commission",
          "european leaders", "european officials", "european support"]:
    if t not in d1:
        d1.append(t)
# rx:\beu\b 保留（EU 专名语境），删 rx:\beurop\w*
if "rx:\\beurop\\w*" in d1:
    d1.remove("rx:\\beurop\\w*")

# 2) 各桶 d2：裸 deal/settlement 短语化
def tighten(d2):
    for t in ["deal", "settlement", "plan"]:
        if t in d2:
            d2.remove(t)
    for t in ["peace deal", "peace settlement"]:
        if t not in d2:
            d2.append(t)

tighten(B["eu_stance"]["require_all"][1])
tighten(B["ua_stance"]["require_all"][1])
tighten(B["talks_process"]["require_all"][0])

# 3) china_xinhua：d2 删 war；d3 删裸 russia/moscow/putin/war，收紧到乌克兰/和谈对象
d2c = B["china_xinhua"]["require_all"][1]
for t in ["war"]:
    if t in d2c:
        d2c.remove(t)
d3c = B["china_xinhua"]["require_all"][2]
for t in ["russia", "russian", "moscow", "putin", "war", "mediat"]:
    if t in d3c:
        d3c.remove(t)
for t in ["peace talks", "peace plan", "mediation", "medyator", "ukraine", "ukrainian",
          "kyiv", "zelensk", "俄乌", "和谈", "停火", "乌克兰", "调停"]:
    if t not in d3c:
        d3c.append(t)

# 4) flags 收紧
w = cfg["flags"]["willingness_positive"]
if "agree\\w* to" in w:
    w.remove("agree\\w* to")
for t in ["agreed to meet", "agreed to talk", "agreed to resume"]:
    if t not in w:
        w.append(t)
cfg["flags"]["envoy_travel"] = [
    "rx:\\b(envoy|delegation|witkoff|kellogg|rubio|special\\s+representative)\\w*\\s+"
    "(visit\\w*|travel\\w*|met|meeting|arriv\\w*|fly\\w*|trip)",
    "rx:\\bvisit\\w*\\s+by\\s+(the\\s+)?(envoy|delegation)",
    "rx:\\b(arriv\\w*|fly\\w*)\\s+to\\s+\\w+\\s+(the\\s+)?(envoy|delegation)?",
    "shuttle diplomac\\w*",
    "delegation arrives", "delegation visited", "envoy arrives", "envoy visited"
]

json.dump(cfg, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("eu d1:", B["eu_stance"]["require_all"][0])
print("china d3:", d3c)
print("envoy_travel:", cfg["flags"]["envoy_travel"][:3])
