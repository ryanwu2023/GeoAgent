#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_cfg5.py — stance.escalate 补 lift sanctions（软化族）。"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))
esc = cfg["stance"]["escalate"]
for t in ["lift sanctions", "lift\\w* sanction", "soften\\w*"]:
    if t not in esc:
        esc.append(t)
json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("esc tail:", esc[-4:])
