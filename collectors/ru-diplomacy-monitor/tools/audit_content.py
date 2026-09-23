#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""audit_content.py — ru-diplomacy-monitor 内容确定性重放审计
1) 零漂移：对快照全部记录用当前引擎重放分类，buckets/kind/stance/flags 必须一致；
2) 完成态可疑扫描：标题含意向词却判成 fact_event 的条目列出（人工复核线索）；
3) flags 矛盾扫描：willingness_positive 与 willingness_negative 同真——列出而非报错
   （真实语料中「愿见特使但拒绝条款」类共存是合理的，交人工判读）。"""
import json, os, sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import ru_diplomacy_monitor as m

def main():
    outdir = os.path.join(BASE, "output")
    cfg = m.load_cfg()
    eng = m.Engine(cfg)
    recs = []
    rec_path = os.path.join(outdir, "2026-09-19", "records.jsonl")
    dated = sorted(d for d in os.listdir(outdir)
                   if len(d) == 10 and d[4] == "-" and os.path.isdir(os.path.join(outdir, d)))
    if dated:
        rec_path = os.path.join(outdir, dated[-1], "records.jsonl")
    with open(rec_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                recs.append(json.loads(line))
    print(f"[audit] 核对 {rec_path}（{len(recs)} 条）")

    drift = []
    for r in recs:
        cls = eng.classify(r.get("title", ""), r.get("summary", ""))
        if cls is None or cls.get("anchor_only"):
            drift.append((r["id"][:8], "重放后掉桶", r["title"][:60]))
            continue
        for k in ("buckets", "kind", "stance", "flags"):
            if cls[k] != r.get(k):
                drift.append((r["id"][:8], f"{k} 漂移", str(r.get(k))[:40]))
    print(f"[audit] 零漂移检查: {'✅ 全部一致' if not drift else '❌'}")
    for d in drift[:10]:
        print("   ", d)

    intent = m.re.compile(r"\b(?:will|to|expected|plan\w*|set to|due to|hop(?:e|es|ed|ing))\b", m.re.I)
    sus = [r for r in recs if r["kind"] == "fact_event"
           and intent.search(r["title"] or "")]
    print(f"[audit] 完成态可疑（标题含意向词）: {len(sus)} 条")
    for r in sus[:8]:
        print("   -", r["title"][:90])

    contra = [r for r in recs
              if (r.get("flags") or {}).get("willingness_positive")
              and (r.get("flags") or {}).get("willingness_negative")]
    print(f"[audit] flags 矛盾（愿谈+拒谈同真，多为合理共存）: {len(contra)} 条")
    for r in contra[:8]:
        print("   -", r["title"][:90])

    bc = Counter(b for r in recs for b in r["buckets"])
    print("[audit] 桶分布:", dict(bc))
    concl = "零漂移" if not drift else "存在漂移，先排查再交付"
    print(f"[audit] 结论: {concl}")
    sys.exit(0 if not drift else 1)

if __name__ == "__main__":
    main()
