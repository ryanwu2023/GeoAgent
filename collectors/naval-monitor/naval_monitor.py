#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
naval_monitor.py — 美军海军动态公开来源监测爬虫

用途
    定期抓取 USNI News(Fleet Tracker / 新闻) 与 navy.mil、defense.gov 公开发布的
    新闻与新闻稿，提取舰艇、海域、坐标，产出可供大模型直接消费的 Markdown 文件。

边界
    只抓取上述站点公开提供 RSS 的内容（RSS 本身即为自动化分发而设计）。
    不抓取 MarineTraffic / VesselFinder 等 AIS 平台的舰船实时位置，也不做实时告警。

依赖
    仅 Python 3.8+ 标准库，无需 pip install。

常用命令
    python naval_monitor.py                 # 抓取并生成日报
    python naval_monitor.py --direct        # 绕过系统代理直连（代理受限环境用）
    python naval_monitor.py --proxy http://127.0.0.1:7890   # 走自己的代理
    python naval_monitor.py --hours 24      # 只看最近 24 小时
    python naval_monitor.py --source usni-fleet-tracker
    python naval_monitor.py --from-file samples   # 解析本地 feed 文件，不联网
    python naval_monitor.py --selftest      # 离线自检，验证解析/过滤/坐标提取
    python naval_monitor.py --list-sources
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_DIR = BASE_DIR / "state"
OUTPUT_DIR = BASE_DIR / "output"
SEEN_PATH = STATE_DIR / "seen.json"

CST = timezone(timedelta(hours=8))


# --------------------------------------------------------------------------- #
# 一、地名坐标字典（经纬度用于把新闻里提到的海域/港口映射到地图坐标）
# --------------------------------------------------------------------------- #
GAZETTEER: Dict[str, Tuple[float, float, str]] = {
    # —— 中东 / 中央司令部辖区 ——
    "strait of hormuz": (26.57, 56.25, "海峡"),
    "hormuz": (26.57, 56.25, "海峡"),
    "persian gulf": (26.00, 52.00, "海域"),
    "arabian gulf": (26.00, 52.00, "海域"),
    "gulf of oman": (24.50, 58.50, "海域"),
    "arabian sea": (15.00, 65.00, "海域"),
    "red sea": (20.00, 38.00, "海域"),
    "bab el-mandeb": (12.58, 43.33, "海峡"),
    "bab al-mandab": (12.58, 43.33, "海峡"),
    "gulf of aden": (12.50, 47.00, "海域"),
    "suez canal": (30.00, 32.58, "运河"),
    "manama": (26.22, 50.58, "港口"),
    "bahrain": (26.07, 50.55, "基地"),
    "jebel ali": (25.01, 55.06, "港口"),
    "dubai": (25.20, 55.27, "港口"),
    "al udeid": (25.12, 51.32, "基地"),
    "doha": (25.29, 51.53, "港口"),
    "kuwait": (29.37, 47.98, "港口"),
    "djibouti": (11.59, 43.15, "基地"),
    "haifa": (32.82, 34.99, "港口"),
    "aqaba": (29.53, 35.00, "港口"),
    "muscat": (23.61, 58.59, "港口"),
    "bandar abbas": (27.18, 56.28, "港口"),
    # —— 地中海 / 欧洲 / 第六舰队 ——
    "mediterranean": (35.00, 18.00, "海域"),
    "eastern mediterranean": (34.50, 33.00, "海域"),
    "western mediterranean": (37.00, 5.00, "海域"),
    "aegean": (38.50, 25.50, "海域"),
    "black sea": (43.00, 34.00, "海域"),
    "baltic sea": (57.00, 19.00, "海域"),
    "north sea": (56.00, 3.00, "海域"),
    "norwegian sea": (67.00, 3.00, "海域"),
    "barents sea": (73.00, 40.00, "海域"),
    "gibraltar": (36.14, -5.35, "海峡"),
    "naples": (40.84, 14.25, "基地"),
    "rota": (36.62, -6.36, "基地"),
    "souda bay": (35.49, 24.09, "基地"),
    "sigonella": (37.40, 14.92, "基地"),
    "toulon": (43.12, 5.93, "港口"),
    "istanbul": (41.01, 28.98, "海峡"),
    "turkish straits": (40.72, 28.22, "海峡"),
    # —— 印太 / 第七舰队 ——
    "south china sea": (15.00, 115.00, "海域"),
    "east china sea": (29.00, 125.00, "海域"),
    "yellow sea": (35.00, 123.00, "海域"),
    "taiwan strait": (24.50, 119.50, "海峡"),
    "philippine sea": (20.00, 135.00, "海域"),
    "sea of japan": (40.00, 135.00, "海域"),
    "indo-pacific": (10.00, 130.00, "区域"),
    "western pacific": (18.00, 135.00, "区域"),
    "bay of bengal": (15.00, 88.00, "海域"),
    "coral sea": (-18.00, 155.00, "海域"),
    "timor sea": (-11.50, 127.00, "海域"),
    "luzon strait": (20.50, 121.50, "海峡"),
    "malacca": (1.80, 102.50, "海峡"),
    "singapore": (1.29, 103.85, "港口"),
    "changi": (1.36, 103.99, "基地"),
    "subic bay": (14.79, 120.28, "港口"),
    "manila": (14.60, 120.95, "港口"),
    "puerto princesa": (9.74, 118.74, "港口"),
    "yokosuka": (35.28, 139.67, "基地"),
    "sasebo": (33.16, 129.72, "基地"),
    "okinawa": (26.21, 127.68, "基地"),
    "iwakuni": (34.14, 132.24, "基地"),
    "guam": (13.44, 144.79, "基地"),
    "apra harbor": (13.44, 144.65, "港口"),
    "pearl harbor": (21.35, -157.95, "基地"),
    "busan": (35.10, 129.04, "港口"),
    "jeju": (33.50, 126.53, "港口"),
    "darwin": (-12.46, 130.84, "港口"),
    "fremantle": (-32.05, 115.74, "港口"),
    "perth": (-31.95, 115.86, "港口"),
    "colombo": (6.93, 79.85, "港口"),
    "mumbai": (18.94, 72.84, "港口"),
    "karachi": (24.86, 67.01, "港口"),
    "chittagong": (22.34, 91.83, "港口"),
    # —— 美洲 / 本土基地 ——
    "norfolk": (36.95, -76.30, "基地"),
    "san diego": (32.68, -117.18, "基地"),
    "mayport": (30.39, -81.42, "基地"),
    "everett": (47.98, -122.22, "基地"),
    "bremerton": (47.56, -122.63, "基地"),
    "kitsap": (47.72, -122.71, "基地"),
    "kings bay": (30.80, -81.51, "基地"),
    "groton": (41.39, -72.09, "基地"),
    # 「Newport News」必须比「Newport」更长地先注册，否则会被 NewPort(R.I.) 抢先匹配 ——
    # 实测把 CVN-73 错误投到了罗德岛。extract_places 按长度降序、长的优先保留。
    "newport news": (37.00, -76.43, "港口"),
    "newport": (41.51, -71.31, "港口"),
    "atlantic ocean": (30.00, -40.00, "海域"),
    "pacific ocean": (10.00, -150.00, "海域"),
    "southern pacific": (-10.00, -90.00, "海域"),
    "eastern pacific": (15.00, -115.00, "海域"),
    "western atlantic": (30.00, -65.00, "海域"),
    "indian ocean": (-10.00, 75.00, "海域"),
    "caribbean": (15.00, -75.00, "海域"),
    "central caribbean": (15.50, -76.00, "海域"),
    "ecuador": (-2.20, -80.90, "沿海国"),
    "middle east": (27.00, 48.00, "区域"),
    "panama canal": (9.08, -79.68, "运河"),
    "guantanamo": (19.90, -75.15, "基地"),
    "rio de janeiro": (-22.90, -43.17, "港口"),
    "valparaiso": (-33.03, -71.63, "港口"),
    # —— 战区 / 编号舰队 ——
    "centcom": (25.00, 55.00, "战区"),
    "fifth fleet": (26.20, 50.60, "舰队"),
    "5th fleet": (26.20, 50.60, "舰队"),
    "sixth fleet": (38.00, 15.00, "舰队"),
    "6th fleet": (38.00, 15.00, "舰队"),
    "seventh fleet": (22.00, 135.00, "舰队"),
    "7th fleet": (22.00, 135.00, "舰队"),
    "third fleet": (33.00, -140.00, "舰队"),
    "3rd fleet": (33.00, -140.00, "舰队"),
    "indopacom": (20.00, 140.00, "战区"),
    "eucom": (48.00, 15.00, "战区"),
    "southcom": (15.00, -75.00, "战区"),
}

# 航母 / 两栖舰 / 主要水面舰 补充映射：把舰名补全为「舰名 (舷号)」
SHIP_NAME_HULL: Dict[str, str] = {
    "nimitz": "CVN-68", "dwight d. eisenhower": "CVN-69", "eisenhower": "CVN-69",
    "carl vinson": "CVN-70", "theodore roosevelt": "CVN-71",
    "abraham lincoln": "CVN-72", "george washington": "CVN-73",
    "john c. stennis": "CVN-74", "harry s. truman": "CVN-75",
    "ronald reagan": "CVN-76", "george h.w. bush": "CVN-77",
    "gerald r. ford": "CVN-78", "john f. kennedy": "CVN-79",
    "wasp": "LHD-1", "essex": "LHD-2", "kearsarge": "LHD-3", "boxer": "LHD-4",
    "bataan": "LHD-5", "iwo jima": "LHD-7", "makin island": "LHD-8",
    "america": "LHA-6", "tripoli": "LHA-7",
}

# --------------------------------------------------------------------------- #
# 二、正则
# --------------------------------------------------------------------------- #
HULL_TYPES = r"(?:CVN|CV|DDG|CG|FFG|LHA|LHD|LPD|LSD|LCS|SSN|SSBN|SSGN|MCM|ESB|EPF)"
# 舰名由「首字母大写词」或「缩写」构成 —— 不含 ". "，天然不会跨句。
# 缩写形式要覆盖 "H.W."（无空格）与 "H. W."（有空格）两种写法；
# 词尾允许逗号，以覆盖 "Frank E. Petersen, Jr." 这类写法。
_NAME_TOKEN = (r"(?:(?:Jr|Sr|II|III|IV)\.|[A-Z]\.(?:\s?[A-Z]\.)*|[A-Z][A-Za-z\'\-]+),?")
_NAME = rf"{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,5}}"
# 形如 USS Gerald R. Ford (CVN-78)：以 USS 开头锚定，避免吞掉前一句
HULL_RE = re.compile(rf"\bUSS\s+({_NAME})\s*\(\s*({HULL_TYPES}-\d{{1,4}})\s*\)")
# 形如 Bataan (LHD-5)：无 USS 前缀时的兜底
BARE_NAME_HULL_RE = re.compile(rf"(?<![\w.])({_NAME})\s*\(\s*({HULL_TYPES}-\d{{1,4}})\s*\)")
BARE_HULL_RE = re.compile(rf"\b({HULL_TYPES})-(\d{{1,4}})\b")


def clean_ship_name(name: str) -> str:
    """规范舰名：压缩空白、去掉逗号前多余空格、去掉尾部逗号。"""
    s = norm_ws(name)
    s = re.sub(r"\s+,", ",", s)
    return s.strip(" ,")
# 十进制坐标：26.57N 56.25E / 26.57°N, 56.25°E
DD_COORD_RE = re.compile(
    r"(?P<lat>\d{1,3}(?:\.\d+)?)\s*°?\s*(?P<lat_h>[NSns])\s*[,/ ]{1,4}"
    r"(?P<lon>\d{1,3}(?:\.\d+)?)\s*°?\s*(?P<lon_h>[EWew])"
)
# 度分坐标：26°34'N 56°15'E
DMS_COORD_RE = re.compile(
    r"(?P<lat_d>\d{1,3})\s*°\s*(?P<lat_m>\d{1,2}(?:\.\d+)?)?\s*[′']?\s*(?P<lat_h>[NSns])\s*[,/ ]{1,4}"
    r"(?P<lon_d>\d{1,3})\s*°\s*(?P<lon_m>\d{1,2}(?:\.\d+)?)?\s*[′']?\s*(?P<lon_h>[EWew])"
)
TAG_RE = re.compile(r"<[^>]+>")

# 这些状态不算「抓取失败」，只是本次没有内容可取
NON_FAILURE_STATUS = ("ok", "ok-stale", "robots-disallow", "no-local-file", "disabled")


# --------------------------------------------------------------------------- #
# 三、工具函数
# --------------------------------------------------------------------------- #
def log(msg: str, verbose: bool = True) -> None:
    if verbose:
        print(msg, file=sys.stderr, flush=True)


def strip_html(text: str) -> str:
    return unescape(TAG_RE.sub(" ", text or "")).strip()


def norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


# --------------------------------------------------------------------------- #
# 四、HTTP 抓取
# --------------------------------------------------------------------------- #
_PROXY_STATE = {"checked": False, "url": None}
_EGRESS_CACHE = {"at": 0.0, "ok": []}
_EGRESS_OPENER: Dict[str, Any] = {}
_EGRESS_DEEP_PROBES = (
    "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d",
    "https://news.google.com/rss/search?q=probe&hl=en-US&gl=US&ceid=US:en",
)

def proxy_candidates():
    """候选出口：环境代理优先，常见本地端口次之，直连兜底。绝不写死单一端口。"""
    out = []
    env = urllib.request.getproxies()
    for k in ("https", "http"):
        u = env.get(k)
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)
    for port in (7897, 7890, 10809, 1080, 8080, 8888, 4780, 2080, 20171, 33210):
        u = "http://127.0.0.1:%d" % port
        if u not in out:
            out.append(u)
    return out + [None]

def _egress_opener(p):
    key = p or ""
    if key not in _EGRESS_OPENER:
        if p:
            _EGRESS_OPENER[key] = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": p, "https": p}))
        else:
            _EGRESS_OPENER[key] = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
    return _EGRESS_OPENER[key]

def _proxy_alive(p, timeout=5.0):
    """通用探活 —— 只负责筛掉**死端口**。

    ★ 为什么不能只靠它（2026-09-20 实测）：57444 出口对 gstatic204 与
      cloudflare trace 两个探针都返回正常，但同一时刻
      Yahoo chart → 403（JS 挑战页）、Google News → 超时。
      它是「半死」出口：连得上，但被目标站风控。
    """
    for probe in ("http://www.gstatic.com/generate_204",
                  "https://www.cloudflare.com/cdn-cgi/trace"):
        try:
            if _egress_opener(p).open(probe, timeout=timeout).status in (200, 204):
                return True
        except Exception:
            continue
    return False

def _proxy_deep_score(p, timeout=6):
    """真实目标深探得分（0–2）：只对**真的会给客户端发挑战页**的站探。"""
    good = 0
    for probe in _EGRESS_DEEP_PROBES:
        try:
            if _egress_opener(p).open(probe, timeout=timeout).status == 200:
                good += 1
        except Exception:
            pass
    return good

def live_proxies():
    """存活出口列表，按「真实目标可用性」降序（缓存 10 分钟）。"""
    now = time.time()
    if _EGRESS_CACHE["ok"] and now - _EGRESS_CACHE["at"] < 600:
        return list(_EGRESS_CACHE["ok"])
    scored = []
    for u in proxy_candidates():
        if not _proxy_alive(u):
            continue
        scored.append((-_proxy_deep_score(u), u))
    scored.sort(key=lambda x: (x[0], x[1] is None, x[1] or ""))
    ok = [u for _, u in scored] or [None]
    _EGRESS_CACHE.update({"at": now, "ok": ok})
    return list(ok)


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

def demote_proxy(p):
    """某出口被目标站拒绝 / 隧道失败时立即摘除（缓存到期后自动重新探测恢复）。"""
    if p in _EGRESS_CACHE.get("ok", []):
        _EGRESS_CACHE["ok"].remove(p)

def _resolve_auto_proxy():
    """向后兼容旧调用：返回**首选**出口。"""
    return live_proxies()[0]


class Fetcher:
    def __init__(self, cfg: Dict[str, Any], direct: bool = False,
                 proxy: Optional[str] = None):
        self.timeout = cfg.get("timeout_sec", 25)
        self.retries = cfg.get("retries", 3)
        self.ua = cfg.get("user_agent", "Mozilla/5.0")
        self.direct = direct
        self.proxy = proxy
        self.pool: List[Optional[str]] = [proxy] if proxy else []
        if not direct and not proxy:
            # 保留**整条**候选链，而不是只挑一个：挑中的那个可能正好是半死的
            self.pool = list(live_proxies())
            if self.pool and self.pool[0]:
                self.proxy = self.pool[0]
        self.robots_cache: Dict[str, List[str]] = {}
        if direct or proxy:
            for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                        "all_proxy", "ALL_PROXY"):
                os.environ.pop(key, None)

    def _opener(self) -> urllib.request.OpenerDirector:
        if self.proxy:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        if self.direct:
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener()

    def _headers(self) -> Dict[str, str]:
        """构造请求头。

        只带 User-Agent 会被 navy.mil / defense.gov 的 Akamai 直接 403，
        必须补齐 Accept-Language / Sec-Fetch-* / Upgrade-Insecure-Requests
        这套现代浏览器的常规头，才会返回 200（2026-09-17 实测）。
        """
        return {
            "User-Agent": self.ua,
            "Accept": ("application/rss+xml, application/atom+xml, application/xml, "
                       "text/xml, text/html;q=0.9, */*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Connection": "keep-alive",
        }

    @staticmethod
    def _decompress(raw: bytes, enc: str) -> bytes:
        """按 Content-Encoding 解压。gzip 与 deflate 都要处理 ——
        声明支持 deflate 却不解码，会直接把 XML 变成二进制垃圾。"""
        enc = (enc or "").lower()
        if "br" in enc:                       # 未声明 br，防御性兜底
            return raw
        try:
            if "gzip" in enc or raw[:2] == b"\x1f\x8b":
                return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            if "deflate" in enc:
                try:
                    return zlib.decompress(raw)          # 带 zlib 头
                except zlib.error:
                    return zlib.decompress(raw, -zlib.MAX_WBITS)  # 裸 deflate
        except Exception:                     # noqa: BLE001
            return raw
        return raw

    def fetch(self, url: str, verbose: bool = True) -> Optional[bytes]:
        last_err = ""
        pool = self.pool or [self.proxy]
        for ei, egress in enumerate(pool):
            for attempt in range(1, self.retries + 1):
                try:
                    req = urllib.request.Request(url, headers=self._headers())
                    op = (_egress_opener(egress) if not self.direct
                          else urllib.request.build_opener(
                              urllib.request.ProxyHandler({})))
                    with op.open(req, timeout=self.timeout) as resp:
                        raw = resp.read()
                        return self._decompress(
                            raw, resp.headers.get("Content-Encoding"))
                except urllib.error.HTTPError as e:
                    last_err = f"HTTP {e.code}"
                    # 410（永久移除）重试没有意义，直接放弃；
                    # 403 在很多站上是「这个出口 IP 被风控」——换出口再试；
                    # 404 必须重试 —— 实测 defense.gov 的 RSS 端点在 Akamai 各边缘节点
                    # 之间状态不一致，同一 URL 会出现「5 次里 4 次 404、1 次 200」。
                    if e.code == 410:
                        break
                    if e.code == 403 and ei < len(pool) - 1:
                        demote_proxy(egress)
                        break
                except Exception as e:            # noqa: BLE001
                    last_err = f"{type(e).__name__}: {str(e)[:120]}"
                    if egress_rotatable(last_err):
                        demote_proxy(egress)
                        break
                if attempt < self.retries:
                    time.sleep(1.5 * attempt)
        log(f"    [!] 抓取失败 ({last_err})  {url}", verbose)
        return None

    def robots_allows(self, url: str) -> bool:
        p = urllib.parse.urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self.robots_cache:
            rules: List[str] = []
            data = self.fetch(f"{root}/robots.txt", verbose=False)
            if data:
                section = None
                for line in data.decode("utf-8", "ignore").splitlines():
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    if line.lower().startswith("user-agent:"):
                        section = line.split(":", 1)[1].strip()
                    elif section == "*" and line.lower().startswith("disallow:"):
                        path = line.split(":", 1)[1].strip()
                        if path:
                            rules.append(path)
            self.robots_cache[root] = rules
        for rule in self.robots_cache[root]:
            if rule != "/" and p.path.startswith(rule.rstrip("*")):
                return False
            if rule == "/" and p.path == "/":
                return False
        return True


# --------------------------------------------------------------------------- #
# 五、Feed 解析（RSS 2.0 / Atom）
# --------------------------------------------------------------------------- #
def parse_feed(raw: bytes) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # 容错：剥掉非法控制字符后重试
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)
        root = ET.fromstring(cleaned)

    items: List[Dict[str, Any]] = []
    nodes = [n for n in root.iter() if local_name(n.tag) in ("item", "entry")]

    for node in nodes:
        rec: Dict[str, Any] = {"title": "", "link": "", "published_raw": "",
                               "summary": "", "categories": []}
        for child in node:
            name = local_name(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                rec["title"] = norm_ws(strip_html(text))
            elif name == "link":
                href = child.attrib.get("href")
                rel = child.attrib.get("rel", "alternate")
                if href and rel == "alternate" and not rec["link"]:
                    rec["link"] = href
                elif text and not rec["link"] and not href:
                    rec["link"] = text
            elif name in ("pubdate", "published", "updated", "date"):
                if text and not rec["published_raw"]:
                    rec["published_raw"] = text
            elif name in ("description", "summary", "content", "encoded"):
                if text and len(text) > len(rec["summary"]):
                    rec["summary"] = norm_ws(strip_html(text))
            elif name == "category":
                term = child.attrib.get("term") or text
                if term:
                    rec["categories"].append(norm_ws(term))
        if rec["title"] and rec["link"]:
            items.append(rec)
    return items


def parse_time(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:                          # noqa: BLE001
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:                          # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# 六、信息抽取
# --------------------------------------------------------------------------- #
def extract_ships(text: str, prefixes: Iterable[str]) -> List[str]:
    found: List[str] = []
    seen = set()
    claimed: List[Tuple[int, int]] = []          # 已被 USS 版本占用的区间

    def add(label: str) -> None:
        # 以舷号为唯一键：同一艘舰的不同写法（"H.W." vs "H. W."）只保留首次出现的完整写法
        hm = BARE_HULL_RE.search(label)
        key = hm.group(0).upper() if hm else label.upper()
        if key not in seen:
            seen.add(key)
            found.append(label)
        else:
            # 已有同名但本次写法更长（更完整）时，替换掉简略写法
            for i, existing in enumerate(found):
                if BARE_HULL_RE.search(existing) and \
                        BARE_HULL_RE.search(existing).group(0).upper() == key and \
                        len(label) > len(existing):
                    found[i] = label
                    break

    # 1) USS <舰名> (<舷号>) —— 最可靠，先跑
    for m in HULL_RE.finditer(text):
        claimed.append(m.span())
        add(f"USS {clean_ship_name(m.group(1))} ({m.group(2).upper()})")

    # 2) <舰名> (<舷号>) 兜底，跳过与上面重叠的区间
    for m in BARE_NAME_HULL_RE.finditer(text):
        s, e = m.span()
        if any(not (e <= cs or s >= ce) for cs, ce in claimed):
            continue
        add(f"{clean_ship_name(m.group(1))} ({m.group(2).upper()})")
        claimed.append((s, e))

    # 3) 裸舷号（如 "SSN-774 class"、"two DDG-51s"）
    allowed = {p.upper() for p in prefixes}
    for m in BARE_HULL_RE.finditer(text):
        pref, num = m.group(1).upper(), m.group(2)
        if pref not in allowed:
            continue
        hull = f"{pref}-{num}"
        if any(hull in f.upper() for f in found):
            continue
        add(hull)

    # 4) 已知舰名映射补全（文本只写舰名没写舷号时）
    #
    # 必须用词边界 + 排除舰级后缀，否则会产生系统性假阳性（2026-09-17 真实数据暴露）：
    #   "Nimitz-class aircraft carrier USS Abraham Lincoln (CVN-72)"
    #       → 朴素子串匹配会把 "Nimitz" 当成 USS Nimitz，凭空多出一艘 CVN-68，
    #         且因该句式在 Fleet Tracker 里高频出现，CVN-68 会被错误挂到南海/东太平洋/
    #         西大西洋等多个互不相干的区域。本次 32 条记录里有 6 条中招。
    #   "American-owned firm" / "American and Filipino planners"
    #       → 朴素子串匹配会把 "America" 当成 USS America (LHA-6)。
    low = text.lower()
    for name, hull in SHIP_NAME_HULL.items():
        pat = rf"(?<![a-z]){re.escape(name)}(?![a-z])(?!\s*-\s*class)"
        if not re.search(pat, low):
            continue
        if any(hull in f.upper() for f in found):
            continue
        add(f"USS {name.title()} ({hull})")
    return found


# 「舰队 / 战区」是编制或辖区概念，不是地理位置。把「在第 5 舰队辖区」投点到巴林，
# 等于把「隶属某舰队」误读成「此刻停在巴林」。这类条目保留在 places 中供阅读，
# 但不进坐标表。
COORDLESS_PLACE_KINDS = ("舰队", "战区")


# 形如 "San Diego (LPD-22)" 的写法里，"San Diego" 是**舰名**不是地名。
# 地名后面紧跟舷号括号时，直接判定为舰名，不当地点。
_HULL_AFTER_RE = re.compile(r"\s*\(\s*[A-Za-z]{1,6}-\d{1,4}\s*\)")


def extract_places(text: str, exclude_phrases: Iterable[str] = (),
                   ) -> List[Tuple[str, Tuple[float, float, str]]]:
    low = text.lower()

    # ① 专有名词遮蔽：像 "Newport Manual"（一本海战法手册）里含有地名 "Newport"，
    #    不分青红皂白就会把某艘舰投到罗德岛。配置里列出这类词组，先屏蔽再匹配。
    #    用等长占位符替换，保住后面所有偏移量不变。
    for ph in exclude_phrases:
        ph = (ph or "").strip().lower()
        if ph and ph in low:
            low = low.replace(ph, "\u0000" * len(ph))

    # ② 逐 key 扫描并记录真实位置（而不是简单 in 判断），以便后续按 span 去重
    #
    # 必须加字母/数字边界，否则短地名会嵌进普通单词里：
    #   "Rota"（西班牙罗塔港）会命中 "Rotational" —— Fleet Tracker 的
    #   "(39 FDNF, 64 Rotational)" 因此被投点到西班牙（2026-09-17 真实数据暴露）。
    # 用 [a-z0-9] 而不是 \b，是因为 key 里可能含空格与连字符，
    # 且要容忍 "Guam's" 这类后接标点的写法。
    hits: List[Tuple[str, int, int]] = []
    for k in GAZETTEER:
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", low):
            if _HULL_AFTER_RE.match(low, m.end()):
                break                          # 这是舰名，整条 key 作废
            hits.append((k, m.start(), m.end()))
            break

    # ③ 长名优先：占用了字符区间的长名，会压制与之重叠的短名。
    #    例："Newport News" 压住 "Newport"，"Strait of Hormuz" 压住 "Hormuz"。
    hits.sort(key=lambda x: (-(x[2] - x[1]), x[1]))
    out: List[Tuple[str, Tuple[float, float, str]]] = []
    kept_spans: List[Tuple[int, int]] = []
    for k, s, e in hits:
        if any(not (e <= cs or s >= ce) for cs, ce in kept_spans):
            continue
        kept_spans.append((s, e))
        out.append((display_name(k.title()), GAZETTEER[k]))
    return out


def extract_coords(text: str) -> List[Tuple[float, float, str]]:
    coords: List[Tuple[float, float, str]] = []

    for m in DD_COORD_RE.finditer(text):
        lat = float(m.group("lat"))
        lon = float(m.group("lon"))
        if m.group("lat_h").upper() == "S":
            lat = -lat
        if m.group("lon_h").upper() == "W":
            lon = -lon
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            coords.append((round(lat, 4), round(lon, 4), "文中显式坐标"))

    for m in DMS_COORD_RE.finditer(text):
        lat = float(m.group("lat_d")) + float(m.group("lat_m") or 0) / 60
        lon = float(m.group("lon_d")) + float(m.group("lon_m") or 0) / 60
        if m.group("lat_h").upper() == "S":
            lat = -lat
        if m.group("lon_h").upper() == "W":
            lon = -lon
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            coords.append((round(lat, 4), round(lon, 4), "文中显式坐标(DMS)"))

    dedup: List[Tuple[float, float, str]] = []
    seen_pt = set()
    for c in coords:
        if (c[0], c[1]) in seen_pt:            # 同一坐标点只保留首次出现的来源标注
            continue
        seen_pt.add((c[0], c[1]))
        dedup.append(c)
    return dedup


def relevance_score(text: str, keywords: List[str]) -> Tuple[int, List[str]]:
    low = text.lower()
    matched = [k for k in keywords if k.lower() in low]
    return len(matched), matched


# --------------------------------------------------------------------------- #
# 六之二、句级局部关联（把舰艇与同句内出现的坐标/海域绑定，避免张冠李戴）
# --------------------------------------------------------------------------- #
_ABBR = {
    "U.S.": "\u0001", "u.s.": "\u0002", "Sept.": "\u0003", "Aug.": "\u0004",
    "Jan.": "\u0005", "Feb.": "\u0006", "Mar.": "\u0007", "Apr.": "\u0008",
    "Jun.": "\u0009", "Jul.": "\u000a", "Oct.": "\u000b", "Nov.": "\u000c",
    "Dec.": "\u000d", "Gen.": "\u000e", "Adm.": "\u000f", "Capt.": "\u0010",
    "Lt.": "\u0011", "Cmdr.": "\u0012", "Mr.": "\u0013", "Dr.": "\u0014",
    "approx.": "\u0015", "e.g.": "\u0016", "i.e.": "\u0017", "No.": "\u0018",
    "vs.": "\u0019", "St.": "\u001a", "Jr.": "\u001b", "Sr.": "\u001c",
}
_ABBR_BACK = {v: k for k, v in _ABBR.items()}
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_SPLIT = re.compile(r";\s+")
# 母港表述：命中的地点是「母港/驻地」，不是当前位置，必须区分开
HOMEPORT_RE = re.compile(
    r"(?:home\s*ported|home\s*port|based|forward[\-\s]deployed)\s+(?:in|at)\s+([^,;.]{1,60})",
    re.I,
)
# 中间名缩写（Gerald R. Ford / Harry S. Truman）：必须保护，否则会在 "R. " 处错误断句。
# 但坐标写法 "135°E. USS ..." 里的 "E." 不能保护 —— 用「前面不是数字/度符号」来区分。
_INITIAL_RE = re.compile(r"(?<![\d\u00b0])(?<![A-Za-z])([A-Z])\.(?=\s+[A-Z])")
_INITIAL_PH = "\ue000"


def split_sentences(text: str) -> List[str]:
    protected = text
    for k, v in _ABBR.items():
        protected = protected.replace(k, v)
    protected = _INITIAL_RE.sub(lambda m: m.group(1) + _INITIAL_PH, protected)
    parts = [p for p in _SENT_SPLIT.split(protected) if p.strip()]
    out = []
    for p in parts:
        for k, v in _ABBR_BACK.items():
            p = p.replace(k, v)
        out.append(norm_ws(p.replace(_INITIAL_PH, ".")))
    return out


def extract_localized(text: str, prefixes: Iterable[str],
                      exclude_phrases: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """返回 [{sentence, ships, coords, places, points, inherited, ambiguous}]。

    做法：
      1. 先切句，再按分号切子句 —— 子句通常各自描述一艘舰的位置；
      2. 子句内有位置但没写舰名时，继承**同一句内前一个子句**的舰名（承前指代），
         标记 inherited=True；
      3. 同句中多舰 + 多位置时标记 ambiguous=True，提示配对关系需人工/模型复核。
    """
    out: List[Dict[str, Any]] = []
    for sent in split_sentences(text):
        clauses = [c for c in _CLAUSE_SPLIT.split(sent) if c.strip()]
        prev_ships: List[str] = []

        for cl in clauses:
            ships = extract_ships(cl, prefixes)
            coords = extract_coords(cl)
            places = extract_places(cl, exclude_phrases)
            inherited = False

            if not ships and (coords or places) and prev_ships:
                ships = prev_ships
                inherited = True

            if ships and (coords or places):
                homeport_spans = [m.group(1).lower() for m in HOMEPORT_RE.finditer(cl)]
                points: List[Dict[str, Any]] = []
                seen_pt = set()
                for c in coords:                   # 原文显式坐标优先
                    pt = (c[0], c[1])
                    if pt not in seen_pt:
                        seen_pt.add(pt)
                        points.append({"lat": c[0], "lon": c[1], "label": c[2],
                                       "kind": "explicit", "homeport": False})
                for n, m in places:
                    if m[2] in COORDLESS_PLACE_KINDS:
                        continue                   # 编制/辖区概念，不投点
                    pt = (m[0], m[1])
                    if pt not in seen_pt:
                        seen_pt.add(pt)
                        points.append({"lat": m[0], "lon": m[1], "label": n, "kind": m[2],
                                       "homeport": any(n.lower() in span or span in n.lower()
                                                       for span in homeport_spans)})

                position_points = [p for p in points if not p["homeport"]]
                out.append({
                    "sentence": norm_ws(cl)[:400],
                    "full_sentence": sent[:600],
                    "ships": ships,
                    "coords": [{"lat": c[0], "lon": c[1], "origin": c[2]} for c in coords],
                    "places": [{"name": n, "lat": m[0], "lon": m[1], "kind": m[2]} for n, m in places],
                    "points": points,
                    "inherited": inherited,
                    # 多舰 + 多个「位置点」时无法仅凭规则确定配对
                    "ambiguous": len(ships) > 1 and len(position_points) > 1,
                })

            if extract_ships(cl, prefixes):        # 只用显式写了舰名的子句更新指代栈
                prev_ships = ships
    return out


_DISPLAY_FIX = [
    (re.compile(r"\b(\d+)(St|Nd|Rd|Th)\b"), lambda m: f"{m.group(1)}{m.group(2).lower()}"),
    (re.compile(r"\bBab El-Mandeb\b"), lambda m: "Bab el-Mandeb"),
]
for _small in ("Of", "The", "And", "In", "On", "At", "To", "For", "El", "De", "La"):
    _DISPLAY_FIX.append((re.compile(rf"\b{_small}\b"), (lambda w: (lambda m: w))(_small.lower())))


def display_name(name: str) -> str:
    out = name
    for pat, rep in _DISPLAY_FIX:
        out = pat.sub(rep, out)
    return out


# --------------------------------------------------------------------------- #
# 七、状态（去重）
# --------------------------------------------------------------------------- #
def load_seen() -> Dict[str, str]:
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text("utf-8"))
        except Exception:                      # noqa: BLE001
            return {}
    return {}


def save_seen(seen: Dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # 只保留 180 天内的记录，避免无限膨胀
    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
    trimmed = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_PATH.write_text(json.dumps(trimmed, ensure_ascii=False, indent=1), "utf-8")


def link_id(link: str) -> str:
    return hashlib.sha1(link.strip().encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 八、主流程
# --------------------------------------------------------------------------- #
def collect(cfg: Dict[str, Any], fetcher: Fetcher, source_ids: Optional[List[str]],
            hours: int, verbose: bool,
            feed_dir: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    prefixes = cfg.get("ship_hull_prefixes", [])
    keywords = cfg.get("relevance_keywords", [])
    excludes = [k.lower() for k in cfg.get("exclude_keywords", [])]
    place_excl = cfg.get("place_exclude_phrases", [])
    tracker_titles = [t.lower() for t in
                      cfg.get("tracker_title_match", ["fleet and marine tracker"])]
    stale_days = cfg.get("request", {}).get("stale_after_days", 14)
    delay = cfg.get("request", {}).get("delay_between_sources_sec", 2.0)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    records: List[Dict[str, Any]] = []
    report: List[Dict[str, str]] = []

    for src in cfg.get("sources", []):
        if source_ids and src["id"] not in source_ids:
            continue

        # 配置里 enabled=false 的源直接跳过（用于临时停用已知失效的 feed）
        if src.get("enabled", True) is False:
            report.append({"id": src["id"], "name": src["name"], "status": "disabled",
                           "count": "0", "newest": "-"})
            log(f"[*] {src['name']}  [-] 已配置 enabled=false，跳过", verbose)
            continue

        raw: Optional[bytes] = None

        # —— 本地文件模式：<feed_dir>/<source_id>.xml 或 .rss ——
        if feed_dir is not None:
            candidate = None
            for ext in (".xml", ".rss", ".atom"):
                p = feed_dir / f"{src['id']}{ext}"
                if p.exists():
                    candidate = p
                    break
            if candidate is None:
                report.append({"id": src["id"], "name": src["name"], "status": "no-local-file", "count": "0"})
                log(f"[*] {src['name']}  <- 本地文件缺失 ({feed_dir / (src['id'] + '.xml')})", verbose)
                continue
            log(f"[*] {src['name']}  <- {candidate}", verbose)
            raw = candidate.read_bytes()
        else:
            log(f"[*] {src['name']}  <{src['url']}>", verbose)
            if cfg.get("request", {}).get("respect_robots", True) and not fetcher.robots_allows(src["url"]):
                report.append({"id": src["id"], "name": src["name"], "status": "robots-disallow", "count": "0"})
                log("    [-] robots.txt 不允许，跳过", verbose)
                continue
            raw = fetcher.fetch(src["url"], verbose)
            # 主 URL 失败时依次尝试备用 URL。
            # 用例：defense.gov 的 RSS 端点经 Akamai 各边缘节点状态不一致，
            # 会间歇性 404；war.gov 是它的完全镜像，命中率翻倍。
            if raw is None:
                for alt in src.get("url_alternates", []):
                    log(f"    [~] 主 URL 失败，尝试备用: {alt}", verbose)
                    raw = fetcher.fetch(alt, verbose)
                    if raw is not None:
                        break
            if raw is not None and raw[:1] not in (b"<", b"\xef", b"\n", b" "):
                report.append({"id": src["id"], "name": src["name"], "status": "not-xml", "count": "0"})
                log("    [!] 返回内容不是 XML（可能被风控页面替换），跳过", verbose)
                continue

        if raw is None:
            report.append({"id": src["id"], "name": src["name"], "status": "failed", "count": "0"})
            continue

        try:
            items = parse_feed(raw)
        except Exception as e:                 # noqa: BLE001
            report.append({"id": src["id"], "name": src["name"],
                           "status": f"parse-error:{type(e).__name__}", "count": "0"})
            log(f"    [!] 解析失败: {e}", verbose)
            continue

        # —— 停更检测：源的"最新一条"比阈值还旧，说明源站停更或 CDN 缓存异常 ——
        dts_all = [d for d in (parse_time(i["published_raw"]) for i in items) if d]
        newest = max(dts_all) if dts_all else None
        age_days = (datetime.now(timezone.utc) - newest).days if newest else None
        stale = bool(newest is not None and age_days is not None and age_days > stale_days)
        if stale:
            log(f"    [!] 该源最新条目为 {newest.strftime('%Y-%m-%d')}（{age_days} 天前），"
                f"超过 {stale_days} 天阈值 —— 疑似源站停更或缓存异常", verbose)

        kept = 0
        for it in items:
            dt = parse_time(it["published_raw"])
            if dt and dt < cutoff:
                continue
            blob = " ".join([it["title"], it["summary"], " ".join(it["categories"])])
            if any(x in blob.lower() for x in excludes):
                continue

            score, matched = relevance_score(blob, keywords)
            ships = extract_ships(blob, prefixes)
            cats = it["categories"]

            # 相关性判定：命中关键词 / 抓到舷号 / 是 Fleet Tracker（按标题识别，不依赖固定源 id）
            is_tracker = src.get("tracker_source", False) or \
                any(t in it["title"].lower() for t in tracker_titles)
            if not (is_tracker or score >= 2 or ships):
                continue

            places = extract_places(blob, place_excl)
            coords = extract_coords(blob)
            localized = extract_localized(blob, prefixes, place_excl)

            records.append({
                "id": link_id(it["link"]),
                "source_id": src["id"],
                "source_name": src["name"],
                "source_priority": src.get("priority", "normal"),
                "source_tags": src.get("tags", []),
                "is_tracker": is_tracker,
                "title": it["title"],
                "link": it["link"],
                "published": dt.astimezone(CST).isoformat(timespec="seconds") if dt else "",
                "published_utc": dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else "",
                "summary": it["summary"][:1200],
                "categories": cats,
                "ships": ships,
                "places": [{"name": n, "lat": m[0], "lon": m[1], "kind": m[2]} for n, m in places],
                "coords": [{"lat": c[0], "lon": c[1], "origin": c[2]} for c in coords],
                "localized": localized,
                "relevance_score": score,
                "matched_keywords": matched[:12],
            })
            kept += 1

        report.append({"id": src["id"], "name": src["name"],
                       "status": "ok-stale" if stale else "ok",
                       "count": str(kept),
                       "newest": newest.strftime("%Y-%m-%d") if newest else "-"})
        log(f"    [ok] 抓到 {len(items)} 条，命中 {kept} 条"
            + (f"  ⚠️ 源疑似停更（最新 {newest.strftime('%Y-%m-%d')}）" if stale else ""), verbose)
        time.sleep(delay)

    return records, report


def dedupe(records: List[Dict[str, Any]], seen: Dict[str, str], today: str,
           use_state: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    new, old = [], []
    for r in records:
        if use_state and r["id"] in seen:
            old.append(r)
        else:
            new.append(r)
            if use_state:
                seen.setdefault(r["id"], today)
    return new, old


def sort_key(r: Dict[str, Any]) -> Tuple[int, int, str]:
    pr = {"high": 0, "normal": 1, "low": 2}.get(r.get("source_priority", "normal"), 1)
    return (pr, -r.get("relevance_score", 0), r.get("published", ""))


def humanize_time(iso: str) -> str:
    if not iso:
        return "时间未知"
    return iso.replace("T", " ")[:16]


def loc_basis(loc: Dict[str, Any]) -> str:
    """给出一行关联依据说明，明确标注不确定性。"""
    if loc.get("ambiguous"):
        return "同句关联 ⚠️多舰多位置，配对需复核"
    if loc.get("inherited"):
        return "同句承前指代（子句未重复舰名）"
    return "直接同句关联"


def render_digest(records_new: List[Dict[str, Any]], records_all: List[Dict[str, Any]],
                  report: List[Dict[str, str]], run_dt: datetime, hours: int,
                  note: str = "", fresh: int = -1) -> str:
    date_str = run_dt.strftime("%Y-%m-%d")
    def _is_tracker(r: Dict[str, Any]) -> bool:
        return bool(r.get("is_tracker")) or r["source_id"] == "usni-fleet-tracker"

    tracker = [r for r in records_new if _is_tracker(r)]
    others = [r for r in records_new if not _is_tracker(r)]
    others.sort(key=sort_key)

    L: List[str] = []
    L.append("---")
    L.append("title: 美军海军动态公开来源监测日报")
    L.append(f"date: {date_str}")
    L.append(f"generated_at: {run_dt.isoformat(timespec='seconds')}")
    L.append(f"lookback_hours: {hours}")
    L.append(f"sources_ok: {sum(1 for s in report if s['status'] == 'ok')}")
    L.append(f"sources_failed: {sum(1 for s in report if s['status'] not in NON_FAILURE_STATUS)}")
    L.append(f"records_new_this_run: {fresh if fresh >= 0 else len(records_new)}")
    L.append(f"records_in_snapshot: {len(records_new)}")
    L.append(f"records_total_in_window: {len(records_all)}")
    L.append("---")
    L.append("")
    L.append(f"# 美军海军动态监测日报 — {date_str}{note}")
    L.append("")
    L.append("> **数据来源**：USNI News、navy.mil、defense.gov **公开发布**的新闻与新闻稿（RSS 分发）。")
    L.append("> **性质说明**：全部内容为公开信息，不含任何 AIS 实时舰位或非公开数据；")
    L.append("> 坐标有两个来源：①原文显式坐标；②文中提及海域/港口对应到地名坐标字典的近似中心点。")
    L.append("> **使用方式**：本文档面向大模型消费，结构化数据见文末 JSON 区块，可直接解析入图。")
    L.append(f"> **快照语义**：本文件是**滚动窗口快照** —— 收录回溯 {hours}h 内抓到的**全部**记录，")
    L.append("> 而不只是「当天新增」。头部 `records_new_this_run` 标明其中本次新出现的条数。")
    L.append("> 掉出时间窗的历史不会丢：跨天累积的完整档案见 `output/ALL-records.jsonl`。")
    L.append("")

    # 一、Fleet Tracker
    L.append("## 一、部署与位置（Fleet Tracker）")
    L.append("")
    if tracker:
        for i, r in enumerate(tracker, 1):
            L.append(f"### {i}. {r['title']}")
            L.append("")
            L.append(f"- **发布**：{humanize_time(r['published'])}")
            L.append(f"- **链接**：{r['link']}")
            if r["ships"]:
                L.append(f"- **舰艇**：{'、'.join(r['ships'])}")
            if r["places"]:
                L.append(f"- **海域**：{'、'.join(p['name'] for p in r['places'])}")
            L.append(f"- **摘要**：{r['summary'][:600] or '（无摘要，见原文）'}")
            L.append("")
    else:
        L.append("_本次抓取窗口内无 Fleet Tracker 更新（该栏目通常在每周一发布）。_")
        L.append("")

    # 二、相关新闻
    L.append("## 二、相关新闻与官方通报")
    L.append("")
    if others:
        for i, r in enumerate(others, 1):
            tail = " 🔴" if r.get("source_priority") == "high" else ""
            L.append(f"### {i}. {r['title']}{tail}")
            L.append("")
            L.append(f"- **来源**：{r['source_name']}")
            L.append(f"- **发布**：{humanize_time(r['published'])}")
            L.append(f"- **链接**：{r['link']}")
            if r["ships"]:
                L.append(f"- **舰艇**：{'、'.join(r['ships'])}")
            if r["places"]:
                L.append(f"- **海域**：{'、'.join(p['name'] for p in r['places'])}")
            if r["coords"] or r["places"]:
                cs = [f"{c['lat']},{c['lon']}" for c in r["coords"]] or \
                     [f"{p['lat']},{p['lon']}" for p in r["places"]]
                L.append(f"- **坐标**：{' | '.join(dict.fromkeys(cs))}")
            L.append(f"- **摘要**：{r['summary'][:600] or '（无摘要，见原文）'}")
            L.append("")
    else:
        L.append("_本次抓取窗口内无新的相关新闻。_")
        L.append("")

    # 三、坐标汇总
    L.append("## 三、坐标汇总（可直接投图）")
    L.append("")
    L.append("| 舰艇 | 纬度 | 经度 | 位置/类型 | 关联依据 |")
    L.append("|---|---|---|---|---|")
    pos_rows: List[Tuple[str, float, float, str, str]] = []
    home_rows: List[Tuple[str, float, float, str, str]] = []
    for r in records_new:
        for loc in r.get("localized", []):
            ships_txt = "、".join(loc["ships"])
            basis = loc_basis(loc)
            for pt in loc.get("points", []):
                kind = "原文显式坐标" if pt["kind"] == "explicit" else f"{pt['label']}（{pt['kind']}·字典近似）"
                target = home_rows if pt.get("homeport") else pos_rows
                target.append((ships_txt, pt["lat"], pt["lon"], kind, basis))
    pos_rows = list(dict.fromkeys(pos_rows))
    home_rows = list(dict.fromkeys(home_rows))

    if pos_rows:
        for ships, lat, lon, kind, basis in pos_rows:
            L.append(f"| {ships} | {lat} | {lon} | {kind} | {basis} |")
    else:
        L.append("| _本窗口未提取到「舰艇+位置」同句关联_ | - | - | - | - |")
    L.append("")
    L.append("> 说明：本表只收录**同一句子/子句内**同时出现舰艇与位置信息的条目，避免把整篇文章的舰艇")
    L.append("> 错误挂到某个坐标上。坐标为原文显式坐标，或用文中海域/港口查地名字典得到的近似中心点，")
    L.append("> **非精确舰位**。可直接粘贴到任何 GIS / 地图工具中投点。")
    L.append("")

    if home_rows:
        L.append("### 母港 / 驻地（**不是当前位置**，仅供识别舰艇归属，勿当作部署位置）")
        L.append("")
        L.append("| 舰艇 | 纬度 | 经度 | 母港/驻地 | 关联依据 |")
        L.append("|---|---|---|---|---|")
        for ships, lat, lon, kind, basis in home_rows:
            L.append(f"| {ships} | {lat} | {lon} | {kind} | {basis} |")
        L.append("")

    # 四、抓取状态
    L.append("## 四、抓取状态")
    L.append("")
    L.append("| 数据源 | 状态 | 命中条数 | 源内最新条目 |")
    L.append("|---|---|---|---|")
    for s in report:
        mark = " ⚠️ 疑似停更" if s.get("status") == "ok-stale" else ""
        L.append(f"| {s['name']} | {s.get('status', '-')}{mark} | {s.get('count', '-')} | "
                 f"{s.get('newest', '-')} |")
    L.append("")
    stale_srcs = [s for s in report if s.get("status") == "ok-stale"]
    if stale_srcs:
        L.append("> ⚠️ **停更提示**：以下源的最新条目已超过 `stale_after_days` 阈值，"
                 "通常是源站停止更新或 CDN 缓存异常，**不是**网络问题。")
        for s in stale_srcs:
            L.append(f"> - **{s['name']}** — 源内最新 {s.get('newest', '?')}")
        L.append("")
        L.append("> 遇到这种情况应改从其他源取同类内容（例如 USNI 的 Fleet Tracker 已改由主站 feed 提供），")
        L.append("> 或在 `config.json` 里把该源 `enabled` 设为 `false`。")
        L.append("")

    # 五、结构化数据给 LLM
    L.append("## 五、结构化数据（JSON）")
    L.append("")
    L.append("```json")
    L.append(json.dumps({
        "date": date_str,
        "generated_at": run_dt.isoformat(timespec="seconds"),
        "record_count": len(records_new),
        "records": records_new,
    }, ensure_ascii=False, indent=1))
    L.append("```")
    L.append("")
    return "\n".join(L)


def render_coordinates_md(records: List[Dict[str, Any]], run_dt: datetime) -> str:
    L = [f"# 坐标清单 — {run_dt.strftime('%Y-%m-%d')}", ""]
    L.append("只收录同一句内同时出现「舰艇 + 位置」的条目，供大模型或 GIS 直接使用。")
    L.append("")
    n = 0
    pos: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    home: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    for r in records:
        for loc in r.get("localized", []):
            for pt in loc.get("points", []):
                (home if pt.get("homeport") else pos).append((loc, pt, r))

    for title, rows, note in (
        ("## 一、部署位置", pos, ""),
        ("## 二、母港 / 驻地", home,
         "> ⚠️ 以下是**母港/驻地**，不是当前位置，勿当作部署位置使用。"),
    ):
        if not rows:
            continue
        L.append(title)
        L.append("")
        if note:
            L.append(note)
            L.append("")
        L.append("| 舰艇 | 纬度 | 经度 | 位置/类型 | 关联依据 | 原文子句 | 出处 |")
        L.append("|---|---|---|---|---|---|---|")
        for loc, pt, r in rows:
            n += 1
            ships_txt = "、".join(loc["ships"])
            kind = "原文显式坐标" if pt["kind"] == "explicit" else f"{pt['label']}（{pt['kind']}·近似）"
            sent = loc.get("sentence", "")[:120].replace("|", "/")
            L.append(f"| {ships_txt} | {pt['lat']} | {pt['lon']} | {kind} | "
                     f"{loc_basis(loc)} | {sent} | {r['link']} |")
        L.append("")
    if n == 0:
        L.append("| - | - | - | _本窗口无同句关联_ | - | - | - |")
        L.append("")
    return "\n".join(L)


def write_outputs(records_new: List[Dict[str, Any]], records_all: List[Dict[str, Any]],
                  report: List[Dict[str, str]], run_dt: datetime, hours: int,
                  out_dir: Optional[Path] = None) -> Dict[str, Path]:
    date_str = run_dt.strftime("%Y-%m-%d")
    OUTPUT_DIR_ROOT = out_dir or OUTPUT_DIR
    day_dir = OUTPUT_DIR_ROOT / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    jsonl = day_dir / f"records-{date_str}.jsonl"

    # 与当天已有记录合并，避免「同日重跑」把当天数据覆盖成仅本次新增。
    # 同 id 的条目用本次新解析结果覆盖旧条目（这样解析器修复能回溯生效）。
    merged: List[Dict[str, Any]] = []
    index_by_id: Dict[str, int] = {}
    if jsonl.exists():
        for line in jsonl.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = old.get("id") or ""
            if rid and rid in index_by_id:
                continue
            if rid:
                index_by_id[rid] = len(merged)
            merged.append(old)

    # ⚠️ 合并源必须用 records_all（本次抓取窗口内的**全部**记录），不能用 records_new（仅去重后的新增）。
    # 原因：跨天运行时 output/<今天>/ 还不存在，若只把「新增」并进来，
    # LATEST.md 会退化成「只有今天新出现的那几条」，窗口内的历史全部丢失。
    # （同一天重跑时二者等价，所以这个坑只在跨天时暴露 —— 只测「同日重跑」会漏掉它。）
    existing_ids = set(index_by_id)
    for r in (records_all or records_new):
        rid = r.get("id") or ""
        if rid and rid in index_by_id:
            merged[index_by_id[rid]] = r          # 用新解析结果覆盖，让解析器修复能回溯生效
            continue
        if rid:
            index_by_id[rid] = len(merged)
        merged.append(r)

    # 「本次新增」= 去重后的新增中，当天文件里尚未出现的那些
    fresh = sum(1 for r in records_new if (r.get("id") or "") not in existing_ids)

    merged.sort(key=lambda r: r.get("published") or "", reverse=True)
    if merged:
        records_new = merged
    add_note = (f"（本次新增 {fresh} 条，当前快照 {len(records_new)} 条）" if merged else "")

    digest = day_dir / f"naval-digest-{date_str}.md"
    digest.write_text(render_digest(records_new, records_all, report, run_dt, hours,
                                    note=add_note, fresh=fresh), "utf-8")

    coords_md = day_dir / f"coordinates-{date_str}.md"
    coords_md.write_text(render_coordinates_md(records_new, run_dt), "utf-8")

    with jsonl.open("w", encoding="utf-8") as fh:
        for r in records_new:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    raw_json = day_dir / f"records-{date_str}.json"
    raw_json.write_text(json.dumps(records_new, ensure_ascii=False, indent=1), "utf-8")

    # ---- 累积总档（跨天不淘汰）----
    # 当日快照会随回溯窗口滚动而丢弃旧记录；本文件按 id 永久累积，
    # 供大模型做跨越数月的长周期分析。首次出现日期记在 first_seen 字段。
    acc_path = OUTPUT_DIR_ROOT / "ALL-records.jsonl"
    acc: List[Dict[str, Any]] = []
    acc_idx: Dict[str, int] = {}
    if acc_path.exists():
        for line in acc_path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = old.get("id") or ""
            if rid and rid in acc_idx:
                continue
            if rid:
                acc_idx[rid] = len(acc)
            acc.append(old)
    for r in records_new:
        rid = r.get("id") or ""
        row = dict(r)
        if rid and rid in acc_idx:
            # 保留首次出现日期，其余字段用本次最新解析结果覆盖
            row["first_seen"] = acc[acc_idx[rid]].get("first_seen") or date_str
            acc[acc_idx[rid]] = row
        else:
            row["first_seen"] = date_str
            if rid:
                acc_idx[rid] = len(acc)
            acc.append(row)
    acc.sort(key=lambda r: r.get("published") or "", reverse=True)
    with acc_path.open("w", encoding="utf-8") as fh:
        for r in acc:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    latest = OUTPUT_DIR_ROOT / "LATEST.md"
    latest.write_text(digest.read_text("utf-8"), "utf-8")

    # 索引
    idx = ["# 监测归档索引", "",
           f"累计档案（跨天不淘汰，按 id 去重）：`ALL-records.jsonl` — **{len(acc)} 条**", "",
           "> `LATEST.md` 与各日 `naval-digest-*.md` 均为**滚动窗口快照**（含窗口内全部记录，",
           "> 非仅当日新增）；真正长期不丢的是 `ALL-records.jsonl`。", "",
           "| 日期 | 快照条数 | 日报 |", "|---|---|---|"]
    for d in sorted([p for p in OUTPUT_DIR_ROOT.iterdir() if p.is_dir()], reverse=True):
        f = d / f"naval-digest-{d.name}.md"
        n = "-"
        jl = d / f"records-{d.name}.jsonl"
        if jl.exists():
            n = str(sum(1 for _ in jl.open(encoding="utf-8")))
        rel = f"{d.name}/{f.name}" if f.exists() else "-"
        idx.append(f"| {d.name} | {n} | {rel} |")
    (OUTPUT_DIR_ROOT / "index.md").write_text("\n".join(idx) + "\n", "utf-8")

    return {"digest": digest, "coords": coords_md, "jsonl": jsonl, "latest": latest,
            "all": acc_path}


# --------------------------------------------------------------------------- #
# 九、离线自检
# --------------------------------------------------------------------------- #
SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Fleet Tracker Archives - USNI News</title>
<item>
  <title>USNI News Fleet and Marine Tracker: Sept. 14, 2026</title>
  <link>https://news.usni.org/2026/09/14/usni-news-fleet-and-marine-tracker-sept-14-2026</link>
  <pubDate>Mon, 14 Sep 2026 19:26:00 +0000</pubDate>
  <description>These are the approximate positions of the U.S. Navy's deployed carrier strike
  groups and amphibious ready groups. USS Gerald R. Ford (CVN-78) is operating in the
  Eastern Mediterranean. USS Harry S. Truman (CVN-75) is in the North Arabian Sea.
  USS Bataan (LHD-5) transited the Strait of Hormuz into the Persian Gulf at 26.57N 56.25E.
  The Abraham Lincoln Carrier Strike Group is in the Philippine Sea, near 20&#176;N 135&#176;E.</description>
  <category>USS Gerald R. Ford (CVN-78)</category>
  <category>USS Bataan (LHD-5)</category>
  <category>Fleet Tracker</category>
</item>
<item>
  <title>Destroyer conducts freedom of navigation transit in Taiwan Strait</title>
  <link>https://news.usni.org/2026/09/13/destroyer-transit</link>
  <pubDate>Sun, 13 Sep 2026 12:00:00 +0000</pubDate>
  <description>USS Higgins (DDG-76) conducted a routine transit of the Taiwan Strait,
  the 7th Fleet said. The guided-missile destroyer deployed with the George Washington
  Carrier Strike Group. Separately, an SSN-774 class submarine arrived in Guam.</description>
  <category>Surface Forces</category>
</item>
<item>
  <title>Navy announces retirement of base landscaping contract</title>
  <link>https://news.usni.org/2026/09/12/landscaping</link>
  <pubDate>Sat, 12 Sep 2026 09:00:00 +0000</pubDate>
  <description>Unrelated administrative news about facilities and housing.</description>
</item>
</channel></rss>
"""


def selftest(verbose: bool = True) -> int:
    print("=== 离线自检 ===")
    cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
    items = parse_feed(SAMPLE_FEED)
    print(f"1) feed 解析: 期望 3 条 -> 实际 {len(items)} 条")
    assert len(items) == 3, "feed 解析数量不符"

    prefixes = cfg["ship_hull_prefixes"]
    keywords = cfg["relevance_keywords"]
    pexcl = cfg.get("place_exclude_phrases", [])

    blob1 = " ".join([items[0]["title"], items[0]["summary"], " ".join(items[0]["categories"])])
    ships1 = extract_ships(blob1, prefixes)
    print(f"2) 舰艇抽取(样本1): {ships1}")
    assert any("CVN-78" in s for s in ships1)
    assert any("LHD-5" in s for s in ships1)
    assert any("CVN-75" in s for s in ships1)

    places1 = extract_places(blob1, pexcl)
    print(f"3) 海域抽取(样本1): {[p[0] for p in places1]}")
    assert any("Hormuz" in p[0] for p in places1)

    coords1 = extract_coords(blob1)
    print(f"4) 坐标抽取(样本1): {coords1}")
    assert (26.57, 56.25, "文中显式坐标") in coords1
    assert (20.0, 135.0, "文中显式坐标") in coords1

    blob2 = " ".join([items[1]["title"], items[1]["summary"]])
    ships2 = extract_ships(blob2, prefixes)
    print(f"5) 舰艇抽取(样本2): {ships2}")
    assert any("DDG-76" in s for s in ships2)

    # 句级局部关联：确认不会把整篇舰艇挂到单个坐标上
    locs = extract_localized(blob1, prefixes, pexcl)
    print("6) 句级局部关联(样本1):")
    for l in locs:
        pts = [(p["lat"], p["lon"]) for p in l["points"]]
        print(f"     舰艇={l['ships']}  位置={pts}")
        print(f"     句: {l['sentence'][:95]}...")
    # 舰名不得因中间名缩写而被截断
    all_loc_ships = [s for l in locs for s in l["ships"]]
    assert any("USS Gerald R. Ford (CVN-78)" == s for s in all_loc_ships), \
        f"舰名被截断: {all_loc_ships}"
    # 霍尔木兹坐标只应关联到同句的 Bataan，而不是全文所有舰艇
    hormuz_ships = [s for l in locs if (26.57, 56.25) in [(p["lat"], p["lon"]) for p in l["points"]]
                    for s in l["ships"]]
    assert any("Bataan" in s for s in hormuz_ships), "霍尔木兹坐标应关联到 Bataan"
    assert not any("Ford" in s or "Truman" in s for s in hormuz_ships), \
        f"其他舰艇被错误关联到霍尔木兹坐标: {hormuz_ships}"

    score3, _ = relevance_score(items[2]["title"] + items[2]["summary"], keywords)
    ships3 = extract_ships(items[2]["title"] + items[2]["summary"], prefixes)
    print(f"7) 无关新闻过滤: 关键词得分={score3}, 舰艇={ships3}")
    assert score3 < 2 and not ships3, "无关新闻未被过滤"

    # 完整渲染一次
    run_dt = datetime.now(CST)
    recs = []
    for it in items:
        dt = parse_time(it["published_raw"])
        blob = " ".join([it["title"], it["summary"], " ".join(it["categories"])])
        sc, mk = relevance_score(blob, keywords)
        recs.append({
            "id": link_id(it["link"]), "source_id": "usni-fleet-tracker",
            "source_name": "USNI News — Fleet & Marine Tracker", "source_priority": "high",
            "source_tags": ["fleet-tracker"], "is_tracker": True,
            "title": it["title"], "link": it["link"],
            "published": dt.astimezone(CST).isoformat(timespec="seconds") if dt else "",
            "published_utc": dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else "",
            "summary": it["summary"][:1200], "categories": it["categories"],
            "ships": extract_ships(blob, prefixes),
            "places": [{"name": n, "lat": m[0], "lon": m[1], "kind": m[2]} for n, m in extract_places(blob, pexcl)],
            "coords": [{"lat": c[0], "lon": c[1], "origin": c[2]} for c in extract_coords(blob)],
            "localized": extract_localized(blob, prefixes, pexcl),
            "relevance_score": sc, "matched_keywords": mk[:12],
        })
    md = render_digest(recs, recs, [{"id": "t", "name": "自检", "status": "ok", "count": "3"}], run_dt, 72)
    print(f"8) Markdown 渲染: {len(md)} 字符")
    assert "## 三、坐标汇总" in md and "```json" in md

    # 9) 易错舰名写法（无空格缩写 / 名字带逗号）
    tricky = ("Carrier Strike Group 10 includes USS George H.W. Bush (CVN-77) and "
              "USS Frank E. Petersen, Jr. (DDG-121), homeported at Pearl Harbor.")
    tricky_ships = extract_ships(tricky, prefixes)
    print(f"9) 易错舰名: {tricky_ships}")
    assert "USS George H.W. Bush (CVN-77)" in tricky_ships, "H.W. 无空格写法识别失败"
    assert "USS Frank E. Petersen, Jr. (DDG-121)" in tricky_ships, "名字带逗号识别失败"
    assert not any(s == "Bush (CVN-77)" for s in tricky_ships), "产生了重复的残缺舰名"
    assert not any(s == "DDG-121" for s in tricky_ships), "舷号未被合并进舰名"

    # 10) 母港识别：homeported 后的地点不能算作部署位置
    hp = ("Guided-missile cruiser USS Robert Smalls (CG-62), homeported in Yokosuka, "
          "is in the South China Sea.")
    hp_locs = extract_localized(hp, prefixes, pexcl)
    print("10) 母港识别:")
    for l in hp_locs:
        for pt in l["points"]:
            print(f"     {l['ships']} -> {pt['label']}  母港={pt['homeport']}")
    scs = [pt for l in hp_locs for pt in l["points"] if pt["label"] == "South China Sea"]
    yok = [pt for l in hp_locs for pt in l["points"] if pt["label"] == "Yokosuka"]
    assert scs and not scs[0]["homeport"], "南海应被识别为部署位置"
    assert yok and yok[0]["homeport"], "横须贺应被识别为母港"

    # 11) 地名歧义：长名必须压住短名（Newport News 不能命中 Newport）
    amb = "Navy, Pentagon Enter Into $1.7 Billion Housing Expansion in Newport News."
    amb_names = [n for n, _ in extract_places(amb, pexcl)]
    print(f"11) 地名歧义: {amb_names}")
    assert "Newport News" in amb_names, "Newport News 未识别"
    assert "Newport" not in amb_names, f"Newport 抢占了 Newport News: {amb_names}"

    # 11b) 专有名词遮蔽：书名 "Newport Manual" 里的 Newport 不是地名
    manual = ("Striking tankers would be legal according to the Newport Manual on the "
              "Law of Naval Warfare, co-author James Kraska said.")
    man_hits = [n for n, _ in extract_places(manual, pexcl)]
    print(f"11b) 专有名词遮蔽: {man_hits}")
    assert not any("Newport" in n for n in man_hits), f"书名被当成地名: {man_hits}"

    # 11c) 舰名后缀：San Diego (LPD-22) 里的 San Diego 是舰名，不是圣地亚哥港
    ship_place = ("USS San Diego (LPD-22) is forward-deployed to Sasebo, Japan.")
    sp = [(n, m) for n, m in extract_places(ship_place, pexcl)]
    print(f"11c) 舰名不当地名: {[n for n, _ in sp]}")
    assert "San Diego" not in [n for n, _ in sp], f"舰名被当地名: {sp}"
    assert any("Sasebo" in n for n, _ in sp), "真实地名 Sasebo 丢失"

    # 12) 编制/辖区概念不投点（5th Fleet 是司令部，不是舰位）
    fleet = "USS George H. W. Bush (CVN-77) is operating in the 5th Fleet area of operations."
    fl_locs = extract_localized(fleet, prefixes, pexcl)
    fl_pts = [pt for l in fl_locs for pt in l["points"]]
    fl_names = [n for l in fl_locs for n in [p["name"] for p in l["places"]]]
    print(f"12) 辖区不投点: points={[(p['label'], p['kind']) for p in fl_pts]}  places={fl_names}")
    assert not fl_pts, f"舰队辖区不应产生坐标点: {fl_pts}"
    assert any("Fleet" in n for n in fl_names), "舰队辖区仍应保留在 places 中供阅读"

    # 13) 写盘回归：跨天不得丢窗口内历史、同日重跑不得清空、累积档不得倒退
    #     教训：只测「同日重跑」会漏掉跨天场景 —— 跨天时 output/<今天>/ 尚不存在，
    #     若合并源用「仅新增」而非「窗口内全部」，LATEST.md 会退化成只有当天新出现的几条。
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        rep_ok = [{"id": "t", "name": "自检", "status": "ok", "count": "3"}]
        cut = max(1, len(recs) // 2)
        half, rest = list(recs)[:cut], list(recs)[cut:]
        d1 = run_dt - timedelta(days=1)
        write_outputs(half, half, rep_ok, d1, 72, out_dir=tmpdir)

        # 第 2 天：抓取窗口内含第 1 天全部记录，但只有 rest 算「新增」
        p2 = write_outputs(rest, half + rest, rep_ok, run_dt, 72, out_dir=tmpdir)
        n2 = sum(1 for _ in p2["jsonl"].open(encoding="utf-8"))
        title2 = next(l for l in p2["digest"].read_text("utf-8").splitlines() if l.startswith("# "))
        print(f"13) 跨天累积: 第1天 {len(half)} 条 -> 第2天快照 {n2} 条 | {title2}")
        assert n2 == len(recs), f"跨天丢了窗口内历史: 期望 {len(recs)}，实得 {n2}"
        assert "本次新增 2 条" in title2 and "当前快照 3 条" in title2, \
            f"标题未正确区分「本次新增」与「快照总数」: {title2}"

        # 同日重跑：本次新增 0 条，快照不得缩水
        p3 = write_outputs([], half + rest, rep_ok, run_dt, 72, out_dir=tmpdir)
        n3 = sum(1 for _ in p3["jsonl"].open(encoding="utf-8"))
        print(f"     同日重跑: {n2} -> {n3} 条")
        assert n3 == n2, f"同日重跑把数据清空了: {n2} -> {n3}"

        # 同 id 用新解析结果覆盖；累积档不倒退且保留 first_seen
        tweaked = dict(recs[0], title="样例 A 已更新")
        p4 = write_outputs([tweaked], [tweaked], rep_ok, run_dt, 72, out_dir=tmpdir)
        titles = [json.loads(x)["title"] for x in p4["jsonl"].read_text("utf-8").splitlines()]
        acc_rows = [json.loads(x) for x in p4["all"].read_text("utf-8").splitlines()]
        acc_fs = {r["title"]: r.get("first_seen") for r in acc_rows}
        print(f"     覆盖: 快照 {len(titles)} 条，含更新标题={'样例 A 已更新' in titles}"
              f"；累积档 {len(acc_rows)} 条，first_seen={acc_fs.get('样例 A 已更新')}")
        assert "样例 A 已更新" in titles and len(titles) == len(recs), "同 id 未按新解析结果覆盖"
        assert len(acc_rows) == len(recs), f"累积档条数异常: {len(acc_rows)}"
        assert acc_fs.get("样例 A 已更新") == d1.strftime("%Y-%m-%d"), \
            f"累积档丢了 first_seen 首次出现日期: {acc_fs.get('样例 A 已更新')}"

    # 14) 词边界：舰级名/普通单词不得被当成舰名或地名（真实数据高频假阳性）
    cls = "Nimitz-class aircraft carrier USS Abraham Lincoln (CVN-72) is in the South China Sea."
    cls_ships = extract_ships(cls, prefixes)
    print(f"14) 舰级不生成舰名: {cls_ships}")
    assert not any("CVN-68" in s for s in cls_ships), f"Nimitz-class 被当成 USS Nimitz: {cls_ships}"
    assert any("CVN-72" in s for s in cls_ships), "真实舰名被误杀"

    word = "The Subic Drydock Corporation, an American-owned firm, said Monday."
    w_ships = extract_ships(word, prefixes)
    print(f"     普通词不生成舰名: {w_ships}")
    assert not any("LHA-6" in s for s in w_ships), f"American 被当成 USS America: {w_ships}"

    r2 = "Total Battle Force Deployed Underway 290 (39 FDNF, 64 Rotational)"
    r2_names = [n for n, _ in extract_places(r2, pexcl)]
    print(f"     普通词不当地名: {r2_names}")
    assert not any("Rota" in n for n in r2_names), f"Rotational 被当成 Rota: {r2_names}"
    real_rota = "USS Roosevelt (DDG-80), homeported at Naval Station Rota, Spain."
    assert any("Rota" in n for n, _ in extract_places(real_rota, pexcl)), "真实 Rota 被误杀"

    print("\n[✓] 全部自检通过。feed 解析、舰艇/海域/坐标抽取、句级关联、母港区分、"
          "地名歧义、专有名词遮蔽、舰名不当地名、辖区过滤、同日重跑保留、"
          "舰级/普通词边界、Markdown 渲染均正常。")
    return 0


# --------------------------------------------------------------------------- #
# 十、CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
    except Exception:                              # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(
        description="美军海军动态公开来源监测爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--config", default=str(CONFIG_PATH), help="配置文件路径")
    ap.add_argument("--hours", type=int, default=None, help="回溯小时数，覆盖配置")
    ap.add_argument("--source", action="append", default=None, help="只抓指定 source id，可重复")
    ap.add_argument("--direct", action="store_true", help="绕过系统代理直连")
    ap.add_argument("--proxy", default=None,
                    help="使用指定代理，如 http://127.0.0.1:7890（优先于 --direct 与环境变量）")
    ap.add_argument("--from-file", default=None,
                    help="本地 Feed 目录：读取 <目录>/<source_id>.xml 而非联网抓取（应对风控/离线场景）")
    ap.add_argument("--out", default=None, help="输出目录，默认 ./output")
    ap.add_argument("--no-state", action="store_true", help="忽略去重状态，全部视为新增")
    ap.add_argument("--dry-run", action="store_true", help="只抓取并打印，不写文件")
    ap.add_argument("--list-sources", action="store_true", help="列出已配置数据源")
    ap.add_argument("--selftest", action="store_true", help="离线自检")
    ap.add_argument("--quiet", action="store_true", help="减少日志")
    args = ap.parse_args(argv)

    verbose = not args.quiet

    if args.selftest:
        return selftest()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        log(f"[!] 找不到配置文件: {cfg_path}")
        return 2
    cfg = json.loads(cfg_path.read_text("utf-8"))

    if args.list_sources:
        print(f"{'ID':<22}{'启用':<6}{'优先级':<10}URL")
        print("-" * 100)
        for s in cfg["sources"]:
            on = "是" if s.get("enabled", True) else "否"
            print(f"{s['id']:<22}{on:<6}{s.get('priority', 'normal'):<10}{s['url']}")
            if s.get("note"):
                print(f"{'':<38}└ {s['note']}")
        return 0

    hours = args.hours if args.hours is not None else cfg.get("lookback_hours", 72)
    run_dt = datetime.now(CST)

    log(f"=== 美军海军动态监测 | {run_dt.strftime('%Y-%m-%d %H:%M:%S')} | 回溯 {hours}h ===", verbose)
    fetcher = Fetcher(cfg.get("request", {}), direct=args.direct, proxy=args.proxy)
    if args.proxy:
        log(f"    模式: 指定代理 {args.proxy}", verbose)
    elif args.direct:
        log("    模式: 直连（已清除代理环境变量）", verbose)
    else:
        # 不指定时，urllib 会自动读取系统代理（Windows 读注册表，*nix 读环境变量）。
        # 把实际出口打印出来 —— 否则「代理端口写错」这类问题很难被发现。
        auto = urllib.request.getproxies()
        picked = auto.get("https") or auto.get("http")
        log(f"    模式: 系统默认（自动探测到代理: {picked or '无，走直连'}）", verbose)

    feed_dir: Optional[Path] = None
    if args.from_file:
        feed_dir = Path(args.from_file)
        if not feed_dir.is_absolute():
            feed_dir = (BASE_DIR / feed_dir).resolve()
        if not feed_dir.is_dir():
            log(f"[!] 本地 Feed 目录不存在: {feed_dir}")
            return 2
        log(f"    模式: 本地文件（{feed_dir}）", verbose)

    records, report = collect(cfg, fetcher, args.source, hours, verbose, feed_dir=feed_dir)

    out_dir: Optional[Path] = None
    if args.out:
        out_dir = Path(args.out)
        if not out_dir.is_absolute():
            out_dir = (BASE_DIR / out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

    seen = load_seen() if not args.no_state else {}
    new, old = dedupe(records, seen, run_dt.strftime("%Y-%m-%d"), not args.no_state)
    if not args.no_state:
        save_seen(seen)

    log(f"\n=== 汇总: 窗口内 {len(records)} 条，新增 {len(new)} 条，历史 {len(old)} 条 ===", verbose)

    if args.dry_run:
        for r in new[:20]:
            print(f"  - [{r['source_id']}] {r['title']}")
            print(f"      舰艇={r['ships']} 海域={[p['name'] for p in r['places']]}")
        return 0

    paths = write_outputs(new, records, report, run_dt, hours, out_dir=out_dir)
    log("\n=== 产出 ===", verbose)
    for k, v in paths.items():
        try:
            shown = v.relative_to(BASE_DIR)
        except ValueError:
            shown = v
        log(f"  {k:<8} {shown}", verbose)

    failed = [s for s in report if s["status"] not in NON_FAILURE_STATUS]
    if failed:
        log(f"\n[!] {len(failed)} 个数据源抓取失败: {', '.join(s['id'] for s in failed)}", verbose)
        log("    若为 HTTP 403 / 隧道 502，请尝试 --direct，或检查出口代理与 IP 是否被目标站点拦截。", verbose)

    print(str(paths["digest"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
