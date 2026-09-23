#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_cfg6.py — 真实语料核对后的四处收紧：
1. italy_election d1 删裸 governo/government，改选举/组阁语境词组
2. eu_politics d1 删裸 commission（保留 european commission）
3. china_xinhua 改三维：d2 欧洲语境门控，d3 俄/乌对象
4. ua_aid_attitude d1 删裸 aiut（意语"帮助"泛词），保留 aiut militare 组合
"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))

# 1
d1 = cfg["buckets"]["italy_election"]["require_all"][0]
for t in ["government", "rx:\\bgoverno\\b"]:
    if t in d1:
        d1.remove(t)
for t in ["summit", "rx:\\bvertice\\b", "rx:\\bnuovo governo\\b",
          "rx:\\bcrisi di governo\\b", "rx:\\bgiuramento\\b"]:
    if t not in d1:
        d1.append(t)

# 2
d1e = cfg["buckets"]["eu_politics"]["require_all"][0]
if "commission" in d1e:
    d1e.remove("commission")

# 3
cx = cfg["buckets"]["china_xinhua"]["require_all"]
d2 = cx[1]
for t in ["russia", "russian", "rx:\\bruss\\w*", "rx:\\brussie\\b", "rx:\\bucrain\\w*"]:
    if t in d2:
        d2.remove(t)
d3 = ["russia", "russian", "ukraine", "ukrainian", "war", "peace", "sanction",
      "trade", "summit", "talks", "rx:\\bruss\\w*", "rx:\\brussie\\b",
      "rx:\\bucrain\\w*", "rx:\\bukrain\\w*", "rx:\\bmosca\\b"]
if len(cx) < 3:
    cx.append(d3)
else:
    cx[2] = d3

# 4
d1a = cfg["buckets"]["ua_aid_attitude"]["require_all"][0]
if "rx:\\baiut\\w*" in d1a:
    d1a.remove("rx:\\baiut\\w*")

json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("italy d1 tail:", d1[-6:])
print("eu d1 has bare commission:", "commission" in d1e)
print("china dims:", len(cx), "d2:", d2[:6], "d3:", d3[:5])
print("aid d1 tail:", d1a[-3:])
