#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""note_f24.py — france24-fr 运行期偶发超时补记（探测 723ms 可用，保留启用）。"""
import json

cfg = json.load(open("config.json", encoding="utf-8"))
for s in cfg["sources"]:
    if s["id"] == "france24-fr":
        s["note"] = ("France24 法语（对乌/对俄态度报道；2026-09-19 探测 200/rss 24 条；"
                     "同日两次运行一次超时一次成功——偶发，保留启用观察；en 路径超时已留档）")
json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("france24-fr note updated")
