import json, pathlib, collections
rows = [json.loads(l) for l in pathlib.Path("output/2026-09-17/records-2026-09-17.jsonl").read_text("utf-8").splitlines() if l.strip()]
print("总记录:", len(rows))
print("按来源:", dict(collections.Counter(r["source_id"] for r in rows)))
print("信号桶分布:", dict(collections.Counter(s["bucket"] for r in rows for s in r["signals"])))
print()
print("=== 弹药合同（金额降序前 12）===")
con = sorted([r for r in rows if r["kind"] == "contract"], key=lambda r: -(r.get("amount_max") or 0))
for r in con[:12]:
    print("  %s | %-34s | %-14s | 弹药=%s" % (
        r["published"][:10], (r["extra"].get("recipient") or "")[:34],
        ("$%.2fM" % (r["amount_max"]/1e6)) if r.get("amount_max") else "—",
        "、".join(r["munitions"][:4]) or "—"))
    print("      ", r["summary"][:110])
print()
print("=== 弹药词命中统计（全体记录）===")
print(dict(collections.Counter(t for r in rows for t in r["munitions"]).most_common(20)))
print()
print("=== 金额抽取样本（非合同类，前 10）===")
for r in [x for x in rows if x["kind"] != "contract"][:999][:10]:
    if r["amounts"]:
        print("  ", r["source_id"], "|", [a["raw"] for a in r["amounts"][:4]], "|", r["title"][:70])
