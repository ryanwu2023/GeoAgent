import json, pathlib
m = json.loads(pathlib.Path("output/2026-09-17/metrics-2026-09-17.json").read_text("utf-8"))
t = m.get("treasury") or {}
print("=== 财政部 最新月份:", t.get("latest_month"), "===")
for k, e in (t.get("lines") or {}).items():
    print("  %-16s 本月=%-16s 环比=%-8s 近3月=%-16s 前3月=%-16s 本财年=%s" % (
        e["label"], e["latest"], ("%.1f%%" % e["mom_pct"]) if e["mom_pct"] is not None else "—",
        e["avg3"], e["avg3_prior"], e["fytd"]))
print("  采购占比:", t.get("procurement_share"))
print()
us = m.get("usaspending") or {}
print("=== USAspending 窗口:", us.get("window"), " 返回类别数:", us.get("rows_returned"), "===")
print("  前N类合计:", us.get("total_top"))
print("  弹药导弹类合计:", us.get("munitions_total"), " 占比:", us.get("munitions_share_in_top"))
print("  弹药类明细:")
for r in (us.get("munitions_rows") or [])[:12]:
    print("    %-6s %-52s %s" % (r["code"], r["name"][:52], r["amount"]))
