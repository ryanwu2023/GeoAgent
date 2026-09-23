import json, pathlib
rows = [json.loads(l) for l in pathlib.Path("output/2026-09-17/records-2026-09-17.jsonl").read_text("utf-8").splitlines() if l.strip()]
for sid in ("federal-register", "govinfo-plaw", "gao-reports"):
    print("=" * 96)
    print(sid)
    print("=" * 96)
    for r in sorted([x for x in rows if x["source_id"] == sid],
                    key=lambda x: (-x.get("relevance", 0), x.get("published", ""))):
        mark = "✅留" if r.get("relevance", 0) >= 2 else "  折"
        print("%s rel=%-3s %s %-78s | %s" % (
            mark, r.get("relevance"), r["published"][:10],
            r["title"][:78], "、".join(r.get("relevance_hits", [])[:4])))
