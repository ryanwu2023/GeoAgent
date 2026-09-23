#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_cfg2.py — italy_election d2 补 coalition（意语行动主体语境）。"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))
d2 = cfg["buckets"]["italy_election"]["require_all"][1]
for t in ["rx:\\bcoalizion\\w*", "rx:\\bcentrosinistr\\w*", "rx:\\bcentrodestr\\w*"]:
    if t not in d2:
        d2.append(t)
json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("italy_election d2 tail:", d2[-5:])
