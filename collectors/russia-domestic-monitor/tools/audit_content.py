#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""audit_content.py — 确定性重放审计
对累积档全量按当前 Engine 重分类，断言零漂移（buckets/kind/flags 完全一致）；
另做完成态可疑扫描与 flags 矛盾扫描。只读不改。"""
import json, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from russia_domestic_monitor import Engine, load_cfg  # noqa: E402

def main():
    outdir = os.path.join(BASE, "output")
    cfg = load_cfg()
    eng = Engine(cfg)
    p_arch = os.path.join(outdir, "ALL-records.jsonl")
    if not os.path.exists(p_arch):
        print("无累积档，跳过")
        return
    with open(p_arch, encoding="utf-8") as f:
        recs = [json.loads(l) for l in f if l.strip()]
    drift = []
    for r in recs:
        cls = eng.classify(r.get("title", ""), r.get("summary", ""))
        cur_buckets = r.get("buckets") or []
        cur_kind = r.get("kind")
        if cls and not cls.get("anchor_only"):
            new = (cls["buckets"], cls["kind"],
                   tuple(sorted(k for k, v in cls["flags"].items() if v)))
        else:
            new = ([], "excluded", ())
        old = (cur_buckets, cur_kind,
               tuple(sorted(k for k, v in (r.get("flags") or {}).items() if v)))
        if new != old and cur_kind != "excluded":
            drift.append({"id": r["id"], "title": r["title"][:70], "old": old, "new": new})
    print(f"[audit] 重放 {len(recs)} 条，漂移 {len(drift)} 条")
    for d in drift[:10]:
        print(f"  ❌ {d['title']}  old={d['old']}  new={d['new']}")

    # 完成态可疑：kind=fact_event 但标题含意向词
    import re
    susp = [r for r in recs if r.get("kind") == "fact_event"
            and re.search(r"\b(?:to|will|plans?|expected|forecast|прогноз|планир)\w*\b",
                          r.get("title", ""), re.I)]
    print(f"[audit] fact_event 标题含意向词可疑 {len(susp)} 条（人工核对项，非错误）")
    for r in susp[:8]:
        print(f"  ⚠️ {r['title'][:90]}")

    # flags 矛盾：easing_measure 与 restriction_measure 同时为真
    both = [r for r in recs
            if (r.get("flags") or {}).get("easing_measure")
            and (r.get("flags") or {}).get("restriction_measure")]
    print(f"[audit] 缓解+收紧 flags 同时命中 {len(both)} 条（同文并存属正常，列出备核）")
    for r in both[:5]:
        print(f"  ℹ️ {r['title'][:90]}")
    verdict = "零漂移" if not drift else f"{len(drift)} 条漂移，须排查"
    print(f"[audit] 结论: {verdict}")
    sys.exit(0 if not drift else 1)

if __name__ == "__main__":
    main()
