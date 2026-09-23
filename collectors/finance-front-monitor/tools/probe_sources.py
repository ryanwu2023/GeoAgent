#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
probe_sources.py —— 金融数据源可用性实测（先测后用，不猜）

纪律（沿用本工作区既有项目）：
  1. **先探测、再写配置**：只有实测返回可解析数据的源才进 config.json。
  2. **记录证据**：每个源记录 http / 字节数 / 解析行数 / 最新日期 / 样例值，
     落盘 tools/_probe_round{N}.json，配置里的 `probe` 字段直接引用它。
  3. **区分「连不上」与「能连但没数据」**：403/451 是风控，200+空表是源本身没货，
     两者处置完全不同。
  4. 探测必须覆盖**失败**，不能只跑成功的源（否则报告里「源全绿」是假的）。

用法：
  python tools/probe_sources.py                  # 自动探测系统代理
  python tools/probe_sources.py --proxy http://127.0.0.1:7897
  python tools/probe_sources.py --round 2        # 证据存 _probe_round2.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ★ 2026-09-23 实测（同一 URL / 同一代理 127.0.0.1:7897 / 同一时刻）：
#     curl + curl/8.19.0  → 200 / 11.3KB / 0.6s      ✓
#     curl + Chrome UA    → 000 / 0 字节 / 25s 超时   ✗
#   即 **UA 自称浏览器就挂，与用哪个客户端无关**。FRED 走 Akamai，
#   它同时看「客户端类型」与「自称的名字」两个维度。
#   本项目原先只在 --transport 一维上做文章（curl 也带 Chrome UA），
#   于是「curl 能通」这条结论从未兑现成可用的抓取 → FRED 被误判为整体不可用。
#   现在 UA 逐目标声明，实测生效值进证据。
FRED_UA = "curl/8.19.0"

PROXY_OVERRIDE = ""


def _opener(tls_verify: bool = True):
    handlers = []
    if PROXY_OVERRIDE == "direct":
        handlers.append(urllib.request.ProxyHandler({}))
    elif PROXY_OVERRIDE:
        handlers.append(urllib.request.ProxyHandler(
            {"http": PROXY_OVERRIDE, "https": PROXY_OVERRIDE}))
    if not tls_verify:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def decompress(raw: bytes, enc: str) -> bytes:
    enc = (enc or "").lower()
    if "deflate" in enc:
        import zlib
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
    if "gzip" in enc:
        import gzip
        try:
            return gzip.decompress(raw)
        except Exception:  # noqa: BLE001
            return raw
    # ★ 服务器有时**不声明** Content-Encoding 却发了 gzip 体
    #   （亚特兰大联储、CFTC Socrata、NY Fed 都这样）。
    #   只看头部声明就会把 gzip 二进制当成文本/JSON，
    #   于是报「JSON 解析失败」，而真相是「缺一步解压」。
    if raw[:2] == b"\x1f\x8b":
        import gzip
        try:
            return gzip.decompress(raw)
        except Exception:  # noqa: BLE001
            return raw
    return raw


CURL_BIN = shutil.which("curl") or "curl"


def curl_get(url: str, *, timeout: int, accept: str, proxy: str,
             ua: str = "") -> dict:
    """curl 传输。存在的理由见 probe_inspect.py 的 FRED 对照实验：
    同一 URL、同一代理，curl 200/268KB，urllib 全部超时。
    换客户端能通就换客户端，但要在证据里记下用的是哪个客户端。

    ★ `ua` 覆盖「自称的名字」这一维：curl 也能自称 Chrome，而那样 FRED 一样挂。"""
    fd, path = tempfile.mkstemp(prefix="pr_", suffix=".bin")
    os.close(fd)
    try:
        args = [CURL_BIN, "-sS", "--location", "--compressed",
                "--max-time", str(timeout), "-o", path,
                "-w", "%{http_code}|%{size_download}|%{content_type}",
                "-H", f"User-Agent: {ua or UA}", "-H", f"Accept: {accept}"]
        if proxy == "direct":
            args += ["--noproxy", "*"]
        elif proxy:
            args += ["-x", proxy]
        args.append(url)
        p = subprocess.run(args, capture_output=True, timeout=timeout + 15)
        code, _, rest = p.stdout.decode("utf-8", "replace").partition("|")
        ctype = rest.split("|")[0] if rest else ""
        with open(path, "rb") as f:
            body = f.read()
        if not code.strip().isdigit() or code.strip() in ("0", "000"):
            # ★ 必须把 curl 的 stderr 带出来。`%{http_code}` 在传输失败时是 `000`，
            #   它**能通过 isdigit()**，于是会被当成整数 0 —— 一个既非成功
            #   也非任何真实 HTTP 状态的值。不显式识别，就会得到
            #   「http=0, err=None」这种完全没有排查线索的记录。
            return {"status": None, "server": "", "ctype": "", "body": b"",
                    "err": f"curl 传输失败（http_code={code.strip()}）"
                           f"stderr={p.stderr.decode('utf-8', 'replace')[:160]!r}"}
        return {"status": int(code), "server": "curl", "ctype": ctype,
                "body": decompress(body, ""), "err": None}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "server": "", "ctype": "", "body": b"",
                "err": f"{type(e).__name__}: {e}"}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


_HOST_NEXT: dict = {}
_HOST_LOCK = threading.Lock()


def _host_wait(url: str, min_interval: float) -> float:
    """同一主机两次请求之间的最小间隔。

    ★ 这不是「礼貌」问题，是**正确性**问题。实测：FRED 单请求 1.3 秒 200；
    同一台机器并发 8 个 + 此前累积 30 次超时之后，全部挂到 20 秒超时。
    源会把「批量并发」判为异常，然后**整体降级**——症状是全红+超时，
    看起来像源挂了。所以探测与抓取都必须按主机串行、限速。
    """
    if min_interval <= 0:
        return 0.0
    host = urllib.parse.urlsplit(url).netloc
    with _HOST_LOCK:
        now = time.monotonic()
        nxt = _HOST_NEXT.get(host, 0.0)
        wait = max(0.0, nxt - now)
        _HOST_NEXT[host] = max(now, nxt) + min_interval
    if wait > 0:
        time.sleep(wait)
    return round(wait, 2)


def get(url: str, *, accept: str = "*/*", timeout: int = 30,
        retries: int = 2, transport: str = "urllib",
        min_interval: float = 0.0, ua: str = "") -> dict:
    _host_wait(url, min_interval)
    if transport == "curl":
        return curl_get(url, timeout=timeout, accept=accept,
                        proxy=PROXY_OVERRIDE, ua=ua)
    last = ""
    for i in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ua or UA, "Accept": accept,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
            })
            r = _opener().open(req, timeout=timeout)
            return {"status": r.status, "server": r.headers.get("Server", ""),
                    "ctype": r.headers.get("Content-Type", ""),
                    "body": decompress(r.read(), r.headers.get("Content-Encoding", "")),
                    "err": None}
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = decompress(e.read(), e.headers.get("Content-Encoding", ""))
            except Exception:  # noqa: BLE001
                pass
            if e.code in (403, 410, 451):
                return {"status": e.code, "server": e.headers.get("Server", ""),
                        "ctype": e.headers.get("Content-Type", ""),
                        "body": body, "err": None}
            last = f"HTTP {e.code}"
            if e.code != 404:
                return {"status": e.code, "server": e.headers.get("Server", ""),
                        "ctype": e.headers.get("Content-Type", ""),
                        "body": body, "err": None}
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if i < retries - 1:
            time.sleep(1.5 * (i + 1))
    return {"status": None, "server": "", "ctype": "", "body": b"", "err": last}


# ---------------------------------------------------------------- 校验器
def v_fred_csv(body: bytes, ctx: dict) -> dict:
    """FRED fredgraph.csv —— 两列 observation_date,<SERIES>；缺失值为 '.'"""
    txt = body.decode("utf-8", "replace").strip()
    # ★ 先挡 HTML：FRED 的 404 页是 HTML，里面有 `background-color: rgb(225,...)`
    #   这种带逗号的 CSS，按 CSV 解析会「成功」得到 1 行假数据。
    if txt.lstrip()[:1] == "<":
        return {"ok": False, "why": "返回 HTML（疑似错误页，不是 CSV）"}
    rows = [r for r in txt.splitlines() if r.strip()]
    if len(rows) < 2:
        return {"ok": False, "why": "行数不足", "rows": len(rows)}
    head = rows[0].split(",")
    # 表头必须是 observation_date,<series>（FRED fredgraph.csv 的固定形状）
    if len(head) != 2 or head[0].strip() != "observation_date":
        return {"ok": False, "why": f"表头不符合 FRED CSV 形状：{head[:3]}",
                "rows": len(rows)}
    data = []
    for r in rows[1:]:
        p = r.split(",")
        if len(p) < 2 or p[1].strip() in ("", "."):
            continue
        try:
            data.append((p[0].strip(), float(p[1])))
        except ValueError:
            continue
    if not data:
        return {"ok": False, "why": "无有效数值行", "rows": len(rows)}
    return {"ok": True, "rows": len(data), "header": head,
            "latest_date": data[-1][0], "latest": data[-1][1],
            "first_date": data[0][0],
            "sample": [list(x) for x in data[-3:]]}


def v_dw_csv(body: bytes, ctx: dict) -> dict:
    """Datawrapper 导出的 CSV（Silver Bulletin 特朗普支持率 kSCt4.csv）。

    ★ 这个校验器必须**按列名**取值，理由与 finance_monitor.parse_named_csv
      完全相同：表头是 `modeldate,approve,disapprove,approve_lo,...`，
      `disapprove` 也在第 2 列且同样是小数字。按位置取会「成功地」取到
      反向的那一列 —— 校验器会报 ok:true，而数据从第一天起就是错的。
      校验器的职责是挡住这种东西，不是复述「HTTP 200」。
    ★ 日期是 `M/D/YYYY`（月在前）。要把**解析失败计入失败**，
      否则一条日期格式全错的序列会以「rows 很多」的形式报 ok。
    """
    date_col = ctx.get("date_col", "modeldate")
    value_col = ctx.get("value_col", "approve")
    fmt = ctx.get("date_format", "%m/%d/%Y")
    txt = body.decode("utf-8-sig", "replace").strip()
    if txt[:400].lower().lstrip().startswith("<"):
        return {"ok": False, "why": "返回 HTML（疑似错误页/被拦截）"}
    rows = [r for r in txt.splitlines() if r.strip()]
    if len(rows) < 2:
        return {"ok": False, "why": "行数不足", "rows": len(rows)}
    hdr = [h.strip() for h in rows[0].split(",")]
    if date_col not in hdr:
        return {"ok": False, "why": f"日期列 {date_col} 不存在", "header": hdr}
    if value_col not in hdr:
        return {"ok": False, "why": f"数值列 {value_col} 不存在", "header": hdr}
    di, vi = hdr.index(date_col), hdr.index(value_col)
    data, bad = [], 0
    for r in rows[1:]:
        p = [c.strip() for c in r.split(",")]
        if len(p) <= max(di, vi):
            bad += 1
            continue
        try:
            d = datetime.strptime(p[di], fmt).date().isoformat()
        except ValueError:
            bad += 1
            continue
        try:
            data.append((d, float(p[vi])))
        except ValueError:
            bad += 1
    if not data:
        return {"ok": False, "why": f"无有效数值行（跳过 {bad} 行）", "header": hdr}
    if bad > len(rows) * 0.05:
        return {"ok": False, "header": hdr, "rows": len(data),
                "why": f"{bad}/{len(rows)} 行无法解析（超过 5%，日期格式或列名可能已变）"}
    return {"ok": True, "rows": len(data), "header": hdr,
            "latest_date": data[-1][0], "latest": data[-1][1],
            "first_date": data[0][0], "bad_rows": bad,
            "sample": [list(x) for x in data[-3:]]}


def v_stooq_csv(body: bytes, ctx: dict) -> dict:
    """Stooq `q/d/l/?s=<sym>&i=d` —— Date,Open,High,Low,Close,Volume。

    ★ 这里要特别识别一种**最容易被误判**的失败：JS 反爬挑战页。
      Stooq 对 12 个不同符号返回**完全相同的 796 字节** HTML，
      HTTP 状态是 200，内容是一段 JS proof-of-work 验证脚本。
      只看状态码 → 以为通了；只看「有没有行」→ 报「符号不存在」，
      然后会去一个个换符号，白折腾。
      所以这里必须把「挑战页」单独报出来，并指明还需要 JS 引擎。
    """
    txt = body.decode("utf-8", "replace").strip()
    low = txt[:600].lower()
    if "<html" in low or "<!doctype" in low:
        if "requires javascript" in low or "verify your browser" in low:
            return {"ok": False,
                    "why": "JS 反爬挑战页（HTTP 200 + 内联 proof-of-work 脚本）"
                           "—— 需要 JS 引擎，urllib/curl 都取不到，"
                           "不是符号错误"}
        return {"ok": False, "why": f"返回 HTML 错误页：{txt[:110]!r}"}
    if "no data" in low:
        return {"ok": False, "why": "Stooq 返回 No data（符号不存在或该市场无数据）"}
    if "exceeded" in low:
        return {"ok": False, "why": f"Stooq 限额提示：{txt[:120]!r}"}
    rows = [r for r in txt.splitlines() if r.strip()]
    if len(rows) < 2:
        return {"ok": False, "why": f"行数不足（{len(rows)}）"}
    head = rows[0].split(",")
    data = []
    for r in rows[1:]:
        p = r.split(",")
        if len(p) < 5:
            continue
        try:
            data.append((p[0].strip(), float(p[4])))
        except ValueError:
            continue
    if not data:
        return {"ok": False, "why": "无有效数值行", "rows": len(rows)}
    return {"ok": True, "rows": len(data), "header": head,
            "latest_date": data[-1][0], "latest": data[-1][1],
            "first_date": data[0][0],
            "sample": [list(x) for x in data[-3:]]}


def v_treasury_csv(body: bytes, ctx: dict) -> dict:
    """美国财政部 par yield curve 日频 CSV：Date 为 MM/DD/YYYY"""
    txt = body.decode("utf-8-sig", "replace").strip()
    rows = [r for r in txt.splitlines() if r.strip()]
    if len(rows) < 2:
        return {"ok": False, "why": "行数不足", "rows": len(rows)}
    rd = csv.DictReader(io.StringIO(txt))
    data = []
    for row in rd:
        d = row.get("Date") or ""
        y = row.get(ctx.get("field", "10 Yr")) or ""
        if not d or not y:
            continue
        try:
            dt = datetime.strptime(d.strip(), "%m/%d/%Y").date().isoformat()
            data.append((dt, float(y)))
        except ValueError:
            continue
    if not data:
        return {"ok": False, "why": "无有效行", "rows": len(rows)}
    data.sort()
    return {"ok": True, "rows": len(data), "header": rd.fieldnames,
            "latest_date": data[-1][0], "latest": data[-1][1],
            "first_date": data[0][0],
            "sample": [list(x) for x in data[-3:]]}


def v_yahoo_chart(body: bytes, ctx: dict) -> dict:
    """Yahoo chart v8 JSON"""
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"JSON 解析失败: {e}"}
    try:
        res = j["chart"]["result"][0]
        ts = res["timestamp"]
        cl = res["indicators"]["quote"][0]["close"]
    except Exception as e:  # noqa: BLE001
        return {"ok": False,
                "why": f"结构不符: {e}; err={j.get('chart', {}).get('error')}"}
    data = []
    for t, c in zip(ts, cl):
        if c is None:
            continue
        data.append((datetime.fromtimestamp(t, timezone.utc).date().isoformat(), c))
    if not data:
        return {"ok": False, "why": "无收盘价"}
    meta = res.get("meta", {})
    return {"ok": True, "rows": len(data),
            "latest_date": data[-1][0], "latest": round(data[-1][1], 4),
            "first_date": data[0][0], "currency": meta.get("currency"),
            "exchange": meta.get("exchangeName"),
            "sample": [list(x) for x in data[-3:]]}


def v_arcgis(body: bytes, ctx: dict) -> dict:
    """ArcGIS FeatureServer query f=json"""
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"JSON 解析失败: {e}"}
    if "error" in j:
        return {"ok": False, "why": f"ArcGIS error: {j['error']}"}
    feats = j.get("features")
    if feats is None:
        return {"ok": False, "why": f"无 features 字段，键={list(j)[:8]}"}
    if not feats:
        return {"ok": False, "why": "features 为空（可连但无数据）", "rows": 0}
    a = feats[0].get("attributes", {})
    return {"ok": True, "rows": len(feats), "fields": list(a.keys())[:40],
            "sample": {k: a[k] for k in list(a)[:14]}}


def v_arcgis_dir(body: bytes, ctx: dict) -> dict:
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"JSON 解析失败: {e}"}
    svcs = j.get("services") or []
    names = [s.get("name") for s in svcs]
    if not names:
        return {"ok": False, "why": f"无 services，键={list(j)[:8]}"}
    hit = [n for n in names if re.search(
        r"choke|port|ship|strait|transit|daily", n, re.I)]
    return {"ok": True, "rows": len(names), "count": len(names),
            "matched": hit[:40], "all_names": names[:80]}


def v_json_generic(body: bytes, ctx: dict) -> dict:
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"JSON 解析失败: {e}"}
    if isinstance(j, dict):
        return {"ok": True, "keys": list(j.keys())[:25],
                "sample": json.dumps(j, ensure_ascii=False)[:600]}
    return {"ok": True, "rows": len(j),
            "sample": json.dumps(j[:2], ensure_ascii=False)[:600]}


def v_arcgis_series(body: bytes, ctx: dict) -> dict:
    """IMF PortWatch 单条海峡单字段 —— 展开成时间序列再判活。

    ★ 这里做**三重校验**，缺一不可：
      1. 能展开出时间序列（不是空表）；
      2. 展开后的 `entity_field` 值必须**等于** `entity_match`。
         只在 URL 的 where 里过滤是不够的：where 写错（比如复制粘贴时
         漏改 portid）会静默返回**另一条海峡**的数据，
         而所有下游数字都"正常"，报告会把巴拿马运河的船流量标成霍尔木兹。
         这类错误在合成样本上永远测不出来。
      3. 日期最新值与覆盖点数要报出来，便于判断发布滞后。
    """
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"JSON 解析失败：{e}"}
    if "error" in j:
        return {"ok": False, "why": f"ArcGIS error: {str(j['error'])[:150]}"}
    feats = j.get("features") or []
    if not feats:
        return {"ok": False, "why": "features 为空"}
    ef = ctx.get("entity_field", "")
    em = ctx.get("entity_match", "")
    got_entities = set()
    series = []
    for f in feats:
        a = f.get("attributes") or {}
        if ef:
            got_entities.add(str(a.get(ef, "")).strip())
        d = str(a.get(ctx.get("date_field", "date"), "")).strip()
        v = a.get(ctx.get("value_field", "value"))
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d) or v is None:
            continue
        try:
            series.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    if not series:
        return {"ok": False, "why": f"展开后无有效行（字段 "
                                    f"{ctx.get('date_field')}/{ctx.get('value_field')}）"}
    series.sort()
    if ef and em and got_entities - {em}:
        return {"ok": False,
                "why": f"实体不匹配：URL 过滤期望 {em}，实际返回 {sorted(got_entities)}"
                       f" —— **where 子句写错了**，会静默串海峡"}
    return {"ok": True, "rows": len(series),
            "first_date": series[0][0], "latest_date": series[-1][0],
            "latest": series[-1][1],
            "entities": sorted(got_entities),
            "gap_days": (datetime.now(timezone.utc).date()
                         - datetime.strptime(series[-1][0], "%Y-%m-%d").date()).days,
            "sample": [list(x) for x in series[-3:]]}


def v_nyfed(body: bytes, ctx: dict) -> dict:
    """纽约联储 markets API（EFFR / SOFR）。
    ★ 这个源的价值在于它是**官方**的当日有效利率，而不是行情商的复述；
      政策预期部分的「当前政策利率」锚点必须用官方值。"""
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"JSON 解析失败：{e}"}
    data = j.get("refRates") or j.get("data")
    if not isinstance(data, list) or not data:
        return {"ok": False, "why": f"无 refRates/data 数组，键={list(j)[:8]}"}
    last = data[-1]
    if not isinstance(last, dict):
        return {"ok": False, "why": f"data 元素不是对象：{str(last)[:80]}"}
    datef = next((k for k in last if "date" in k.lower()), "")
    # ★ 响应按日期**降序**排列，data[-1] 是最旧的一条；
    #   latest 必须取日期最大者，否则证据里会写着一个 19 个月前的日期。
    last = max(data, key=lambda x: str(x.get(datef, "")))
    fld = ctx.get("field") or ""
    ratef = (fld if fld in last else next(
        (k for k in last
         if "rate" in k.lower() or "percent" in k.lower()), ""))
    if not datef or not ratef:
        return {"ok": False, "why": f"未找到日期/利率字段，键={list(last)[:12]}"}
    return {"ok": True, "rows": len(data), "fields": list(last),
            "latest_date": str(last[datef])[:10],
            "latest": last[ratef]}


def v_text(body: bytes, ctx: dict) -> dict:
    t = body.decode("utf-8", "replace")
    return {"ok": bool(t.strip()), "chars": len(t), "head": t[:200]}


def v_eia_wpsr(body: bytes, ctx: dict) -> dict:
    """EIA 周报 table1.csv：第 0 行表头含 m/d/yy 两个周日期；
    必须能找到 SPR 行且数值可解析——否则视为拿到了错误页。"""
    txt = body.decode("utf-8-sig", "replace").strip()
    lines = [l for l in txt.splitlines() if l.strip()]
    if len(lines) < 2:
        return {"ok": False, "why": f"行数不足（{len(lines)}）"}
    if '"STUB_1"' not in lines[0]:
        return {"ok": False, "why": f"表头异常：{lines[0][:100]!r}"}
    spr = next((l for l in lines if "Strategic Petroleum Reserve" in l), None)
    com = next((l for l in lines if "Commercial (Excluding SPR)" in l), None)
    if not spr or not com:
        return {"ok": False, "why": "找不到 SPR / Commercial 行"}
    try:
        v_spr = float(spr.split('","')[1].replace('"', ""))
        v_com = float(com.split('","')[1].replace('"', ""))
    except (IndexError, ValueError):
        return {"ok": False, "why": f"SPR 行数值解析失败：{spr[:80]!r}"}
    return {"ok": True, "rows": len(lines) - 1,
            "latest_date": lines[0].split('","')[1],
            "latest": v_spr, "spr_mbd": v_spr, "commercial_mbd": v_com}


VALIDATORS = {
    "fred_csv": v_fred_csv,
    "dw_csv": v_dw_csv,
    "eia_wpsr": v_eia_wpsr,
    "stooq_csv": v_stooq_csv,
    "treasury_csv": v_treasury_csv,
    "yahoo_chart": v_yahoo_chart,
    "arcgis": v_arcgis,
    "arcgis_dir": v_arcgis_dir,
    "arcgis_series": v_arcgis_series,
    "nyfed": v_nyfed,
    "json": v_json_generic,
    "text": v_text,
}

# ---------------------------------------------------------------- 海峡
# IMF PortWatch 的 chokepoint 编号来自实测（services 目录 + 去重查询），
# 不是猜的：chokepoint4 = Bab el-Mandeb Strait，chokepoint6 = Strait of Hormuz。
# ★ 编号一旦写错就会静默串海峡，所以每个条目都单独探测、单独留证据。
ARCGIS_BASE = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/"
               "rest/services/Daily_Chokepoints_Data/FeatureServer/0/query")

CHOKEPOINTS = [
    ("chokepoint4", "Bab el-Mandeb Strait", "曼德海峡"),
    ("chokepoint6", "Strait of Hormuz", "霍尔木兹海峡"),
    ("chokepoint1", "Suez Canal", "苏伊士运河"),
    ("chokepoint7", "Cape of Good Hope", "好望角（绕行替代路线）"),
]
CHOKE_FIELDS = [("n_total", "总通行艘次"),
                ("n_tanker", "油轮艘次"),
                ("capacity_tanker", "油轮载重吨")]


# ---------------------------------------------------------------- 候选清单
FRED_SERIES = [
    ("DGS10", "10Y 美债收益率 %"),
    ("DGS2", "2Y 美债收益率 %"),
    ("DGS30", "30Y 美债收益率 %"),
    ("DGS3MO", "3M 美债收益率 %"),
    ("DGS5", "5Y 美债收益率 %"),
    ("DGS1", "1Y 美债收益率 %"),
    ("DCOILBRENTEU", "Brent 原油 $/bbl"),
    ("DCOILWTICO", "WTI 原油 $/bbl"),
    ("DHHNGSP", "Henry Hub 天然气 $/MMBtu"),
    ("DFF", "有效联邦基金利率 EFFR"),
    ("DFEDTARU", "联邦基金目标区间上限"),
    ("DFEDTARL", "联邦基金目标区间下限"),
    ("T10YIE", "10Y 通胀预期（盈亏平衡）"),
    ("T5YIE", "5Y 通胀预期（盈亏平衡）"),
    ("T5YIFR", "5Y5Y 远期通胀预期"),
    ("VIXCLS", "VIX 波动率指数"),
    ("SP500", "标普 500 指数"),
    ("NASDAQCOM", "纳斯达克综合指数"),
    ("DJIA", "道琼斯工业指数"),
    ("DTWEXBGS", "贸易加权美元指数（广义）"),
    ("WCSSTUS1", "美国战略石油储备（千桶，周频）"),
    ("WCESTUS1", "美国商业原油库存（千桶，周频）"),
    ("BAMLH0A0HYM2", "美国高收益债信用利差 %"),
    ("BAMLC0A0CM", "美国投资级信用利差 %"),
    ("BOGZ1FL073169303Q", "消费信贷?（探测用）"),
    ("DEXUSEU", "美元/欧元"),
    ("DEXCHUS", "美元/人民币"),
    ("GASREGW", "美国汽油零售价 $/gal（周频）"),
    ("TOTALSA", "美国汽车销量（月频，探测）"),
    ("PCU324110324110", "炼油 PPI（探测用）"),
    ("WTISPLC", "WTI 现货月均价（月频）"),
]

STOOQ_SYMBOLS = [
    ("cb.f", "Brent 原油（Stooq）"),
    ("cl.f", "WTI 原油（Stooq）"),
    ("ng.f", "天然气（Stooq）"),
    ("^spx", "标普 500（Stooq）"),
    ("^ndq", "纳斯达克 100（Stooq）"),
    ("^dji", "道琼斯（Stooq）"),
    ("^vix", "VIX（Stooq）"),
    ("10usy.b", "10Y 美债收益率（Stooq）"),
    ("2usy.b", "2Y 美债收益率（Stooq）"),
    ("dx.f", "美元指数 DXY（Stooq）"),
    ("xauusd", "黄金现货（Stooq）"),
    ("bdiy", "波罗的海干散货（Stooq）"),
]

YAHOO_SYMBOLS = [
    ("BZ=F", "Brent 期货"),
    ("CNY=X", "美元兑人民币"),
    ("HYG", "垃圾债 ETF"),
    ("CL=F", "WTI 期货"),
    ("NG=F", "天然气期货"),
    ("^GSPC", "标普 500"),
    ("^IXIC", "纳斯达克综合"),
    ("^DJI", "道琼斯"),
    ("^VIX", "VIX"),
    ("^TNX", "10Y 收益率×10"),
    ("DX-Y.NYB", "美元指数"),
    ("GC=F", "黄金期货"),
    ("ITA", "美国航空航天国防 ETF"),
    ("XLE", "能源板块 ETF"),
    ("FRO", "Frontline（油轮）"),
    ("STNG", "Scorpio Tankers"),
    ("DHT", "DHT Holdings"),
    ("ZIM", "以星航运"),
    ("XOM", "埃克森美孚"),
    ("LMT", "洛克希德·马丁"),
    ("RTX", "雷神技术"),
    ("USO", "美国原油基金 ETF"),
    ("^FVX", "5Y 收益率×10"),
    ("^TYX", "30Y 收益率×10"),
]

ARCGIS_CANDIDATES = [
    ("pw-daily-chokepoints",
     "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
     "Daily_Chokepoints_Data/FeatureServer/0/query"
     "?where=1%3D1&outFields=*&resultRecordCount=5&f=json"),
    ("pw-daily-chokepoints2",
     "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
     "Chokepoints_Data/FeatureServer/0/query"
     "?where=1%3D1&outFields=*&resultRecordCount=5&f=json"),
    ("pw-services-dir",
     "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services?f=json"),
    ("pw-port-daily",
     "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
     "PortWatch_Daily_Ports_Data/FeatureServer/0/query"
     "?where=1%3D1&outFields=*&resultRecordCount=3&f=json"),
]

EXTRA_CANDIDATES = [
    ("cme-fedwatch-html",
     "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html",
     "text", {}),
    ("cme-quotes-fedfunds",
     "https://www.cmegroup.com/CmeWS/mvc/Quotes/Front/Month/305?quoteType=settle", "json", {}),
    ("cme-sofr-strip",
     "https://www.cmegroup.com/services/sofr-strip-rates/", "json", {}),
    ("atlantafed-mpt",
     "https://www.atlantafed.org/cqer/research/market-probability-tracker", "text", {}),
    ("atlantafed-mpt-csv",
     "https://www.atlantafed.org/-/media/documents/cqer/researchcc/"
     "market-probability-tracker/market-probability-tracker-data.csv", "text", {}),
    ("nyfed-effr",
     "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json", "json", {}),
    ("nyfed-sofr",
     "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json", "json", {}),
    ("eia-v1-nokey",
     "https://www.eia.gov/opendata/qb.php?sdid=PET.WCSSTUS1.W&f=json", "json", {}),
    ("eia-v2-nokey",
     "https://api.eia.gov/v2/petroleum/stoc/wstk/data/?frequency=weekly"
     "&data[0]=value&facets[series][]=WCSSTUS1&length=5", "json", {}),
    ("imf-portwatch-home",
     "https://portwatch.imf.org/", "text", {}),
    ("unctad-chokepoint",
     "https://unctadstat.unctad.org/datacentre/", "text", {}),
    ("treasury-yield-curve-xml",
     "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
     "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026", "text", {}),
    ("fed-h41",
     "https://www.federalreserve.gov/feeds/h41.xml", "text", {}),
    ("cftc-cot",
     "https://www.cftc.gov/dea/futures/deacmesf.htm", "text", {}),
    ("kpler-public", "https://www.kpler.com/", "text", {}),
    ("suez-canal-authority", "https://www.suezcanal.gov.eg/English/Pages/default.aspx",
     "text", {}),
]


def build_targets() -> list:
    t = []
    # ★ URL 模板必须与 config.json 里**逐字一致**。
    #   探测用的 URL 和实际抓取用的 URL 不一致，是最隐蔽的一类假证据：
    #   证据上写着「可用」，跑起来却永远失败（或反过来，悄悄少了 cosd ，
    #   于是每次拉全量历史，文件大 10 倍）。
    for sid, label in FRED_SERIES:
        t.append({"id": f"fred:{sid}", "provider": "fred", "label": label,
                  "url": (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
                          f"?id={sid}&cosd=2016-01-01"),
                  "kind": "fred_csv", "accept": "text/csv,*/*", "ctx": {},
                  # ★ FRED 必须用 curl 风格 UA（见 FRED_UA 的注释）。
                  #   这一列以前缺失，是 FRED 被误判为「整链不可用」的真因。
                  "ua": FRED_UA})

    # ---- Datawrapper 导出的 CSV（Silver Bulletin 特朗普支持率）----
    # 用途：TACO 五因子里的「特朗普净支持率」（对侧 taco-monitor 的 RCPPTAPP）。
    # URL 与 taco-monitor 配置**逐字一致**。
    t.append({"id": "datawrapper:trump_approval", "provider": "datawrapper",
              "label": "特朗普净支持率（Silver Bulletin / Datawrapper kSCt4）",
              "url": "https://static.dwcdn.net/data/kSCt4.csv",
              "kind": "dw_csv", "accept": "text/csv,*/*",
              "ctx": {"date_col": "modeldate", "value_col": "approve",
                      "date_format": "%m/%d/%Y"},
              "ua": FRED_UA})   # 该站对 Python-urllib UA 返回 403，curl 风格稳定
    for sym, label in STOOQ_SYMBOLS:
        t.append({"id": f"stooq:{sym}", "provider": "stooq", "label": label,
                  "url": f"https://stooq.com/q/d/l/?s={sym}&i=d",
                  "kind": "stooq_csv", "accept": "text/csv,*/*", "ctx": {}})
    for sym, label in YAHOO_SYMBOLS:
        t.append({"id": f"yahoo:{sym}", "provider": "yahoo", "label": label,
                  "url": ("https://query1.finance.yahoo.com/v8/finance/chart/"
                          f"{urllib.parse.quote(sym)}?range=2y&interval=1d"),
                  "kind": "yahoo_chart", "accept": "application/json,*/*", "ctx": {}})
    for name, url in ARCGIS_CANDIDATES:
        kind = "arcgis_dir" if "services?f=json" in url else "arcgis"
        t.append({"id": f"arcgis:{name}", "provider": "arcgis", "label": name,
                  "url": url, "kind": kind, "accept": "application/json,*/*", "ctx": {}})
    for pid, pname, zh in CHOKEPOINTS:
        for fld, fzh in CHOKE_FIELDS:
            if fld == "capacity_tanker" and pid == "chokepoint7":
                continue          # 好望角只取通行艘次，载重吨对本项目无解释力
            q = (f"?where=portid%3D%27{pid}%27&outFields=date,portid,{fld}"
                 f"&orderByFields=date%20DESC&resultRecordCount=1000"
                 f"&returnGeometry=false&f=json")
            t.append({"id": f"arcgis:{pid}:{fld}", "provider": "arcgis",
                      "label": f"{zh} · {fzh}", "url": ARCGIS_BASE + q,
                      "kind": "arcgis_series", "accept": "application/json,*/*",
                      "ctx": {"date_field": "date", "value_field": fld,
                              "entity_field": "portid", "entity_match": pid}})
    t.append({"id": "treasury:daily-yield-2026", "provider": "treasury",
              "label": "美国财政部日频国债收益率曲线 2026",
              "url": ("https://home.treasury.gov/resource-center/data-chart-center/"
                      "interest-rates/daily-treasury-rates.csv/2026/all"
                      "?type=daily_treasury_yield_curve"
                      "&field_tdr_date_value=2026&page&_format=csv"),
              "kind": "treasury_csv", "accept": "text/csv,*/*",
              "ctx": {"field": "10 Yr"}})
    # ★ 财政部的 CSV 是**按年**导出的：只取当年，1 月初就只有几个点，
    #   分位数与 σ 全部失真而程序不会报错。所以配置里用两年的 url 列表拼起来。
    #   这里也按同样方式探测，避免「探测 2026、实际用 2025+2026」的证据错配。
    t.append({"id": "treasury:daily-yield-2025", "provider": "treasury",
              "label": "美国财政部日频国债收益率曲线 2025（跨年补齐用）",
              "url": ("https://home.treasury.gov/resource-center/data-chart-center/"
                      "interest-rates/daily-treasury-rates.csv/2025/all"
                      "?type=daily_treasury_yield_curve"
                      "&field_tdr_date_value=2025&page&_format=csv"),
              "kind": "treasury_csv", "accept": "text/csv,*/*",
              "ctx": {"field": "10 Yr"}})
    for y in (2026, 2025):
        t.append({"id": f"treasury:tips-{y}", "provider": "treasury",
                  "label": f"美国财政部 TIPS 真实收益率曲线 {y}",
                  "url": ("https://home.treasury.gov/resource-center/"
                          "data-chart-center/interest-rates/"
                          "daily-treasury-rates.csv/" + str(y) + "/all"
                          "?type=daily_treasury_real_yield_curve"
                          "&field_tdr_date_value=" + str(y) +
                          "&page&_format=csv"),
                  "kind": "treasury_csv", "accept": "text/csv,*/*",
                  "ctx": {"field": "10 YR"}})
    t.append({"id": "nyfed:effr", "provider": "nyfed",
              "label": "纽约联储 有效联邦基金利率 EFFR（近 400 日）",
              "url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/400.json",
              "kind": "nyfed", "accept": "application/json,*/*", "ctx": {}})
    t.append({"id": "nyfed:fedtarget", "provider": "nyfed",
              "label": "纽约联储 EFFR 记录中的 FOMC 目标区间上限",
              "url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/400.json",
              "kind": "nyfed", "accept": "application/json,*/*",
              "ctx": {"field": "targetRateTo"}})
    t.append({"id": "eia_wpsr:table1", "provider": "eia_wpsr",
              "label": "EIA 周报 status report table1（SPR / 商业库存）",
              "url": "https://ir.eia.gov/wpsr/table1.csv",
              "kind": "eia_wpsr", "accept": "text/csv,*/*", "ctx": {}})
    t.append({"id": "nyfed:sofr", "provider": "nyfed",
              "label": "纽约联储 有担保隔夜融资利率 SOFR",
              "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json",
              "kind": "nyfed", "accept": "application/json,*/*", "ctx": {}})
    for name, url, kind, ctx in EXTRA_CANDIDATES:
        t.append({"id": f"extra:{name}", "provider": "extra", "label": name,
                  "url": url, "kind": kind, "accept": "*/*", "ctx": ctx})
    return t


def main() -> int:
    global PROXY_OVERRIDE
    ap = argparse.ArgumentParser(description="金融数据源可用性实测")
    ap.add_argument("--proxy", default="", help="显式代理；'direct' 表示直连")
    ap.add_argument("--round", default="1", help="探测轮次（决定证据文件名）")
    ap.add_argument("--only", default="", help="只测匹配该子串的源")
    ap.add_argument("--timeout", type=int, default=12, help="单次请求超时秒")
    ap.add_argument("--threads", type=int, default=12, help="并发度")
    ap.add_argument("--retries", type=int, default=1,
                    help="探测阶段重试次数。★ 探测的目的是快速判活/判死，不是抓全数据；"
                         "重试留给正式抓取。")
    ap.add_argument("--budget", type=int, default=420,
                    help="总墙钟预算（秒）。★ 串行探测实测跑了 20 分钟还没完——"
                         "探测脚本自己就在排队等超时。预算到点即收工，未开始的源"
                         "如实标 skipped_budget，**绝不假装测过**。")
    ap.add_argument("--transport", default="urllib", choices=["urllib", "curl"],
                    help="HTTP 客户端。★ 不是实现细节：实测同一 URL、同一代理下 "
                         "curl 200 / urllib 超时（Akamai 指纹判定），"
                         "因此「用哪个客户端」必须进证据，也必须在配置里写清楚。")
    ap.add_argument("--min-interval", type=float, default=0.6,
                    help="同一主机两次请求的最小间隔（秒）。并发会触发源的整体降级，"
                         "所以默认就带限速。")
    args = ap.parse_args()

    PROXY_OVERRIDE = args.proxy
    auto = urllib.request.getproxies()
    egress = (args.proxy or auto.get("https") or auto.get("http") or "无（直连）")
    print(f"出口：{egress}")
    print(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
          f"并发={args.threads} 超时={args.timeout}s 预算={args.budget}s")
    print("=" * 78)

    targets = build_targets()
    if args.only:
        # 逗号分隔的多个子串，任一命中即测（OR）。
        # 有它才能「只测一类源」，否则要么全测（慢、还可能触发降级），
        # 要么一个一个跑（容易漏）。
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        targets = [t for t in targets if any(k in t["id"] for k in keys)]
    deadline = time.time() + args.budget
    lock = threading.Lock()

    results = []

    def run_one(t: dict) -> dict:
        if time.time() > deadline:
            return {"id": t["id"], "provider": t["provider"], "label": t["label"],
                    "url": t["url"], "http": None, "server": "", "ctype": "",
                    "bytes": 0, "sec": 0.0, "err": "budget", "ok": False,
                    "why": "skipped_budget（总预算耗尽，未测）"}
        t0 = time.time()
        r = get(t["url"], accept=t["accept"], timeout=args.timeout,
                retries=args.retries, transport=args.transport,
                min_interval=args.min_interval, ua=t.get("ua", ""))
        el = round(time.time() - t0, 2)
        rec = {"id": t["id"], "provider": t["provider"], "label": t["label"],
               "url": t["url"], "http": r["status"], "server": r["server"],
               "ctype": r["ctype"], "bytes": len(r["body"] or b""),
               "sec": el, "err": r["err"], "transport": args.transport,
               # ★ 实际生效的 UA 必须进证据：只记 transport 会漏掉
               #   「curl 但自称 Chrome」这种照样被风控的组合。
               "ua": (t.get("ua") or UA)[:64]}
        # ★ 必须精确判 200：写成 `if r["status"]` 会把 404 也算通过。
        #   2026-09-23 实测踩到：FRED 对已停用的序列返回 404 + 一个 HTML
        #   错误页，v_fred_csv 从 HTML 里「成功地」解析出 1 行数值，
        #   于是 WCSSTUS1 / WCESTUS1 被记成 ok:true 写进证据文件，
        #   而正式抓取时永远是空 —— 假证据比没证据更危险。
        if r["status"] == 200 and r["body"]:
            try:
                v = VALIDATORS[t["kind"]](r["body"], t["ctx"])
            except Exception as e:  # noqa: BLE001
                v = {"ok": False, "why": f"校验器异常 {type(e).__name__}: {e}"}
        else:
            ext = ("（HTML 错误页，不是数据）"
                   if (r["body"] or b"").lstrip()[:1] == b"<" else "")
            v = {"ok": False,
                 "why": f"非 200 或无响应体（http={r['status']} "
                        f"err={r['err']}）{ext}"}
        rec.update(v)
        with lock:
            mark = "OK " if rec.get("ok") else "** "
            extra = (f"rows={rec.get('rows')} latest={rec.get('latest_date')} "
                     f"val={rec.get('latest')}" if rec.get("ok")
                     else f"why={rec.get('why')}")
            print(f"[{mark}] {t['id']:<34} http={rec['http']} "
                  f"{rec['bytes']:>7}B {el:>5.2f}s  {extra[:78]}", flush=True)
        return rec

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as ex:
        for rec in ex.map(run_one, targets):
            results.append(rec)

    out = os.path.join(HERE, f"_probe_round{args.round}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"egress": egress,
                   "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "threads": args.threads, "timeout": args.timeout,
                   "budget": args.budget, "results": results},
                  f, ensure_ascii=False, indent=1)

    ok = [r for r in results if r.get("ok")]
    skip = [r for r in results if str(r.get("why", "")).startswith("skipped_budget")]
    tested = len(results) - len(skip)
    print("=" * 78)
    print(f"可用 {len(ok)} / 已测 {tested} / 共 {len(results)}"
          + (f"  （预算内未测 {len(skip)}，不算通过）" if skip else "")
          + f"   证据 → {os.path.relpath(out, ROOT)}")
    print("\n可用清单（provider 分组）：")
    byp = {}
    for r in ok:
        byp.setdefault(r["provider"], []).append(r["id"])
    for p, ids in sorted(byp.items()):
        print(f"  {p:<10} ({len(ids):>2})  {' '.join(ids)[:160]}")
    bad = [r for r in results if not r.get("ok") and r not in skip]
    if bad:
        print("\n不可用（按原因）：")
        grp = {}
        for r in bad:
            key = str(r.get("why", ""))[:46]
            grp.setdefault(key, []).append(r["id"])
        for k, ids in sorted(grp.items(), key=lambda x: -len(x[1])):
            print(f"  [{len(ids):>2}] {k}  → {' '.join(ids)[:110]}")
    if skip:
        print("\n预算内未测：")
        for r in skip:
            print(f"  {r['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
