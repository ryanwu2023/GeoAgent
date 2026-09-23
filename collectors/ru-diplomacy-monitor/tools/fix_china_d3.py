#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_china_d3.py — 删 china_xinhua d3 的裸 russ 词干（shell 转义补丁失败后的正规补丁）"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))
d3 = cfg["buckets"]["china_xinhua"]["require_all"][2]
target = "rx:\\bruss\\w*"
if target in d3:
    d3.remove(target)
    print("removed", target)
json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("d3 now:", d3)
