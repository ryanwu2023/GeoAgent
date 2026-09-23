import json, urllib.request, urllib.parse
names = ["Total--Department of Defense--Military Programs", "Total--Procurement",
         "Total--Operation and Maintenance", "Total--Military Personnel",
         "Total--Research, Development, Test, and Evaluation"]
filt = "record_date:gte:2025-10-01,classification_desc:in:(%s)" % ",".join(names)
url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_5"
       "?sort=-record_date&page[size]=200&filter=" + urllib.parse.quote(filt))
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
d = json.loads(urllib.request.urlopen(req, timeout=45).read())
print("count =", d["meta"]["total-count"])
for r in d["data"][:15]:
    print("  ", r["record_date"], "|", (r["classification_desc"] or "")[:48], "| m =", r.get("current_month_net_outly_amt"))
