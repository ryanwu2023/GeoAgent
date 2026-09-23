#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三轮 —— ①重试瞬时失败（代理抖动）②从首页 HTML 里挖真实 feed 地址。

第二轮暴露：
  * 美方一批源同时报 SSL EOF，但同一域名上一轮还是 200 → 判定为**代理瞬时抖动**，
    必须带重试重测，不能据此停用。
  * 伊朗侧多个站（sepahnews/farsnews/kayhan/defapress）首页可达，
    但猜的 RSS 路径不对 → 正确做法是从首页 <link rel="alternate" type="application/rss+xml">
    里读出真实 feed 地址，而不是继续猜。
"""
from __future__ import annotations

import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import gzip
import io
import zlib

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HDRS = {
    "User-Agent": UA,
    "Accept": ("application/rss+xml, application/xml, text/xml, "
               "text/html;q=0.9, */*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Connection": "keep-alive",
}
ITEM_RE = re.compile(r"<(?:item|entry)\b", re.I)
FEED_LINK_RE = re.compile(
    r"""<link[^>]+(?:type=["']application/(?:rss|atom)\+xml["'])[^>]*>""", re.I)
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
TITLE_RE = re.compile(r"""title=["']([^"']*)["']""", re.I)


def decompress(raw: bytes, enc: str) -> bytes:
    enc = (enc or "").lower()
    try:
        if "gzip" in enc or raw[:2] == b"\x1f\x8b":
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:  # noqa: BLE001
        pass
    return raw


def get(url: str, verify: bool = True, tries: int = 3, timeout: int = 25) -> dict:
    ctx = None if verify else ssl._create_unverified_context()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HDRS)
            r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            body = decompress(r.read(), r.headers.get("Content-Encoding", ""))
            return {"status": r.status, "server": r.headers.get("Server", ""),
                    "body": body, "final": r.geturl(), "err": None, "tries": i + 1}
        except urllib.error.HTTPError as e:
            b = b""
            try:
                b = decompress(e.read(), e.headers.get("Content-Encoding", ""))
            except Exception:  # noqa: BLE001
                pass
            return {"status": e.code, "server": e.headers.get("Server", ""),
                    "body": b, "final": url, "err": None, "tries": i + 1}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            time.sleep(2.0 * (i + 1))
    return {"status": None, "server": "", "body": b"", "final": url,
            "err": last, "tries": tries}


def discover_feeds(page_url: str, verify: bool = False) -> list:
    r = get(page_url, verify=verify)
    if not r["body"]:
        return []
    html = r["body"].decode("utf-8", "replace")
    found = []
    for tag in FEED_LINK_RE.findall(html):
        m = HREF_RE.search(tag)
        if not m:
            continue
        href = m.group(1)
        t = TITLE_RE.search(tag)
        found.append((href, t.group(1) if t else ""))
    # 兜底：页脚里出现的 /rss 或 .xml 链接
    for m in re.finditer(r"""href=["']([^"']*(?:/rss|/feed|\.xml|/atom)[^"']*)["']""",
                         html, re.I):
        h = m.group(1)
        if not any(h == f[0] for f in found):
            found.append((h, "(footer-scan)"))
    seen, out = set(), []
    for h, t in found:
        if h in seen:
            continue
        seen.add(h)
        out.append((h, t))
    return out[:14]


def main() -> int:
    auto = urllib.request.getproxies()
    print("=" * 96)
    print(f"实际出口代理: {auto.get('https') or auto.get('http') or '无（直连）'}")
    print("=" * 96)

    print("\n########## A. 重测第二轮报 SSL EOF 的美方源（带 3 次重试）##########")
    retry_list = [
        ("war-gov-news", "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=30"),
        ("fedreg-dod", "https://www.federalregister.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=department-of-defense&per_page=30&order=newest"),
        ("dvidshub-search-centcom", "https://www.dvidshub.net/rss/search?q=CENTCOM"),
        ("navcent", "https://www.cusnc.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1015&max=30"),
        ("centcom-site-1000", "https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1000&max=30"),
        ("africom-news", "https://www.africom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1042&max=30"),
        ("army-news", "https://www.army.mil/rss/static/1.xml"),
        ("navy-news", "https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1080&max=30"),
        ("dod-pentagon-briefings", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=13&Site=945&max=30"),
    ]
    for label, url in retry_list:
        r = get(url, tries=3)
        body = r["body"]
        n = len(ITEM_RE.findall(body.decode("utf-8", "replace")))
        head = body[:150].decode("utf-8", "replace").replace("\n", " ")
        print(f"\n-- {label}")
        print(f"   status={r['status']} server={r['server'] or '-'} bytes={len(body)} "
              f"items={n} tries={r['tries']}")
        if r["err"]:
            print(f"   err: {r['err'][:120]}")
        print(f"   head: {head[:130]}")

    print("\n\n########## B. 从首页挖真实 feed 地址 ##########")
    pages = [
        ("sepahnews", "https://sepahnews.ir/"),
        ("farsnews", "https://www.farsnews.ir/"),
        ("defapress", "https://defapress.ir/"),
        ("kayhan", "https://kayhan.ir/"),
        ("mashreghnews", "https://www.mashreghnews.ir/"),
        ("irna-en", "https://en.irna.ir/"),
        ("tehrantimes", "https://www.tehrantimes.com/"),
        ("mehr-en", "https://en.mehrnews.com/"),
        ("parstoday", "https://parstoday.ir/"),
        ("isna-en", "https://en.isna.ir/"),
        ("iran-mfa", "https://en.mfa.gov.ir/"),
        ("centcom", "https://www.centcom.mil/"),
        ("defense-gov", "https://www.defense.gov/"),
    ]
    for label, url in pages:
        try:
            feeds = discover_feeds(url)
        except Exception as e:  # noqa: BLE001
            print(f"\n-- {label:14} 取首页失败: {e}")
            continue
        print(f"\n-- {label:14} {url}")
        if not feeds:
            print("   （未发现 feed 声明）")
        for h, t in feeds:
            print(f"   {h:70} {t[:40]}")

    print("\n\n########## C. 伊朗侧已知可用的确认 + defapress/kayhan 常见路径 ##########")
    probes = [
        ("defapress-a", "https://defapress.ir/fa/rss/allnews"),
        ("defapress-b", "https://defapress.ir/rss"),
        ("defapress-c", "https://defapress.ir/fa/rss/1"),
        ("kayhan-a", "https://kayhan.ir/fa/rss/allnews"),
        ("kayhan-b", "https://kayhan.ir/rss.xml"),
        ("mashreghnews", "https://www.mashreghnews.ir/rss"),
        ("parstoday", "https://parstoday.ir/en/rss"),
        ("isna-en", "https://en.isna.ir/rss"),
        ("tehrantimes", "https://www.tehrantimes.com/rss"),
        ("mehr-en", "https://en.mehrnews.com/rss"),
        ("irna-en", "https://en.irna.ir/rss"),
    ]
    for label, url in probes:
        r = get(url, tries=2)
        n = len(ITEM_RE.findall(r["body"].decode("utf-8", "replace")))
        flag = "✅" if n else "❌"
        print(f"-- {flag} {label:14} status={r['status']} items={n:4} "
              f"bytes={len(r['body']):7} {('err=' + r['err'][:50]) if r['err'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
