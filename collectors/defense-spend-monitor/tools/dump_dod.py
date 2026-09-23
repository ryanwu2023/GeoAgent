import json, urllib.request, urllib.parse, re
BASE = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_5"
        "?sort=-record_date&page[size]=500&page[number]={p}&filter=record_date:eq:2026-08-31")
def fetch(p):
    url = BASE.format(p=p).replace("[", "%5B").replace("]", "%5D")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=45).read())
rows = []
for p in (1, 2):
    rows += fetch(p)["data"]
print("总行数", len(rows))
pat = re.compile(r"defense|military|procurement|ammunition|missile|aircraft", re.I)
for r in rows:
    d = r.get("classification_desc") or ""
    if pat.search(d):
        print("  id=%-6s p=%-6s | %-58s | m=%-18s | y=%s" % (
            r.get("classification_id"), r.get("parent_id"), d[:58],
            r.get("current_month_net_outly_amt"), r.get("current_fytd_net_outly_amt")))
