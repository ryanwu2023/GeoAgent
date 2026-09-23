#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_cfg.py — 修复 ruble_fx d1 中被 shell 转义写坏的 rx: 条目"""
import json, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(BASE, "config.json")
cfg = json.load(open(p, encoding="utf-8"))
d1 = cfg["buckets"]["ruble_fx"]["require_all"][0]
# 清掉写坏的变体
d1[:] = [t for t in d1 if "////" not in t and "ставк" not in t or t.startswith("rx:") and "\\" in t and "////" not in t]
for t in ["rx:\\bключев\\w* ставк\\w*", "rx:\\bставк\\w* цб"]:
    if t not in d1:
        d1.append(t)
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("d1 tail:", d1[-4:])
