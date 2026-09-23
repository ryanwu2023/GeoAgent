# -*- coding: utf-8 -*-
"""第 5 轮探测：专攻**美方官方源偏少**这个结构性偏差。

背景：第 1–4 轮已确认 centcom.mil 全站被 CDN 拦（403 AkamaiGHost），
只能用聚合器间接取，导致美方展示条数（18）显著少于伊方（60）。
本轮目标：在不做任何风控绕过（不伪造浏览器指纹、不绕挑战页）的前提下，
找**同样是官方一手**的替代美方源。

判定口径沿用前几轮：能拿到 200 且解析出条目才算可用；
403/404/证书错误一律记录并按「停用留档」处理。
"""
import gzip
import io
import os
import ssl
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none", "Upgrade-Insecure-Requests": "1",
}

CANDIDATES = [
    # (源id, 说明, url)
    ("navy-rss", "美海军官网 AFPIMS RSS（Site 猜测）",
     "https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=25"),
    ("navy-rss2", "美海军官网 RSS 另一 Site",
     "https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=1060&max=25"),
    ("af-rss", "美空军官网 RSS",
     "https://www.af.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1000&max=25"),
    ("army-rss", "美陆军官网 RSS",
     "https://www.army.mil/rss/"),
    ("dvidshub-rss", "DVIDS（国防部影像分发，官方）全站 RSS",
     "https://www.dvidshub.net/rss/"),
    ("dvidshub-centcom", "DVIDS 中央司令部单位 RSS",
     "https://www.dvidshub.net/rss/unit/1013"),
    ("centcom-articles", "CENTCOM 文章列表（非 RSS，看是否可达）",
     "https://www.centcom.mil/MEDIA/ARTICLES/"),
    ("jcs-rss", "美军参联会官网 RSS",
     "https://www.jcs.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1084&max=25"),
    ("dod-contracts-rss", "国防部合同公告 RSS（验证 AFPIMS 模式可用性）",
     "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=25"),
    ("gnews-centcom-readiness", "聚合器：CENTCOM 战备相关（无 site: 限制）",
     "https://news.google.com/rss/search?q=CENTCOM%20readiness%20OR%20%22Central%20Command%22%20readiness&hl=en-US&gl=US&ceid=US:en"),
    ("gnews-dod-statement", "聚合器：五角大楼 表态",
     "https://news.google.com/rss/search?q=%22Pentagon%22%20%22readiness%22%20statement&hl=en-US&gl=US&ceid=US:en"),
    ("gnews-irgc-claim", "聚合器：IRGC 宣称（对照用）",
     "https://news.google.com/rss/search?q=IRGC%20claims%20strike&hl=en-US&gl=US&ceid=US:en"),
]


def fetch(url: str, verify: bool = True):
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return r.status, r.headers.get("Server", ""), raw


def count_entries(raw: bytes):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return None, f"XML 解析失败: {e}"
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    latest, newest = 0, ""
    for it in items:
        for tag in ("pubDate", "{http://www.w3.org/2005/Atom}updated"):
            el = it.find(tag)
            if el is not None and el.text:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(el.text)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt > latest:
                        latest = dt
                        newest = el.text
                except Exception:
                    pass
    return len(items), newest


def main() -> int:
    proxies = urllib.request.getproxies()
    print(f"代理出口：{proxies or '直连'}")
    print("=" * 92)
    for sid, desc, url in CANDIDATES:
        line = f"{sid:<24}"
        try:
            st, srv, raw = fetch(url)
            n, extra = count_entries(raw)
            if n is None:
                print(f"{line} HTTP {st}  Server={srv or '-'}  ⚠ {extra}")
            else:
                flag = "✅" if n else "⚪"
                print(f"{line} HTTP {st}  Server={srv or '-'}  {flag} 条目 {n}"
                      f"  最新 {extra}")
        except urllib.error.HTTPError as e:
            print(f"{line} HTTP {e.code}  Server={e.headers.get('Server','-')}  ❌ "
                  f"{str(e.reason)[:50]}")
        except urllib.error.URLError as e:
            print(f"{line} 网络错误 ❌ {str(e.reason)[:70]}")
        except Exception as e:
            print(f"{line} 其它错误 ❌ {type(e).__name__}: {str(e)[:60]}")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    os.makedirs(os.path.join(ROOT, ".probe"), exist_ok=True)
    raise SystemExit(main())
