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
    if "<rss" in head or "<feed" in head or "<rdf" in head:
        return "rss-ok", ""   # 无 <?xml 声明也算（jpost/aljazeera 实测）
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

GN = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"

# ---- 候选源清单（tier: 谁加工的这份材料就是什么等级）----
CANDIDATES = [
    # 海湾/阿拉伯媒体（T3）
    ("national-rss", "https://www.thenationalnews.com/rss", "T3", "The National", "阿联酋大报（基线文章源）"),
    ("arabnews-rss", "https://www.arabnews.com/rss", "T3", "Arab News", "沙特系英文"),
    ("arabnews-front", "https://www.arabnews.com/rss.xml", "T3", "Arab News", "备用"),
    ("alarabiya-en", "https://english.alarabiya.net/.rss/all", "T3", "Al Arabiya English", "沙特系"),
    ("alarabiya-break", "https://english.alarabiya.net/rss/all.xml", "T3", "Al Arabiya English", "备用"),
    ("gulfnews-rss", "https://gulfnews.com/rss?generatorName=uae", "T3", "Gulf News", "阿联酋"),
    ("gulfnews2", "https://gulfnews.com/rss", "T3", "Gulf News", "备用"),
    ("aljazeera-all", "https://www.aljazeera.com/xml/rss/all.xml", "T3", "Al Jazeera", "卡塔尔系（区域视角）"),
    ("aawsat-feed", "https://english.aawsat.com/feed", "T3", "Asharq Al-Awsat", "沙特系英文（israel 项目已验证）"),
    # 伊拉克
    ("rudaw-rss", "https://www.rudaw.net/english/rss", "T3", "Rudaw", "库尔德媒体（伊拉克北部）"),
    ("rudaw2", "https://www.rudaw.net/rss", "T3", "Rudaw", "备用"),
    ("shafaq-en", "https://shafaq.com/en/rss", "T3", "Shafaq News", "伊拉克本土（民兵袭击一手转述）"),
    ("shafaq2", "https://shafaq.com/rss.xml", "T3", "Shafaq News", "备用"),
    ("iraqinews", "https://www.iraqinews.com/feed/", "T3", "IraqiNews", "伊拉克"),
    # 国际媒体
    ("bbci-mideast", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "T3", "BBC", "中东频道"),
    ("guardian-mideast", "https://www.theguardian.com/world/middleeast/rss", "T3", "The Guardian", "中东频道"),
    ("nyt-mideast", "https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml", "T3", "NYT", "中东频道"),
    ("mee-rss", "https://www.middleeasteye.net/rss", "T3", "Middle East Eye", "批评视角"),
    ("rstatecraft", "https://responsiblestatecraft.org/feed", "T3", "Responsible Statecraft", "政策分析"),
    # 分析/研究（T2）
    ("isw-update", "https://www.understandingwar.org/backgrounder/rss.xml", "T2", "ISW", "伊朗更新每日（民兵袭击计数权威）"),
    ("isw2", "https://www.understandingwar.org/rss.xml", "T2", "ISW", "备用"),
    ("icg-rss", "https://www.crisisgroup.org/rss.xml", "T2", "Intl Crisis Group", "也门/海湾分析"),
    ("ctp-rss", "https://www.criticalthreats.org/rss", "T2", "Critical Threats", "伊朗威胁项目"),
    # 官方（T1）
    ("un-press", "https://press.un.org/en/rss.xml", "T1", "United Nations", "UN 新闻稿（安理会/秘书长）"),
    ("qna-rss", "https://qna.org.qa/en/News-Area/rss", "T1", "Qatar News Agency", "卡塔尔官方通讯社（探测）"),
    ("qna2", "https://www.qna.org.qa/en/rss", "T1", "QNA", "备用"),
    ("centcom-rss", "https://www.centcom.mil/MEDIA/PRESS-RELEASES/rss/", "T1", "CENTCOM", "美军中央司令部新闻稿（探测）"),
    # Google News 关键词通道（T3 转述层）
    ("gn-mecca-pact", GN.format('"Mecca+(defence+OR+defense)+agreement"+OR+"Mecca+pact"'), "T3", "Google News", "麦加防务协议总通道"),
    ("gn-pact-egypt", GN.format('"Mecca+pact"+Egypt+join'), "T3", "Google News", "埃及加入动向"),
    ("gn-mmdc", GN.format('maritime+coalition+Red+Sea+shipping+Saudi'), "T3", "Google News", "海上联盟/红海护航"),
    ("gn-saudi-houthi", GN.format('Saudi+Houthi+(strike+OR+attack+OR+ceasefire)'), "T3", "Google News", "沙特-胡塞战事"),
    ("gn-houthi-mandeb", GN.format('Houthi+Bab+el-Mandeb+(Mokha+OR+Perim+OR+Hodeida)'), "T3", "Google News", "胡塞-曼德海峡要地"),
    ("gn-pakistan-ksa", GN.format('Pakistan+Saudi+defence+(troops+OR+aid+OR+agreement)'), "T3", "Google News", "巴基斯坦-沙特军援"),
    ("gn-iraq-militia", GN.format('Iraq+militia+attack+"US+troops"+OR+"US+base"'), "T3", "Google News", "伊拉克民兵袭美军"),
    ("gn-kataib", GN.format('"Kataib+Hezbollah"+OR+"Islamic+Resistance+in+Iraq"'), "T3", "Google News", "真主旅/伊拉克伊斯兰抵抗"),
    ("gn-pmf", GN.format('Popular+Mobilization+Forces+(strike+OR+killed)'), "T3", "Google News", "PMF 动向"),
    ("gn-gulf-iran", GN.format('(Kuwait+OR+Bahrain+OR+Qatar+OR+UAE+OR+Oman)+Iran+attack'), "T3", "Google News", "海湾五国遭伊朗攻击"),
    ("gn-oman-mediate", GN.format('Oman+mediate+(Iran+OR+Houthi+OR+US)'), "T3", "Google News", "阿曼斡旋"),
    ("gn-qatar-mediate", GN.format('Qatar+mediation+(Iran+OR+Hormuz+OR+Gulf)'), "T3", "Google News", "卡塔尔斡旋"),
    ("gn-yemen-gov", GN.format('Yemen+government+forces+(Houthi+OR+front+OR+offensive)'), "T3", "Google News", "也门政府军战线"),
    ("gn-redsea-ship", GN.format('"Red+Sea"+vessel+(attacked+OR+struck+OR+seized)'), "T3", "Google News", "红海航运袭击"),
    ("gn-hormuz-gulf", GN.format('"Strait+of+Hormuz"+(tanker+OR+transit+OR+closure)'), "T3", "Google News", "霍尔木兹/海湾航运"),
    ("gn-iran-axis", GN.format('Iran+(proxy+OR+axis)+Gulf+(Saudi+OR+UAE+OR+Jordan)'), "T3", "Google News", "伊朗对海湾渗透"),
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
