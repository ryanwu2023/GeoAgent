#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_ruble_d2.py — ruble_fx d2 补 Банк России 俄语名与出口关联语境
（真实语料漏检：『Крепкий рубль обвалил экспорт Ростсельмаша』
 『Банк России немного повысил курс доллара』）"""
import json, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(BASE, "config.json")
cfg = json.load(open(p, encoding="utf-8"))
d2 = cfg["buckets"]["ruble_fx"]["require_all"][1]
for t in ["rx:\\bбанк росси\\w*", "rx:\\bэкспорт\\w*"]:
    if t not in d2:
        d2.append(t)
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("ruble d2 tail:", d2[-3:])
