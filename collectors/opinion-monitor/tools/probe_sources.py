#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_sources.py — opinion-monitor 数据源探测
逐 URL 实测可达性，判定 RSS 可用性（按内容判定，不信状态码）。
用法: python tools/probe_sources.py [--only substr] [--min-interval 1.2]
"""
import json, os, sys, time, gzip, io, zlib
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "_probe_round1.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def sniff_verdict(ct, body):
    """按内容判定，不信状态码。返回 (verdict, detail)"""
    if body is None:
        return "no-body", ""
    head = body[:800].lstrip().lower()
    low = body[:4000].lower()
    if low.startswith("<?xml") and ("<rss" in low or "<feed" in low or "<rdf" in low):
        return "rss-ok", ""
    if "<html" in low or head.startswith("<!doctype html"):
        if "just a moment" in low or "challenge" in low or "cf-browser-verification" in low:
            return "js-challenge", "CDN 挑战页，假 200"
        if "akamaighost" in low or "access denied" in low or "error reference" in low:
            return "cdn-block", "CDN 拦截页"
        return "html", "普通 HTML（非 feed）"
    if low.startswith("{") or low.startswith("["):
        return "json", ""
    return "unknown", body[:120]

def fetch(url, timeout=25):
    p = urlparse(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, application/json;q=0.8, text/html;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            enc = (r.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or raw[:2] == b"\x1f\x8b":
                try: raw = gzip.decompress(raw)
                except Exception:
                    try: raw = zlib.decompress(raw, 47)
                    except Exception: pass
            elif enc == "deflate":
                try: raw = zlib.decompress(raw)
                except Exception: raw = zlib.decompress(raw, -15)
            body = raw.decode("utf-8", "replace")
            return {"status": r.status, "ms": int((time.time()-t0)*1000),
                    "server": r.headers.get("Server") or "",
                    "ct": r.headers.get("Content-Type") or "",
                    "len": len(body), "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ms": int((time.time()-t0)*1000),
                "server": e.headers.get("Server") if e.headers else "",
                "ct": e.headers.get("Content-Type") if e.headers else "",
                "len": 0, "body": ""}
    except Exception as e:
        return {"status": 0, "ms": int((time.time()-t0)*1000), "server": "",
                "ct": "", "len": 0, "body": "", "err": f"{type(e).__name__}: {e}"[:160]}

# ---- 候选源清单（tier: 谁加工的这份材料就是什么等级）----
GN = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"
CANDIDATES = [
    # id, url, tier, publisher, note
    ("senate-votes-rss", "https://www.senate.gov/general/rss/RSS_votes.xml", "T1",
     "US Senate", "参议院唱名表决 RSS（战争权力决议投票为 T1 原始记录）"),
    ("govinfo-cbd-rss", "https://www.govinfo.gov/rss/cbd.xml", "T1",
     "govinfo", "国会法案汇总 RSS"),
    ("paul-press", "https://www.paul.senate.gov/rss/press-releases", "T1",
     "Sen. Rand Paul", "议员本人新闻稿"),
    ("paul-press2", "https://www.paul.senate.gov/rss/press-releases/", "T1",
     "Sen. Rand Paul", "备用 URL（带斜杠）"),
    ("murkowski-press", "https://www.murkowski.senate.gov/rss/press-releases", "T1",
     "Sen. Murkowski", "议员本人新闻稿"),
    ("collins-press", "https://www.collins.senate.gov/rss/press-releases", "T1",
     "Sen. Collins", "议员本人新闻稿"),
    ("kaine-press", "https://www.kaine.senate.gov/rss/press-releases", "T1",
     "Sen. Kaine", "战争权力决议发起人"),
    ("massie-rss", "https://massie.house.gov/rss.xml", "T1",
     "Rep. Massie", "众议员新闻稿"),
    ("apnorc-feed", "https://apnorc.org/feed/", "T1",
     "AP-NORC", "民调机构一手发布"),
    ("yougov-rss", "https://today.yougov.com/politics/rss", "T1",
     "YouGov", "民调机构一手发布"),
    ("yougov-rss2", "https://today.yougov.com/rss", "T1", "YouGov", "备用"),
    ("gallup-rss", "https://news.gallup.com/rss/news.xml", "T1",
     "Gallup", "民调机构一手发布"),
    ("ipsos-rss", "https://www.ipsos.com/en-us/rss.xml", "T1",
     "Ipsos", "路透/益普索民调一手方"),
    ("tucker-feed", "https://tuckercarlson.com/feed/", "T1",
     "Tucker Carlson", "意见领袖本人平台（其表态的原始出处）"),
    ("rcp-index", "https://www.realclearpolitics.com/index.xml", "T3",
     "RealClearPolitics", "民调聚合（二手）"),
    ("npr-politics", "https://feeds.npr.org/1001/rss.xml", "T3",
     "NPR", "主流媒体（6月决议报道即 NPR）"),
    ("thehill-feed", "https://thehill.com/feed/", "T3", "The Hill", "国会报道主源"),
    ("politico-congress", "https://www.politico.com/rss/congress.xml", "T3",
     "Politico", "国会报道"),
    ("reason-feed", "https://reason.com/feed/", "T3", "Reason", "自由意志派观点媒体"),
    ("tac-feed", "https://www.theamericanconservative.com/feed/", "T3",
     "The American Conservative", "反干预右翼观点媒体"),
    ("antiwar-feed", "https://antiwar.com/feed/", "T3", "Antiwar.com", "反战专业媒体"),
    ("intercept-feed", "https://theintercept.com/feed/", "T3", "The Intercept", "进步派观点媒体"),
    ("cd-feed", "https://www.commondreams.org/feed", "T3", "Common Dreams", "进步派观点媒体"),
    # Google News 关键词查询（T3，覆盖转述层：Reuters/AP/WaPo 民调报道、意见领袖动向）
    ("gn-poll-generic", GN.format('"Iran+war"+poll+Americans'), "T3", "Google News",
     "民调报道总通道"),
    ("gn-poll-reuters", GN.format('Reuters+Ipsos+Iran+poll'), "T3", "Google News",
     "路透/益普索转述"),
    ("gn-poll-apnorc", GN.format('AP-NORC+Iran'), "T3", "Google News", "AP-NORC 转述"),
    ("gn-poll-wapo", GN.format('Washington+Post+Iran+poll'), "T3", "Google News",
     "华盛顿邮报-夏尔学校民调转述"),
    ("gn-tucker", GN.format('Tucker+Carlson+Iran'), "T3", "Google News",
     "卡尔森动向（本人平台抓不到时退此层）"),
    ("gn-kent", GN.format('"Joe+Kent"+Iran'), "T3", "Google News", "肯特动向"),
    ("gn-maga-dissent", GN.format('Iran+war+(Massie+OR+Greene+OR+"Megyn+Kelly"+OR+Rogan)'),
     "T3", "Google News", "MAGA 反战其余意见领袖"),
    ("gn-warpowers", GN.format('"war+powers"+Iran+Senate+vote'), "T3", "Google News",
     "国会战争权力动向（媒体转述层）"),
    ("gn-protest", GN.format('Iran+war+protest+rally+Americans'), "T3", "Google News",
     "抗议/集会动向"),
]

def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only")+1]
    min_iv = 1.2
    if "--min-interval" in sys.argv:
        min_iv = float(sys.argv[sys.argv.index("--min-interval")+1])

    proxies = urllib.request.getproxies()
    print(f"[proxy] 出口配置: {proxies}   (自动探测，未写死)")
    print(f"[probe] 候选源 {len(CANDIDATES)} 个，min-interval={min_iv}s")
    results = []
    last_by_host = {}
    for cid, url, tier, pub, note in CANDIDATES:
        if only and only not in cid:
            continue
        host = urlparse(url).netloc
        wait = last_by_host.get(host)
        if wait is not None:
            dt = time.time() - wait
            if dt < min_iv:
                time.sleep(min_iv - dt)
        r = fetch(url)
        last_by_host[host] = time.time()
        v, detail = sniff_verdict(r["ct"], r["body"])
        item = {"id": cid, "url": url, "tier": tier, "publisher": pub, "note": note,
                "status": r["status"], "ms": r["ms"], "server": r["server"],
                "ct": r["ct"][:60], "len": r["len"], "verdict": v, "detail": detail}
        if "err" in r: item["err"] = r["err"]
        results.append(item)
        flag = "✅" if v == "rss-ok" else ("⚠️ " if v in ("json",) else "❌")
        print(f"{flag} {cid:18s} {r['status']:>3} {v:12s} {r['len']:>8}B  {detail[:60]}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"probed_at": datetime.now(timezone.utc).isoformat(),
                   "proxy": proxies, "results": results}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["verdict"] == "rss-ok")
    print(f"\n[probe] rss-ok {ok}/{len(results)}，证据已落 {OUT}")

if __name__ == "__main__":
    main()
