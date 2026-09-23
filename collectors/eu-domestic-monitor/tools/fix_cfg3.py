#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_cfg3.py — ua_aid_attitude 补意/法语援助词形（sostegno/aiuto）。"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))
d1 = cfg["buckets"]["ua_aid_attitude"]["require_all"][0]
d3 = cfg["buckets"]["ua_aid_attitude"]["require_all"][2]
for t in ["rx:\\bsostegn\\w*", "rx:\\baiut\\w* militare\\b", "rx:\\baiut\\w*"]:
    if t not in d1:
        d1.append(t)
for t in ["rx:\\bsostegn\\w*"]:
    if t not in d3:
        d3.append(t)
json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("d1 tail:", d1[-4:])
print("d3 tail:", d3[-3:])
