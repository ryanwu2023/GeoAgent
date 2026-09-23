#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""note_iz.py — iz 运行时 403 留档（探测 200 与运行 403 并存：疑限频）"""
import json, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(BASE, "config.json")
cfg = json.load(open(p, encoding="utf-8"))
for s in cfg["sources"]:
    if s["id"] == "iz":
        s["note"] = ("消息报（2026-09-19 探测 200/rss 50 条；同日运行期 403——疑同主机限频，"
                     "保留启用待下次运行自动回补；连续 403 再降级停用）")
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("iz note updated")
