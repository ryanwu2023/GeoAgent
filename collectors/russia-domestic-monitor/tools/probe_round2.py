#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_round2.py — SSL 放行复测 + RSS 路径变体探测"""
import gzip, json, os, re, ssl, time, zlib, urllib.request
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
      "Accept-Encoding": "gzip, deflate", "Connection": "close"}

# SSL 放行上下文：仅用于排除本地代理 TLS 解密导致的证书链失败（对端仍为目标官方站，
# 非风控绕过；note 中如实记录）。若依旧失败则停用留档。
CTX_UNVERIFIED = ssl._create_unverified_context()

def fetch(url, ctx=None, timeout=25):
    hdr = UA
    try:
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
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
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"[:90].encode()

def sniff(status, raw):
    if status != 200:
        err = raw.decode("utf-8", "replace")[:70] if raw else ""
        return f"{status} {err}"
    body = raw.decode("utf-8", "replace")
    head = body[:300].lstrip().lower()
    if head.startswith("<?xml") or "<rss" in head or "<feed" in head:
        n = body.count("<item") + body.count("<entry")
        fresh = ""
        m = re.search(r"<pubDate>(.*?)</pubDate>", body) or re.search(r"<updated>(.*?)</updated>", body)
        if m: fresh = m.group(1)[:32]
        return f"rss items={n} fresh={fresh}"
    return f"html size={len(body)} links={len(re.findall(r'https?://', body))}"

def fetch2(url, label, ctx=None):
    st, raw = fetch(url, ctx)
    print(f"{label:<18} {sniff(st, raw)[:80]}  {url[:72]}")
    return st, raw

# --- SSL 放行复测 ---
print("== SSL 放行复测（代理 TLS 解密致证书链失败的站）==")
for sid, url in [
    ("cbr-press", "https://www.cbr.ru/eng/press/pr/?getrss=1"),
    ("cbr-press-ru", "https://www.cbr.ru/press/pr/?getrss=1"),
    ("cbr-rates", "https://www.cbr.ru/eng/currency_base/daily/"),
    ("rosstat", "https://rosstat.gov.ru/rss"),
    ("mid-ru", "https://www.mid.ru/ru/rss/?id=main"),
    ("rt", "https://www.rt.com/rss/news/"),
]:
    fetch2(url, sid, CTX_UNVERIFIED); time.sleep(1.2)

# --- 路径变体 ---
print("\n== 路径变体探测 ==")
for sid, url in [
    ("rbc-main", "https://rssexport.rbc.ru/rbcnews/news/30/main.rss"),
    ("rbc-feed", "https://www.rbc.ru/rss/news"),
    ("rbc-econ2", "https://rssexport.rbc.ru/rbcnews/news/30/economics.rss"),
    ("meduza-en2", "https://meduza.io/en/rss/all"),
    ("meduza-ru2", "https://meduza.io/rss"),
    ("gov-ru", "https://government.ru/rss/allnews/rss.xml"),
    ("gov-en2", "http://government.ru/en/news/rss/"),
    ("minfin2", "https://minfin.gov.ru/ru/press-center/rss/"),
    ("minfin3", "https://minfin.gov.ru/ru/"),
    ("duma-ru2", "https://duma.gov.ru/rss/news/"),
    ("duma-ru3", "http://duma.gov.ru/rss/news/"),
    ("tass-econ", "https://tass.com/economics/rss"),
    ("tass-rss2", "https://tass.com/rss/press-releases.xml"),
    ("lenta-econ", "https://lenta.ru/rss/economics"),
    ("lenta-russia", "https://lenta.ru/rss/russia"),
    ("iz", "https://iz.ru/xml/rss/all.xml"),
    ("vedomosti", "https://www.vedomosti.ru/rss/news"),
    ("russiancouncil", "https://russiancouncil.ru/rss/"),
    ("ifri", "https://www.ifri.org/en/rss"),
]:
    fetch2(url, sid); time.sleep(1.2)

# --- gnews 单通道复测（确认出口对 google 域状态） ---
print("\n== gnews 出口复测 ==")
fetch2("https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "gn-test")
