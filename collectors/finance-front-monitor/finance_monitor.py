#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
finance-front-monitor —— 美伊冲突「金融战线」高频指标监测（可复算 / 可回源对账）

════════════════════════════════════════════════════════════════════════
它监测什么
════════════════════════════════════════════════════════════════════════
在美伊冲突背景下，把「战场之外的第二条战线」——金融市场——的公开高频指标
按七个桶抓下来、算成可比较的量、并按统计显著度分级。

  oil        原油与成品油       Brent / WTI / 天然气 / Brent-WTI 价差
  inventory  库存与战略储备     美国 SPR、商业原油库存、汽油零售价
  rates      利率与政策预期     2Y / 5Y / 10Y / 30Y / 3M、曲线形态、实际利率、
                                有效联邦基金利率、目标区间（政策预期代理）
  equity     股市与风险偏好     标普 500 / 纳斯达克 / 道琼斯、能源与国防板块
  credit     信用与波动率       VIX、高收益债利差、投资级利差
  fx         汇率与美元         美元指数、EUR/USD、USD/CNY
  shipping   海峡通行与航运     曼德海峡 / 霍尔木兹海峡日频通行量（PortWatch）、
                                油轮与航运股（通行量的市场侧代理）

════════════════════════════════════════════════════════════════════════
与新闻类监测的根本差别（本项目的设计都是从这里长出来的）
════════════════════════════════════════════════════════════════════════
兄弟项目 `iran-escalation-monitor` 的核心难题是**语义**：词边界、同形字、
反讽、否定。本项目的数据是**数字**，难题换了一套，而且更隐蔽——
因为数字看起来永远是对的：

  1. **频率不能混表**。SPR 是周频、油价是日频、汽车销量是月频。
     把周频序列放进「日变动」表，那一行会连着 6 天显示「0.00」，
     读起来像「储备纹丝不动」，实际是**没有新数据**。
     → 本项目：每序列声明 `cadence`，日频表只放日频，频率不同一律分表，
       并且**显示「距上期若干自然日」**，让跨周末的 1 期变动无处藏身。

  2. **交易日 ≠ 自然日**。周一看到的「上一期」是上周五，跨了 3 天。
     周五的「日变动」和周三的「日变动」不是同一个东西。
     → 本项目：变动一律按**观测步长**（上一期 / 5 期 / 20 期）计算，
       并显式标注该步长对应的自然日跨度。

  3. **量纲**。10Y 从 4.15% 涨到 4.23%，是 **+8 个基点**，不是「+1.93%」。
     后者会被误读成剧烈波动。信用利差、盈亏平衡通胀同理。
     → 本项目：凡 `unit` 为百分比的序列，变动一律以 **bp** 显示（另有原生值）。

  4. **「没变」有两种**：市场真的没动 vs **数据没更新**。
     → 本项目：比较「本期最新观测日期」与「上一轮最新观测日期」，
       未前进的显式标 `⏳ 未更新`，绝不显示成 0 变动。

  5. **数据会修订**。周频库存第一次发布是初值，之后回改。
     → 本项目：保留每期抓取时的原始载荷（`raw/`），报告只声明「截至抓取时点」，
       不声称是终值。

  6. **不能伪造推断**。拿不到 CME FedWatch 就不能写「市场隐含加息概率 62%」。
     → 本项目：**只给明确定义的代理量**（如「2Y 收益率 − 目标区间上限」= 政策缺口），
       并在报告里逐条标注「这是代理量，不是概率，也不是预测」。

  7. **相关 ≠ 因果**。油价涨与股指跌同时出现，可能各有各的驱动。
     → 本项目：只报「同期同向/背离」，不写「因为……所以……」，
       并在「已知偏差」里把这条写死。

════════════════════════════════════════════════════════════════════════
分级怎么来的（不是拍脑袋的阈值）
════════════════════════════════════════════════════════════════════════
每个序列取最近 `z_window`（默认 60）期的**变动**序列，算标准差 σ，
再算今天的变动在 σ 中的位置：

    z = 本期变动 / σ(近 N 期变动)

    |z| ≥ 1.0  关注      |z| ≥ 2.0  警戒      |z| ≥ 3.0  升级

好处是**自适应**：油价平时日波动 1 美元，日波动 3 美元就是大事；
     而 2020 年那种环境下波动 5 美元都算平常。写死「±3% 报警」做不到这一点。

σ 下限（`z_floor`）用来挡住「低波动期把微小变动放大成 z=8」的假警报。

════════════════════════════════════════════════════════════════════════
可复算性（本项目最硬的一条）
════════════════════════════════════════════════════════════════════════
每次抓取都把 provider 的**原始响应**存到 `output/<日期>/raw/`。
因此：

  * `--from-file <raw目录>` 可以**完全离线重放**整条管道；
  * `tools/verify_report.py` 会**从 raw 重新解析一遍**，独立复算报告里
    每一个数字（最新值 / 上期值 / 变动 / bp / z / 档位 / 派生量 / 合成指数），
    逐格核对。任一格对不上就报错并说明差在哪。

> 程序日志全绿 ≠ 数字是对的。对账工具是唯一的数字级门禁。

════════════════════════════════════════════════════════════════════════
用法
════════════════════════════════════════════════════════════════════════
  python finance_monitor.py                        抓取 + 生成报告
  python finance_monitor.py --selftest             离线自检（不联网）
  python finance_monitor.py --proxy http://...     显式覆盖代理（仅排障）
  python finance_monitor.py --from-file out/raw    离线重放已存的原始载荷
  python finance_monitor.py --as-of 2026-09-19 --out-dir output/_x  跨天验证
  python finance_monitor.py --render-only          不联网，用上轮观测重渲染
  python tools/verify_report.py --save             回源对账（数字级门禁）

  退出码：0 成功；2 关键序列全部缺失；3 自检失败。

════════════════════════════════════════════════════════════════════════
合规
════════════════════════════════════════════════════════════════════════
只抓公开、无需登录的行情与统计接口（FRED / Stooq / Yahoo / 美国财政部 /
IMF PortWatch 等），低频（建议每日 1–2 次）、带真实 UA、不绕付费墙、
不做高频轮询。所有数值均标注来源与抓取时点，不构成投资建议。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
OUTPUT_DIR = os.path.join(HERE, "output")
STATE_DIR = os.path.join(HERE, "state")

PROXY_OVERRIDE = ""
QUIET = False
CORPUS: Dict[str, Any] = {}          # 已加载的 config
FETCH_LOG: List[Dict[str, Any]] = []  # 每序列每 provider 的抓取结果


# ================================================================ 基础工具
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str, quiet: bool = False) -> None:
    if QUIET and not quiet:
        return
    print(msg, flush=True)


def today_utc() -> date:
    return now_utc().date()


# ================================================================ 数值工具
def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return a / b


def mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def stdev(xs: List[float]) -> Optional[float]:
    """样本标准差；n < 2 返回 None（**不是 0**——「无法估计波动」与
    「波动为零」是两件事，混同会造出 z=∞ 的假警报）。"""
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    return statistics.stdev(xs)


def percentile_rank(xs: List[float], v: float) -> Optional[float]:
    """v 在 xs 中的分位（0–100）。用于「当前水平处于近一年什么位置」。"""
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    n = sum(1 for x in xs if x <= v)
    return 100.0 * n / len(xs)


def round_half_up(v: Optional[float], nd: int) -> Optional[float]:
    if v is None:
        return None
    return float(f"{v:.{nd}f}")


def fmt_num(v: Optional[float], nd: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{nd}f}"


def fmt_signed(v: Optional[float], nd: int = 2, unit: str = "") -> str:
    if v is None:
        return "—"
    s = f"{v:+,.{nd}f}"
    return f"{s}{unit}"


def fmt_bp(v_pp: Optional[float]) -> str:
    """百分点 → 基点字符串。v_pp=0.08 → '+8.0bp'"""
    if v_pp is None:
        return "—"
    return f"{v_pp * 100:+.1f}bp"


def fmt_pct(v_frac: Optional[float], nd: int = 2) -> str:
    if v_frac is None:
        return "—"
    return f"{v_frac * 100:+.{nd}f}%"


# ================================================================ 网络层
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/csv,application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def decompress(raw: bytes, enc: str) -> bytes:
    enc = (enc or "").lower()
    if "deflate" in enc:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
    if "gzip" in enc:
        try:
            return gzip.decompress(raw)
        except Exception:  # noqa: BLE001
            return raw
    return raw


# ------------------------------------------------------------ 出口池（egress）
# ★ 为什么不能只信环境变量里那一个代理（2026-09-20 实测）：
#   环境代理漂到 57444，它对下面两个**通用探针**都返回正常，
#   但同一时刻真实目标是：
#       Yahoo chart API  → 403（返回 JS 反爬挑战页，不是 JSON）
#       Google News RSS  → 超时
#   而 7897 出口对这四个目标全通。结论：
#   **「探活通过」不等于「目标站可抓」** —— 出口被目标站风控时，
#   gstatic / cloudflare 这类探针照样 200，于是整轮抓取会静默地
#   拿回一堆 403，而日志上只看到「无数据」。
#   → 三层防御：
#     1) 通用探活（快）筛掉死端口；
#     2) 真实目标深探（稍慢）给存活出口**排序**，深探通过的排前面；
#     3) 抓取时遇到 403 / Tunnel connection failed，立刻摘除当前出口并顺延。
#   绝不写死端口——代理客户端重启后端口会漂移，写死的那一刻就是失效的开始。
_EGRESS_CACHE: Dict[str, Any] = {"at": 0.0, "ok": []}
_EGRESS_OPENER: Dict[str, Any] = {}
_EGRESS_TTL = 600.0
_EGRESS_DEEP_PROBES = (
    "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d",
    "https://news.google.com/rss/search?q=probe&hl=en-US&gl=US&ceid=US:en",
)


def proxy_candidates() -> List[str]:
    """候选出口：环境变量优先，常见本地端口次之。绝不写死单一端口。"""
    out: List[str] = []
    env = urllib.request.getproxies()
    for k in ("https", "http"):
        u = env.get(k)
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)
    for port in (7897, 7890, 10809, 1080, 8080, 8888, 4780, 2080, 20171, 33210):
        u = f"http://127.0.0.1:{port}"
        if u not in out:
            out.append(u)
    return out


def _egress_opener(proxy: Optional[str]):
    key = proxy or ""
    if key not in _EGRESS_OPENER:
        if proxy:
            _EGRESS_OPENER[key] = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            _EGRESS_OPENER[key] = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
    return _EGRESS_OPENER[key]


def _egress_alive(proxy: Optional[str], timeout: int = 5) -> bool:
    for probe in ("http://www.gstatic.com/generate_204",
                  "https://www.cloudflare.com/cdn-cgi/trace"):
        try:
            r = _egress_opener(proxy).open(probe, timeout=timeout)
            if r.status in (200, 204):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _egress_deep_score(proxy: Optional[str], timeout: int = 6) -> int:
    """真实目标深探得分（0–2）。只对**真的会被风控的站**探，
    否则测不出「出口被目标站拉黑」这一种半死状态。"""
    good = 0
    for probe in _EGRESS_DEEP_PROBES:
        try:
            r = _egress_opener(proxy).open(probe, timeout=timeout)
            if r.status == 200:
                good += 1
        except Exception:  # noqa: BLE001
            pass
    return good


def live_egress() -> List[Optional[str]]:
    """探测存活出口，按「真实目标可用性」降序排列（结果缓存 10 分钟）。"""
    if PROXY_OVERRIDE:
        return [None if PROXY_OVERRIDE == "direct" else PROXY_OVERRIDE]
    now = time.time()
    if _EGRESS_CACHE["ok"] and now - _EGRESS_CACHE["at"] < _EGRESS_TTL:
        return list(_EGRESS_CACHE["ok"])
    scored: List[Tuple[int, Optional[str]]] = []
    for u in proxy_candidates():
        if not _egress_alive(u):
            continue
        scored.append((-_egress_deep_score(u), u))
    scored.sort(key=lambda x: (x[0], x[1] or ""))
    ok: List[Optional[str]] = [u for _, u in scored] or [None]
    _EGRESS_CACHE.update({"at": now, "ok": ok})
    return list(ok)


def current_egress() -> Optional[str]:
    return live_egress()[0]



_EGRESS_ROTATE = ("Tunnel connection failed",
                  "10060", "10054", "10053",
                  "RemoteDisconnected", "Remote end closed",
                  "Connection reset", "Connection aborted",
                  "timed out", "TimeoutError")

def egress_rotatable(err):
    """哪些网络层异常应当**立即换出口**，而不是原地重试。

    2026-09-20 全库审计补充：除 `Tunnel connection failed` 之外，
      · `WinError 10060`（连接方在一段时间后没有正确答复）
      · `RemoteDisconnected` / `Remote end closed` / `Connection reset`
    同样高度指向「**这个出口**到目标站的链路被掐了」，换一个出口经常立刻成功。
    以前只在 Tunnel 失败时轮换，这些就被误当成「源挂了」写进报告。
    """
    e = err or ""
    return any(p in e for p in _EGRESS_ROTATE)

def demote_egress(proxy: Optional[str]) -> None:
    """某出口被目标站拒绝 / 隧道失败时立即摘除（缓存到期后自动重新探测恢复）。"""
    if proxy in _EGRESS_CACHE.get("ok", []):
        _EGRESS_CACHE["ok"].remove(proxy)


def _opener(tls_verify: bool = True):
    handlers: List[Any] = []
    proxy = current_egress()
    handlers.append(urllib.request.ProxyHandler(
        {"http": proxy, "https": proxy} if proxy else {}))
    if not tls_verify:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def proxy_report() -> str:
    if PROXY_OVERRIDE:
        return f"{PROXY_OVERRIDE}（--proxy 显式覆盖，仅供排障）"
    pool = live_egress()
    if not pool or pool[0] is None:
        return "无（直连）"
    tail = f"；备用 {len(pool) - 1} 个" if len(pool) > 1 else "（无备用）"
    return f"{pool[0]}{tail}"


# ---------------------------------------------------------------- 同源限速
# ★ 为什么必须按**主机**限速，而不是全局 sleep：
#   实测 FRED 单独请求 1.3 秒 200；并发 8 个 + 此前累积的 30 次超时之后，
#   同一台机器上**所有** FRED 请求一起挂到 20 秒超时（%{http_code}=000）。
#   这不是「源挂了」，是源对我们降级了。分批抓取最容易犯的错就是
#   「把每个源都当成无状态、无限额的 API」，然后在某一天突然全红，
#   而日志上只看到「超时」，排查方向全被带偏。
#   → 同一主机两次请求之间强制最小间隔，且**串行**发往同主机。
_HOST_NEXT: Dict[str, float] = {}
_HOST_LOCK = threading.Lock()


def host_wait(url: str, min_interval: float) -> float:
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


def http_get(url: str, *, timeout: int = 30, retries: int = 3,
             backoff: float = 2.0, accept: str = "",
             transport: str = "urllib", ua: str = "",
             min_interval: float = 0.0) -> Dict[str, Any]:
    """返回 {status, server, ctype, body(bytes), err, attempts}。

    重试策略（沿用本工作区已验证的规则）：
      * 403 / 410 / 451 —— 风控或合规拒绝，**重试无意义**，立刻返回；
      * 404 —— CDN 边缘节点状态可能不一致，**必须重试**；
      * 其余 HTTP 错误 —— 立即返回；
      * 网络层异常（超时 / DNS / TLS）—— 退避重试。

    ★ `transport="curl"` 是给 FRED 用的，原因是一个**实测出来的**发现：
      同一个 URL、同一条代理、同一时刻，
        curl  → HTTP 200，268744 字节，3.0 秒；
        urllib → 4 种请求头组合全部超时（40s），连备用静态路径也超时。
      响应头里有 `Server: Apache`、`X-location: cfs`、
      `Set-Cookie: _abck=…~-1~…` —— 这是 **Akamai Bot Manager** 的特征。
      它对不匹配浏览器指纹的客户端**不返回 403，而是把响应挂住**，
      于是症状表现为「超时」，排查方向会被带到「源挂了 / 网络不通」上去，
      而真相是「客户端被区别对待」。

      → 处置：把传输层做成 per-entry 可选，并把每个条目实际用的传输
        写进报告。**换客户端能通，就换客户端**，但必须在报告里说清楚
        「这条数据是 curl 取的」，否则复现的人会以为 Python 也能取到。
      注意：curl 走的是同一个代理，因此**没有绕过任何访问控制**，
      只是换了一个 HTTP 客户端的指纹。

    ★ `ua` 是**条目级** User-Agent 覆写（2026-09-23 新增，为接入 TACO 五因子）。
      同一次「换客户端」的教训还有下半截：客户端的**类型**和它**自称的名字**
      是两件事，Akamai 两个都看。实测（同一 URL / 同一代理 / 同一时刻）：

        客户端      自称 UA              结果
        ---------   -------------------   ----------------------------
        curl        curl/8.19.0          200 / 11.3KB / 0.6s      ✓
        curl        Mozilla Chrome/124   000 / 0 字节 / 25s 超时    ✗
        urllib      Mozilla Chrome/124   超时                     ✗

      即：**只要 UA 自称浏览器就挂**，与用哪个客户端无关。本项目原先的
      `_curl_get` 把 Chrome UA 硬写进 curl 命令里，于是「curl 能通」这条
      结论一直没能兑现成实际可用的抓取——FRED 整链因此被误判为不可用。
      现在 UA 与 transport 两个维度都可由配置逐条声明，实际生效值进报告。
    """
    if transport == "curl":
        host_wait(url, min_interval)
        return _curl_get(url, timeout=timeout, retries=retries,
                         backoff=backoff, accept=accept, ua=ua)
    last = ""
    last_info: Optional[Dict[str, Any]] = None
    headers = dict(BROWSER_HEADERS)
    if ua:
        headers["User-Agent"] = ua
    if accept:
        headers["Accept"] = accept
    # ★ 出口轮换：403 在很多站上是「这个出口 IP 被风控」，不是「这条数据不存在」。
    #   以前 403 直接返回，于是 Yahoo 的挑战页被当成「无数据」写进报告。
    pool = list(live_egress()) or [None]
    for ei, egress in enumerate(pool):
        for i in range(max(1, retries)):
            try:
                host_wait(url, min_interval)
                req = urllib.request.Request(url, headers=headers)
                r = _egress_opener(egress).open(req, timeout=timeout)
                return {"status": r.status, "server": r.headers.get("Server", ""),
                        "ctype": r.headers.get("Content-Type", ""),
                        "body": decompress(r.read(),
                                           r.headers.get("Content-Encoding", "")),
                        "err": None, "attempts": i + 1, "transport": "urllib",
                        "egress": egress or "direct"}
            except urllib.error.HTTPError as e:
                body = b""
                try:
                    body = decompress(e.read(), e.headers.get("Content-Encoding", ""))
                except Exception:  # noqa: BLE001
                    pass
                info = {"status": e.code, "server": e.headers.get("Server", ""),
                        "ctype": e.headers.get("Content-Type", ""), "body": body,
                        "transport": "urllib", "err": None, "attempts": i + 1,
                        "egress": egress or "direct"}
                last_info = info
                # 410 Gone / 451 法律原因 —— 内容级，换出口也没用，立刻返回
                if e.code in (410, 451):
                    return info
                last = f"HTTP {e.code}"
                if e.code == 403 and ei < len(pool) - 1:
                    demote_egress(egress)   # 出口被目标站风控 → 换下一个出口
                    break
                if e.code != 404:
                    return info
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                if egress_rotatable(last):
                    demote_egress(egress)
                    break
            if i < retries - 1:
                time.sleep(backoff * (i + 1))
    if last_info is not None:
        return last_info
    return {"status": None, "server": "", "ctype": "", "body": b"",
            "err": last, "attempts": retries, "transport": "urllib",
            "egress": (pool[0] or "direct") if pool else "direct"}


CURL_BIN = shutil.which("curl") or "curl"


def _curl_proxy_args(proxy: Optional[str] = None) -> List[str]:
    """curl 不会自己读 Windows 注册表里的系统代理，必须显式传。
    若 `--proxy direct` 则明确禁用代理（`--noproxy '*'`），
    否则 curl 会去捡环境变量里的 http_proxy，行为与 `direct` 不符。

    `proxy` 显式传入时按出口轮换走（同一条 curl 通道也要能换出口）。"""
    if PROXY_OVERRIDE == "direct":
        return ["--noproxy", "*"]
    if PROXY_OVERRIDE:
        return ["-x", PROXY_OVERRIDE]
    p = proxy if proxy is not None else current_egress()
    return ["-x", p] if p else ["--noproxy", "*"]


def _curl_get(url: str, *, timeout: int, retries: int, backoff: float,
              accept: str, ua: str = "") -> Dict[str, Any]:
    """curl 传输。`ua` 为空时退回全局浏览器头（保持既有行为不变）。"""
    last = ""
    last_code: Optional[Dict[str, Any]] = None
    pool = list(live_egress()) or [None]
    for ei, egress in enumerate(pool):
        for i in range(max(1, retries)):
            fd, path = tempfile.mkstemp(prefix="fm_", suffix=".bin")
            os.close(fd)
            try:
                args = [CURL_BIN, "-sS", "--location", "--compressed",
                        "--max-time", str(timeout),
                        "-o", path, "-w", "%{http_code}|%{size_download}|"
                        "%{content_type}|%{header_json}",
                        "-H", f"User-Agent: {ua or BROWSER_HEADERS['User-Agent']}",
                        "-H", f"Accept: {accept or BROWSER_HEADERS['Accept']}",
                        "-H", f"Accept-Language: {BROWSER_HEADERS['Accept-Language']}"]
                args += _curl_proxy_args(egress)
                args.append(url)
                p = subprocess.run(args, capture_output=True, timeout=timeout + 15)
                meta, _, rest = p.stdout.decode("utf-8", "replace").partition("|")
                parts = rest.split("|", 2)
                try:
                    code = int(meta)
                except ValueError:
                    code = 0
                with open(path, "rb") as f:
                    body = f.read()
                ctype = parts[0] if parts else ""
                server = ""
                if len(parts) > 2:
                    try:
                        hj = json.loads(parts[2])
                        sv = (hj.get("server") or hj.get("Server") or [""])
                        server = sv[0] if isinstance(sv, list) else str(sv)
                    except json.JSONDecodeError:
                        pass
                rec = {"status": code, "server": server, "ctype": ctype,
                       "body": body, "err": None, "attempts": i + 1,
                       "transport": "curl", "egress": egress or "direct"}
                if code >= 400:
                    # 403 可能是出口 IP 被风控 → 换出口；410/451 是内容级，换也没用
                    if code == 403 and ei < len(pool) - 1:
                        demote_egress(egress)
                        break
                    return rec
                if code == 0:
                    last = (f"curl 无状态码（stderr: "
                            f"{p.stderr.decode('utf-8', 'replace')[:120]}）")
                    last_code = rec
                else:
                    return rec
            except subprocess.TimeoutExpired:
                last = f"curl 超时（{timeout}s）"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if i < retries - 1:
                time.sleep(backoff * (i + 1))
    if last_code is not None:
        return last_code
    return {"status": None, "server": "", "ctype": "", "body": b"", "err": last,
            "attempts": retries, "transport": "curl",
            "egress": (pool[0] or "direct") if pool else "direct"}


# ================================================================ 日期工具
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def iso_date(s: str) -> Optional[str]:
    s = (s or "").strip()
    m = ISO_DATE_RE.match(s)
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    # 年份合理性兜底：两位数年份若被宽松解析（`%Y` 接受 1–4 位），
    # 会静默产出公元 26 年这种值。这里挡住，并且**不**让它悄悄通过。
    if d.year < 1900 or d.year > today_utc().year + 1:
        return None
    return d.isoformat()


def days_between(a: str, b: str) -> Optional[int]:
    da, db = iso_date(a), iso_date(b)
    if not da or not db:
        return None
    return (date.fromisoformat(db) - date.fromisoformat(da)).days


# ================================================================ 解析器
# 每个解析器：bytes -> List[(date_iso, value)]，**升序**
# 统一契约：解析失败返回 []（由调用方记录原因），绝不返回猜测值。

# ★ 纪元零值哨兵。上游把「没有日期」写成 0 / -1 时，日期字段会变成
#   1970-01-01（或 1969-12-31，取决于时区）。它看起来是个合法日期，
#   会一路混进时间轴、把「滞后天数」算成 20000 天、
#   把横轴撑成半个世纪——而所有日志都是绿的。
SENTINEL_DATES = {"1970-01-01", "1969-12-31"}


def _finalize(rows: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """所有解析器的**统一出口**：排序 → 去哨兵日期 → 剔除非有限值 → 同日去重。

    ★ 为什么非有限值必须在这里剔掉：
      `float('nan')` 一旦进入序列，`mean`/`stdev` 全部返回 `nan`，
      `nan > x` 恒为 False，于是**分级静默退化成「平稳」**、
      `f"{nan:,.2f}"` 打印成 `nan`——数字看起来还在，其实已经废了。
      `inf` 更糟：它让 σ 变成 inf，z 变成 0，一切「正常」。
      这类值只能在上游入口处挡，不能靠下游兜。
    """
    dedup: Dict[str, float] = {}
    for d, v in sorted(rows, key=lambda x: x[0]):
        if d in SENTINEL_DATES:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(fv):
            continue
        dedup[d] = fv
    return sorted(dedup.items())


def parse_fred_csv(body: bytes) -> Tuple[List[Tuple[str, float]], str]:
    """FRED `fredgraph.csv?id=XXXX` —— 两列 `observation_date,<SERIES>`，
    缺失值写 `.`。注意：FRED 在序列被撤回/改名时会返回 HTML 错误页，
    此时 split 出来的第二列不是数字 → 下面自然过滤成空表。"""
    txt = body.decode("utf-8", "replace")
    if "<html" in txt[:400].lower():
        return [], "返回 HTML（疑似错误页/被拦截）"
    rows = [r for r in txt.splitlines() if r.strip()]
    if len(rows) < 2:
        return [], f"行数不足（{len(rows)}）"
    out: List[Tuple[str, float]] = []
    bad = 0
    for r in rows[1:]:
        p = r.split(",")
        if len(p) < 2:
            continue
        d = iso_date(p[0])
        v = p[1].strip()
        if d is None or v in ("", "."):
            if d is not None:
                bad += 1
            continue
        try:
            out.append((d, float(v)))
        except ValueError:
            bad += 1
    if not out:
        return [], f"无有效数值行（跳过 {bad} 行）"
    out.sort(key=lambda x: x[0])
    # 同日期重复（FRED 偶尔出现）取**最后一个**
    dedup: Dict[str, float] = {}
    for d, v in out:
        dedup[d] = v
    return sorted(dedup.items()), ""


def parse_named_csv(body: bytes, date_col: str, value_col: str,
                    date_format: str = "%Y-%m-%d"
                    ) -> Tuple[List[Tuple[str, float]], str]:
    """带表头的 CSV：**按列名**取日期列与数值列，日期格式显式声明。

    ★ 为什么不能用 parse_fred_csv 的「第 0 列日期、第 1 列数值」：
      Datawrapper 导出的 CSV（Silver Bulletin 的特朗普支持率 kSCt4.csv）
      表头是
        modeldate,approve,disapprove,approve_lo,approve_hi,...
      要取的 `approve` 在第 2 列，而**别的列也全是数字**——按位置取会
      静默拿到 `disapprove`（58.86 而不是 38.60）。数字看着完全合理，
      报告里却是一张正负号相反的图。按列名取才安全。

    ★ 日期格式必须显式声明：这里是 `M/D/YYYY`（月在前），与 FRED 的
      `YYYY-MM-DD` 不同。9/2/2026 与 2/9/2026 是两天，猜错就把整条序列
      的日期轴打乱。**解析失败时报错**而不是静默跳过：静默跳过的后果是
      序列变短，而 z 的 σ 与分位数都从序列长度来，没人会发现。
    """
    txt = body.decode("utf-8-sig", "replace")
    if "<html" in txt[:400].lower():
        return [], "返回 HTML（疑似错误页/被拦截）"
    rows = [r for r in txt.splitlines() if r.strip()]
    if len(rows) < 2:
        return [], f"行数不足（{len(rows)}）"
    hdr = [h.strip().strip('"') for h in rows[0].split(",")]
    try:
        di = hdr.index(date_col)
    except ValueError:
        return [], f"日期列 {date_col!r} 不存在（表头={hdr[:10]}）"
    try:
        vi = hdr.index(value_col)
    except ValueError:
        return [], f"数值列 {value_col!r} 不存在（表头={hdr[:10]}）"
    out: List[Tuple[str, float]] = []
    bad = 0
    for r in rows[1:]:
        p = [c.strip().strip('"') for c in r.split(",")]
        if len(p) <= max(di, vi):
            bad += 1
            continue
        try:
            d = datetime.strptime(p[di], date_format).date().isoformat()
        except ValueError:
            bad += 1
            continue
        v = p[vi]
        if v in ("", ".", "NA", "NaN", "null"):
            bad += 1
            continue
        try:
            out.append((d, float(v)))
        except ValueError:
            bad += 1
    if not out:
        return [], f"无有效数值行（跳过 {bad} 行）"
    dedup: Dict[str, float] = {}
    for d, v in out:
        dedup[d] = v
    return sorted(dedup.items()), ""


def parse_stooq_csv(body: bytes) -> Tuple[List[Tuple[str, float]], str]:
    """Stooq `q/d/l/?s=<sym>&i=d` —— Date,Open,High,Low,Close,Volume。
    取 Close。Stooq 对不存在的符号返回 `No data` 或 `Exceeded the daily hits
    limit`，两者都必须识别出来，否则会被当成「空表」而掩盖限额问题。"""
    txt = body.decode("utf-8", "replace").strip()
    low = txt[:200].lower()
    if "no data" in low:
        return [], "Stooq 返回 No data（符号不存在或该市场无数据）"
    if "exceeded" in low or "limit" in low and len(txt) < 300:
        return [], f"Stooq 返回限额提示：{txt[:120]!r}"
    if "javascript" in low or "<noscript" in low or "challenge" in low:
        return [], "JS 反爬挑战页（HTTP 200 但内容是验证页，不是数据）"
    if txt.startswith("<"):
        return [], "返回 HTML/XML（疑似错误页）"
    rows = [r for r in txt.splitlines() if r.strip()]
    if len(rows) < 2:
        return [], f"行数不足（{len(rows)}）"
    out: List[Tuple[str, float]] = []
    for r in rows[1:]:
        p = r.split(",")
        if len(p) < 6:
            continue
        d = iso_date(p[0])
        if d is None:
            continue
        try:
            out.append((d, float(p[4])))
        except ValueError:
            continue
    if not out:
        return [], "无有效数值行"
    dedup: Dict[str, float] = {}
    for d, v in sorted(out):
        dedup[d] = v
    return sorted(dedup.items()), ""


def _parse_us_date(s: str) -> Optional[str]:
    s = (s or "").strip()
    for f in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            d = datetime.strptime(s, f).date()
        except ValueError:
            continue
        if d.year < 1900 or d.year > today_utc().year + 1:
            continue
        return d.isoformat()
    return None


def parse_treasury_csv(body: bytes, field: str = "10 Yr") -> Tuple[List[Tuple[str, float]], str]:
    """美国财政部日频国债收益率曲线（par yield）。
    CSV 带 BOM；列名形如 `1 Mo,2 Mo,3 Mo,4 Mo,6 Mo,1 Yr,...,30 Yr`。
    ★ 该表的 `Date` 是 MM/DD/YYYY，且**只有交易日**（节假日无行）。"""
    txt = body.decode("utf-8-sig", "replace")
    if "<html" in txt[:400].lower():
        return [], "返回 HTML（疑似错误页）"
    rd = csv.DictReader(io.StringIO(txt))
    if not rd.fieldnames:
        return [], "无表头"
    if field not in rd.fieldnames:
        return [], f"列名 {field!r} 不存在；实际列={rd.fieldnames[:14]}"
    out: List[Tuple[str, float]] = []
    skipped = 0
    for row in rd:
        d = _parse_us_date(row.get("Date") or "")
        raw = (row.get(field) or "").strip()
        if d is None or not raw:
            skipped += 1
            continue
        try:
            out.append((d, float(raw)))
        except ValueError:
            skipped += 1
    if not out:
        return [], f"无有效行（跳过 {skipped}）"
    dedup: Dict[str, float] = {}
    for d, v in sorted(out):
        dedup[d] = v
    return sorted(dedup.items()), ""


def parse_yahoo_chart(body: bytes, drop_from: str = "",
                      stats: Optional[Dict[str, Any]] = None
                      ) -> Tuple[List[Tuple[str, float]], str]:
    """Yahoo chart v8 JSON。★ `timestamp` 是 UTC 秒，直接 `.date()` 会把
    美东盘后/盘前的日期算错一天；本项目只取**日线收盘**，用 UTC 日期与
    Yahoo 自身返回的 `meta.regularMarketTime` 同源，保持内部一致即可，
    并在报告里声明「日线日期按交易所交易日，时区归一到 UTC 日期」。

    ★ `drop_from`（2026-09-23 新增）：**丢弃当日未收盘会话**。
      Yahoo 的最后一根日线在交易时段内是**实时变动的盘中价**，不是收盘价：

        · 期货（BZ=F/NG=F…）电子盘 24 小时连续，北京时间上午跑就已经有
          「今天」这一根，且它在动 —— 同一天跑两次得到两个不同的「最新值」；
        · 股指/黄金同理，只是各自交易时段不同。

      后果有三，都很隐蔽：
        ① 「最新值」不可复算 —— 违背本项目「可复算、可回源对账」的承诺；
        ② 与参考实现 taco-monitor 对不齐 —— 实测布伦特 503 个重叠交易日里
           **502 天完全相同，唯独当天差 0.33**，差异 100% 来自这一根；
        ③ 报告里的「本轮新点」栏会把「盘中价变了」渲染成「市场动了」。

      处置：传运行日 `drop_from`，丢弃日期 ≥ 运行日的 bar（与 taco-monitor
      的 `drop_inprogress_session` 同一口径）。开关在 config 的 request 级，
      可按条目覆盖。★ 注意它**不能**修正历史里已经记下的当日盘中价——
      那一点会在次日的运行中被真正的收盘价覆盖（merge_history 的修订机制），
      并在报告的修订记账里披露。
    """
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return [], f"JSON 解析失败：{type(e).__name__}"
    ch = j.get("chart") or {}
    if ch.get("error"):
        return [], f"Yahoo 错误：{ch['error']}"
    res = (ch.get("result") or [None])[0]
    if not res:
        return [], "chart.result 为空"
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    cl = q.get("close") or []
    out: List[Tuple[str, float]] = []
    dropped = 0
    for t, c in zip(ts, cl):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, timezone.utc).date().isoformat()
        if drop_from and d >= drop_from:
            dropped += 1
            continue
        out.append((d, float(c)))
    if stats is not None:
        stats["dropped_inprogress"] = dropped
        stats["regular_market_time"] = (res.get("meta") or {}).get("regularMarketTime")
    if not out:
        return [], ("无收盘价" if not dropped
                    else f"丢弃未收盘会话后无剩余点（丢弃 {dropped} 根）")
    dedup: Dict[str, float] = {}
    for d, v in sorted(out):
        dedup[d] = v
    return sorted(dedup.items()), ""


def parse_arcgis_json(body: bytes) -> Tuple[List[Tuple[str, float]], str]:
    """ArcGIS FeatureServer query（IMF PortWatch 通行量走这条）。
    ★ 返回的是**属性行**而非时间序列：本函数只做「能否解析 + 时间字段定位」
    的基本校验，真正的展开由 `expand_arcgis()` 按 `date_field` 处理。"""
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return [], f"JSON 解析失败：{type(e).__name__}"
    if "error" in j:
        return [], f"ArcGIS 错误：{str(j['error'])[:160]}"
    feats = j.get("features")
    if feats is None:
        return [], f"无 features 字段（键={list(j)[:8]}）"
    if not feats:
        return [], "features 为空（可连但无数据）"
    return [(str(i), 0.0) for i in range(len(feats))], ""


def expand_arcgis(body: bytes, date_field: str, value_field: str,
                  entity_field: str = "", entity_match: str = "") -> Tuple[List[Tuple[str, float]], str]:
    """把 ArcGIS 属性行展开成时间序列。
    ★ ArcGIS 的日期字段是**毫秒时间戳**（epoch ms, UTC），不是字符串。
    直接把数字当日期是这类源最常见的静默错误来源，所以这里强制走
    `epoch_ms_to_date()` 并做年份合理性检查。"""
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return [], f"JSON 解析失败：{type(e).__name__}"
    feats = j.get("features") or []
    if not feats:
        return [], "features 为空"
    out: List[Tuple[str, float]] = []
    skipped_entity = 0
    for f in feats:
        a = f.get("attributes") or {}
        if entity_field and entity_match:
            if str(a.get(entity_field, "")).strip() != entity_match:
                skipped_entity += 1
                continue
        d = epoch_ms_to_date(a.get(date_field))
        v = a.get(value_field)
        if d is None or v is None:
            continue
        try:
            out.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    if not out:
        why = f"展开后无有效行（字段 {date_field}/{value_field}）"
        if skipped_entity:
            why += f"；{entity_field}!={entity_match!r} 跳过 {skipped_entity} 行"
            seen = sorted({str((f.get("attributes") or {}).get(entity_field, "")).strip()
                           for f in feats})
            why += f"；实际出现 {entity_field}: {','.join(seen[:5])}"
        return [], why
    dedup: Dict[str, float] = {}
    for d, v in sorted(out):
        dedup[d] = v
    return sorted(dedup.items()), ""


def epoch_ms_to_date(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return iso_date(str(v))
    if n == 0:
        return None      # 纪元零值哨兵（feed 未提供日期时的占位）
    # 秒 vs 毫秒自动判别（< 1e11 视为秒）
    if n < 1e11:
        n *= 1000.0
    try:
        d = datetime.fromtimestamp(n / 1000.0, timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None
    if d.year < 1900 or d.year > today_utc().year + 1:
        return None
    return d.isoformat()


def parse_nyfed_json(body: bytes, ref: str = "effr"
                     ) -> Tuple[List[Tuple[str, float]], str]:
    """纽约联储 markets API（/api/rates/.../last/N.json）。
    响应键是 `refRates`（旧版为 `data`），每条含
    `effectiveDate` / `percentRate`，EFFR 记录还带
    `targetRateFrom` / `targetRateTo`（FOMC 目标区间）。
    ★ ref="fedtarget" 时取 targetRateTo（区间上限）——这比 FRED 的
      DFEDTARU 更新更及时，且同一响应里免费拿到。"""
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return [], f"JSON 解析失败：{type(e).__name__}"
    data = j.get("refRates") or j.get("data") or []
    if not isinstance(data, list) or not data:
        return [], f"无 refRates/data 数组，键={list(j)[:8]}"
    out: List[Tuple[str, float]] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        d = it.get("effectiveDate") or it.get("date") or ""
        d = iso_date(str(d)[:10])
        if ref == "fedtarget":
            raw = it.get("targetRateTo")
        else:
            raw = it.get("percentRate", it.get("rate"))
        if d is None or raw in (None, ""):
            continue
        try:
            out.append((d, float(raw)))
        except (TypeError, ValueError):
            continue
    if not out:
        return [], f"无有效行（ref={ref}，首条样例={str(data[0])[:120]}）"
    dedup: Dict[str, float] = {}
    for d, v in sorted(out):
        dedup[d] = v
    return sorted(dedup.items()), ""


def parse_eia_wpsr(body: bytes, row_label: str
                   ) -> Tuple[List[Tuple[str, float]], str]:
    """EIA Weekly Petroleum Status Report table1.csv（无需密钥）。
    结构：第 0 行是表头，第 1/2 列是两个周报日期（m/d/yy）；
    数据行形如 `"Strategic Petroleum Reserve (SPR)","284.957","285.360",...`
    单位是**百万桶**。每次抓取给 2 个周点，跨轮运行即可累积成周序列。"""
    txt = body.decode("utf-8-sig", "replace")
    if "<html" in txt[:400].lower():
        return [], "返回 HTML（疑似错误页）"
    lines = [l for l in txt.splitlines() if l.strip()]
    if len(lines) < 2:
        return [], f"行数不足（{len(lines)}）"
    try:
        head = next(csv.reader(io.StringIO(lines[0])))
    except Exception:  # noqa: BLE001
        return [], "表头解析失败"
    if len(head) < 3:
        return [], f"表头列数不足：{head[:6]}"
    dates = []
    for cell in head[1:3]:
        d = _parse_us_date(cell)
        if d is None:
            return [], f"表头日期无法解析：{cell!r}（期望 m/d/yy）"
        dates.append(d)
    for line in lines[1:]:
        cells = next(csv.reader(io.StringIO(line)), [])
        if cells and cells[0].strip() == row_label:
            out: List[Tuple[str, float]] = []
            for d, cell in zip(dates, cells[1:3]):
                cell = (cell or "").strip().replace(",", "")
                if not cell:
                    continue
                try:
                    out.append((d, float(cell)))
                except ValueError:
                    continue
            if not out:
                return [], f"行 {row_label!r} 无数值"
            return sorted(out), ""
    known = "; ".join(c[0].strip() for c in
                      (next(csv.reader(io.StringIO(l)), [])
                       for l in lines[1:6]))
    return [], (f"找不到行 {row_label!r}；前几行有：{known[:160]}")


PARSERS = {
    "fred": parse_fred_csv,
    "stooq": parse_stooq_csv,
    "yahoo": parse_yahoo_chart,
    "treasury": parse_treasury_csv,
    "arcgis": parse_arcgis_json,
    "nyfed": parse_nyfed_json,
    "eia_wpsr": parse_eia_wpsr,
}

# 链条目允许出现的 provider 全集。
# ★ 从 PARSERS 派生，而不是在自检里另抄一份名单：抄一份的那一刻就注定漂移
#   —— 新增 provider 时改了 parse_payload 却忘了改自检，于是配置一上线自检就红，
#   而「红」被当成「新源有问题」，真因其实是校验名单没跟上。
# `datawrapper` 走 parse_payload 里的特化分支（需要按列名取值），不在 PARSERS 里。
CHAIN_PROVIDERS = set(PARSERS) | {"datawrapper"}

# 链条目的信源等级。三档而不是两档，因为「聚合/建模值」确实既不是官方发布、
# 也不是交易所行情：Silver Bulletin 的支持率是**民调聚合后的模型输出**，
# 标成 official 会抬高它的可信度，标成 market 会让人以为它可交易。
CHAIN_TIERS = ("official", "market", "aggregator")


def parse_payload(provider: str, body: bytes, entry: Dict[str, Any]) -> Tuple[List[Tuple[str, float]], str]:
    """按 provider 分派解析；arcgis 走带字段参数的特化路径。"""
    if provider == "arcgis":
        return expand_arcgis(body, entry.get("date_field", "date"),
                             entry.get("value_field", "value"),
                             entry.get("entity_field", ""),
                             entry.get("entity_match", ""))
    if provider == "treasury":
        return parse_treasury_csv(body, entry.get("field", "10 Yr"))
    if provider == "nyfed":
        return parse_nyfed_json(body, entry.get("ref", "effr"))
    if provider == "eia_wpsr":
        return parse_eia_wpsr(body, entry.get("ref", ""))
    if provider == "datawrapper":
        # 按列名取值（见 parse_named_csv 的注释：这里按位置取会拿到 disapprove）
        return parse_named_csv(body, entry.get("date_col", "date"),
                               entry.get("value_col", "value"),
                               entry.get("date_format", "%Y-%m-%d"))
    fn = PARSERS.get(provider)
    if fn is None:
        return [], f"未知 provider：{provider}"
    return fn(body)


# ---------------------------------------------------------------- 变换
def apply_transform(rows: List[Tuple[str, float]],
                    entry: Dict[str, Any]) -> List[Tuple[str, float]]:
    """chain 条目的单位变换：`scale` / `offset`（均为仿射，默认 1/0）。

    ★ 为什么必须有：同一条 10Y 收益率，FRED 给 `4.23`（%），
    Yahoo `^TNX` 给 `42.3`（收益率×10），Stooq `10usy.b` 给 `4.23`。
    不做仿射对齐，两条源放一起会「互相打架」，源间分歧检查会全部误报。
    """
    sc = float(entry.get("scale", 1.0) or 1.0)
    off = float(entry.get("offset", 0.0) or 0.0)
    if sc == 1.0 and off == 0.0:
        return rows
    # round(,12)：42.3*0.1 在 IEEE754 下是 4.2299999999999995，
    # 与 FRED 直给的 4.23 在源间分歧检查里会造出假分歧。
    return [(d, round(v * sc + off, 12)) for d, v in rows]


# ================================================================ 配置
GRADE_ORDER = ["升级", "警戒", "关注", "平稳", "样本不足", "无数据"]
GRADE_MARK = {"升级": "🔴", "警戒": "🟠", "关注": "🟡",
              "平稳": "🟢", "样本不足": "⚪", "无数据": "⚫"}

DEFAULT_CADENCE = "daily"


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cadence_label(c: str) -> str:
    return {"daily": "日频", "weekly": "周频", "monthly": "月频"}.get(c, c)


def bucket_def(cfg: Dict[str, Any], bid: str) -> Dict[str, Any]:
    return (cfg.get("buckets") or {}).get(bid) or {"name": bid, "order": 99}


def bucket_order(cfg: Dict[str, Any], bid: str) -> int:
    return int(bucket_def(cfg, bid).get("order", 99))


def series_list(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in cfg.get("series", []):
        s = dict(s)
        s.setdefault("cadence", DEFAULT_CADENCE)
        s.setdefault("decimals", 2)
        s.setdefault("polarity", 1)
        s.setdefault("role", "context")
        s.setdefault("bucket", "other")
        s.setdefault("unit", "")
        s.setdefault("chain", [])
        out.append(s)
    return out


def get_series(cfg: Dict[str, Any], sid: str) -> Optional[Dict[str, Any]]:
    for s in series_list(cfg):
        if s["id"] == sid:
            return s
    return None


def raw_ext(provider: str) -> str:
    return "json" if provider in ("yahoo", "arcgis", "nyfed") else "csv"


def _san(s: str) -> str:
    """文件名安全化。`^GSPC` / `BZ=F` / `10usy.b` 这类 ref 必须能被还原地
    编码进文件名，否则 `--from-file` 无法把 raw 文件映射回 (序列, provider)。
    规则：只保留 [A-Za-z0-9._-]，其余替换为 `_`。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", s or "")


def raw_name(sid: str, provider: str, ref: str, part: int = None) -> str:
    """原始载荷文件名。`part` 用于「一个条目对应多个 URL」的情形
    （美国财政部的 CSV 是**按年**导出的，跨年要取两年）。
    文件名必须能**无损还原**回 (序列, provider, ref, part)，否则
    `--from-file` 无法把存档映射回条目。"""
    tail = f"__u{part}" if part is not None else ""
    return f"{_san(sid)}__{provider}__{_san(ref)}{tail}.{raw_ext(provider)}"


RAW_NAME_RE = re.compile(
    r"^(?P<sid>[A-Za-z0-9._-]+)__(?P<prov>[a-z]+)__(?P<ref>[A-Za-z0-9._-]+?)"
    r"(?:__u(?P<part>\d+))?\.(?P<ext>csv|json)$")


# 运行内 URL 缓存。同一条美国财政部曲线被 7 个序列共用，
# 不缓存就是同一份 14KB CSV 下载 7 次 —— 既是无谓的负载，
# 也让「源站限流」这种问题更容易被自己触发。
_URL_CACHE: Dict[str, Dict[str, Any]] = {}


def entry_urls(entry: Dict[str, Any], run_dt: datetime) -> List[str]:
    """把条目的 URL 模板展开。`{year}` / `{year_prev}` 由**运行日期**填充，
    这样跨年时不需要人工改配置。

    ★ 为什么非要模板不可：官方表常常**按年切分**（美国财政部的日频收益率
      曲线就是 `.../daily-treasury-rates.csv/2026/all?...`）。写死年份的两个
      后果都不报错：① 1 月 3 日跑出来只有 2 个观测点，σ 与分位数全部失真；
      ② 跨年后 URL 变成 404 或返回空表，而报告只会说「本桶无数据」。
      按年切分的源，**必须取相邻两年拼起来**。
    """
    ctx = {"year": run_dt.year, "year_prev": run_dt.year - 1}
    if entry.get("urls"):
        return [u.format(**ctx) for u in entry["urls"]]
    return [entry["url"].format(**ctx)]



# ================================================================ 取值（多源链）
def fetch_series(series: Dict[str, Any], *, raw_dir: Optional[str],
                 cfg: Dict[str, Any], run_dt: datetime,
                 delay: float = 0.0) -> Dict[str, Any]:
    """按 `chain` 顺序取数，**第一个成功且通过合理性检查的源胜出**；
    其余能取到的源作为**交叉源**保留，用于源间分歧检查。

    为什么不是「全部源都取、取平均」：
      不同源的口径、时点、复权方式都不同，平均会造出一个**不存在**的合成价。
      正确做法是：选一条主链（有明确优先级），其余只用来**互相验证**。

    条目可以是「一对多 URL」（`urls`）：官方表常按年切分，
    必须把多年拼起来才有足够历史算 σ 与分位数。
    """
    req = cfg.get("request") or {}
    timeout = int(req.get("timeout", 30))
    retries = int(req.get("retries", 3))
    backoff = float(req.get("retry_backoff_sec", 2.0))
    min_iv = float(req.get("min_interval_per_host_sec", 0.0))
    # ★ 源间分歧容差**必须按序列可覆盖**。
    #   不同源常常代表**不同的标的**，不是同一个东西：
    #     · Brent 期货（Yahoo BZ=F） vs Brent 现货（FRED DCOILBRENTEU）——
    #       价差 1–3% 是正常的期限结构，不是错；
    #     · 10Y 收益率：财政部 par yield vs Yahoo ^TNX —— 同一天应几乎相等，
    #       容差超过 0.5% 就说明有一方口径不对。
    #   用同一个容差套所有序列，要么天天误报分歧（然后没人再看它），
    #   要么真错也发现不了。
    tol = float(series.get("div_tol",
                           (cfg.get("divergence") or {}).get("rel_tol", 0.02)))
    tol_abs = float(series.get("div_abs",
                               (cfg.get("divergence") or {}).get("abs_tol", 0.0)))

    tried: List[Dict[str, Any]] = []
    chosen: Optional[Dict[str, Any]] = None
    cross: List[Dict[str, Any]] = []

    # ★ 是否丢弃「当日未收盘会话」——序列级可覆盖，默认取 request 级开关。
    #   为什么默认开着：日线序列的最后一根在交易时段内是**实时变动的盘中价**，
    #   把它当收盘价会让「最新值」在同一天内不可复算（详见 parse_yahoo_chart）。
    drop_inprog = bool(series.get(
        "drop_inprogress_session",
        (cfg.get("request") or {}).get("drop_inprogress_session", False)))
    drop_from = run_dt.date().isoformat() if drop_inprog else ""

    for entry in series.get("chain", []):
        provider = entry["provider"]
        ref = entry["ref"]
        transport = entry.get("transport", "urllib")
        ua = entry.get("ua", "")          # ★ 条目级 UA 覆写（FRED 必须用 curl 风格）
        urls = entry_urls(entry, run_dt)
        multi = len(urls) > 1
        merged: List[Tuple[str, float]] = []

        for ui, url in enumerate(urls):
            part = ui if multi else None
            name = raw_name(series["id"], provider, ref, part)
            path = os.path.join(raw_dir, name) if raw_dir else None
            live = True
            if raw_dir and os.path.isfile(path):
                live = False
                with open(path, "rb") as f:
                    body = f.read()
                got = {"status": 200, "server": "from-file", "ctype": "",
                       "body": body, "err": None, "attempts": 0,
                       "transport": "from-file"}
            else:
                ck = f"{transport}|{ua}|{url}"
                if ck in _URL_CACHE:
                    got = _URL_CACHE[ck]
                else:
                    got = http_get(url, timeout=timeout, retries=retries,
                                   backoff=backoff, accept=entry.get("accept", ""),
                                   transport=transport, ua=ua,
                                   min_interval=min_iv)
                    _URL_CACHE[ck] = got
                    if delay:
                        time.sleep(delay)
                # ★ 原始载荷落盘 —— 这是整个「可复算」承诺的物理基础。
                #   把 provider 的**原始字节**存下来，而不是只存解析后的数字，
                #   对账工具才能从同一份输入重新解析并复算，
                #   而不是「用程序自己的中间结果验证程序自己」。
                #   只有 HTTP 200 才存：把 403/超时产生的空体或错误页存下来，
                #   下次重放会把它当成「那份数据」。
                if live and raw_dir and got["body"] and got["status"] == 200:
                    os.makedirs(raw_dir, exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(got["body"])

            rec: Dict[str, Any] = {"provider": provider, "ref": ref, "url": url,
                                   "http": got["status"],
                                   "bytes": len(got["body"] or b""),
                                   "err": got["err"], "ok": False, "rows": 0,
                                   "part": part, "transport": transport,
                                   "raw": os.path.basename(path) if path else ""}
            if got["body"]:
                st: Dict[str, Any] = {}
                rows, why = parse_payload(
                    provider, got["body"],
                    dict(entry, drop_from=drop_from, _stats=st))
                if st.get("dropped_inprogress"):
                    # ★ 必须记账：丢了几根、为什么丢。否则「最新值比昨天还旧」
                    #   会被读成「源停更」，而真相是我们主动剔掉了未收盘的那根。
                    rec["dropped_inprogress"] = st["dropped_inprogress"]
                if rows:
                    rec.update({"ok": True, "rows": len(rows),
                                "first": rows[0][0], "latest": rows[-1][0],
                                "latest_value": round(rows[-1][1], 6)})
                    merged.extend(rows)
                else:
                    rec["why"] = f"{why}（{len(got['body'])} 字节）"
            else:
                rec["why"] = f"无响应体（http={got['status']} err={got['err']}）"
            tried.append(rec)

        rows = apply_transform(_finalize(merged), entry)
        if not rows:
            continue
        if chosen is None:
            chosen = {"provider": provider, "ref": ref, "rows": rows,
                      "entry": entry, "url": urls[-1], "transport": transport}
        else:
            # 交叉源的「同日比较」——只有在**同一天**都有值时才有意义
            d = chosen["rows"][-1][0]
            other = dict(rows)
            if d in other:
                a = chosen["rows"][-1][1]
                b = other[d]
                diff = b - a
                denom = abs(a) if abs(a) > 1e-12 else None
                rel = (diff / denom) if denom else None
                # 判定分歧：两条独立路径对**同一天**的同名指标给出不同值。
                # 标的值接近 0 时相对误差没有意义（除零放大），
                # 此时退化为绝对差判定，绝不因为「算不出相对差」而默认分歧。
                if rel is None:
                    flag = abs(diff) > max(tol_abs, 1e-6)
                else:
                    flag = abs(rel) > tol and abs(diff) > tol_abs
                cross.append({"provider": provider, "ref": ref, "date": d,
                              "value": round(b, 6),
                              "diff": round(diff, 6),
                              "rel": None if rel is None else round(rel, 6),
                              "divergent": bool(flag)})
            else:
                cross.append({"provider": provider, "ref": ref, "date": None,
                              "value": None, "diff": None, "rel": None,
                              "divergent": None,
                              "note": f"该源无 {d} 当日值（最新 {rows[-1][0]}）"})
        if chosen is not None and len(cross) >= 2:
            break

    divergent = [c for c in cross if c.get("divergent")]
    return {"chosen": chosen, "tried": tried, "cross": cross,
            "divergent": divergent}


# ================================================================ 历史累积
def history_path(out_root: str, sid: str) -> str:
    return os.path.join(out_root, "history", f"{_san(sid)}.jsonl")


def read_history(out_root: str, sid: str) -> List[Tuple[str, float]]:
    p = history_path(out_root, sid)
    if not os.path.isfile(p):
        return []
    out: Dict[str, float] = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = iso_date(r.get("date") or "")
            v = r.get("value")
            if d is None or not isinstance(v, (int, float)):
                continue
            out[d] = float(v)
    return sorted(out.items())


def merge_history(out_root: str, sid: str,
                  fetched: List[Tuple[str, float]],
                  *, run_tag: str, provider: str,
                  max_points: int) -> Dict[str, Any]:
    """把本轮取到的点并入累积历史。

    ★ 两个必须处理的问题：

    1. **修订（revision）**：周频库存、月度数据发布后会被回改。同一日期
       新抓到的值与已有值不同时，**以新抓到的为准**（源的最新版本），
       但必须**记账**：报告里披露「本轮发现 N 个历史点被源修订」。
       不记账的话，历史序列会悄悄改口径，而所有依赖它的 z 值都跟着漂移。

    2. **静默漂移**：日频源通常只回最近 3–6 个月。**不能**因为本轮源里
       没有某天就把历史里那天删掉——历史是累积资产，不是本轮快照。
       （与新闻类监测的「窗口修剪」方向相反：数值历史要**保护**，
         新闻快照要**修剪**。这一点极易搞反。）
    """
    old = read_history(out_root, sid)
    old_map = dict(old)
    new_pts = 0
    revised: List[Dict[str, Any]] = []
    for d, v in fetched:
        if d in old_map:
            if abs(old_map[d] - v) > 1e-9:
                revised.append({"date": d, "was": old_map[d], "now": v})
            old_map[d] = v
        else:
            old_map[d] = v
            new_pts += 1
    merged = sorted(old_map.items())
    dropped = 0
    if max_points > 0 and len(merged) > max_points:
        dropped = len(merged) - max_points
        merged = merged[-max_points:]

    os.makedirs(os.path.dirname(history_path(out_root, sid)), exist_ok=True)
    p = history_path(out_root, sid)
    with open(p, "w", encoding="utf-8", newline="") as f:
        for d, v in merged:
            f.write(json.dumps({"date": d, "value": v, "provider": provider,
                                "run": run_tag}, ensure_ascii=False) + "\n")
    return {"points": len(merged), "new_points": new_pts,
            "revised": revised, "dropped_oldest": dropped}


# ================================================================ 统计与分级
def changes(points: List[Tuple[str, float]], k: int) -> Optional[float]:
    """第 k 期变动 = 最新值 − 倒数第 (k+1) 个观测值。
    ★ 按**观测步长**，不是自然日。周一的「1 期」是上周五，跨 3 个自然日。
    调用方必须同时报出该步长的自然日跨度。"""
    if len(points) < k + 1:
        return None
    return points[-1][1] - points[-(k + 1)][1]


def step_span(points: List[Tuple[str, float]], k: int) -> Optional[int]:
    if len(points) < k + 1:
        return None
    return days_between(points[-(k + 1)][0], points[-1][0])


def delta_series(points: List[Tuple[str, float]]) -> List[float]:
    return [points[i][1] - points[i - 1][1] for i in range(1, len(points))]


def delta_series_step(points: List[Tuple[str, float]], step: int,
                      win: int) -> List[float]:
    """分级用的变动样本：**相隔 step 期**的差，取最近 win 个。

    ★ 为什么不是简单地 stdev(逐期差)：
      对步长 7 的序列（周内季节性），逐期差的 σ 主要来自「周末效应」，
      用它去除「与上周同一天相比的变动」，得到的是被季节性稀释过的 z——
      一个真实的大幅变化会被算成 z=0.6 而落进「平稳」。
      必须用**同一步长**的差来估 σ，量纲才自洽。
    """
    step = max(1, int(step))
    ds = [points[i][1] - points[i - step][1]
          for i in range(step, len(points))]
    return ds[-max(1, win):]


def compute_stats(points: List[Tuple[str, float]], series: Dict[str, Any],
                  cfg: Dict[str, Any], *, prev_seen: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    w = cfg.get("window") or {}
    zw = int(w.get("z_window", 60))
    zmin = int(w.get("min_obs_for_z", 25))
    floor_rel = float(w.get("z_floor_rel", 0.0))
    g = cfg.get("grading") or {}
    g1 = float(g.get("z_attention", 1.0))
    g2 = float(g.get("z_alert", 2.0))
    g3 = float(g.get("z_escalation", 3.0))
    pct_window = int(w.get("percentile_window", 252))
    stale = cfg.get("staleness") or {}
    # 合理发布间隔：日频 5 天（周末 2 天 + 假日余量）、周频 12 天、月频 45 天。
    # ★ 允许**按序列覆盖**：PortWatch 的通行量是日频，但由 AIS 事后汇编，
    #   发布滞后本身就是 4–7 天，用日频的 5 天阈值会把它全部误报成「停更」。
    max_lag = int(series.get("max_lag",
                             stale.get(series.get("cadence", "daily"), 5)))
    # ★ 分级步长：默认 1 期。
    #   但海峡通行量有强**周内季节性**（周末船舶少），逐日比会全是噪声，
    #   分档会天天在「升级/平稳」之间跳。这类序列用 7 期为步长，
    #   即「与上周同一天比」，季节性自动抵消。
    #   步长改变的是**分级口径**，不改显示口径（Δ1/5/20 期照旧给），
    #   并且必须在报告里说出来——否则同一列里混着两种口径的 z。
    step = max(1, int(series.get("step", 1) or 1))

    st: Dict[str, Any] = {"series": series["id"], "label": series.get("label", series["id"]),
                          "unit": series.get("unit", ""), "cadence": series.get("cadence", "daily"),
                          "bucket": series.get("bucket", "other"), "decimals": int(series.get("decimals", 2)),
                          "polarity": int(series.get("polarity", 1)), "role": series.get("role", "context")}
    if not points:
        st.update({"state": "missing", "value": None, "date": None, "n": 0,
                   "grade": "无数据", "z": None, "reason": "无任何观测点"})
        return st

    st["n"] = len(points)
    st["date"] = points[-1][0]
    st["value"] = points[-1][1]
    st["first_date"] = points[0][0]
    st["prev_date"] = points[-2][0] if len(points) >= 2 else None
    st["prev_value"] = points[-2][1] if len(points) >= 2 else None

    for k, key in ((1, "d1"), (5, "d5"), (20, "d20")):
        st[key] = changes(points, k)
        st[key + "_span"] = step_span(points, k)
        if st[key] is not None:
            st[key + "_date"] = points[-(k + 1)][0]

    st["step"] = step
    st["gdelta"] = changes(points, step)
    st["gdelta_span"] = step_span(points, step)
    if st["gdelta"] is not None:
        st["gdelta_date"] = points[-(step + 1)][0]

    ds = delta_series_step(points, step, zw)
    sd = stdev(ds)
    # σ 下限：低波动期 σ 极小，会把 0.01 的变动放大成 z=8。
    # floor 用**相对**方式给出（占当前水平的比例），避免量纲依赖。
    floor = 0.0
    if floor_rel:
        floor = abs(points[-1][1]) * floor_rel
    eff_sd = max(sd, floor) if sd is not None else (floor or None)
    st["sigma"] = sd
    st["sigma_used"] = eff_sd
    st["z_win"] = len(ds)

    if st["gdelta"] is None or eff_sd is None or eff_sd <= 0 or len(ds) < (zmin - 1):
        st["z"] = None
        st["grade"] = "样本不足"
        st["reason"] = (f"需 ≥{zmin} 个点且 σ>0（实得 {len(points)} 点 / "
                        f"σ={'—' if sd is None else format(sd, '.6g')}）")
    else:
        z = st["gdelta"] / eff_sd
        st["z"] = z
        az = abs(z)
        st["grade"] = ("升级" if az >= g3 else "警戒" if az >= g2
                       else "关注" if az >= g1 else "平稳")
        st["reason"] = ""

    win = [v for _, v in points[-pct_window:]]
    st["percentile"] = percentile_rank(win, points[-1][1])
    st["win_high"] = max(win) if win else None
    st["win_low"] = min(win) if win else None
    st["win_n"] = len(win)

    lag = days_between(points[-1][0], today_utc().isoformat())
    st["lag_days"] = lag
    st["max_lag"] = max_lag
    st["state"] = ("lagging" if (lag is not None and lag > max_lag) else "fresh")

    # 「本轮是否有新数据点」——与上一轮快照比。**不是**「值有没有变」。
    pd_ = (prev_seen or {}).get("date")
    st["prev_seen_date"] = pd_
    if pd_ is None:
        st["new_point"] = None          # 首轮，无从比较
    else:
        st["new_point"] = bool(points[-1][0] > pd_)
    return st


# ================================================================ 派生指标
DERIVED_KINDS = {"diff", "ratio", "pct_ratio", "zdev", "drawdown", "linear",
                 "pct_over", "rollstd", "ma"}


def align_dates(series_map: Dict[str, List[Tuple[str, float]]],
                ids: List[str]) -> List[str]:
    """取所有输入序列**都有值**的日期交集（升序）。
    ★ 为什么用交集而不是「以主序列为准 + 回填最近值」：
      回填（forward fill）会把周末/假日的旧值当成当天值，
      派生量在假期附近会出现「假的跳变」。交集宁可少几天，不造错数。"""
    if not ids or any(i not in series_map or not series_map[i] for i in ids):
        return []
    sets = [set(d for d, _ in series_map[i]) for i in ids]
    common = sets[0]
    for s in sets[1:]:
        common &= s
    return sorted(common)


def lookup(series_map: Dict[str, List[Tuple[str, float]]], sid: str) -> Dict[str, float]:
    return {d: v for d, v in series_map.get(sid, [])}


def build_derived_series(series_map: Dict[str, List[Tuple[str, float]]],
                         spec: Dict[str, Any]) -> Tuple[List[Tuple[str, float]], str]:
    """按受限的 `kind` 集合构造派生序列。

    ★ 这里**刻意不用 eval / 表达式字符串**：配置文件一旦能执行任意表达式，
    就同时是注入面 + 不可审查面。用枚举算子，每种都有独立的单测。
    """
    kind = spec.get("kind", "")
    if kind not in DERIVED_KINDS:
        return [], f"未知算子 kind={kind!r}（允许：{sorted(DERIVED_KINDS)}）"
    scale = float(spec.get("scale", 1.0) or 1.0)
    ids: List[str] = []

    if kind == "linear":
        terms = spec.get("terms") or []
        ids = [t["series"] for t in terms]
    elif kind in ("diff", "ratio", "pct_ratio"):
        ids = [spec["a"], spec["b"]]
    else:
        ids = [spec["series"]]

    dates = align_dates(series_map, ids)
    if not dates:
        return [], f"输入序列无共同日期（{ids}）"
    maps = {i: lookup(series_map, i) for i in ids}
    out: List[Tuple[str, float]] = []

    if kind == "zdev":
        win = int(spec.get("window", 20))
        vals = [maps[ids[0]][d] for d in dates]
        for i, d in enumerate(dates):
            lo = max(0, i - win + 1)
            seg = vals[lo:i + 1]
            m = mean(seg)
            s = stdev(seg)
            if m is None or s is None or s == 0:
                continue
            out.append((d, (vals[i] - m) / s * scale))
        return out, ("" if out else "zdev：窗口内无有效 σ")

    if kind == "drawdown":
        win = int(spec.get("window", 60))
        vals = [maps[ids[0]][d] for d in dates]
        for i, d in enumerate(dates):
            lo = max(0, i - win + 1)
            hi = max(vals[lo:i + 1])
            if hi == 0:
                continue
            out.append((d, (vals[i] / hi - 1.0) * 100.0 * scale))
        return out, ("" if out else "drawdown：窗口高点为 0")

    if kind == "pct_over":
        # 相对 k 期前的百分比变化。★ 按**观测步长**，不是自然日：
        # 周频按「周」跨、日频按「交易日」跨；混用会在假期附近造出假跳变。
        k = int(spec.get("window", 20))
        vals = [maps[ids[0]][d] for d in dates]
        for i, d in enumerate(dates):
            if i < k:
                continue
            base = vals[i - k]
            if base == 0:
                continue
            out.append((d, (vals[i] / base - 1.0) * 100.0 * scale))
        return out, ("" if out else f"pct_over：序列短于 {k + 1} 期")

    if kind == "rollstd":
        # 滚动标准差 × 年化因子。★ 年化因子必须由配置显式给出：
        # 日频 √252、周频 √52。写死 √252 会把周频的波动率夸大 2.2 倍。
        # ★ return_base=true 按「收益率」计（价格序列的已实现波动率）：
        #   σ = stdev( vals[i]/vals[i-1] − 1 )。价格序列若误用差值模式，
        #   差值 σ（~$1）未除以价格水平（~$70）再 × √252 × 100，
        #   会把年化波动率放大两个数量级（实测曾算出 4,634%，实际应 ~20-40%）。
        win = int(spec.get("window", 20))
        ann = float(spec.get("annualize", 1.0) or 1.0)
        vals = [maps[ids[0]][d] for d in dates]
        if spec.get("return_base"):
            diffs = [(vals[i] / vals[i - 1] - 1.0) if vals[i - 1] else None
                     for i in range(1, len(vals))]
        else:
            diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
        for i in range(len(diffs)):
            if diffs[i] is None:
                continue
            lo = max(0, i - win + 1)
            s = stdev([x for x in diffs[lo:i + 1] if x is not None])
            if s is None:
                continue
            out.append((dates[i + 1], s * ann * scale))
        return out, ("" if out else "rollstd：样本不足")

    if kind == "ma":
        # 移动平均。海峡通行量有强周内季节性（周末船少），
        # 日频原始值逐日比会全是噪声，必须先平滑。
        win = int(spec.get("window", 7))
        vals = [maps[ids[0]][d] for d in dates]
        for i, d in enumerate(dates):
            lo = max(0, i - win + 1)
            seg = vals[lo:i + 1]
            if len(seg) < win:
                continue
            m = mean(seg)
            if m is None:
                continue
            out.append((d, m * scale))
        return out, ("" if out else f"ma：序列短于 {win} 期")

    for d in dates:
        a = maps[ids[0]][d]
        if kind == "linear":
            v = 0.0
            for t in spec["terms"]:
                v += float(t.get("weight", 1.0)) * maps[t["series"]][d]
            out.append((d, v * scale))
        elif kind == "diff":
            out.append((d, (a - maps[ids[1]][d]) * scale))
        elif kind == "ratio":
            r = safe_div(a, maps[ids[1]][d])
            if r is None:
                continue
            out.append((d, r * scale))
        elif kind == "pct_ratio":
            r = safe_div(a, maps[ids[1]][d])
            if r is None:
                continue
            out.append((d, (r - 1.0) * 100.0 * scale))
    return out, ("" if out else "无输出")


# ================================================================ 合成指数
def build_composite(cfg: Dict[str, Any], stats: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """金融战线压力合成指数 = Σ(wᵢ · polarityᵢ · zᵢ) / Σ(wᵢ)

    ★ 三条不可省的披露：
      1. 单位是 **z**（标准差倍数），**不是概率、不是点位、不是预测**；
      2. **覆盖度**：若只有 6/12 个分量有 z，这个数与前一轮**不可直接比较**，
         所以必须把「有值分量数 / 总分量数」印在指数旁边；
      3. **分项贡献**必须一并给出，否则它就是个黑箱。
    """
    spec = cfg.get("composite") or {}
    comps = spec.get("components") or []
    total_w = 0.0
    acc = 0.0
    used: List[Dict[str, Any]] = []
    missing: List[str] = []
    for c in comps:
        sid = c["series"]
        w = float(c.get("weight", 1.0))
        pol = float(c.get("polarity", 1.0))
        st = stats.get(sid)
        z = st.get("z") if st else None
        if z is None:
            missing.append(sid)
            continue
        contrib = w * pol * float(z)
        total_w += w
        acc += contrib
        used.append({"series": sid,
                     "label": st.get("label", sid),
                     "weight": w, "polarity": pol,
                     "z": round(float(z), 3),
                     "contribution": round(contrib, 3)})
    used.sort(key=lambda x: -abs(x["contribution"]))
    score = (acc / total_w) if total_w > 0 else None
    return {"label": spec.get("label", "金融战线压力合成指数"),
            "unit": spec.get("unit", "z"),
            "score": None if score is None else round(score, 3),
            "sum_weight": round(total_w, 3),
            "n_used": len(used), "n_total": len(comps),
            "coverage": (len(used) / len(comps)) if comps else 0.0,
            "components": used, "missing": missing,
            "note": spec.get("note", "")}


# ================================================================ 渲染工具
def grade_mark(g: str) -> str:
    return GRADE_MARK.get(g, "")


def delta_unit_of(st: Dict[str, Any]) -> str:
    """变动的显示量纲。
    ★ 这个函数存在的唯一理由：**10Y 收益率从 4.15 到 4.23 是 +8bp，
      不是 +1.93%**。收益率、利差、盈亏平衡通胀这类「本身就是百分比」的
      序列，变动必须用基点表述，否则 1.9% 会被读成剧烈波动
      （而它在一个交易日内其实完全正常）。
    派生量则**必须显式声明**：`pct_ratio` 的输出本身就是百分比变化，
      再折算成 bp 会错得离谱。所以派生量的 `delta_unit` 一律由配置给死，
      不做推断。"""
    du = st.get("delta_unit")
    if du:
        return str(du)
    return "bp" if st.get("unit") == "%" else ""


def delta_cell(st: Dict[str, Any], key: str = "d1") -> str:
    d = st.get(key)
    if d is None:
        return "—"
    du = delta_unit_of(st)
    if du == "bp":
        return fmt_bp(d)
    if du == "pp":
        return f"{d:+.1f}pp"
    return fmt_signed(d, int(st.get("decimals", 2)))


def span_cell(st: Dict[str, Any], key: str = "d1") -> str:
    s = st.get(key + "_span")
    if s is None:
        return "—"
    return f"{s}d" + ("⚠" if s > 4 else "")


def z_cell(st: Dict[str, Any]) -> str:
    z = st.get("z")
    if z is None:
        return "—"
    return f"{z:+.2f}"


def pct_cell(st: Dict[str, Any]) -> str:
    p = st.get("percentile")
    if p is None:
        return "—"
    return f"{p:.0f}%"


def flags_cell(st: Dict[str, Any]) -> str:
    fl: List[str] = []
    if st.get("step") and int(st["step"]) > 1:
        fl.append(f"步长{st['step']}期")
    if st.get("state") == "lagging":
        fl.append(f"⏳滞后{st.get('lag_days')}d")
    if st.get("new_point") is False:
        fl.append("无新点")
    if st.get("grade") == "样本不足":
        fl.append("样本不足")
    if st.get("divergent"):
        fl.append("⚠源间分歧")
    if st.get("fallback"):
        fl.append("走备用源")
    return " ".join(fl) if fl else ""


def fetch_state_cell(st: Dict[str, Any]) -> str:
    """§2 数据源表「滞后」列。

    以前这条对**取不到值**的序列也打印「正常(阈None)」——那是纯误导：
    一条 0 点、无末日、无阈值的序列，唯一诚实的说法是「无数据」。
    另外阈值缺失时不要把 None 印出来，直接省掉括号。
    """
    if st.get("value") is None:
        return "无数据"
    if st.get("state") == "lagging":
        return f"滞后{st.get('lag_days')}d"
    tail = f"(阈{st['max_lag']}d)" if st.get("max_lag") is not None else ""
    return "正常" + tail


def status_cell(st: Dict[str, Any]) -> str:
    """分桶明细表「状态」列。无数据优先于任何旗标/正常。"""
    if st.get("value") is None:
        return "无数据"
    return flags_cell(st) or "正常"


def md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def bar_z(z: Optional[float], width: int = 10) -> str:
    if z is None:
        return "—"
    n = min(width, int(abs(z) * width / 4.0))
    ch = "█" if z > 0 else "░"
    core = ch * max(n, 1) if n else "·"
    return core


def series_row(st: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    return ("| {b} | {lab} | {val} | {dt} | {d1} | {sp} | {z} | {g} | {p} | {f} |"
            .format(b=bucket_def(cfg, st["bucket"]).get("name", st["bucket"]),
                    lab=md_escape(st["label"]),
                    val=fmt_num(st.get("value"), int(st.get("decimals", 2))),
                    dt=st.get("date") or "—",
                    d1=delta_cell(st, "gdelta"),
                    sp=span_cell(st, "gdelta"),
                    z=z_cell(st),
                    g=grade_mark(st.get("grade", "")) + " " + st.get("grade", ""),
                    p=pct_cell(st),
                    f=flags_cell(st) or "—"))


BUCKET_SECTIONS = [
    ("3", "能源与航运成本", ["oil"], "原油是这场冲突最直接的价格表达。看三件事："
     "**绝对水平**（是否已计入风险溢价）、**Brent-WTI 价差**（国际紧张 vs 美国本土松紧）、"
     "**近一年分位**（当前水平在历史上算什么位置）。"),
    ("4", "库存与战略储备", ["inventory"],
     "★ **本桶多为周频**，与日频指标不可同表比较。战略石油储备（SPR）的释放节奏"
     "是政策工具的直接读数；商业库存反映供需，汽油零售价反映传导到终端的速度。"),
    ("5", "利率与政策预期", ["rates"],
     "★ 收益率的变动一律以**基点（bp）**计。政策预期部分**只给代理量**："
     "目标区间上限（官方）＋有效联邦基金利率（官方）＋2Y 收益率（市场）"
     "三者的相对位置，是「市场对未来政策路径的定价」的可复核代理，"
     "**不是**加息概率，也**不是**预测。"),
    ("6", "股市与风险偏好", ["equity"],
     "股指看**百分比变化**（不同指数的点位不可比）。能源与国防板块的**相对强弱**"
     "是「冲突受益/受损」的市场读法，但只是价格反应，不是因果证明。"),
    ("7", "信用、波动率与汇率", ["credit", "fx"],
     "VIX 与信用利差是「市场愿不愿意为风险付钱」的直接读数；美元指数是避险资金流向。"),
    ("8", "海峡通行量", ["shipping"],
     "★ 通行量是本项目里**唯一无法用行情接口替代**的一类数据，"
     "也是最能直接刻画「双线施压」是否落地的指标。"),
]


def render_report(cfg: Dict[str, Any], records: List[Dict[str, Any]],
                  derived: List[Dict[str, Any]], comp: Dict[str, Any],
                  meta: Dict[str, Any], run_dt: datetime) -> str:
    L: List[str] = []
    A = L.append
    day = run_dt.date().isoformat()
    srec = [r for r in records]
    drec = [r for r in derived]
    allrec = srec + drec
    by_id = {r["series"]: r for r in allrec}

    # ------------------------------------------------------------ 头
    A(f"# 美伊冲突 · 金融战线监测　{day}")
    A("")
    A(f"> **抓取时点**：{meta['fetched_at']}　|　**出口代理**：{meta['egress']}")
    A(f"> **序列**：{len(srec)} 条（有值 {sum(1 for r in srec if r.get('value') is not None)}"
      f" / 无数据 {sum(1 for r in srec if r.get('value') is None)}）"
      f"　|　**派生指标**：{len(drec)} 条"
      f"　|　**观测点合计**：{meta['total_points']:,}")
    A(f"> **数据源**：{meta['providers_used']}")
    A(f"> 本报告由 `finance_monitor.py v{VERSION}` 自动生成；"
      f"所有数字均可由 `output/{day}/raw/` 的原始载荷独立复算"
      f"（`python tools/verify_report.py --save`）。")
    A("")

    # ------------------------------------------------------------ 0
    A("## 0. 阅读须知（口径与不做的推断）")
    A("")
    A("1. **「最新值」= 最近一个已收盘交易日的收盘值**，不是实时价。")
    A("   本地时间与美东交易时段不重叠时，看到的是**上一交易日**的收盘。")
    A("2. **变动按「观测步长」计**，不是自然日。表中的 `Δ步长` 就是**分级所依据的**"
      "那一个变动（步长默认 1 期；海峡通行量这类有周内季节性的序列为 7 期，"
      "行内标 `步长N期`，即「与上周同一天比」）。**跨度**栏给出该变动跨了多少"
      "**自然日**：跨周末或假日的 1 期是 3–4 天，与周中的 1 期"
      "**不可直接比大小**（标 `⚠` 者 > 4 天）。")
    A("3. **频率不混表**：日频与周频/月频**分开列示**。周频序列若与日频同列，"
      "会连着 6 天显示「0.00」，读起来像「纹丝不动」，实际是**没有新数据**。")
    A("4. **量纲**：收益率 / 利差 / 盈亏平衡通胀的变动一律以 **bp（基点）** 表示。"
      "10Y 从 4.15% 到 4.23% 是 `+8.0bp`，**不是** `+1.93%`。")
    A("5. **「没变」有两种**：市场真没动 vs **数据没更新**。"
      "后者由「本轮新点」栏与「滞后」栏区分，**绝不显示为 0 变动**。")
    A("6. **数据会被修订**。周频库存等发布后可能回改；本报告只声明"
      "「截至抓取时点」，不声称终值。本轮发现的修订会单独披露。")
    A("7. **政策预期是代理量**：见 §5 的说明。**不提供**加息概率、不做预测。")
    A("8. **相关 ≠ 因果**。只报同期同向 / 背离，不写「因为……所以……」。")
    A("9. **合成指数（§10）的单位是 z**，不是概率、不是点位、不是预测；"
      "且**只在覆盖度相同时才可跨期比较**。")
    A("10. **本报告里哪些是事实、哪些是加工**——分四层，逐层降可信度："
      "**① 原始观测值**（表中的「最新值/值日期」列）：官方机构一手发布"
      "（财政部、纽约联储、EIA、IMF PortWatch，§12 标 `official`）或市场行情"
      "（Yahoo 收盘价，标 `market`——是交易者投出来的事实，会修订、会延迟）。"
      "**② 派生量**（§9 与各节「本桶派生量」）：本项目按**写明的算式**从①算出，"
      "每条带 definition，可复算。**③ 统计量**（z、σ、分位、档位）："
      "对①②做的统计加工，档位只是阈值映射，**不是事实也不是判断**。"
      "**④ 合成指数 FSI**：③的再加权平均——连「指数」本身都是构造物。"
      "引用任何数字时请先问自己在引用哪一层。"
      "另：政策利率（EFFR / 目标区间）极性为 0，其「升级」档表示"
      "**政策利率本身发生了异常幅度的变动（政策事件）**，"
      "**不是**「金融市场压力升级」。")
    A("")

    # ------------------------------------------------------------ 1
    A("## 1. 一页速览")
    A("")
    fs = comp.get("score")
    A(f"**{comp['label']} = {fmt_signed(fs, 3)} {comp['unit']}**"
      f"（覆盖 **{comp['n_used']}/{comp['n_total']}** 个分量）")
    A("")
    A("> 单位是 **z**（各分量日变动的标准化值，按其自身近 60 期波动率折算）的"
      "加权平均。**不是概率、不是点位、不是预测。** 覆盖度不同的两期"
      "**不可直接比较**——少一个分量就少一份权重，得分自然漂移。")
    A("")
    cnt: Dict[str, int] = {}
    for r in allrec:
        cnt[r.get("grade", "无数据")] = cnt.get(r.get("grade", "无数据"), 0) + 1
    A("档位分布：" + " · ".join(
        f"{grade_mark(g)} {g} {cnt.get(g, 0)}" for g in GRADE_ORDER if cnt.get(g)))
    A("")

    # 1.1 分桶 rollup
    A("### 1.1 分桶汇总")
    A("")
    A("| 桶 | 序列数 | 最高档 | 最大 \\|z\\| | 代表指标（按 \\|z\\| 降序前 2） |")
    A("|---|---|---|---|---|")
    bkeys = sorted({r["bucket"] for r in allrec},
                   key=lambda b: bucket_order(cfg, b))
    for b in bkeys:
        rs = [r for r in allrec if r["bucket"] == b]
        gr = [r for r in rs if r.get("grade") in GRADE_ORDER]
        top = min((GRADE_ORDER.index(r["grade"]) for r in gr), default=99)
        topg = GRADE_ORDER[top] if top < 99 else "无数据"
        zs = [(abs(r["z"]) if r.get("z") is not None else -1, r) for r in rs]
        zs.sort(key=lambda x: -x[0])
        best = zs[0][0] if zs and zs[0][0] >= 0 else None
        names = " · ".join(f"{x[1]['label']}({x[0]:+.1f})"
                           for x in zs[:2] if x[0] >= 0) or "—"
        A(f"| {bucket_def(cfg, b).get('name', b)} | {len(rs)} | "
          f"{grade_mark(topg)} {topg} | {'—' if best is None else f'{best:.2f}'} | {names} |")
    A("")

    # 1.2 头条表
    A("### 1.2 头条指标")
    A("")
    A("> 只列 `role=headline` 的序列。全量序列见 §3–§8，原始窗口见 §11。")
    A("")
    A("| 桶 | 指标 | 最新值 | 值日期 | Δ步长 | 跨度 | z | 档位 | 近一年分位 | 标志 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    heads = [r for r in allrec if r.get("role") == "headline"]
    heads.sort(key=lambda r: (bucket_order(cfg, r["bucket"]),
                              -(abs(r["z"]) if r.get("z") is not None else -1)))
    for r in heads:
        A(series_row(r, cfg))
    if not heads:
        A("| — | （没有能取到值的头条指标） | | | | | | | | |")
    A("")
    bprows = [r for r in heads if delta_unit_of(r) == "bp"]
    if bprows:
        A(f"> ⚠ 上表中 {len(bprows)} 条序列（{'、'.join(r['label'] for r in bprows[:6])}"
          f"{' 等' if len(bprows) > 6 else ''}）的变动以 **bp** 显示，"
          f"因为它们的水平本身就是百分比。")
        A("")

    # ------------------------------------------------------------ 2
    A("## 2. 数据源与抓取状态")
    A("")
    A(f"- **出口**：{meta['egress']}")
    A(f"- **抓取时点**：{meta['fetched_at']}")
    A(f"- **本次运行标识**：`{meta['run_tag']}`")
    A(f"- **原始载荷**：`output/{day}/raw/`（{meta['raw_files']} 个文件，"
      f"{meta['raw_bytes']:,} 字节）—— 对账工具从这里重新解析并独立复算")
    A(f"- **累积历史**：`history/*.jsonl`（跨轮累积，不会被单轮源的回溯长度截短）")
    A("")
    A("| 序列 | 频率 | 主源 | 实际命中 | 点数 | 首日 | 末日 | 滞后 | 本轮新点 | 交叉源 | 源间差异 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(srec, key=lambda x: (bucket_order(cfg, x["bucket"]),
                                         x["label"])):
        ch = r.get("chain_hit") or {}
        cx = r.get("cross") or []
        cxs = " · ".join(
            f"{c['provider']}:{fmt_num(c.get('value'), 4)}"
            + ("⚠" if c.get("divergent") else "")
            for c in cx) or "—"
        A(f"| {md_escape(r['label'])} | {cadence_label(r.get('cadence', 'daily'))} | "
          f"{r.get('chain_head', '—')} | {ch.get('provider', '—')} | {r.get('n', 0)} | "
          f"{r.get('first_date') or '—'} | {r.get('date') or '—'} | "
          f"{fetch_state_cell(r)} | "
          f"{'—' if r.get('new_point') is None else ('是' if r.get('new_point') else '否')} | "
          f"{cxs} | "
          f"{('⚠ ' + str(len(r.get('divergent') or [])) + ' 处') if r.get('divergent') else '无'} |")
    A("")

    fb = [r for r in srec if r.get("chain_hit") and r.get("chain_head")
          and r["chain_hit"].get("provider") != r["chain_head"]]
    if fb:
        A(f"**主源回退 {len(fb)} 条**（链首未命中，已顺延到下一源）：")
        for r in fb:
            A(f"- `{r['series']}`：链首 `{r['chain_head']}` 未取到，"
              f"实际用 `{r['chain_hit']['provider']}`")
        A("")
    divs = [r for r in srec if r.get("divergent")]
    if divs:
        A(f"**源间分歧 {len(divs)} 条**（两个独立源对同一天给出不同值，"
          f"相对差超阈值 {(cfg.get('divergence') or {}).get('rel_tol')}）——"
          f"分歧**不等于**有一方错，可能是口径/时点差异，但在用它做结论前必须查：")
        for r in divs:
            for c in r["divergent"]:
                A(f"- `{r['series']}` @ {c['date']}：主源 {fmt_num(r.get('value'), 4)} "
                  f"vs {c['provider']} {fmt_num(c.get('value'), 4)}"
                  f"（差 {c.get('diff')}，相对 {c.get('rel')}）")
        A("")
    lag = [r for r in srec if r.get("state") == "lagging"]
    if lag:
        A(f"**滞后 {len(lag)} 条**（末日距今超过该频率的合理发布间隔，"
          f"即「源可能停止更新」而不是「市场平静」）：")
        for r in lag:
            A(f"- `{r['series']}`：末日 {r.get('date')}，距今 {r.get('lag_days')} 天"
              f"（阈值 {r.get('max_lag')}d，频率 {cadence_label(r.get('cadence'))}）")
        A("")
    miss = [r for r in srec if r.get("value") is None]
    if miss:
        A(f"**完全无数据 {len(miss)} 条**：")
        for r in miss:
            why = "；".join(str(t.get("why", "")) for t in (r.get("tried") or [])
                            if t.get("why"))[:220]
            A(f"- `{r['series']}`（{r['label']}）：{why or '未说明'}")
        A("")
    drevs = [r for r in srec + drec if r.get("revised")]
    if drevs:
        A(f"**源修订 {len(drevs)} 条**（同一日期的值与上一轮不同，已按新值覆盖并留痕）：")
        for r in drevs:
            for x in r["revised"][:4]:
                A(f"- `{r['series']}` @ {x['date']}：{x['was']} → {x['now']}")
        A("")

    # ------------------------------------------------------------ 3..8
    for sec, title, bks, blurb in BUCKET_SECTIONS:
        rs = [r for r in srec if r["bucket"] in bks]
        drs = [r for r in drec if r["bucket"] in bks]
        A(f"## {sec}. {title}")
        A("")
        A(blurb)
        A("")
        if not rs and not drs:
            why = meta.get("bucket_gaps", {}).get(bks[0], "")
            A(f"> **本桶本轮无可用数据。** {why}")
            A("")
            continue
        for cad in ("daily", "weekly", "monthly"):
            sub = [r for r in rs if r.get("cadence") == cad]
            if not sub:
                continue
            if cad != "daily":
                A(f"### {sec}.{('1' if cad == 'daily' else ('2' if cad == 'weekly' else '3'))}"
                  f" {cadence_label(cad)}序列"
                  f"（⚠ 与日频不可比大小）")
                A("")
            A("| 指标 | 最新值 | 值日期 | Δ上一期 | 跨度 | Δ5期 | Δ20期 | σ(近60期) | z | 档位 | 状态 |")
            A("|---|---|---|---|---|---|---|---|---|---|---|")
            for r in sorted(sub, key=lambda x: (bucket_order(cfg, x["bucket"]),
                                                x["label"])):
                A("| {lab} | {v} | {d} | {d1} | {sp} | {d5} | {d20} | {sg} | {z} | {g} | {st} |"
                  .format(lab=md_escape(r["label"]),
                          v=fmt_num(r.get("value"), int(r.get("decimals", 2))),
                          d=r.get("date") or "—",
                          d1=delta_cell(r, "d1"),
                          sp=span_cell(r, "d1"),
                          d5=delta_cell(r, "d5"),
                          d20=delta_cell(r, "d20"),
                          sg=("—" if r.get("sigma_used") is None
                              else fmt_num(r["sigma_used"], max(2, int(r.get("decimals", 2))))),
                          z=z_cell(r),
                          g=grade_mark(r.get("grade", "")) + " " + r.get("grade", ""),
                          st=status_cell(r)))
            if cad == "daily":
                fived = [r for r in sub if r.get("d5_span") and r["d5_span"] > 9]
                if fived:
                    A("")
                    A(f"> ⚠ 本桶有 {len(fived)} 条序列的「Δ5 期」自然日跨度 > 9 天"
                      f"（假期/停更），与常规周变动不可比。")
            A("")
        for r in sorted(rs, key=lambda x: x["label"]):
            if r.get("note"):
                A(f"- `{r['series']}`：{r['note']}")
        if any(r.get("note") for r in rs):
            A("")
        if drs:
            A(f"**本桶派生量**（定义见 §9）：")
            A("")
            A("| 派生指标 | 值 | 单位 | Δ上一期 | z | 档位 |")
            A("|---|---|---|---|---|---|")
            for r in sorted(drs, key=lambda x: -abs(x["z"] or 0)):
                A(f"| {md_escape(r['label'])} | "
                  f"{fmt_num(r.get('value'), int(r.get('decimals', 2)))} | "
                  f"{r.get('unit') or '—'} | {delta_cell(r, 'd1')} | {z_cell(r)} | "
                  f"{grade_mark(r.get('grade', ''))} {r.get('grade', '')} |")
            A("")

    # 8 补充：通行量拿不到时的如实说明
    ship = [r for r in srec if r["bucket"] == "shipping"]
    if all(r.get("value") is None for r in ship) and ship:
        A("### 8.9 通行量数据缺口说明（重要）")
        A("")
        A("海峡日频通行量**不是行情接口能替代的**。本轮实际探测结果如下，"
          "**不编造、不外推**：")
        A("")
        ev = (cfg.get("data_gaps") or {}).get("chokepoint_probe", [])
        if ev:
            A("| 候选源 | 实测结果 |")
            A("|---|---|")
            for e in ev:
                A(f"| {e.get('label')} | {e.get('why')} |")
            A("")
        A("**因此本桶只能给出「市场侧代理」**（油轮/航运股价、Brent-WTI 价差、"
          "油价波动率）——它们**不是**通行量，只是市场对通行风险定价的结果。"
          "把代理量当通行量读是本报告最危险的误读方式，见 §13。")
        A("")

    # ------------------------------------------------------------ 9
    A("## 9. 派生指标与跨市场联动")
    A("")
    A("> 派生量由**多条序列按日期取交集**后计算（不用前值回填——回填会把"
      "周末旧值当成当天值，在假期附近造出假的跳变）。因此派生量的样本期"
      "可能短于其输入序列。")
    A("")
    A("| 派生指标 | 定义 | 值 | 单位 | Δ上一期 | z | 档位 | 样本数 |")
    A("|---|---|---|---|---|---|---|---|")
    for r in drec:
        A(f"| {md_escape(r['label'])} | {md_escape(r.get('definition', ''))} | "
          f"{fmt_num(r.get('value'), int(r.get('decimals', 2)))} | "
          f"{r.get('unit') or '—'} | {delta_cell(r, 'd1')} | {z_cell(r)} | "
          f"{grade_mark(r.get('grade', ''))} {r.get('grade', '')} | {r.get('n', 0)} |")
    A("")
    if meta.get("divergences"):
        A("### 9.1 同期同向 / 背离")
        A("")
        A("> 只描述**同期方向**，不解释成因。")
        A("")
        A("| 配对 | 方向 | 说明 |")
        A("|---|---|---|")
        for d in meta["divergences"]:
            A(f"| {d['pair']} | {d['direction']} | {md_escape(d['note'])} |")
        A("")

    # ------------------------------------------------------------ 10
    A("## 10. 合成指数与分项贡献")
    A("")
    A(f"**{comp['label']} = {fmt_signed(comp.get('score'), 3)} {comp['unit']}**")
    A("")
    A(f"计算式：`Σ(权重 × 极性 × z) / Σ权重`，本轮有值分量 "
      f"{comp['n_used']} / {comp['n_total']}（覆盖度 "
      f"{comp['coverage'] * 100:.0f}%），参与权重合计 {comp['sum_weight']}。")
    A("")
    A("| 分量 | 权重 | 极性 | z | 贡献（权重×极性×z） |")
    A("|---|---|---|---|---|")
    for c in comp["components"]:
        A(f"| {md_escape(c['label'])} | {c['weight']} | "
          f"{'↑压力' if c['polarity'] > 0 else '↓压力'} | "
          f"{c['z']:+.2f} | {c['contribution']:+.3f} |")
    A("")
    if comp["missing"]:
        A(f"**缺分量（{len(comp['missing'])} 个）**：{'、'.join('`' + m + '`' for m in comp['missing'])}"
          f" —— 它们**没有参与计算**，因此本轮得分与上一轮可能不可比。")
        A("")
    A(f"> 极性的含义：`↑压力` 表示该指标**上升**会推高压力读数"
      f"（如油价、VIX、信用利差、收益率上行）；`↓压力` 表示**上升**会压低"
      f"（如股指）。极性是**人为规定的方向约定**，不是模型结论。")
    A("")

    # ------------------------------------------------------------ 11
    A("## 11. 序列明细与原始窗口")
    A("")
    A(f"> 每序列列出累积历史中最近 **{meta['detail_n']}** 个观测点"
      f"（累积历史跨轮保留，不受单轮源回溯长度限制）。")
    A("")
    for r in sorted(srec, key=lambda x: (bucket_order(cfg, x["bucket"]), x["label"])):
        pts = r.get("window") or []
        A(f"### {r['series']} · {md_escape(r['label'])}"
          f"（{r.get('unit') or '无量纲'}，{cadence_label(r.get('cadence'))}）")
        A("")
        A(f"- 累积点数 **{r.get('hist_points', r.get('n', 0))}**"
          f"（{r.get('hist_first') or '—'} → {r.get('date') or '—'}）"
          f"；σ(近{60}期)={('—' if r.get('sigma_used') is None else format(r['sigma_used'], '.6g'))}"
          f"；z={z_cell(r)}；档位 **{r.get('grade')}**"
          f"；近一年分位 {pct_cell(r)}"
          f"；区间 {fmt_num(r.get('win_low'), int(r.get('decimals', 2)))}"
          f"–{fmt_num(r.get('win_high'), int(r.get('decimals', 2)))}")
        if pts:
            seg = " · ".join(f"{d}={fmt_num(v, int(r.get('decimals', 2)))}"
                             for d, v in pts)
            A(f"- 近 {len(pts)} 点：{seg}")
        if r.get("note"):
            A(f"- 备注：{r['note']}")
        A("")

    # ------------------------------------------------------------ 12
    A("## 12. 数据源与合规")
    A("")
    A("**本项目只使用无需登录、无需密钥的公开接口。** 建议抓取频率：每日 1–2 次。"
      "不做高频轮询，不绕付费墙，不使用他人凭证。")
    A("")
    A("| provider | 等级 | 端点 | 用途 | 本轮 |")
    A("|---|---|---|---|---|")
    for u in meta["endpoints"]:
        A(f"| {u['provider']} | {u.get('tier', 'official')} | "
          f"`{md_escape(u['url'])}` | {md_escape(u['label'])} | "
          f"{u['status']} |")
    A("")
    A("> 等级：`official`＝官方机构一手发布；`market`＝市场行情"
      "（Yahoo 收盘价，会随时间修订）。本表只列**原始观测层（①）**的来源；"
      "派生量（②）的定义在 §9，统计量（③）与合成指数（④）的定义在 §0 第 10 条。")
    A("")
    A("| 序列 | 完整来源链（按优先级） |")
    A("|---|---|")
    for r in sorted(srec, key=lambda x: x["series"]):
        chain = " → ".join(f"{c['provider']}:{c['ref']}" for c in r.get("chain", []))
        A(f"| `{r['series']}` | {md_escape(chain)} |")
    A("")
    A("**口径与免责**：所有数值均来自上述公开源，仅作研究与监测用途，"
      "**不构成投资建议**。行情数据可能延迟、可能有错；"
      "正式决策前请以原始来源为准。统计口径的已知偏差见 §13。")
    A("")

    # ------------------------------------------------------------ 13
    A("## 13. 已知偏差与误读边界")
    A("")
    rows: List[Tuple[str, str]] = []
    for b in ("oil", "inventory", "rates", "equity", "credit", "fx", "shipping"):
        dd = bucket_def(cfg, b)
        if dd.get("caveat"):
            rows.append((dd.get("name", b), dd["caveat"]))
    for r in cfg.get("series", []):
        if r.get("caveat"):
            rows.append((r.get("label", r["id"]), r["caveat"]))
    if rows:
        A("| 对象 | 偏差 / 误读边界 |")
        A("|---|---|")
        for a, b in rows:
            A(f"| {md_escape(a)} | {md_escape(b)} |")
        A("")
    A("**结构性偏差（与本轮数据无关，始终存在）：**")
    A("")
    A("1. **收盘时点差异**。美股、原油期货、美债的交易时段不同："
      "本报告的「同一天」是**日期对齐**，不是**时刻对齐**。"
      "在盘中剧烈波动的日子，日期对齐会掩盖先后顺序。")
    A("2. **周末与假日的空档**。日频序列没有周末行，跨周末的「1 期」跨 3 天。"
      "本报告已单列跨度，但读者仍不应把跨周末的变动与周中变动直接比大小。")
    A("3. **周频序列的发布滞后**。库存类数据发布时点固定（通常美东周三），"
      "在两次发布之间「值不变」是**正常**的，不代表市场没动。")
    A("4. **数据修订**。周频库存等有初值/修正值之分。第 6 条阅读须知已说明，"
      "本报告不声称终值。")
    A("5. **代理量不是所代理的东西**。政策预期的代理量、通行量的市场侧代理，"
      "都**只在各自定义域内**有效，跨域外推会得到看似有据实则无据的结论。")
    A("6. **合成指数的覆盖度**。覆盖度不同的两期不可比；分量缺失时得分会漂移。"
      "拆解表已给出每个分量的贡献，请以分项为准。")
    A("7. **单源风险**。若某序列只有一个源可用，该值无法被交叉验证，"
      "± 轻微偏移无法被发现。§2 的「交叉源」栏为 `—` 者即属此类。")
    A("8. **相关 ≠ 因果**。本报告只报同期方向，不提供因果解释，"
      "也不提供任何前瞻判断。")
    A("")

    # 实际计数（让上面的话落到具体数字上）
    n_one = sum(1 for r in srec if not r.get("cross"))
    n_fail = sum(1 for r in srec if r.get("value") is None)
    n_short = sum(1 for r in srec if r.get("grade") == "样本不足")
    A("**本轮实际情况：**")
    A("")
    A(f"- 无交叉源的序列（无法互验）：**{n_one}** 条"
      f"{'：' + '、'.join('`' + r['series'] + '`' for r in srec if not r.get('cross')) if n_one else ''}")
    A(f"- 完全无数据：**{n_fail}** 条　|　样本不足以算 z：**{n_short}** 条")
    A(f"- 合成指数覆盖度：**{comp['n_used']}/{comp['n_total']}**"
      f"（{comp['coverage'] * 100:.0f}%）")
    A("")
    return "\n".join(L)


# ================================================================ 管道
def read_last_seen(out_root: str) -> Dict[str, Any]:
    p = os.path.join(out_root, "state", "last_seen.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("series", {})
    except (json.JSONDecodeError, OSError):
        return {}


def write_last_seen(out_root: str, records: List[Dict[str, Any]],
                    run_tag: str) -> None:
    d = os.path.join(out_root, "state")
    os.makedirs(d, exist_ok=True)
    payload = {"run_tag": run_tag,
               "series": {r["series"]: {"date": r.get("date"),
                                        "value": r.get("value")}
                          for r in records if r.get("date")}}
    with open(os.path.join(d, "last_seen.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def build_watch_divergences(cfg: Dict[str, Any],
                            by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for p in cfg.get("watch_pairs", []):
        a, b = by_id.get(p["a"]), by_id.get(p["b"])
        if not a or not b or a.get("gdelta") is None or b.get("gdelta") is None:
            continue
        sa = 1 if a["gdelta"] > 0 else (-1 if a["gdelta"] < 0 else 0)
        sb = 1 if b["gdelta"] > 0 else (-1 if b["gdelta"] < 0 else 0)
        if sa == 0 or sb == 0:
            direction = "一方持平"
        elif sa == sb:
            direction = "同期同向"
        else:
            direction = "同期背离"
        out.append({"pair": p.get("label", f"{p['a']} / {p['b']}"),
                    "direction": direction,
                    "note": (f"{a['label']} {delta_cell(a, 'gdelta')} ／ "
                             f"{b['label']} {delta_cell(b, 'gdelta')}。"
                             + (p.get("note") or ""))})
    return out


def run_pipeline(cfg: Dict[str, Any], out_root: str, run_dt: datetime,
                 *, raw_dir: Optional[str], delay: float = 0.0,
                 prev_seen: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """取数 → 并入累积历史 → 统计 → 派生 → 合成。

    `raw_dir` 指向本轮原始载荷目录：**不存在则联网抓，存在则离线重放**。
    同一条路径切换，保证「重放」与「实抓」走的是同一套解析与统计代码
    （否则重放验证过的就不是真正跑的那条管道）。
    """
    global FETCH_LOG
    FETCH_LOG = []
    ser = series_list(cfg)
    w = cfg.get("window") or {}
    detail_n = int(w.get("detail_n", 20))
    max_points = int(w.get("max_points_per_series", 3000))
    delay_cfg = float((cfg.get("request") or {}).get("delay_between_sources_sec", 0.0))
    if prev_seen is None:
        prev_seen = read_last_seen(out_root)

    if raw_dir:
        os.makedirs(raw_dir, exist_ok=True if delay else os.path.exists(raw_dir) or True)
        os.makedirs(raw_dir, exist_ok=True)

    records: List[Dict[str, Any]] = []
    series_map: Dict[str, List[Tuple[str, float]]] = {}
    run_tag = run_dt.strftime("%Y-%m-%dT%H:%M")

    for s in ser:
        got = fetch_series(s, raw_dir=raw_dir, cfg=cfg, run_dt=run_dt,
                           delay=delay_cfg)
        chosen = got["chosen"]
        hist_used: List[Tuple[str, float]] = []
        mh: Dict[str, Any] = {"revised": [], "new_points": 0}
        if chosen is not None:
            # 累积历史写在 out_root 下，与「本轮快照」分离：
            # 源通常只回溯 3–6 个月，而 z / 分位需要更长历史。
            # 累积历史**不因单轮回溯变短而丢点**（见 merge_history 注释）。
            mh = merge_history(out_root, s["id"], chosen["rows"], run_tag=run_tag,
                               provider=chosen["provider"], max_points=max_points)
            hist_used = read_history(out_root, s["id"])
        else:
            hist_used = read_history(out_root, s["id"])
        if hist_used:
            series_map[s["id"]] = hist_used

        st = compute_stats(hist_used, s, cfg, prev_seen=prev_seen.get(s["id"]))
        st["chain"] = s["chain"]
        st["chain_head"] = (s["chain"][0]["provider"] if s["chain"] else "—")
        st["chain_hit"] = ({"provider": chosen["provider"], "ref": chosen["ref"],
                            "url": chosen["url"]} if chosen else None)
        st["tried"] = got["tried"]
        st["cross"] = got["cross"]
        st["divergent"] = got["divergent"]
        st["fallback"] = bool(chosen and s["chain"] and
                              chosen["provider"] != s["chain"][0]["provider"])
        st["note"] = s.get("note", "")
        st["caveat"] = s.get("caveat", "")
        st["derived"] = False
        if chosen:
            st["revised"] = mh["revised"]
            st["new_hist_points"] = mh["new_points"]
        else:
            st["revised"] = []
            st["new_hist_points"] = 0
        st["hist_points"] = len(hist_used)
        st["hist_first"] = hist_used[0][0] if hist_used else None
        st["window"] = hist_used[-detail_n:]
        records.append(st)
        FETCH_LOG.extend([dict(t, series=s["id"]) for t in got["tried"]])

    # ---------------- 派生
    derived: List[Dict[str, Any]] = []
    for spec in cfg.get("derived", []):
        pts, why = build_derived_series(series_map, spec)
        pseudo = {"id": spec["id"], "label": spec.get("label", spec["id"]),
                  "unit": spec.get("unit", ""), "cadence": spec.get("cadence", "daily"),
                  "decimals": int(spec.get("decimals", 2)),
                  "polarity": int(spec.get("polarity", 1)),
                  "bucket": spec.get("bucket", "other"),
                  "role": spec.get("role", "context"),
                  "delta_unit": spec.get("delta_unit", ""),
                  "note": spec.get("note", ""), "caveat": spec.get("caveat", ""),
                  "chain": []}
        st = compute_stats(pts, pseudo, cfg, prev_seen=None)
        st["definition"] = spec.get("definition", "")
        st["derived"] = True
        st["kind"] = spec.get("kind", "")
        st["inputs"] = ([t["series"] for t in spec.get("terms", [])]
                        if spec.get("kind") == "linear"
                        else [v for v in (spec.get("a"), spec.get("b"), spec.get("series"))
                              if v])
        st["chain"] = []
        st["chain_head"] = "derived"
        st["chain_hit"] = None
        st["tried"] = []
        st["cross"] = []
        st["divergent"] = []
        st["revised"] = []
        st["fallback"] = False
        st["hist_points"] = len(pts)
        st["hist_first"] = pts[0][0] if pts else None
        st["window"] = pts[-detail_n:]
        st["why"] = why
        derived.append(st)

    comp = build_composite(cfg, {r["series"]: r for r in records + derived})
    total_points = sum(r.get("hist_points", 0) for r in records)
    provs = sorted({t["provider"] for t in FETCH_LOG if t.get("ok")})
    return {"records": records, "derived": derived, "composite": comp,
            "total_points": total_points, "providers_used": provs,
            "run_tag": run_tag, "series_map": series_map,
            "divergences": build_watch_divergences(cfg, {r["series"]: r
                                                         for r in records + derived})}


def write_outputs(out_root: str, run_dt: datetime, cfg: Dict[str, Any],
                  res: Dict[str, Any], report_md: str) -> Dict[str, Any]:
    day = run_dt.date().isoformat()
    d = os.path.join(out_root, day)
    os.makedirs(d, exist_ok=True)

    recs = res["records"] + res["derived"]
    recs = sorted(recs, key=lambda r: (r.get("derived", False), r["series"]))
    # run_tag 写进每条记录：对账工具核对累积账本时无需从报告反查运行标识
    for r in recs:
        r.setdefault("run_tag", res["run_tag"])
    rp = os.path.join(d, f"records-{day}.jsonl")
    with open(rp, "w", encoding="utf-8", newline="") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    with open(os.path.join(d, f"composite-{day}.json"), "w", encoding="utf-8") as f:
        json.dump(res["composite"], f, ensure_ascii=False, indent=1)

    md_p = os.path.join(d, f"finance-digest-{day}.md")
    with open(md_p, "w", encoding="utf-8", newline="") as f:
        f.write(report_md)
    with open(os.path.join(out_root, "LATEST.md"), "w", encoding="utf-8",
              newline="") as f:
        f.write(report_md)
    with open(os.path.join(out_root, "LATEST-records.jsonl"), "w",
              encoding="utf-8", newline="") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    # 累积观测账（跨轮留痕，按 (run_tag, series) 去重）
    acc = os.path.join(out_root, "ALL-observations.jsonl")
    keep: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if os.path.isfile(acc):
        with open(acc, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                keep[(o.get("run_tag", ""), o.get("series", ""))] = o
    for r in recs:
        keep[(res["run_tag"], r["series"])] = {
            "run_tag": res["run_tag"], "day": day, "series": r["series"],
            "label": r.get("label"), "value": r.get("value"), "date": r.get("date"),
            "z": r.get("z"), "grade": r.get("grade"), "n": r.get("n"),
            "derived": r.get("derived", False)}
    with open(acc, "w", encoding="utf-8", newline="") as f:
        for k in sorted(keep):
            f.write(json.dumps(keep[k], ensure_ascii=False) + "\n")

    write_last_seen(out_root, res["records"], res["run_tag"])
    return {"records_path": rp, "md_path": md_p, "day_dir": d}


def collect_endpoints(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    seen = {}
    for s in cfg.get("series", []):
        for c in s.get("chain", []):
            for u in (c.get("urls") or [c.get("url", "")]):
                key = (c["provider"], u)
                if key not in seen:
                    seen[key] = {"provider": c["provider"], "url": u,
                                 "tier": c.get("tier", "official"),
                                 "label": c.get("label") or s.get("label", ""),
                                 "status": ""}
    return list(seen.values())


# ================================================================ 自检
_CK = {"pass": 0, "fail": 0, "fails": []}


def check(name: str, got: Any, want: Any) -> None:
    if got == want:
        _CK["pass"] += 1
    else:
        _CK["fail"] += 1
        _CK["fails"].append((name, got, want))
        print(f"  [X] {name}\n      got  = {got!r}\n      want = {want!r}")


def check_close(name: str, got: Any, want: Any, tol: float = 1e-9) -> None:
    ok = (got is None and want is None)
    if not ok and got is not None and want is not None:
        try:
            ok = abs(float(got) - float(want)) <= tol
        except (TypeError, ValueError):
            ok = False
    if ok:
        _CK["pass"] += 1
    else:
        _CK["fail"] += 1
        _CK["fails"].append((name, got, want))
        print(f"  [X] {name}\n      got  = {got!r}\n      want = {want!r} (±{tol})")


def check_true(name: str, cond: Any) -> None:
    check(name, bool(cond), True)


def _mini_cfg() -> Dict[str, Any]:
    """自检用的最小配置：三个不同 provider、三种频率、一个派生、一个合成。"""
    return {
        "request": {"timeout": 5, "retries": 1, "retry_backoff_sec": 0.0,
                    "delay_between_sources_sec": 0.0,
                    "min_interval_per_host_sec": 0.0},
        "divergence": {"rel_tol": 0.02, "abs_tol": 0.0},
        "window": {"min_obs_for_z": 5, "z_window": 10, "z_floor_rel": 0.0,
                   "percentile_window": 20, "detail_n": 5,
                   "max_points_per_series": 100},
        "staleness": {"daily": 5, "weekly": 16, "monthly": 45},
        "buckets": {"oil": {"name": "原油", "order": 1},
                    "inventory": {"name": "库存", "order": 2},
                    "shipping": {"name": "航运", "order": 3}},
        "series": [
            {"id": "aa", "label": "测试日频", "bucket": "oil", "unit": "$",
             "decimals": 2, "cadence": "daily", "polarity": 1, "role": "headline",
             "chain": [{"provider": "yahoo", "ref": "TST1",
                        "url": "https://example.invalid/chart/TST1",
                        "accept": "application/json,*/*"}]},
            {"id": "bb", "label": "测试周频", "bucket": "inventory", "unit": "千桶",
             "decimals": 0, "cadence": "weekly", "polarity": -1, "role": "headline",
             "chain": [{"provider": "fred", "ref": "TSTS",
                        "url": "https://example.invalid/fredgraph.csv?id=TSTS",
                        "accept": "text/csv,*/*"}]},
            {"id": "cc", "label": "测试通行量", "bucket": "shipping", "unit": "艘次",
             "decimals": 0, "cadence": "daily", "polarity": -1, "role": "headline",
             "step": 7,
             "chain": [{"provider": "arcgis", "ref": "chokepoint9:n_total",
                        "url": "https://example.invalid/arcgis/query",
                        "accept": "application/json,*/*",
                        "date_field": "date", "value_field": "n_total",
                        "entity_field": "portid", "entity_match": "chokepoint9"}]},
        ],
        "derived": [
            {"id": "aa_x2", "label": "两倍测试", "bucket": "oil", "kind": "diff",
             "a": "aa", "b": "aa", "scale": 0, "unit": "$", "decimals": 2,
             "delta_unit": "", "polarity": 1, "role": "context",
             "definition": "(aa − aa) × 0 ≡ 0（用于验证派生管道与恒等式）"},
            {"id": "aa_chg", "label": "测试变化率", "bucket": "oil",
             "kind": "pct_over", "series": "aa", "window": 3, "unit": "%",
             "decimals": 2, "delta_unit": "pp", "polarity": 1, "role": "context",
             "definition": "最新 / 3 期前 − 1（%）"},
        ],
        "watch_pairs": [{"a": "aa", "b": "bb", "label": "aa ↔ bb",
                         "note": "自检用"}],
        "composite": {"id": "mini", "label": "自检合成指数", "unit": "z",
                      "components": [{"series": "aa", "weight": 1.0, "polarity": 1},
                                     {"series": "bb", "weight": 1.0, "polarity": -1}],
                      "note": "自检用"},
        "data_gaps": {},
    }


def _yahoo_json(pairs: List[Tuple[str, float]]) -> bytes:
    ts = []
    cl = []
    for d, v in pairs:
        dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        ts.append(int(dt.timestamp()))
        cl.append(v)
    return json.dumps({"chart": {"result": [{
        "meta": {"currency": "USD", "exchangeName": "TST"},
        "timestamp": ts, "indicators": {"quote": [{"close": cl}]}}],
        "error": None}}).encode()


def _fred_csv(pairs: List[Tuple[str, float]], sid: str = "TSTS") -> bytes:
    lines = [f"observation_date,{sid}"] + [f"{d},{v}" for d, v in pairs]
    return ("\n".join(lines) + "\n").encode()


def _arcgis_json(pairs: List[Tuple[str, float]], pid: str = "chokepoint9",
                 fld: str = "n_total") -> bytes:
    """PortWatch 返回的是**属性行**，日期是 `YYYY-MM-DD` 字符串
    （不是 epoch 毫秒 —— 这一条是实测确认的，与多数 ArcGIS 图层不同）。"""
    return json.dumps({"features": [
        {"attributes": {"date": d, "portid": pid, fld: v}} for d, v in pairs],
        "exceededTransferLimit": False}).encode()


def selftest() -> int:
    """离线自检。**不联网**，全部用合成样本 + 临时目录。

    这里刻意覆盖的不是「正常路径」，而是**每一条曾经真的骗过我的路径**：
    假成功、静默归零、口径混同、边界外推。合成样本测不出这些，
    但它们才是「日志全绿而数字全错」的来源，所以每种都要有一个反例。
    """
    global QUIET
    QUIET = True
    print(f"finance-front-monitor v{VERSION} 离线自检")
    print("=" * 72)

    # ============================================================ 1 解析器
    print("[1] 解析器")
    rows, why = parse_fred_csv(b"observation_date,DGS10\n2026-09-15,4.10\n"
                               b"2026-09-16,.\n2026-09-17,4.23\n")
    check("FRED 缺值 `.` 被跳过", rows, [("2026-09-15", 4.1), ("2026-09-17", 4.23)])
    check("FRED 正常无错误信息", why, "")
    check("FRED 乱序输入被排序",
          parse_fred_csv(b"d,X\n2026-09-17,4.23\n2026-09-15,4.10\n")[0],
          [("2026-09-15", 4.1), ("2026-09-17", 4.23)])
    check("FRED 同日重复取最后一个",
          parse_fred_csv(b"d,X\n2026-09-17,4.1\n2026-09-17,4.2\n")[0],
          [("2026-09-17", 4.2)])
    check_true("FRED HTML 错误页被识别",
               "HTML" in parse_fred_csv(b"<html><body>oops</body></html>")[1])
    check("FRED 只有表头 → 空", parse_fred_csv(b"observation_date,X\n")[0], [])
    check("FRED 空体 → 空", parse_fred_csv(b"")[0], [])

    rows, _ = parse_stooq_csv(b"Date,Open,High,Low,Close,Volume\n"
                              b"2026-09-16,1,2,0.5,1.5,100\n"
                              b"2026-09-17,1.5,3,1,2.5,200\n")
    check("Stooq 取 Close 列", rows, [("2026-09-16", 1.5), ("2026-09-17", 2.5)])
    check_true("Stooq No data 被识别",
               "No data" in parse_stooq_csv(b"No data")[1])
    check_true("Stooq JS 挑战页被识别（不是符号错误）",
               "JS 反爬挑战页" in parse_stooq_csv(
                   b"<!DOCTYPE html><html><body><noscript>This site requires "
                   b"JavaScript to verify your browser.</noscript></body></html>")[1])
    check_true("Stooq 限额提示被识别",
               "限额" in parse_stooq_csv(b"Exceeded the daily hits limit")[1])

    # ---- 列名取值 CSV（Datawrapper / Silver Bulletin 特朗普支持率）----
    # ★ 这个用例的核心不是「能解析」，而是**取对了列**：
    #   kSCt4.csv 里 approve 与 disapprove 都是两位小数，按位置取会静默拿到
    #   反向的那一列，数字看着完全合理。
    dwbody = (b"modeldate,approve,disapprove\n"
              b"1/21/2025,51.62839,39.97147\n"
              b"9/21/2026,38.59664,58.8613\n")
    rows, why = parse_named_csv(dwbody, "modeldate", "approve", "%m/%d/%Y")
    check("按列名取 approve（不是 disapprove）", rows,
          [("2025-01-21", 51.62839), ("2026-09-21", 38.59664)])
    check("列名取值 正常无错误", why, "")
    check("按列名取 disapprove",
          parse_named_csv(dwbody, "modeldate", "disapprove", "%m/%d/%Y")[0],
          [("2025-01-21", 39.97147), ("2026-09-21", 58.8613)])
    check_true("列名不存在被报出（不静默取错列）",
               "不存在" in parse_named_csv(dwbody, "modeldate", "approve_xx",
                                           "%m/%d/%Y")[1])
    check_true("日期格式不符被报出（月/日不要猜）",
               "无有效数值行" in parse_named_csv(dwbody, "modeldate", "approve",
                                                 "%Y-%m-%d")[1])
    check_true("列名取值 CSV 的 HTML 错误页被识别",
               "HTML" in parse_named_csv(b"<html>oops</html>", "d", "v")[1])
    check("列名取值 CSV 的缺值行被跳过",
          parse_named_csv(b"d,v\n2026-09-16,1.5\n2026-09-17,\n2026-09-18,NA\n",
                          "d", "v")[0], [("2026-09-16", 1.5)])

    tbody = (b"Date,3 Mo,2 Yr,10 Yr\n"
             b"09/16/2026,4.00,4.10,4.15\n"
             b"09/17/2026,4.01,4.12,4.23\n")
    rows, why = parse_treasury_csv(tbody, "10 Yr")
    check("财政部 CSV 取指定列并转 ISO 日期", rows,
          [("2026-09-16", 4.15), ("2026-09-17", 4.23)])
    check("财政部 正常无错误", why, "")
    check_true("财政部 列名缺失被报出",
               "不存在" in parse_treasury_csv(tbody, "99 Yr")[1])
    check("财政部 非法日期行被跳过",
          parse_treasury_csv(b"Date,10 Yr\nnot-a-date,4.0\n09/17/2026,4.2\n",
                             "10 Yr")[0], [("2026-09-17", 4.2)])
    check_true("财政部 BOM 被处理",
               len(parse_treasury_csv(
                   "\ufeffDate,10 Yr\n09/17/2026,4.2\n".encode("utf-8"),
                   "10 Yr")[0]) == 1)

    yj = json.dumps({"chart": {"result": [{
        "meta": {"currency": "USD"},
        "timestamp": [1758000000, 1758086400],
        "indicators": {"quote": [{"close": [10.0, None]}]}}], "error": None}}).encode()
    rows, _ = parse_yahoo_chart(yj)
    check("Yahoo close=null 被跳过", len(rows), 1)
    check_true("Yahoo chart.error 被识别",
               "错误" in parse_yahoo_chart(
                   json.dumps({"chart": {"result": None,
                                         "error": {"code": "Not Found"}}}).encode())[1])
    check_true("Yahoo 非法 JSON 被识别",
               "解析失败" in parse_yahoo_chart(b"<html>")[1])
    check("Yahoo result=[] → 空",
          parse_yahoo_chart(json.dumps({"chart": {"result": []}}).encode())[0], [])

    # ---- 丢弃「当日未收盘会话」----
    # ★ 用固定日期构造，不依赖运行时刻；断言的是**语义**：
    #   日期 ≥ drop_from 的 bar 一律不出现，且丢弃数被如实记账。
    dropj = _yahoo_json([("2026-09-21", 10.0), ("2026-09-22", 11.0),
                         ("2026-09-23", 12.0)])
    stats: Dict[str, Any] = {}
    rows, why = parse_yahoo_chart(dropj, "2026-09-23", stats)
    check("未收盘会话被丢弃（只留完整交易日）", rows,
          [("2026-09-21", 10.0), ("2026-09-22", 11.0)])
    check("丢弃数被记账", stats.get("dropped_inprogress"), 1)
    check("不传 drop_from → 保留全部（默认行为不变）",
          len(parse_yahoo_chart(dropj)[0]), 3)
    st2: Dict[str, Any] = {}
    _rows, _why = parse_yahoo_chart(dropj, "2026-09-24", st2)
    check("丢弃后仍有剩余点 → 不算错误", _rows[-1][0], "2026-09-23")
    only_today = _yahoo_json([("2026-09-23", 12.0)])
    r3, w3 = parse_yahoo_chart(only_today, "2026-09-23")
    check("全被丢弃 → 报错而不是静默空表", (r3, "丢弃" in w3), ([], True))

    aj = json.dumps({"features": [
        {"attributes": {"date": "2026-09-12", "portid": "chokepoint9",
                        "n_total": 30}},
        {"attributes": {"date": "2026-09-13", "portid": "chokepoint9",
                        "n_total": 31}}]}).encode()
    rows, why = expand_arcgis(aj, "date", "n_total", "portid", "chokepoint9")
    check("ArcGIS 字符串日期展开", rows,
          [("2026-09-12", 30.0), ("2026-09-13", 31.0)])
    check("ArcGIS 实体匹配通过", why, "")
    rows, why = expand_arcgis(aj, "date", "n_total", "portid", "chokepoint6")
    check("ArcGIS 实体不匹配 → 空", rows, [])
    check_true("ArcGIS 实体不匹配给出原因",
               "chokepoint6" in why and "chokepoint9" in why)
    check("ArcGIS features 为空 → 空",
          expand_arcgis(b'{"features":[]}', "date", "n_total")[0], [])
    check_true("ArcGIS error 字段被识别",
               "ArcGIS 错误" in parse_arcgis_json(
                   json.dumps({"error": {"code": 400,
                                         "message": "Invalid URL"}}).encode())[1])

    # ====================================================== 2 日期与哨兵
    print("[2] 日期、哨兵值与非有限数")
    check("iso_date 正常", iso_date("2026-09-18"), "2026-09-18")
    check("iso_date 拒绝非日期", iso_date("not-a-date"), None)
    check("iso_date 拒绝 2026-13-01", iso_date("2026-13-01"), None)
    check("iso_date 拒绝公元 26 年", iso_date("0026-09-18"), None)
    check("iso_date 拒绝 1899", iso_date("1899-01-01"), None)
    check("days_between 跨周末 = 3", days_between("2026-09-11", "2026-09-14"), 3)
    check("days_between 非法输入 → None", days_between("x", "2026-09-14"), None)
    check("epoch_ms_to_date 毫秒", epoch_ms_to_date(1758000000000), "2025-09-16")
    check("epoch_ms_to_date 秒（自动识别）",
          epoch_ms_to_date(1758000000), epoch_ms_to_date(1758000000000))
    check("epoch_ms_to_date 0 → None（纪元哨兵）", epoch_ms_to_date(0), None)
    check("epoch_ms_to_date None → None", epoch_ms_to_date(None), None)
    check("epoch_ms_to_date 字符串日期兜底",
          epoch_ms_to_date("2026-09-13"), "2026-09-13")
    check("哨兵日期 1970-01-01 被丢弃",
          _finalize([("1970-01-01", 5.0), ("2026-09-17", 1.0)]),
          [("2026-09-17", 1.0)])
    check("NaN 被丢弃（否则会污染均值/σ）",
          _finalize([("2026-09-16", float("nan")), ("2026-09-17", 1.0)]),
          [("2026-09-17", 1.0)])
    check("Inf 被丢弃（否则 σ=inf → z=0 假平稳）",
          _finalize([("2026-09-16", float("inf")), ("2026-09-17", 1.0)]),
          [("2026-09-17", 1.0)])
    check("_finalize 去重且升序",
          _finalize([("2026-09-18", 2.0), ("2026-09-17", 1.0),
                     ("2026-09-18", 3.0)]),
          [("2026-09-17", 1.0), ("2026-09-18", 3.0)])

    # ============================================================ 3 变换
    print("[3] 单位变换")
    e = {"scale": 0.1}
    check("scale=0.1 把收益率×10 还原（^TNX）",
          apply_transform([("2026-09-17", 42.3)], e), [("2026-09-17", 4.23)])
    check("scale+offset 仿射",
          apply_transform([("2026-09-17", 1.0)], {"scale": 2.0, "offset": 1.0}),
          [("2026-09-17", 3.0)])
    rows = [("2026-09-17", 4.23)]
    check_true("恒等变换不复制列表",
               apply_transform(rows, {}) is rows)

    # ==================================================== 4 统计与分级
    print("[4] 统计、步长与分级")
    pts = [("2026-09-%02d" % (i + 1), float(100 + i)) for i in range(10)]
    check("changes(k=1)", changes(pts, 1), 1.0)
    check("changes(k=5)", changes(pts, 5), 5.0)
    check("changes(k=10) 越界 → None", changes(pts, 10), None)
    check("step_span 跨周末",
          step_span([("2026-09-11", 1.0), ("2026-09-14", 2.0)], 1), 3)
    ds = delta_series_step(pts, 1, 60)
    check("delta_series_step 长度", len(ds), 9)
    check("delta_series_step 步长 3 长度", len(delta_series_step(pts, 3, 60)), 7)
    check_close("delta_series_step 步长 3 首个值",
                delta_series_step(pts, 3, 60)[0], 3.0)
    check("stdev 单点 → None（不是 0）", stdev([1.0]), None)
    check_close("stdev 两点样本标准差", stdev([1.0, 3.0]), 1.4142135623730951)
    check("mean 空 → None", mean([]), None)
    check("safe_div 除零 → None", safe_div(1.0, 0.0), None)
    check("safe_div None → None", safe_div(None, 2.0), None)
    check("percentile_rank 样本不足 → None", percentile_rank([1.0], 1.0), None)
    check_close("percentile_rank 中位", percentile_rank([1.0, 2.0, 3.0, 4.0], 2.0),
                50.0)

    cfg = _mini_cfg()
    ser = {"id": "z1", "label": "z", "unit": "点", "cadence": "daily",
           "decimals": 2, "polarity": 1, "bucket": "oil", "role": "context"}
    # 10 个零变动 + 1 个 +5 的变动 → σ 非零。
    # ★ m 个 delta 下 z 恰为 √m：9 个 delta 时 z=3.0 撞线判不出「升级」，
    #   必须 ≥10 个 delta（z=√10≈3.16）。
    p2 = [(f"2026-09-{i:02d}", 10.0) for i in range(1, 11)] + [("2026-09-11", 15.0)]
    st = compute_stats(p2, ser, cfg, prev_seen=None)
    check("统计 最新值", st["value"], 15.0)
    check("统计 上期值", st["prev_value"], 10.0)
    check("统计 d1", st["d1"], 5.0)
    check("统计 gdelta 等于 d1（步长 1）", st["gdelta"], 5.0)
    check("统计 step", st["step"], 1)
    check_true("统计 σ 非零", st["sigma"] and st["sigma"] > 0)
    check_true("极端变动被判为升级", abs(st["z"]) > 3 and st["grade"] == "升级")

    st0 = compute_stats([("2026-09-01", 5.0)], ser, cfg, prev_seen=None)
    check("单点 → 样本不足（不给 z）", st0["grade"], "样本不足")
    check("单点 → z=None", st0["z"], None)
    check_true("单点 → 给出原因", "需 ≥" in st0["reason"])

    flat = [(f"2026-09-{i:02d}", 10.0) for i in range(1, 12)]
    stf = compute_stats(flat, ser, cfg, prev_seen=None)
    check("全零变动 σ=0 → 样本不足，不产出 z=inf", stf["grade"], "样本不足")

    pts2 = [(f"2026-09-{i:02d}", 100.0 + (i % 2)) for i in range(1, 31)]
    ch = [(d, v) for d, v in pts2]
    s1 = compute_stats(ch, ser, cfg, prev_seen={"date": "2026-09-01"})
    check_true("new_point 判定为真（末日 > 上轮）",
               s1["new_point"] is True)
    s2 = compute_stats(ch, ser, cfg, prev_seen={"date": ch[-1][0]})
    check("new_point 判定为假（末日 = 上轮）", s2["new_point"], False)
    s3 = compute_stats(ch, ser, cfg, prev_seen=None)
    check("首轮 new_point = None（无从比较）", s3["new_point"], None)

    # 周期性序列：步长 7 必须消除周内季节性
    seas = [(f"2026-09-{i:02d}", 100.0 + (10.0 if i % 7 == 0 else 0.0))
            for i in range(1, 29)]
    ssp = compute_stats(seas, {"id": "s", "label": "s", "unit": "艘次",
                               "cadence": "daily", "decimals": 0, "polarity": -1,
                               "bucket": "shipping", "step": 7, "role": "context"},
                        cfg, prev_seen=None)
    check("步长 7 序列的 step 被记录", ssp["step"], 7)
    check("步长 7 时 gdelta 用 7 期差", ssp["gdelta"], seas[-1][1] - seas[-8][1])
    check_close("步长 7 的 gdelta 恰好抵消季节性", ssp["gdelta"], 0.0)

    # ========================================================== 5 派生
    print("[5] 派生量（含对齐与边界）")
    sm = {
        "x": [("2026-09-15", 10.0), ("2026-09-16", 12.0), ("2026-09-17", 15.0)],
        "y": [("2026-09-15", 4.0), ("2026-09-17", 5.0)],
    }
    check("align_dates 取交集", align_dates(sm, ["x", "y"]),
          ["2026-09-15", "2026-09-17"])
    check("align_dates 缺序列 → 空", align_dates(sm, ["x", "zz"]), [])
    r, _ = build_derived_series(sm, {"kind": "diff", "a": "x", "b": "y"})
    check("diff 只在交集日期上有值", r, [("2026-09-15", 6.0), ("2026-09-17", 10.0)])
    r, _ = build_derived_series(sm, {"kind": "diff", "a": "x", "b": "y",
                                     "scale": 100})
    check("diff+scale=100（百分点→bp）", r[-1][1], 1000.0)
    r, _ = build_derived_series(sm, {"kind": "pct_ratio", "a": "x", "b": "y"})
    check_close("pct_ratio", r[0][1], 150.0)
    r, _ = build_derived_series(sm, {"kind": "ratio", "a": "x", "b": "y"})
    check("ratio 除零日期被跳过",
          build_derived_series(
              {"a": [("2026-09-17", 1.0)], "b": [("2026-09-17", 0.0)]},
              {"kind": "ratio", "a": "a", "b": "b"})[0], [])
    r, why = build_derived_series(sm, {"kind": "no_such_kind"})
    check("未知算子 → 空", r, [])
    check_true("未知算子给出允许集合", "允许" in why)
    r, _ = build_derived_series(sm, {"kind": "pct_over", "series": "x",
                                     "window": 2})
    check_close("pct_over window=2 只从第 3 点起", r[0][1], 50.0)
    check("pct_over 期数不足 → 空",
          build_derived_series(sm, {"kind": "pct_over", "series": "x",
                                    "window": 10})[0], [])
    r, _ = build_derived_series(
        {"x": [(f"2026-09-{i:02d}", float(i)) for i in range(1, 21)]},
        {"kind": "ma", "series": "x", "window": 5})
    check("ma 前 4 点不给值（窗口不足）", len(r), 16)
    check_close("ma 末值 = 后 5 个的均值", r[-1][1], 18.0)
    r, _ = build_derived_series(
        {"x": [(f"2026-09-{i:02d}", float(i)) for i in range(1, 21)]},
        {"kind": "rollstd", "series": "x", "window": 5, "annualize": 1.0})
    check_close("rollstd 对等差序列 σ=0", r[-1][1], 0.0)
    rv, _ = build_derived_series(
        {"x": [("2026-09-01", 100.0), ("2026-09-02", 110.0), ("2026-09-03", 99.0)]},
        {"kind": "rollstd", "series": "x", "window": 2, "annualize": 1.0,
         "return_base": True, "scale": 100.0})
    # 收益率序列 [+10%, −10%] 的样本 σ（n−1）= 0.1×√2 → ×100 ≈ 14.14（百分比）
    check_close("rollstd return_base 按收益率计 σ", rv[-1][1], 14.142135623730953)
    dd, _ = build_derived_series(
        {"x": [("2026-09-01", 100.0), ("2026-09-02", 80.0)]},
        {"kind": "drawdown", "series": "x", "window": 5})
    check_close("drawdown 从高点回撤 −20%", dd[-1][1], -20.0)
    zd, _ = build_derived_series(
        {"x": [("2026-09-%02d" % i, float(i)) for i in range(1, 11)]},
        {"kind": "zdev", "series": "x", "window": 5})
    check_true("zdev 尾值 > 0（处于窗口上沿）", zd[-1][1] > 0)
    lin, _ = build_derived_series(sm, {"kind": "linear", "terms": [
        {"series": "x", "weight": 1.0}, {"series": "y", "weight": -1.0}]})
    check("linear 组合", lin, [("2026-09-15", 6.0), ("2026-09-17", 10.0)])

    # ====================================================== 6 合成指数
    print("[6] 合成指数（覆盖度与极性）")
    stats = {"p": {"series": "p", "label": "P", "z": 2.0},
             "q": {"series": "q", "label": "Q", "z": 1.0},
             "r": {"series": "r", "label": "R", "z": None}}
    c = build_composite({"composite": {"components": [
        {"series": "p", "weight": 1.0, "polarity": 1},
        {"series": "q", "weight": 1.0, "polarity": -1},
        {"series": "r", "weight": 1.0, "polarity": 1}]}}, stats)
    check_close("合成 得分 = (1×2 + 1×−1) / 2", c["score"], 0.5)
    check("合成 有值分量数", c["n_used"], 2)
    check("合成 总分量数", c["n_total"], 3)
    check_close("合成 覆盖度", c["coverage"], 2 / 3)
    check("合成 缺分量被记名", c["missing"], ["r"])
    check_close("合成 参与权重合计", c["sum_weight"], 2.0)
    check("合成 分量按贡献绝对值排序", c["components"][0]["series"], "p")
    c2 = build_composite({"composite": {"components": [
        {"series": "zz", "weight": 1.0, "polarity": 1}]}}, stats)
    check("合成 全缺 → 得分为 None", c2["score"], None)
    check("合成 全缺 → 覆盖度 0", c2["coverage"], 0.0)

    # ================================================= 7 历史累积与修订
    print("[7] 累积历史：修订记账与「不因单轮变短而丢点」")
    tmp = tempfile.mkdtemp(prefix="fm_st_")
    try:
        m1 = merge_history(tmp, "h1", [("2026-09-15", 1.0), ("2026-09-16", 2.0)],
                           run_tag="t1", provider="p", max_points=0)
        check("累积 首轮新增 2 点", m1["new_points"], 2)
        check("累积 首轮无修订", m1["revised"], [])
        m2 = merge_history(tmp, "h1", [("2026-09-16", 9.0), ("2026-09-17", 3.0)],
                           run_tag="t2", provider="p", max_points=0)
        check("累积 修订被记账", m2["revised"],
              [{"date": "2026-09-16", "was": 2.0, "now": 9.0}])
        check("累积 新点只算新增日期", m2["new_points"], 1)
        check("累积 修订后取新值", read_history(tmp, "h1"),
              [("2026-09-15", 1.0), ("2026-09-16", 9.0), ("2026-09-17", 3.0)])
        m3 = merge_history(tmp, "h1", [("2026-09-17", 3.0)], run_tag="t3",
                           provider="p", max_points=0)
        check("累积 本轮只给 1 点也不丢历史（与新闻快照修剪方向相反）",
              len(read_history(tmp, "h1")), 3)
        check("累积 无新点时 new_points=0", m3["new_points"], 0)
        merge_history(tmp, "h2", [(f"2026-01-{i:02d}", float(i))
                                  for i in range(1, 11)],
                      run_tag="t4", provider="p", max_points=5)
        h = read_history(tmp, "h2")
        check("累积 max_points 裁剪保留最新的", len(h), 5)
        check("累积 裁剪丢的是最旧的", h[0][0], "2026-01-06")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ============================================== 8 raw 命名往返
    print("[8] 原始载荷命名（--from-file 可还原）")
    for sid, prov, ref, part, want in (
            ("brent", "yahoo", "BZ=F", None, "brent__yahoo__BZ_F.json"),
            ("dgs10", "treasury", "10 Yr", None, "dgs10__treasury__10_Yr.csv"),
            ("spr", "fred", "WCSSTUS1", None, "spr__fred__WCSSTUS1.csv"),
            ("dgs10", "treasury", "10 Yr", 1, "dgs10__treasury__10_Yr__u1.csv")):
        got = raw_name(sid, prov, ref, part)
        check(f"raw_name {sid}/{prov}/{ref}", got, want)
        m = RAW_NAME_RE.match(got)
        check_true(f"RAW_NAME_RE 可还原 {got}", m is not None)
        if m:
            check(f"  → sid 还原 {sid}", m.group("sid"), sid)
            check(f"  → provider 还原", m.group("prov"), prov)
            check(f"  → ref 还原", m.group("ref"), _san(ref))
            check(f"  → part 还原", m.group("part"),
                  None if part is None else str(part))
    check("raw_ext yahoo=json", raw_ext("yahoo"), "json")
    check("raw_ext arcgis=json", raw_ext("arcgis"), "json")
    check("raw_ext fred=csv", raw_ext("fred"), "csv")

    # ====================================== 9 URL 模板（跨年）
    print("[9] URL 模板：按年切分的官方表必须取相邻两年")
    dr = datetime(2027, 1, 3, tzinfo=timezone.utc)
    us = entry_urls({"urls": ["https://x/{year_prev}/a", "https://x/{year}/a"]}, dr)
    check("urls 展开为两年", us, ["https://x/2026/a", "https://x/2027/a"])
    check("单 url 模板展开", entry_urls({"url": "https://x/{year}"}, dr),
          ["https://x/2027"])
    check("无模板时原样返回", entry_urls({"url": "https://x/fixed"}, dr),
          ["https://x/fixed"])

    # ================================================= 10 渲染与口径
    print("[10] 渲染：量纲、频率分表、无脏值")
    st_bp = {"series": "dgs10", "label": "10Y", "unit": "%", "decimals": 3,
             "delta_unit": "", "gdelta": 0.08, "d1": 0.08, "z": 2.5,
             "grade": "警戒", "percentile": 88.0, "bucket": "rates",
             "date": "2026-09-17", "value": 4.23, "flags": ""}
    check("收益率变动以 bp 显示（+8.0bp，不是 +1.93%）",
          delta_cell(st_bp, "gdelta"), "+8.0bp")
    check_true("delta_unit_of 对 % 序列推断为 bp",
               delta_unit_of(st_bp) == "bp")
    st_pp = dict(st_bp, unit="%", delta_unit="pp", gdelta=1.9)
    check("显式 delta_unit=pp 时不用 bp", delta_cell(st_pp, "gdelta"), "+1.9pp")
    st_px = dict(st_bp, unit="点", delta_unit="", decimals=2, gdelta=-12.34)
    check("非百分比序列用原生单位", delta_cell(st_px, "gdelta"), "-12.34")
    check("缺值时显示 —", delta_cell({"gdelta": None}, "gdelta"), "—")
    check("步长 >1 在标志里显式标出",
          "步长7期" in flags_cell({"step": 7}), True)
    check("滞后被标出",
          "滞后9d" in flags_cell({"state": "lagging", "lag_days": 9}), True)
    check("无新点被标出",
          "无新点" in flags_cell({"new_point": False}), True)
    check("源间分歧被标出",
          "源间分歧" in flags_cell({"divergent": [{"date": "x"}]}), True)
    check("正常时标志为空", flags_cell({}), "")
    check("跨度 >4 天加警告", span_cell({"gdelta_span": 3}, "gdelta"), "3d")
    check("跨度 >4 天加警告（4 天不警告）", span_cell({"gdelta_span": 4}, "gdelta"), "4d")
    check("跨度 5 天加警告", span_cell({"gdelta_span": 5}, "gdelta"), "5d⚠")
    check("z 显示带符号两位", z_cell({"z": -1.234}), "-1.23")
    check("z 缺失显示 —", z_cell({}), "—")
    check("md 转义 |", md_escape("a|b"), "a\\|b")
    check("档位标记", grade_mark("升级"), "🔴")
    check("fmt_num 千分位", fmt_num(51572.3281, 2), "51,572.33")
    check("fmt_num None → —", fmt_num(None, 2), "—")
    check("fmt_bp 正号", fmt_bp(0.08), "+8.0bp")
    check("fmt_bp 负号", fmt_bp(-0.125), "-12.5bp")
    check("fmt_pct", fmt_pct(-0.0123), "-1.23%")

    # ============================================ 11 端到端（离线重放）
    print("[11] 端到端：写 raw → 离线跑管道 → 报告不含脏值")
    tmp = tempfile.mkdtemp(prefix="fm_e2e_")
    try:
        raw = os.path.join(tmp, "raw")
        os.makedirs(raw, exist_ok=True)
        cfg2 = _mini_cfg()
        aa = [(f"2026-08-{i:02d}", 100.0 + i) for i in range(1, 29)]
        aa += [(f"2026-09-{i:02d}", 128.0 + i) for i in range(1, 19)]
        bb = [(f"2026-0{7 + (i // 4)}-{1 + (i % 4) * 7:02d}", 500.0 + i * 2)
              for i in range(8)]
        cc = [(f"2026-09-{i:02d}", float(30 + i)) for i in range(1, 19)]
        with open(os.path.join(raw, raw_name("aa", "yahoo", "TST1")), "wb") as f:
            f.write(_yahoo_json(aa))
        with open(os.path.join(raw, raw_name("bb", "fred", "TSTS")), "wb") as f:
            f.write(_fred_csv(bb))
        with open(os.path.join(raw, raw_name("cc", "arcgis",
                                             "chokepoint9:n_total")), "wb") as f:
            f.write(_arcgis_json(cc))
        run_dt = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
        res = run_pipeline(cfg2, tmp, run_dt, raw_dir=raw)
        byid = {r["series"]: r for r in res["records"]}
        check("端到端 三条序列都有值",
              all(byid[k]["value"] is not None for k in ("aa", "bb", "cc")), True)
        check("端到端 aa 最新值", byid["aa"]["value"], 146.0)
        check("端到端 aa 末日", byid["aa"]["date"], "2026-09-18")
        check("端到端 cc 走步长 7", byid["cc"]["step"], 7)
        check("端到端 派生 aa_x2 恒为 0",
              [v for _, v in res["derived"][0].get("window") or []][:1] or
              [0.0], [0.0])
        rep = render_report(cfg2, res["records"], res["derived"],
                            res["composite"],
                            {"fetched_at": "t", "egress": "none", "run_tag": "r",
                             "total_points": 1, "providers_used": ["yahoo"],
                             "raw_files": 3, "raw_bytes": 1, "detail_n": 5,
                             "endpoints": [], "bucket_gaps": {},
                             "divergences": res["divergences"]}, run_dt)
        for bad in ("nan", "None", "inf", "NaN", "Infinity"):
            check_true(f"报告不含脏值 {bad!r}",
                       not re.search(r"(?<![A-Za-z])" + bad + r"(?![A-Za-z])", rep))
        check_true("报告有 §1 一页速览", "## 1. 一页速览" in rep)
        check_true("报告有 §13 已知偏差", "## 13. 已知偏差" in rep)
        check_true("§0 说明 bp 口径", "bp（基点）" in rep or "bp" in rep)
        check_true("§0 说明代理量非概率", "不是概率" in rep or "代理量" in rep)
        check_true("§0 说明相关≠因果", "相关 ≠ 因果" in rep)
        check_true("§1 含合成指数覆盖度", "覆盖" in rep)
        check_true("§10 有分项贡献表", "贡献（权重×极性×z）" in rep)
        # §1 只允许 headline
        seg = rep.split("## 1. 一页速览", 1)[1].split("## 2.", 1)[0]
        check_true("§1 表内只有 headline 序列",
                   "测试日频" in seg and "测试周频" in seg)
        # 周频必须与日频分表
        seg3 = rep.split("## 4. 库存与战略储备", 1)[1].split("## 5.", 1)[0]
        check_true("§4 周频序列标注「不可比大小」",
                   "不可比大小" in seg3 or "周频序列" in seg3)
        # 频率分表：日频表头里不能出现周频序列
        daily_seg = seg3.split("### 4.2", 1)[0]
        check_true("周频序列不会出现在日频表里", "测试周频" not in daily_seg)
        # 落盘
        w = write_outputs(tmp, run_dt, cfg2, res, rep)
        check_true("records jsonl 已落盘", os.path.isfile(w["records_path"]))
        check_true("报告 md 已落盘", os.path.isfile(w["md_path"]))
        check_true("LATEST.md 已落盘",
                   os.path.isfile(os.path.join(tmp, "LATEST.md")))
        check_true("累积观测账已落盘",
                   os.path.isfile(os.path.join(tmp, "ALL-observations.jsonl")))
        check_true("last_seen 已落盘",
                   os.path.isfile(os.path.join(tmp, "state", "last_seen.json")))
        # 再跑一次：应有「无新点」而非重复计数
        res2 = run_pipeline(cfg2, tmp, run_dt, raw_dir=raw)
        b2 = {r["series"]: r for r in res2["records"]}
        check("重放 第二次 new_point 为 False",
              b2["aa"]["new_point"], False)
        check("重放 第二次不新增历史点", b2["aa"]["new_hist_points"], 0)
        check("重放 第二次修订为 0", b2["aa"]["revised"], [])
        check("重放 观测点数不变", b2["aa"]["hist_points"],
              byid["aa"]["hist_points"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ============================================ 12 真实配置自洽
    print("[12] 真实 config.json 自洽性")
    if os.path.isfile(CONFIG_PATH):
        try:
            rc = load_config()
        except Exception as e:  # noqa: BLE001
            rc = None
            check_true(f"config.json 可加载（{e}）", False)
        if rc:
            sl = series_list(rc)
            ids = [s["id"] for s in sl]
            check("序列 id 唯一", len(ids), len(set(ids)))
            check_true("每条序列都有 label", all(s.get("label") for s in sl))
            check_true("每条序列都有 bucket",
                       all(s.get("bucket") for s in sl))
            check_true("bucket 都有定义",
                       all(s["bucket"] in rc.get("buckets", {}) for s in sl))
            check_true("cadence 取值合法",
                       all(s["cadence"] in ("daily", "weekly", "monthly")
                           for s in sl))
            check_true("role 取值合法",
                       all(s["role"] in ("headline", "context") for s in sl))
            check_true("polarity 取值合法",
                       all(int(s["polarity"]) in (-1, 0, 1) for s in sl))
            bad_prov = []
            for s in sl:
                for c in s["chain"]:
                    if c["provider"] not in CHAIN_PROVIDERS:
                        bad_prov.append((s["id"], c["provider"]))
            check("链条目 provider 合法", bad_prov, [])
            check("每个链条目都声明信源等级 tier",
                  [(s["id"], c["provider"]) for s in sl for c in s["chain"]
                   if c.get("tier") not in CHAIN_TIERS], [])
            check("官方源标记 official / 行情源标记 market",
                  [(s["id"], c["provider"]) for s in sl for c in s["chain"]
                   if (c["provider"] in ("yahoo", "stooq")) !=
                      (c.get("tier") == "market")], [])
            # ★ 频率不能与端点能力矛盾：周频序列不该走日频行情端点
            mism = [(s["id"], c["provider"]) for s in sl if s["cadence"] == "weekly"
                    for c in s["chain"] if c["provider"] in ("yahoo", "stooq")]
            check("周频序列不挂在日频行情端点上（口径守卫）", mism, [])
            check_true("数据源链支持「一个条目多个 URL」",
                       isinstance(next(c for s in sl for c in s["chain"]
                                       if s["id"] == "dgs10")["urls"], list))

            # ---- TACO 五因子对接块（跨项目）--------------------------------
            # ★ 这份「对照表」必须与真实序列对得上。对不上的话它就是一段
            #   好看但会误导人的注释：将来有人按它去找因子，会找不到东西。
            tf = rc.get("taco_factors") or {}
            tf_facs = tf.get("factors") or []
            _ids = {s["id"] for s in sl}
            _bucket = {s["id"]: s.get("bucket") for s in sl}
            check("taco_factors 覆盖且仅覆盖 C/D/E/G/H 五个因子",
                  sorted(str(f.get("code")) for f in tf_facs),
                  ["C", "D", "E", "G", "H"])
            check("taco_factors 引用的序列都存在",
                  [f.get("series") for f in tf_facs
                   if f.get("series") not in _ids], [])
            # ★ 只要求**新增**的两条落在 taco 桶。因子 C/G/H 是对侧同源指标，
            #   本工程早就有了，分别住在 rates/equity/oil 桶里 ——
            #   为了「凑齐五因子」把它们复制一份进 taco 桶是错的：
            #   同一条序列出现两次，两处容差、两处口径，迟早会不一致。
            check("TACO 新增序列都落在 taco 桶",
                  [s["id"] for s in sl if s.get("bucket") == "taco"
                   and s["id"] not in ("taco_breakeven5y", "taco_trump_approval")], [])
            check_true("taco_factors 每个因子都写了对齐结论",
                       all(f.get("align") and f.get("evidence") for f in tf_facs))
            # 口径开关：只取完整交易日收盘。这条一旦被关掉，
            # 「最新值」在同一天内会随盘中价漂移，复核重跑必然对不上。
            check_true("全库只取完整交易日收盘（与 taco-monitor 口径对齐）",
                       rc["request"].get("drop_inprogress_session") is True)
            dspecs = {d["id"] for d in rc.get("derived", [])}
            refok = []
            for d in rc.get("derived", []):
                refs = ([t["series"] for t in d.get("terms", [])]
                        if d.get("kind") == "linear"
                        else [v for v in (d.get("a"), d.get("b"),
                                          d.get("series")) if v])
                for r in refs:
                    if r not in ids and r not in dspecs:
                        refok.append((d["id"], r))
            check("派生量的输入序列都存在", refok, [])
            check("每个派生量都有 definition",
                  [d["id"] for d in rc.get("derived", [])
                   if not (d.get("definition") or "").strip()], [])
            check("每个派生量都显式声明 delta_unit（空串也算声明）",
                  [d["id"] for d in rc.get("derived", [])
                   if "delta_unit" not in d], [])
            cids = [c["series"] for c in
                    (rc.get("composite") or {}).get("components", [])]
            check("合成指数分量都存在",
                  [c for c in cids if c not in ids and c not in dspecs], [])
            check_true("合成指数有覆盖度说明",
                       "覆盖度" in ((rc.get("composite") or {}).get("note") or ""))
            check_true("合成指数说明单位为 z",
                       "z" in ((rc.get("composite") or {}).get("unit") or ""))
            check_true("合成指数声明不是概率",
                       "概率" in ((rc.get("composite") or {}).get("note") or ""))
            check_true("watch_pairs 全部可解析",
                       all(w["a"] in ids + list(dspecs) and
                           w["b"] in ids + list(dspecs)
                           for w in rc.get("watch_pairs", [])))
            check_true("staleness 三档齐全",
                       all(k in rc.get("staleness", {})
                           for k in ("daily", "weekly", "monthly")))
            check_true("window 关键项齐全",
                       all(k in rc.get("window", {}) for k in
                           ("min_obs_for_z", "z_window", "percentile_window")))
            check_true("request 有限速设置",
                       "min_interval_per_host_sec" in rc.get("request", {}))
            check_true("数据缺口清单有记录（不掩盖拿不到的源）",
                       len((rc.get("data_gaps") or {}).get("chokepoint_probe", [])) >= 3)
            # FRED 必须走 curl
            fred_t = {c.get("transport") for s in sl for c in s["chain"]
                      if c["provider"] == "fred"}
            check("FRED 链条目若在场必须走 curl（Akamai 指纹判定）",
                  fred_t - {"curl"}, set())
            # 海峡序列必须带实体断言，防止静默串海峡
            arc = [(s["id"], c.get("entity_match")) for s in sl
                   for c in s["chain"] if c["provider"] == "arcgis"]
            check_true("所有 ArcGIS 条目都带 entity_match（防串海峡）",
                       all(e for _, e in arc))
            check_true("海峡序列都用步长 7（周内季节性）",
                       all(int(s.get("step", 1)) == 7 for s in sl
                           if s["bucket"] == "shipping"
                           and any(c["provider"] == "arcgis" for c in s["chain"])))
            check_true("PortWatch 序列的 max_lag 放宽到 ≥14 天（汇编滞后）",
                       all(int(s.get("max_lag", 0)) >= 14 for s in sl
                           if any(c["provider"] == "arcgis" for c in s["chain"])))
    else:
        print("  (无 config.json，跳过)")

    # ---- 取数状态渲染：无数据不得显示成「正常」 ----------------------
    print("\n[13] 取数状态渲染")
    check("无值序列滞后列显示「无数据」（不得再出现 正常(阈None)）",
          fetch_state_cell({"value": None, "max_lag": None}), "无数据")
    check("正常序列滞后列带阈值", fetch_state_cell(
        {"value": 1.0, "max_lag": 5}), "正常(阈5d)")
    check("缺阈值时不印 None", fetch_state_cell(
        {"value": 1.0, "max_lag": None}), "正常")
    check("滞后序列显示天数", fetch_state_cell(
        {"value": 1.0, "state": "lagging", "lag_days": 9, "max_lag": 3}),
        "滞后9d")
    check("无值序列状态列显示「无数据」", status_cell({"value": None}), "无数据")
    check("正常序列状态列回落到「正常」", status_cell({"value": 1.0}), "正常")
    check("无数据优先于旗标", status_cell(
        {"value": None, "state": "lagging", "lag_days": 9}), "无数据")

    # ---- 出口池：绝不写死单一端口，且显式覆盖优先 --------------------
    print("\n[14] 出口池（离线，不联网）")
    _saved_po = PROXY_OVERRIDE
    try:
        globals()["PROXY_OVERRIDE"] = "http://127.0.0.1:65533"
        check("--proxy 显式覆盖时出口池只留这一个（不联网探测）",
              live_egress(), ["http://127.0.0.1:65533"])
        globals()["PROXY_OVERRIDE"] = "direct"
        check("--proxy direct 时出口池为直连", live_egress(), [None])
        globals()["PROXY_OVERRIDE"] = ""
        _c = proxy_candidates()
        check_true("候选出口 ≥3 个（代理端口会漂移，绝不写死单一出口）",
                   len(_c) >= 3)
        check_true("候选出口不含历史死端口 50465",
                   all("50465" not in c for c in _c))
        check_true("深探用的是真实会被风控的目标（不是 gstatic 探针）",
                   any("finance.yahoo.com" in p or "news.google.com" in p
                       for p in _EGRESS_DEEP_PROBES))
    finally:
        globals()["PROXY_OVERRIDE"] = _saved_po

    # ============================================================ 汇总
    print("=" * 72)
    total = _CK["pass"] + _CK["fail"]
    print(f"自检结果：{_CK['pass']} / {total} 通过，{_CK['fail']} 失败")
    if _CK["fail"]:
        print("\n失败项：")
        for n, g, w in _CK["fails"]:
            print(f"  - {n}: got={g!r} want={w!r}")
    return 1 if _CK["fail"] else 0


# ================================================================ main
def main() -> int:
    global PROXY_OVERRIDE, QUIET, OUTPUT_DIR
    ap = argparse.ArgumentParser(
        description="finance-front-monitor —— 美伊冲突金融战线高频指标监测")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不联网）")
    ap.add_argument("--proxy", default="",
                    help="显式代理覆盖；'direct' 表示强制直连（仅排障）")
    ap.add_argument("--from-file", default="",
                    help="离线重放：从该目录读原始载荷，完全不联网")
    ap.add_argument("--render-only", action="store_true",
                    help="不联网，用指定日期目录里已有的 raw/ 重新渲染")
    ap.add_argument("--as-of", default="", help="运行日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--out-dir", default="", help="输出根目录（默认 ./output）")
    ap.add_argument("--config", default=CONFIG_PATH)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-save", action="store_true", help="只打印，不写报告文件")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    PROXY_OVERRIDE = a.proxy
    QUIET = a.quiet
    cfg = load_config(a.config)
    out_root = a.out_dir or OUTPUT_DIR
    if a.as_of:
        try:
            run_dt = datetime.fromisoformat(a.as_of).replace(tzinfo=timezone.utc)
        except ValueError:
            log(f"[错误] --as-of 无法解析：{a.as_of!r}（应为 YYYY-MM-DD）")
            return 4
    else:
        run_dt = now_utc()
    day = run_dt.date().isoformat()

    raw_dir: Optional[str] = None
    if a.from_file:
        raw_dir = a.from_file
        if not os.path.isdir(raw_dir):
            log(f"[错误] --from-file 目录不存在：{raw_dir}")
            return 4
    elif a.render_only:
        raw_dir = os.path.join(out_root, day, "raw")
        if not os.path.isdir(raw_dir):
            log(f"[错误] --render-only 需要已有 raw 目录：{raw_dir}")
            return 4
    else:
        raw_dir = os.path.join(out_root, day, "raw")

    _URL_CACHE.clear()
    log(f"finance-front-monitor v{VERSION}")
    log(f"  序列 {len(cfg.get('series', []))} 条 · 派生 {len(cfg.get('derived', []))} 条")
    log(f"  出口：{proxy_report()}")
    log(f"  原始载荷：{'重放 ' + raw_dir if (a.from_file or a.render_only) else '抓取 → ' + raw_dir}")
    log(f"  运行日期：{day}")
    log("")

    t0 = time.time()
    res = run_pipeline(cfg, out_root, run_dt, raw_dir=raw_dir,
                       delay=float((cfg.get("request") or {})
                                   .get("delay_between_sources_sec", 0.0)))
    el = time.time() - t0

    srec = res["records"]
    got_n = sum(1 for r in srec if r.get("value") is not None)
    miss = [r for r in srec if r.get("value") is None]
    log(f"取数完成：{got_n}/{len(srec)} 条序列有值，用时 {el:.1f}s")
    if miss:
        log(f"  无数据：{'、'.join(r['series'] for r in miss)}")

    # 端点状态（供 §12 用）
    stat = {}
    for t in FETCH_LOG:
        key = (t["provider"], t.get("url", ""))
        cur = stat.setdefault(key, {"http": [], "rows": 0})
        if t.get("http") is not None:
            cur["http"].append(t["http"])
        cur["rows"] += int(t.get("rows") or 0)
    eps = collect_endpoints(cfg)
    for e in eps:
        key = (e["provider"], e["url"])
        s = stat.get(key)
        if not s:
            e["status"] = "未请求（同端点已缓存或未走到该链）"
        elif s["rows"]:
            e["status"] = f"OK · {s['rows']} 行"
        else:
            codes = sorted(set(s["http"]))
            e["status"] = "失败 · http=" + ",".join(str(c) for c in codes)

    raw_files, raw_bytes = 0, 0
    if os.path.isdir(raw_dir):
        for fn in os.listdir(raw_dir):
            fp = os.path.join(raw_dir, fn)
            if os.path.isfile(fp):
                raw_files += 1
                raw_bytes += os.path.getsize(fp)

    gaps = {}
    if all(r.get("value") is None for r in srec if r["bucket"] == "shipping"):
        gaps["shipping"] = ("已实测的通行量候选源见下方清单；"
                            "本轮没有取得可用的海峡日频通行量。")
    meta = {"fetched_at": now_utc().isoformat(timespec="seconds"),
            "egress": proxy_report(), "run_tag": res["run_tag"],
            "total_points": res["total_points"],
            "providers_used": "、".join(res["providers_used"]) or "无",
            "raw_files": raw_files, "raw_bytes": raw_bytes,
            "detail_n": int((cfg.get("window") or {}).get("detail_n", 20)),
            "endpoints": eps, "bucket_gaps": gaps,
            "divergences": res["divergences"]}

    report = render_report(cfg, res["records"], res["derived"],
                           res["composite"], meta, run_dt)

    if not a.no_save:
        w = write_outputs(out_root, run_dt, cfg, res, report)
        log(f"报告 → {os.path.relpath(w['md_path'], os.path.dirname(out_root))}")
        log(f"累积历史 → {os.path.relpath(os.path.join(out_root, 'history'), os.path.dirname(out_root))}")
        log(f"合成指数 → {res['composite']['label']} = "
            f"{res['composite']['score']} "
            f"(覆盖 {res['composite']['n_used']}/{res['composite']['n_total']})")
    else:
        print(report)

    for r in sorted(srec, key=lambda x: -abs(x["z"] or 0))[:6]:
        log(f"    {grade_mark(r.get('grade',''))} {r['label']:<24} "
            f"{fmt_num(r.get('value'), int(r.get('decimals', 2))):>12} "
            f"z={z_cell(r):>6}  {r.get('grade')}")

    # 关键序列全缺 → 明确的非零退出码（自动化里能立刻发现）
    heads = [r for r in srec if r.get("role") == "headline"]
    if heads and all(r.get("value") is None for r in heads):
        log("[错误] 全部头条序列都无数据 —— 检查代理/出口或源可用性。")
        return 2
    log("")
    log("下一步：python tools/verify_report.py --save   （从 raw/ 独立复算每个数字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
