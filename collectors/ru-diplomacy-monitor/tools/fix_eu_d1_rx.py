#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fix_eu_d1_rx.py — eu_stance d1 补回 rx:\\beu\\b（shell 转义补丁把 \\b 吃成 /b，改用本脚本）"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))
d1 = cfg["buckets"]["eu_stance"]["require_all"][0]
# 清掉 shell 转义写坏的条目
bad = "rx:////beu////b"
if bad in d1:
    d1.remove(bad)
    print("removed bad:", bad)
target = "rx:\\beu\\b"
if target not in d1:
    d1.append(target)
    print("added:", target)
json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("eu d1:", d1)
