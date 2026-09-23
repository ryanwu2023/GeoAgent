import json, urllib.request, urllib.parse
def q(label, params):
    url = "https://www.federalregister.gov/api/v1/documents.json?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=40).read())
        print(f"--- {label}: 共 {d.get('count')} 篇，返回 {len(d.get('results',[]))} ---")
        for r in d.get("results", [])[:4]:
            print("   ", r.get("publication_date"), "|", (r.get("type") or "")[:14], "|", (r.get("title") or "")[:88])
    except Exception as e:
        print(f"--- {label}: 失败 {type(e).__name__}: {e}")
base = {"per_page": 5, "order": "newest",
        "fields[]": ["title", "publication_date", "html_url", "type", "agencies"]}
q("drawdown 授权", {**base, "conditions[term]": "drawdown of defense articles",
                  "conditions[publication_date][gte]": "2025-01-01"})
q("弹药/军火采购", {**base, "conditions[term]": "munitions procurement",
                "conditions[publication_date][gte]": "2025-01-01"})
q("总统决定书", {**base, "conditions[presidential_document_type]": "determination",
              "conditions[publication_date][gte]": "2025-06-01"})
q("伊朗相关", {**base, "conditions[term]": "Iran", "conditions[agencies][]": "defense-department",
            "conditions[publication_date][gte]": "2025-06-01"})
