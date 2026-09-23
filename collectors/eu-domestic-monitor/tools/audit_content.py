#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""audit_content.py — 确定性重放审计：用当前引擎对快照记录的 title+summary 重算，
桶/kind/stance/flags 必须零漂移；另扫完成态可疑与 flags 矛盾。"""
import json, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import eu_domestic_monitor as m

def main():
    cfg = m.load_cfg()
    eng = m.Engine(cfg)
    outdir = os.path.join(BASE, "output")
    import re
    dates = sorted((d for d in os.listdir(outdir)
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
                    and os.path.isdir(os.path.join(outdir, d))), reverse=True)
    if not dates:
        print("无快照可审计"); return 1
    dd = dates[0]
    recs = [json.loads(l) for l in open(os.path.join(outdir, dd, "records.jsonl"),
                                        encoding="utf-8") if l.strip()]
    drift = suspicious = contra = 0
    for r in recs:
        cls = eng.classify(r["title"], r.get("summary", ""))
        if not cls or cls.get("anchor_only"):
            print(f"  [drift] 重放掉桶: {r['title'][:60]}")
            drift += 1
            continue
        if (sorted(cls["buckets"]) != sorted(r["buckets"]) or cls["kind"] != r["kind"]
                or cls["stance"] != r["stance"] or cls["flags"] != r["flags"]):
            print(f"  [drift] 分类漂移: {r['title'][:60]}\n"
                  f"    旧: {r['buckets']}/{r['kind']}/{r['stance']}\n"
                  f"    新: {cls['buckets']}/{cls['kind']}/{cls['stance']}")
            drift += 1
        t = r["title"] + "\n" + (r.get("summary") or "")
        # 完成态可疑：事件动词紧邻意向词
        if cls["kind"] == "fact_event":
            for mm in eng.event_re.finditer(t):
                pre = t[max(0, mm.start() - 32):mm.start()]
                if eng.intent_re.search(pre):
                    print(f"  [susp] 完成态紧邻意向词: …{t[max(0,mm.start()-50):mm.start()+20]}… | {r['title'][:50]}")
                    suspicious += 1
                    break
        # flags 矛盾：援助限制 + 援助承诺 同真
        fl = cls["flags"]
        if fl.get("aid_restriction") and fl.get("aid_commitment"):
            print(f"  [contra] 援助承诺+限制同真: {r['title'][:70]}")
            contra += 1
    print(f"[audit] {dd} 快照 {len(recs)} 条：漂移 {drift}，完成态可疑 {suspicious}，flags 矛盾 {contra}")
    print("结论：" + ("零漂移，通过" if drift == 0 else "存在漂移，先修引擎/词表"))
    return 0 if drift == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
