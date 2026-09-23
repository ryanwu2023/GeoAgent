#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_sources.py — russia-domestic-monitor 候选信源探测
按实测结果决定启用/停用留档（note 记录实测日期+状态+原因+替代方案）。"""
import gzip, json, os, re, sys, time, zlib, urllib.request
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def fetch(url, timeout=25):
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
           "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
           "Accept-Encoding": "gzip, deflate", "Connection": "close"}
    try:
        req = urllib.request.Request(url, headers=hdr)
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
            return r.status, raw, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""
    except Exception as e:
        return 0, b"", f"{type(e).__name__}: {e}"[:90]

def sniff(status, raw, ctype):
    """判断内容形态与条目数"""
    if status != 200:
        return {"status": status, "kind": "http-err", "items": 0, "note": ctype}
    body = raw.decode("utf-8", "replace")
    head = body[:300].lstrip().lower()
    if head.startswith("<?xml") or "<rss" in head or "<feed" in head:
        n_item = body.count("<item") + body.count("<entry")
        freshest = ""
        for m in re.finditer(r"<pubDate>(.*?)</pubDate>|<updated>(.*?)</updated>", body):
            freshest = (m.group(1) or m.group(2) or "")
        return {"status": 200, "kind": "rss/atom", "items": n_item, "freshest": freshest.strip()[:32]}
    n = len(re.findall(r"https?://[^\s\"'<>]+", body))
    return {"status": 200, "kind": "html" if "html" in head or "<body" in head or len(body) > 4000 else "text",
            "items": 0, "links": n, "size": len(body)}

CANDIDATES = [
    # --- 俄官方通讯社/媒体 ---
    ("tass", "https://tass.com/rss/v2.xml"),
    ("tass-politics", "https://tass.com/politics/rss"),
    ("interfax", "https://www.interfax.ru/rss.asp"),
    ("rbc-top", "https://rssexport.rbc.ru/rbcnews/news/30/top.rss"),
    ("rbc-economics", "https://rssexport.rbc.ru/rbcnews/news/60/economics.rss"),
    ("rbc-politics", "https://rssexport.rbc.ru/rbcnews/news/60/politics.rss"),
    ("kommer sant", "https://www.kommersant.ru/RSS/news.xml"),
    ("moscowtimes", "https://www.themoscowtimes.com/rss/news"),
    ("meduza-en", "https://meduza.io/en/rss"),
    ("meduza-ru", "https://meduza.io/rss/all"),
    ("ria", "https://ria.ru/export/rss2/archive/index.xml"),
    ("lenta", "https://lenta.ru/rss/news"),
    ("rt", "https://www.rt.com/rss/news/"),
    ("kommersant-econ", "https://www.kommersant.ru/RSS/section_economics.xml"),
    # --- 官方机构 ---
    ("cbr-press", "https://www.cbr.ru/eng/press/pr/?getrss=1"),
    ("cbr-press-ru", "https://www.cbr.ru/press/pr/?getrss=1"),
    ("minfin", "https://minfin.gov.ru/ru/press-center/rss"),
    ("rosstat", "https://rosstat.gov.ru/rss"),
    ("duma-news", "https://duma.gov.ru/rss/news/"),
    ("duma-en", "http://duma.gov.ru/en/rss/news/"),
    ("cipkr-election", "https://cikrf.ru/rss/"),
    ("government-ru", "http://government.ru/en/rss/allnews/rss.xml"),
    ("kremlin-en", "http://en.kremlin.ru/feeds/news"),
    ("mid-ru", "https://www.mid.ru/ru/rss/?id=main"),
    # --- 国际研究/媒体 ---
    ("carnegie", "https://carnegieendowment.org/rss/sol/?fa=en"),
    ("csis", "https://www.csis.org/analysis/feed"),
    ("reuters-biz", "https://www.reuters.com/rssfeed/businessNews/"),  # 已知常被挡
    ("bloomberg-rss", "https://feeds.bloomberg.com/markets/news.rss"),
    # --- 中国窗口 ---
    ("xinhua-home", "https://english.news.cn/home.htm"),
    ("chinanews-scroll", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    ("cnworld", "http://www.news.cn/world/rss.xml"),
    # --- gnews 通道（出口依赖） ---
    ("gn-duma", "https://news.google.com/rss/search?q=Russia+(%22State+Duma%22+OR+%22Duma+election%22+OR+%22parliamentary+election%22)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-oil", "https://news.google.com/rss/search?q=Russia+(oil+OR+crude+OR+%22oil+products%22+OR+diesel+OR+gasoline)+(export+OR+shipments+OR+bans+OR+discount)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-deficit", "https://news.google.com/rss/search?q=Russia+(%22budget+deficit%22+OR+%22fiscal%22+OR+%22oil+and+gas+revenues%22+OR+budget)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-milspend", "https://news.google.com/rss/search?q=Russia+(%22military+spending%22+OR+%22defense+budget%22+OR+%22military+budget%22)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-ruble", "https://news.google.com/rss/search?q=(ruble+OR+rouble)+(exchange+rate+OR+CBR+OR+%22central+bank%22)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-labor", "https://news.google.com/rss/search?q=Russia+(%22labor+shortage%22+OR+%22workforce+shortage%22+OR+unemployment+OR+hiring)&hl=en-US&gl=US&ceid=US:en"),
    ("gn-xinhua", "https://news.google.com/rss/search?q=Xinhua+Russia&hl=en-US&gl=US&ceid=US:en"),
    ("gn-china-ru", "https://news.google.com/rss/search?q=(China+OR+Beijing)+Russia+(economy+OR+trade+OR+energy+OR+cooperation)&hl=en-US&gl=US&ceid=US:en"),
]

print("[proxy]", urllib.request.getproxies())
rows = []
for sid, url in CANDIDATES:
    st, raw, extra = fetch(url)
    info = sniff(st, raw, extra)
    host = urlparse(url).netloc
    line = f"{st:>3} {info['kind']:<9} items={info.get('items', 0):<4}"
    if info.get("freshest"):
        line += f" fresh={info['freshest']}"
    if info.get("links"):
        line += f" links={info['links']} size={info.get('size')}"
    if info.get("note"):
        line += f" [{info['note']}]"
    print(f"{sid:<16} {line}  {url[:70]}")
    rows.append({"id": sid, "url": url, "status": st, **info})
    time.sleep(1.2)

with open(os.path.join(BASE, "output", "_probe_results.json"), "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1)
print("\n结果已落盘 output/_probe_results.json")
