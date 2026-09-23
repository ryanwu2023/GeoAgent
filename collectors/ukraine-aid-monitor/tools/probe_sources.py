#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_sources.py — ukraine-aid-monitor 数据源探测
逐 URL 实测可达性，按内容判定 RSS 可用性（不信状态码）。
用法: python tools/probe_sources.py [--only substr] [--min-interval 1.2]
"""
import json, os, sys, time, gzip, zlib
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_probe_round1.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def sniff_verdict(ct, body):
    if body is None:
        return "no-body", ""
    head = body[:800].lstrip().lower()
    low = body[:4000].lower()
    if "<rss" in head or "<feed" in head or "<rdf" in head:
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

GN = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"

# ---- 候选源清单（tier: 谁加工的这份材料就是什么等级）----
CANDIDATES = [
    # --- T1 官方一手 ---
    ("dod-contracts", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?PortalID=1&ModuleID=1210", "T1", "US DoD", "国防合同公告 RSS（武器援助合同一手）"),
    ("dod-news", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?PortalID=1&ModuleID=1073", "T1", "US DoD", "国防部新闻 RSS（援助包宣布）"),
    ("state-press", "https://www.state.gov/press-releases/feed/", "T1", "US State Dept", "国务院新闻稿（对外军售/援助决定）"),
    ("whitehouse-actions", "https://www.whitehouse.gov/presidential-actions/feed/", "T1", "White House", "总统行动（PDD/行政令，含援乌决定）"),
    ("whitehouse-news", "https://www.whitehouse.gov/news/feed/", "T1", "White House", "白宫新闻"),
    ("treasury-press", "https://home.treasury.gov/news/press-releases/feed", "T1", "US Treasury", "财政部（制裁/贷款安排）"),
    ("uk-gov-ukraine", "https://www.gov.uk/government/all.atom?keywords=Ukraine", "T1", "UK GOV", "英国政府涉乌全部发布（援助一手）"),
    ("un-press", "https://press.un.org/en/rss.xml", "T1", "United Nations", "UN 新闻稿（人道/斡旋，diplomacy 项目同源在用）"),
    ("nato-news", "https://www.nato.int/cps/en/natohq/news.htm?rss=true", "T1", "NATO", "北约新闻（试 RSS 参数路径）"),
    ("eu-eeas", "https://www.eeas.europa.eu/api/news-rss/news.xml", "T1", "EU EEAS", "欧盟外交署新闻 RSS"),
    ("imf-press", "https://www.imf.org/en/News/RSS?Language=ENG&category=press", "T1", "IMF", "IMF 新闻稿（贷款计划一手）"),
    ("worldbank-news", "https://www.worldbank.org/en/news/all/rss", "T1", "World Bank", "世行新闻（对乌金融）"),
    ("ebrd-news", "https://www.ebrd.com/press/rss.xml", "T1", "EBRD", "欧洲复兴开发银行"),
    # --- T2 研究机构 ---
    ("kiel-tracker", "https://www.ifw-kiel.de/topics/war-against-ukraine/ukraine-support-tracker/", "T2", "Kiel Institute", "乌克兰援助追踪器（援助数据权威机构页，非 RSS）"),
    ("kiel-rss", "https://www.ifw-kiel.de/rss.xml", "T2", "Kiel Institute", "Kiel RSS 备选路径"),
    ("crisisgroup", "https://www.crisisgroup.org/rss.xml", "T2", "ICG", "国际危机组织（ua-front 在用）"),
    ("csis-analysis", "https://www.csis.org/analysis/feed", "T2", "CSIS", "战略与国际研究中心分析"),
    ("atlantic-council", "https://www.atlanticcouncil.org/feed/", "T2", "Atlantic Council", "大西洋理事会"),
    # --- T3 媒体直连 ---
    ("xinhua-world", "http://www.news.cn/english/rss/worldrss.xml", "T3", "Xinhua 新华社", "新华社英文国际频道 RSS（用户指定）"),
    ("xinhua-world-2", "https://english.news.cn/rss/world.xml", "T3", "Xinhua 新华社", "新华社英文 RSS 备选路径"),
    ("xinhua-gn", GN.format("Xinhua+Ukraine"), "T3", "Google News", "新华社涉乌报道聚合通道（出口依赖 google）"),
    ("china-gn", GN.format("(China+OR+Beijing+OR+%22Wang+Yi%22+OR+%22Li+Hui%22)+Ukraine+(aid+OR+peace+OR+talks+OR+reconstruction)"), "T3", "Google News", "中国视角涉乌（中方表态/和平方案）"),
    ("kyivpost", "https://www.kyivpost.com/feed", "T3", "Kyiv Post", "乌英文老牌（ua-front 实测可用路径）"),
    ("euromaidan", "https://euromaidanpress.com/feed/", "T3", "Euromaidan Press", "乌独立英文媒体"),
    ("tass", "https://tass.com/rss/v2.xml", "T3", "TASS", "俄国家通讯社（俄方对援助立场窗口）"),
    ("moscowtimes", "https://www.themoscowtimes.com/rss/news", "T3", "Moscow Times", "独立俄媒"),
    ("defense-news", "https://www.defensenews.com/arc/outboundfeeds/rss/", "T3", "Defense News", "军事媒体（援助交付报道）"),
    ("breaking-defense", "https://breakingdefense.com/feed/", "T3", "Breaking Defense", "军事媒体"),
    ("kyiv-independent", "https://kyivindependent.com/feed/", "T3", "Kyiv Independent", "乌英文大报（前项目 404，复测）"),
    # --- gnews 关键词通道（T3 转述层，出口依赖 google）---
    ("gn-aid-pkg", GN.format("Ukraine+(aid+OR+assistance+OR+funding)+package+(billion+OR+million)+announce"), "T3", "Google News", "援助包宣布通道"),
    ("gn-aid-us", GN.format("(Pentagon+OR+%22State+Department%22+OR+White+House)+Ukraine+(aid+OR+weapons+OR+funding)+package"), "T3", "Google News", "美国援助通道"),
    ("gn-aid-eu", GN.format("(EU+OR+European+Union+OR+Brussels+OR+von+der+Leyen)+Ukraine+(aid+OR+funding+OR+reparations+loan)"), "T3", "Google News", "欧盟援助通道"),
    ("gn-loans", GN.format("Ukraine+(IMF+OR+loan+OR+G7+OR+ERA+OR+debt+OR+restructuring+OR+reparations+loan)+(billion+OR+finance)"), "T3", "Google News", "贷款/金融援助通道"),
    ("gn-weapons", GN.format("Ukraine+(weapons+OR+ammunition+OR+Patriot+OR+HIMARS+OR+F-16+OR+artillery+shells+OR+missiles)+(deliver+OR+supply+OR+transfer+OR+pledge)"), "T3", "Google News", "武器援助交付通道"),
    ("gn-intel", GN.format("Ukraine+(intelligence+sharing+OR+satellite+OR+targeting+OR+CIA)+(US+OR+United+States)"), "T3", "Google News", "情报支持通道"),
    ("gn-aid-cut", GN.format("Ukraine+aid+(cut+OR+halt+OR+delay+OR+pause+OR+curtail+OR+reduce)+ Trump"), "T3", "Google News", "援助收缩/延误通道"),
    ("gn-russia-react", GN.format("Russia+(warn+OR+reaction+OR+response)+(NATO+OR+Western)+Ukraine+(weapons+OR+aid+OR+escalation)"), "T3", "Google News", "俄方对援助反应通道"),
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
        flag = "✅" if v == "rss-ok" else ("⚠️ " if v in ("json", "html") else "❌")
        print(f"{flag} {cid:18s} {r['status']:>3} {v:13s} {r['len']:>8}B  {detail[:50]}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"probed_at": datetime.now(timezone.utc).isoformat(),
                   "proxy": proxies, "results": results}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["verdict"] == "rss-ok")
    print(f"\n[probe] rss-ok {ok}/{len(results)}，证据已落 {OUT}")

if __name__ == "__main__":
    main()
