import json, urllib.request, urllib.parse
url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_5"
       "?sort=-record_date&page%5Bsize%5D=100&filter=" + urllib.parse.quote("record_date:eq:2026-08-31"))
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
d = json.loads(urllib.request.urlopen(req, timeout=40).read())
rows = d["data"]
print(f"2026-08-31 共 {len(rows)} 行，总计数 {d['meta']['total-count']}")
print(f"{'科目':<62} {'本月净支出':>18} {'本财年累计':>20}")
for r in rows:
    desc = (r.get("classification_desc") or "")[:60]
    m = r.get("current_month_net_outly_amt")
    y = r.get("current_fytd_net_outly_amt")
    if m is None and y is None:
        continue
    print(f"{desc:<62} {str(m):>18} {str(y):>20}")
