#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_sources.py — iran-domestic-monitor 数据源探测
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
    # --- 官方一手 T1 ---
    ("khamenei-en", "https://english.khamenei.ir/rss", "T1", "Leader.ir (EN)", "最高领袖官网英文（公开露面/会见一手记录）"),
    ("khamenei-fa", "https://khamenei.ir/rss", "T1", "Leader.ir", "领袖官网主站（备用）"),
    ("irna-en", "https://en.irna.ir/rss", "T1", "IRNA (EN)", "伊朗官方通讯社英文（官方公告/统计发布）"),
    ("irna-en2", "https://en.irna.ir/rss/home", "T1", "IRNA (EN)", "IRNA 备用路径"),
    ("president-ir", "https://www.president.ir/en/rss", "T1", "President.ir", "总统府（探测，历史上 JS 壳）"),
    # --- 官方数据门户（探测可达性；多为 HTML/接口）---
    ("cbi-ir", "https://www.cbi.ir/", "T1", "Central Bank of Iran", "央行官网（汇率/物价；历史上对外 403）"),
    ("amar-ir", "https://amar.org.ir/", "T1", "Statistical Center of Iran", "统计中心（CPI/失业率发布）"),
    ("bonbast", "https://bonbast.com/", "T3", "BonBast", "里亚尔市场汇率聚合（社区报价汇总）"),
    ("tgju", "https://www.tgju.org/", "T3", "TGJU", "伊朗行情站（波斯语，美元/里亚尔）"),
    # --- 研究机构 T2 ---
    ("carnegie-iran", "https://carnegieendowment.org/rss/solr/?lang=en", "T2", "Carnegie", "卡内基（伊朗政权结构分析）"),
    ("mei-rss", "https://www.mei.edu/rss.xml", "T2", "Middle East Institute", "MEI（伊朗政治经济）"),
    ("atlantic-iran", "https://www.atlanticcouncil.org/category/blogs/iransource/feed/", "T2", "Atlantic Council IranSource", "IranSource 专栏"),
    ("ctp-rss", "https://www.criticalthreats.org/rss", "T2", "Critical Threats Project", "伊朗威胁项目（ISW 系）"),
    ("isw-update", "https://www.understandingwar.org/backgrounder/rss.xml", "T2", "ISW", "Iran Update（上项目实测 403，复测）"),
    # --- 媒体 T3 ---
    ("iranintl-en", "https://www.iranintl.com/en/rss", "T3", "Iran International (EN)", "伊朗国际（伦敦，伊朗内政覆盖密）"),
    ("iranintl2", "https://iranintl.com/en/rss", "T3", "Iran International", "备用路径"),
    ("radiofarda", "https://www.radiofarda.com/rss/", "T3", "Radio Farda (RFE/RL)", "自由欧洲电台波斯语部英文页"),
    ("radiofarda2", "https://www.rferl.org/rss/rssall", "T3", "RFE/RL", "RFE/RL 全源"),
    ("almonitor", "https://www.al-monitor.com/rss", "T3", "Al-Monitor", "中东专业媒体（部分付费墙）"),
    ("aljazeera-all", "https://www.aljazeera.com/xml/rss/all.xml", "T3", "Al Jazeera", "半岛（区域视角）"),
    ("bbci-mideast", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "T3", "BBC", "中东频道"),
    ("guardian-mideast", "https://www.theguardian.com/world/iran/rss", "T3", "The Guardian", "Guardian 伊朗频道"),
    ("nyt-mideast", "https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml", "T3", "NYT", "中东频道"),
    ("presstv", "https://www.presstv.ir/rss.xml", "T3", "Press TV", "国家台（宣传口径，标注使用）"),
    ("presstv2", "https://presstv.ir/rss.xml", "T3", "Press TV", "备用"),
    ("tehrantimes", "https://www.tehrantimes.com/rss", "T3", "Tehran Times", "英文官媒报纸（官方立场窗口）"),
    ("tehrantimes2", "https://tehrantimes.com/rss", "T3", "Tehran Times", "备用"),
    # --- Google News 关键词通道（T3 转述层）---
    ("gn-rial", GN.format('"iranian+rial"+(exchange+rate+OR+dollar+OR+toman)'), "T3", "Google News", "里亚尔汇率总通道"),
    ("gn-rial-deval", GN.format('Iran+rial+(plunge+OR+slump+OR+devaluat+OR+"record+low")'), "T3", "Google News", "里亚尔贬值动态"),
    ("gn-food-price", GN.format('Iran+(food+prices+OR+bread+price+OR+meat+price+OR+inflation)'), "T3", "Google News", "物价/食品/通胀"),
    ("gn-cpi", GN.format('Iran+(CPI+OR+"inflation+rate")+statistics+(release+OR+report)'), "T3", "Google News", "官方统计发布"),
    ("gn-unemploy", GN.format('Iran+(unemployment+OR+jobless)+(youth+OR+graduates+OR+rate)'), "T3", "Google News", "失业率"),
    ("gn-oil-export", GN.format('Iran+crude+oil+exports+(barrels+OR+shipments+OR+tankers+OR+China)'), "T3", "Google News", "原油出口规模"),
    ("gn-fuel", GN.format('Iran+(fuel+OR+gasoline+petrol)+shortage+(queues+OR+rationing+OR+crisis)'), "T3", "Google News", "燃料短缺/配给"),
    ("gn-power", GN.format('Iran+(power+outage+OR+blackout+OR+"electricity+cuts"+OR+"power+cuts")'), "T3", "Google News", "限电/停电"),
    ("gn-energy", GN.format('Iran+(energy+crisis+OR+gas+shortage+OR+"fuel+rationing")'), "T3", "Google News", "能源危机总通道"),
    ("gn-irgc", GN.format('IRGC+OR+"Revolutionary+Guards"+Iran+(power+OR+economy+OR+crackdown+OR+arrests)'), "T3", "Google News", "革命卫队权势"),
    ("gn-irgc-gov", GN.format('Pezeshkian+(IRGC+OR+"Revolutionary+Guards"+OR+parliament+OR+government)'), "T3", "Google News", "文官政府 vs IRGC"),
    ("gn-khamenei", GN.format('Khamenei+(met+OR+speech+OR+appearance+OR+public)'), "T3", "Google News", "领袖公开露面"),
    ("gn-succession", GN.format('Iran+supreme+leader+(succession+OR+successor+OR+Mojtaba+OR+health)'), "T3", "Google News", "继承/健康/权威重塑"),
    ("gn-protest", GN.format('Iran+(protests+OR+protesters)+(Tehran+OR+arrested+OR+riot)'), "T3", "Google News", "街头抗议"),
    ("gn-subsidy", GN.format('Iran+(subsidy+OR+rationing+OR+"coupon")+bread+OR+fuel'), "T3", "Google News", "补贴/配给券"),
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
        print(f"{flag} {cid:16s} {r['status']:>3} {v:13s} {r['len']:>8}B  {detail[:50]}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"probed_at": datetime.now(timezone.utc).isoformat(),
                   "proxy": proxies, "results": results}, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in results if r["verdict"] == "rss-ok")
    print(f"\n[probe] rss-ok {ok}/{len(results)}，证据已落 {OUT}")

if __name__ == "__main__":
    main()
