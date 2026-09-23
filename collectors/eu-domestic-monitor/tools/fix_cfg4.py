#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_cfg4.py — ua_aid_attitude/china_xinhua d2 补意语 Ucraina 词干。"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))
for bid in ("ua_aid_attitude", "china_xinhua"):
    d2 = cfg["buckets"][bid]["require_all"][1]
    for t in ["rx:\\bucrain\\w*"]:
        if t not in d2:
            d2.append(t)
    print(bid, "d2 tail:", d2[-3:])
json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
