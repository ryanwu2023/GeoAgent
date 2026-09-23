#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二轮诊断 —— 针对第一轮暴露的三类失败做根因定位。

第一轮结论：
  * status=200 bytes=0            → 参数值无效（不是被拦），要换正确 Site/ContentType
  * 403 + Server=AkamaiGHost      → CDN 拦 HTML 页
  * CERTIFICATE_VERIFY_FAILED     → 证书链不被信任。**必须区分**：
                                     (a) 站点用自有 CA 签发（信任库问题，内容真实可取）
                                     (b) 本地代理劫持返回自己的证书（取到的其实是代理的错误页）
                                    判定方法：关掉校验握手，看拿到的是真 RSS 还是代理错误页
  * UNEXPECTED_EOF_WHILE_READING  → TLS 握手被中断（SNI 阻断 / 站点拒绝该网络路径）
                                    对照组：换 HTTP(80)、换 IP、换 SNI

本脚本只做诊断，**不落地任何「绕过」行为** —— 结论用于决定该源是
「可用 / 需显式开启不安全 TLS / 停用留档」。
"""
from __future__ import annotations

import gzip
import io
import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
import zlib
import re
from datetime import datetime, timezone

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
PRINTABLE = re.compile(rb"[ -~]{4,}")


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


def peek_tls(host: str, port: int = 443) -> dict:
    """关校验握手，取对端证书里的 DN 字符串。用于区分「自有CA」vs「代理劫持」。"""
    out = {"host": host, "ok": False, "dn_hits": [], "err": None, "proto": None}
    try:
        ctx = ssl._create_unverified_context()
        with socket.create_connection((host, port), timeout=20) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as ss:
                out["ok"] = True
                out["proto"] = ss.version()
                der = ss.getpeercert(binary_form=True)
        if der:
            strs = [s.decode("ascii", "replace") for s in PRINTABLE.findall(der)]
            keep = [s for s in strs
                    if any(k in s for k in ("Inc", "Ltd", "CA", "Authority", "GmbH",
                                            "Corp", "LLC", "Iran", "Shenas", "Clash",
                                            "Certum", "Let's Encrypt", "R10", "R11",
                                            "E1", "Cloudflare", "Google", "Organization",
                                            "Organizational", "Common", "Country"))]
            out["dn_hits"] = keep[:18]
    except Exception as e:  # noqa: BLE001
        out["err"] = f"{type(e).__name__}: {e}"
    return out


def raw_fetch(url: str, verify: bool = True, http_only: bool = False,
              timeout: int = 25) -> dict:
    if http_only:
        url = re.sub(r"^https://", "http://", url)
    ctx = None
    if not verify and url.startswith("https://"):
        ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers=HDRS)
    try:
        r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        body = decompress(r.read(), r.headers.get("Content-Encoding", ""))
        return {"status": r.status, "server": r.headers.get("Server", ""),
                "bytes": len(body), "body": body, "url": url, "err": None}
    except urllib.error.HTTPError as e:
        b = b""
        try:
            b = decompress(e.read(), e.headers.get("Content-Encoding", ""))
        except Exception:  # noqa: BLE001
            pass
        return {"status": e.code, "server": e.headers.get("Server", ""),
                "bytes": len(b), "body": b, "url": url, "err": None}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "server": "", "bytes": 0, "body": b"",
                "url": url, "err": f"{type(e).__name__}: {e}"}


def classify(r: dict) -> str:
    if r["status"] is None:
        return f"①网络/TLS层 —— {r['err']}"
    if r["status"] in (401, 403, 406, 429):
        return f"②风控层 —— HTTP {r['status']} Server={r['server'] or '?'}"
    if r["status"] == 404:
        return "③源可用层 —— 404 路径已变"
    if r["status"] in (301, 302, 307, 308):
        return f"→ 重定向 HTTP {r['status']}"
    if r["status"] == 200 and r["bytes"] == 0:
        return "④参数无效 —— 200 但空响应（不是被拦）"
    if r["status"] == 200:
        n = len(ITEM_RE.findall(r["body"].decode("utf-8", "replace")))
        if n:
            return f"✅ 可用（{n} 条）"
        head = r["body"][:120].decode("utf-8", "replace").replace("\n", " ")
        return f"200 但非 feed —— {head}"
    return f"HTTP {r['status']}"


def main() -> int:
    auto = urllib.request.getproxies()
    print("=" * 96)
    print(f"实际出口代理: {auto.get('https') or auto.get('http') or '无（直连）'}")
    print("=" * 96)

    print("\n########## A. 伊朗站 TLS 根因诊断 ##########")
    for h in ("sepahnews.ir", "www.farsnews.ir", "www.tasnimnews.com",
              "www.presstv.ir", "english.khamenei.ir", "www.tehrantimes.com"):
        d = peek_tls(h)
        print(f"\n-- {h}: handshake={'OK' if d['ok'] else 'FAIL'}{' proto=' + str(d['proto']) if d['proto'] else ''}")
        if d["err"]:
            print(f"   err: {d['err'][:140]}")
        if d["dn_hits"]:
            print(f"   证书 DN 片段: {d['dn_hits']}")

    print("\n########## B. 关校验后能否拿到真实内容（区分「自有CA」vs「代理劫持」）##########")
    for label, url in [
        ("sepahnews", "https://sepahnews.ir/"),
        ("farsnews-rss", "https://www.farsnews.ir/rss"),
        ("tasnim-en", "https://www.tasnimnews.com/en/rss/feed/0/7/0/all-news"),
        ("presstv", "https://www.presstv.ir/rss.xml"),
        ("khamenei", "https://english.khamenei.ir/rss"),
    ]:
        r = raw_fetch(url, verify=False)
        print(f"\n-- {label} (verify=False)")
        print(f"   {classify(r)}  bytes={r['bytes']}")
        if r["bytes"]:
            print(f"   首200字: {r['body'][:200].decode('utf-8','replace').replace(chr(10),' ')}")

    print("\n########## C. 换 HTTP(80) 通道 ##########")
    for label, url in [
        ("sepahnews-http", "https://sepahnews.ir/"),
        ("tasnim-http", "https://www.tasnimnews.com/en/rss/feed/0/7/0/all-news"),
        ("farsnews-http", "https://www.farsnews.ir/rss"),
    ]:
        r = raw_fetch(url, http_only=True)
        print(f"-- {label:16} {classify(r)}  bytes={r['bytes']}")

    print("\n########## D. 伊朗侧备选源（IRGC 相关度高的优先）##########")
    iran_alt = [
        ("defapress-rss", "https://defapress.ir/fa/rss"),
        ("defapress-home", "https://defapress.ir/"),
        ("mashreghnews-rss", "https://www.mashreghnews.ir/rss"),
        ("jahannews-rss", "https://www.jahannews.com/rss"),
        ("iranpress-rss", "https://iranpress.com/rss"),
        ("iranpress-home", "https://iranpress.com/"),
        ("parstoday-rss", "https://parstoday.ir/en/rss"),
        ("isna-en-rss", "https://en.isna.ir/rss"),
        ("isna-fa-rss", "https://www.isna.ir/rss"),
        ("ifpnews-rss", "https://ifpnews.com/feed"),
        ("kayhan-rss", "https://kayhan.ir/fa/rss"),
        ("alalam-rss", "https://www.alalam.ir/rss"),
        ("irib-en-rss", "https://english.irib.ir/rss"),
        ("snntv-rss", "https://www.snntv.ir/rss"),
    ]
    iran_res = []
    for label, url in iran_alt:
        r = raw_fetch(url)
        v = classify(r)
        iran_res.append({"label": label, "url": url, "verdict": v,
                         "status": r["status"], "server": r["server"],
                         "bytes": r["bytes"]})
        print(f"-- {label:20} {v[:76]}")

    print("\n########## E. 公开聚合器兜底（Google News RSS，T3 线索层）##########")
    agg = [
        ("gnews-irgc", "https://news.google.com/rss/search?q=IRGC&hl=en-US&gl=US&ceid=US:en"),
        ("gnews-sepah", "https://news.google.com/rss/search?q=IRGC+statement+readiness&hl=en-US&gl=US&ceid=US:en"),
        ("gnews-centcom", "https://news.google.com/rss/search?q=CENTCOM&hl=en-US&gl=US&ceid=US:en"),
    ]
    for label, url in agg:
        r = raw_fetch(url)
        v = classify(r)
        print(f"-- {label:16} {v[:76]}")

    print("\n########## F. CENTCOM / 美方补探（找正确 Site id 与镜像域）##########")
    us_alt = [
        ("centcom-site-1000", "https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1000&max=30"),
        ("centcom-site-1060-ct2", "https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=2&Site=1060&max=30"),
        ("centcom-site-1", "https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1&max=30"),
        ("centcom-feeds-path", "https://www.centcom.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1060&max=30&isdashboardselected=0"),
        ("war-gov-news", "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=30"),
        ("dvidshub-centcom", "https://www.dvidshub.net/rss/unit/1060"),
        ("dvidshub-search", "https://www.dvidshub.net/rss/search?q=CENTCOM"),
        ("fedreg-dod-correct", "https://www.federalregister.gov/api/v1/documents.json?conditions%5Bagencies%5D%5B%5D=department-of-defense&per_page=30&order=newest"),
        ("navcent", "https://www.cusnc.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=1015&max=30"),
    ]
    for label, url in us_alt:
        r = raw_fetch(url)
        v = classify(r)
        print(f"-- {label:20} {v[:76]}")

    print("\n" + "=" * 96)
    print("D 组结果存档 → .probe/probe2_iran.json")
    with open(".probe/probe2_iran.json", "w", encoding="utf-8") as f:
        json.dump(iran_res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
