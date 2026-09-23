#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_cfg1.py — russia_policy d2 补 dialogue/normalisation 词形族。"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))
d2 = cfg["buckets"]["russia_policy"]["require_all"][1]
for t in ["dialogue", "rx:\\bdialogue\\b", "rx:\\bdialogo\\b",
          "rx:\\bnormalis\\w*", "rx:\\bnormaliz\\w*"]:
    if t not in d2:
        d2.append(t)
json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("russia_policy d2 tail:", d2[-6:])
