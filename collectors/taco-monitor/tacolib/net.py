# -*- coding: utf-8 -*-
"""
net.py —— 抓取通道（零第三方依赖）

设计要点（每条都对应一次真实踩坑，见 README §4）：
  1. curl 主通道 / urllib 兜底
     urllib 在部分环境下会因 TLS 指纹被 CDN 风控判为异常而整体超时，
     curl 同 URL 单发 200。故 curl 优先，urllib 仅作兜底。
  2. 同主机限速（默认 1.2s）
     实测并发 8 个请求后全部挂到超时。限速是正确性要求，不是礼貌。
  3. 假 200 识别
     某些源对每个符号都返回同一个 JS 挑战页且 HTTP=200。
     校验器按【内容】判定，不看状态码。
  4. 原子写入
     先写 .tmp 再 os.replace，避免半截文件被下游当成完整载荷。
  5. 代理
     默认自动探测系统代理；RM_PROXY 环境变量可显式覆盖（仅供排障）。
     绝不把端口写死——实测同一天内会漂。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------------
# 代理
# ----------------------------------------------------------------------------

def _raw_system_proxy():
    """读系统代理设置（不做健康检查）。"""
    try:
        from urllib.request import getproxies
        p = getproxies()
        return (p.get("http") or p.get("https"),
                p.get("https") or p.get("http"))
    except Exception:                                        # noqa: BLE001
        return None, None


def _proxy_alive(proxy_url, timeout=1.5):
    """TCP 探测代理端口是否真的在监听。

    踩坑记录：Windows 注册表里的系统代理经常指向一个已经退出的本地端口
    （实测 51681 / 63299 / 56716 都是死的）。把它塞进 curl -x 的结果是
    整个通道 0 字节超时——症状与「被 CDN 风控」一模一样，极难排查。
    所以：先探活，死的直接跳过，绝不硬塞。
    """
    if not proxy_url:
        return False
    try:
        import socket
        from urllib.parse import urlparse
        u = urlparse(proxy_url if "//" in proxy_url else "http://" + proxy_url)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:                                        # noqa: BLE001
        return False


_PROXY_CACHE = {}

# 出口探针：一个极轻、稳定、无风控的端点
PROBE_URL = "https://www.gstatic.com/generate_204"


def _try_direct(url, timeout):
    """直连探测（强制 --noproxy '*'，忽略环境变量里的失效代理）。"""
    exe = _curl_binary()
    if not exe:
        return False
    try:
        p = subprocess.run([exe, "-sS", "-o", os.devnull, "--noproxy", "*",
                            "--max-time", str(int(timeout)),
                            "--connect-timeout", str(int(timeout)),
                            "-w", "%{http_code}", url],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout + 5)
        code = p.stdout.decode("ascii", "replace").strip()
        return code.isdigit() and int(code) in (200, 204)
    except Exception:                                        # noqa: BLE001
        return False


def _try_via_proxy(url, proxy, timeout):
    exe = _curl_binary()
    if not exe or not proxy:
        return False
    try:
        p = subprocess.run([exe, "-sS", "-o", os.devnull, "-x", proxy,
                            "--max-time", str(int(timeout)),
                            "--connect-timeout", str(int(timeout)),
                            "-w", "%{http_code}", url],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout + 5)
        code = p.stdout.decode("ascii", "replace").strip()
        return code.isdigit() and int(code) in (200, 204)
    except Exception:                                        # noqa: BLE001
        return False


def _env_proxy_names():
    """返回当前环境里被设置的非空代理变量名（供报告披露）。"""
    names = []
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "ALL_PROXY", "all_proxy"):
        if os.environ.get(k):
            names.append(k)
    return names


def choose_egress(force=False):
    """实测选择出口，返回 (http_proxy, https_proxy)。

    踩坑记录（这是本项目最贵的一课，两个坑叠在一起）：
      坑 1：Windows 注册表里的系统代理经常指向一个【仍在监听但已无出口】的
            本地端口。TCP 探活返回 alive=True，但 curl -x 过去 0 字节超时。
      坑 2：本机环境变量里注入了 http_proxy / https_proxy（实测 56716），
            同样是死端口。curl 与 urllib 都会【默认读取】它，于是即便不传
            -x，请求也照样被送去死代理 —— 症状与「被 CDN 风控」一模一样。
      结论：TCP 探活不够，必须实测真实端点；且不指定代理时必须显式
            --noproxy '*' / ProxyHandler({})，绝不能靠默认行为。

    决策顺序：
      1. RM_PROXY 环境变量 —— 显式覆盖，直接用（用户排障优先，不实测）。
      2. 直连实测 —— 通了就用直连（最常见、最快）。
      3. 系统代理实测 —— 直连不通才试。
      4. 都失败 —— 返回直连（乐观默认，让上层重试逻辑去撞）。
    """
    if _PROXY_CACHE.get("resolved") and not force:
        return _PROXY_CACHE["pair"]

    env_names = _env_proxy_names()

    override = os.environ.get("RM_PROXY", "").strip()
    if override:
        pair = (override, override)
        _PROXY_CACHE.update(resolved=True, pair=pair, reason="RM_PROXY-env",
                            proxy=override, tested=False, env_proxy_vars=env_names)
        return pair

    # 2) 直连
    if _try_direct(PROBE_URL, 8):
        _PROXY_CACHE.update(resolved=True, pair=(None, None),
                            reason="direct-tested-ok", proxy=None, tested=True,
                            env_proxy_vars=env_names)
        return None, None

    # 3) 系统代理
    ph, ps = _raw_system_proxy()
    cand = ps or ph
    if cand and _try_via_proxy(PROBE_URL, cand, 10):
        _PROXY_CACHE.update(resolved=True, pair=(ph, ps),
                            reason="system-proxy-tested-ok", proxy=cand,
                            tested=True, env_proxy_vars=env_names)
        return ph, ps

    # 4) 都失败
    _PROXY_CACHE.update(resolved=True, pair=(None, None),
                        reason="all-egress-failed->direct", proxy=cand,
                        tested=True, env_proxy_vars=env_names)
    return None, None


def system_proxy(cfg=None):
    """兼容旧签名：返回当前选定的 (http_proxy, https_proxy)。"""
    return choose_egress()


def proxy_decision():
    """返回出口决策的可读说明（供报告头部打印）。"""
    choose_egress()
    return {
        "reason": _PROXY_CACHE.get("reason"),
        "proxy": _PROXY_CACHE.get("proxy"),
        "tested": _PROXY_CACHE.get("tested"),
        "env_RM_PROXY": os.environ.get("RM_PROXY") or None,
        "env_proxy_vars_ignored": _PROXY_CACHE.get("env_proxy_vars") or [],
    }


# ----------------------------------------------------------------------------
# 限速
# ----------------------------------------------------------------------------

_LAST_HIT = {}


def _throttle(url, min_interval):
    """同主机最小间隔限速。"""
    if min_interval <= 0:
        return
    try:
        host = url.split("//", 1)[1].split("/", 1)[0]
    except IndexError:
        host = url
    now = time.time()
    last = _LAST_HIT.get(host)
    if last is not None:
        wait = min_interval - (now - last)
        if wait > 0:
            time.sleep(wait)
    _LAST_HIT[host] = time.time()


# ----------------------------------------------------------------------------
# 通道
# ----------------------------------------------------------------------------

class FetchResult(object):
    def __init__(self, ok, url, channel, http, body, error, elapsed):
        self.ok = ok
        self.url = url
        self.channel = channel
        self.http = http
        self.body = body
        self.error = error
        self.elapsed = elapsed

    @property
    def nbytes(self):
        return len(self.body) if self.body else 0

    def sha256(self):
        if not self.body:
            return None
        return hashlib.sha256(self.body).hexdigest()[:16]

    def __repr__(self):
        return ("<FetchResult ok=%s ch=%s http=%s bytes=%s %.2fs%s>"
                % (self.ok, self.channel, self.http, self.nbytes, self.elapsed,
                   "" if self.ok else " err=%s" % self.error))


def _curl_binary():
    """定位 curl。优先 PATH，其次 Windows 自带 System32\\curl.exe。"""
    found = shutil.which("curl")
    if found:
        return found
    if os.name == "nt":
        cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            "System32", "curl.exe")
        if os.path.exists(cand):
            return cand
    return None


def _fetch_curl(url, timeout, ua, proxy, method=None, data=None, headers=None,
                cookie_jar=None, cookie_write=None):
    """用 curl 抓取。返回 (http_code:int|None, body:bytes|None, err:str|None)。

    【关键】必须显式处理代理开关，不能靠默认行为：
      本机环境（WorkBuddy / 系统）会注入 http_proxy / https_proxy 环境变量，
      实测指向 127.0.0.1:56716 —— 一个仍在监听但已无出口的端口。
      curl 默认会读这些环境变量，结果 0 字节超时，症状与「被 CDN 风控」
      一模一样，极难排查。
      所以：不指定代理时强制 --noproxy '*' 走直连；指定代理时才用 -x。

    method/data/headers/cookie_* 为可选扩展（默认 None = 行为与本函数只有 GET
    时完全一致，既有源不受影响）。需要它们的场景：Bonbast 的「首页取令牌 →
    POST /json」两步抓取，且该站要求带 cookie 与会话头（否则返回令牌失效）。
    """
    exe = _curl_binary()
    if not exe:
        return None, None, "curl-not-found"

    # 用临时文件接 body，避免二进制/大载荷经管道被截断或编码破坏
    fd, tmp = tempfile.mkstemp(prefix="taco_dl_", suffix=".bin")
    os.close(fd)
    hdr_fd, hdr_tmp = tempfile.mkstemp(prefix="taco_hdr_", suffix=".txt")
    os.close(hdr_fd)
    try:
        cmd = [exe, "-sS", "-L", "--max-time", str(int(timeout)),
               "--connect-timeout", str(min(20, int(timeout))),
               "-A", ua,
               "-o", tmp, "-D", hdr_tmp,
               "-w", "%{http_code}"]
        if proxy:
            cmd += ["-x", proxy]
        else:
            cmd += ["--noproxy", "*"]
        # 以下四段只在显式传入时追加，GET 路径的 argv 与扩展前逐字相同。
        if cookie_jar and os.path.exists(cookie_jar):
            cmd += ["-b", cookie_jar]
        if cookie_write:
            cmd += ["-c", cookie_write]
        if method and method.upper() != "GET":
            cmd += ["-X", method.upper()]
        if data:
            payload = data.decode("utf-8") if isinstance(data, bytes) else data
            # --data-raw：不做 @文件 解释，避免表单值被 curl 当文件名读
            cmd += ["--data-raw", payload]
        for hk, hv in (headers or {}).items():
            cmd += ["-H", "%s: %s" % (hk, hv)]
        cmd.append(url)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout + 20)
        code_s = proc.stdout.decode("ascii", "replace").strip()
        http = int(code_s) if code_s.isdigit() else None
        body = None
        if os.path.exists(tmp):
            with open(tmp, "rb") as f:
                body = f.read()
        if http is None or http == 0:
            err = proc.stderr.decode("utf-8", "replace").strip()[:200]
            return None, body, err or "curl-no-status"
        return http, body, None
    except subprocess.TimeoutExpired:
        return None, None, "curl-timeout"
    except Exception as e:                                   # noqa: BLE001
        return None, None, "curl-error:%s" % e
    finally:
        for p in (tmp, hdr_tmp):
            try:
                os.remove(p)
            except OSError:
                pass


def _fetch_urllib(url, timeout, ua, proxy_http, proxy_https):
    """urllib 兜底。返回 (http, body, err)。

    注意：环境变量里的 http_proxy 会被 urllib 自动读取，而本机环境注入的
    56716 是失效代理。所以【不指定代理时必须显式清空】——用一个把
    proxies 映射为空的 ProxyHandler。
    """
    try:
        if proxy_http or proxy_https:
            handler = urllib.request.ProxyHandler(
                {"http": proxy_http or proxy_https,
                 "https": proxy_https or proxy_http})
        else:
            handler = urllib.request.ProxyHandler({})   # 显式禁用代理
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request(url, headers={
            "User-Agent": ua,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:                                    # noqa: BLE001
            body = None
        return e.code, body, "http-%s" % e.code
    except Exception as e:                                   # noqa: BLE001
        return None, None, "urllib-error:%s" % str(e)[:160]


def fetch(url, cfg=None, timeout=None, retries=None, min_interval=None,
          force_channel=None, ua=None, ua_ladder=None,
          method=None, data=None, headers=None,
          cookie_jar=None, cookie_write=None, egress=None):
    """抓取 URL，返回 FetchResult。

    cfg: 整个 config dict（读 request 块）。可全部用显式参数覆盖。
    force_channel: 'curl' | 'urllib' | None(自动)
    ua: 显式指定 User-Agent（覆盖 cfg）
    ua_ladder: UA 阶梯；某档失败自动换下一档。None 时读 cfg。
    egress: 按**单个数据源**覆盖出口。None=沿用全局实测决策（默认，与既有源
            逐字相同）；"proxy"=强制走系统代理；"direct"=强制直连。

    【为什么要按源覆盖】全局 choose_egress 只回答「直连能不能通」这一个问题，
    隐含假设是全网出口一致。但本机实测不是：直连能通 FRED / ArcGIS，唯独
    Yahoo 会被网关换成一张 HTML 拦截页（拿不到 200）。这种混合拓扑必须在源上
    声明，否则要么全局挂代理拖慢所有源，要么某个源永远抓不到。

    method/data/headers/cookie_jar/cookie_write: 可选，默认为 None ——
      此时 argv 与「只有 GET」时逐字相同，所有既有源零影响。
      传了 data 或非 GET method 时只走 curl 通道（urllib 兜底不支持 POST），
      且传入 cookie_write 可把站点下发的 cookie 落盘，供同一流程的下一步复用。

    【UA 阶梯的由来（本项目第二贵的一课）】
      实测 2026-09：
        FRED   —— 封浏览器 UA（Mozilla/5.0 / Chrome）→ 0 字节超时；
                  放行 curl/* 与 Python-urllib/*。
        Datawrapper —— 封 Python-urllib/* → 403；放行 curl/* 与浏览器 UA。
        ArcGIS —— 都放行。
      所以【没有单一 UA 能让所有源满意】，而失败表现是「超时」或「403」——
      看起来像网络问题，实为 UA 指纹。策略：默认用 curl 风格 UA（三源全通），
      失败时按阶梯降级重试，并在报告里披露实际生效的 UA。
    """
    cfg = cfg or {}
    req = cfg.get("request", {})
    timeout = timeout or req.get("timeout_sec", 40)
    retries = retries if retries is not None else req.get("retries", 3)
    backoff = req.get("retry_backoff_sec", [2, 5, 10])
    if min_interval is None:
        min_interval = req.get("min_interval_per_host_sec", 1.2)

    # UA 阶梯：None 表示用 curl 内置默认 UA
    if ua_ladder is None:
        ua_ladder = req.get("ua_ladder") or [req.get("user_agent", "curl/8.19.0")]
    if ua is not None:
        ua_ladder = [ua]
    # 规范化：空字符串 -> None（curl 默认 UA）
    ua_ladder = [(_ua if _ua else None) for _ua in ua_ladder]

    proxy_http, proxy_https = choose_egress(cfg)
    if egress == "proxy":
        # 强制走系统代理。代理不可用就**明确失败**，绝不静默回落直连 ——
        # 静默回落正是「这个源今天没数据、但没人知道为什么」的来源。
        _ph, _ps = _raw_system_proxy()
        _cand = _ps or _ph
        if not _cand:
            return FetchResult(False, url, "none", None, b"",
                               "egress-proxy-required-but-no-system-proxy", 0.0)
        proxy_http, proxy_https = _ph, _ps
    elif egress == "direct":
        proxy_http, proxy_https = None, None
    attempts = []
    last = None
    n_round = max(1, retries)
    # 非 GET 请求只走 curl：urllib 兜底没实现 POST，硬塞会发成 GET 而拿到
    # 一个「看起来成功」的错响应 —— 这是最容易骗过校验的一类失败。
    is_post = bool(data) or (bool(method) and method.upper() != "GET")

    for attempt in range(n_round):
        cur_ua = ua_ladder[min(attempt, len(ua_ladder) - 1)]
        _throttle(url, min_interval)
        t0 = time.time()

        if force_channel != "urllib":
            http, body, err = _fetch_curl(url, timeout, cur_ua, proxy_https,
                                          method=method, data=data,
                                          headers=headers,
                                          cookie_jar=cookie_jar,
                                          cookie_write=cookie_write)
            # 假 200 检测：状态码 200 但 body 为空
            if http == 200 and body:
                r = FetchResult(True, url, "curl", http, body, None,
                                time.time() - t0)
                r.ua = cur_ua
                r.attempts = attempts
                return r
            last = FetchResult(False, url, "curl", http, body,
                               err or ("empty-body" if http == 200 else "no-200"),
                               time.time() - t0)
            last.ua = cur_ua
            if force_channel != "curl" and not is_post:
                http2, body2, err2 = _fetch_urllib(url, timeout, cur_ua,
                                                   proxy_http, proxy_https)
                if http2 == 200 and body2:
                    r = FetchResult(True, url, "urllib", http2, body2, None,
                                    time.time() - t0)
                    r.ua = cur_ua
                    r.attempts = attempts
                    return r
                if http2 is not None and last.http is None:
                    last = FetchResult(False, url, "urllib", http2, body2,
                                       err2, time.time() - t0)
                    last.ua = cur_ua
        else:
            http2, body2, err2 = _fetch_urllib(url, timeout, cur_ua,
                                               proxy_http, proxy_https)
            if http2 == 200 and body2:
                r = FetchResult(True, url, "urllib", http2, body2, None,
                                time.time() - t0)
                r.ua = cur_ua
                r.attempts = attempts
                return r
            last = FetchResult(False, url, "urllib", http2, body2, err2,
                               time.time() - t0)
            last.ua = cur_ua

        attempts.append({"attempt": attempt + 1, "ua": cur_ua or "(curl-default)",
                         "channel": last.channel, "http": last.http,
                         "error": last.error})
        if attempt < n_round - 1:
            time.sleep(backoff[min(attempt, len(backoff) - 1)])

    if last is not None:
        last.attempts = attempts
    return last


# ----------------------------------------------------------------------------
# 原子写入
# ----------------------------------------------------------------------------

# 写入降级记录：目标文件被别人占用（用户用 WPS/Excel 打开着很常见）时，
# 退写到带时间戳的备用名，并在这里记账，供报告与对账环节显式披露。
WRITE_FALLBACKS = []
# 「rename-over 被拒但文件可写」时改用就地覆盖的记账（见 atomic_write_bytes 第 2 档）。
# 与 WRITE_FALLBACKS 分开：降级写是「换了文件名」，就地覆盖是「没换名但失去了原子性」，
# 两者的下游影响完全不同，混在一起披露会说不清。
INPLACE_WRITES = []
# 清理旧降级文件时失败的记录（见 _prune_fallbacks）。
# 单独记账而不是并进 WRITE_FALLBACKS：前者是「本轮写盘降级」，后者是
# 「历史副本没清掉」，成因和处置完全不同，混在一起会让披露失去指向。
PRUNE_FAILURES = []


def _fallback_path(path):
    """taco-X.csv -> taco-X.locked-<YYYYMMDD-HHMMSS>.csv"""
    root, ext = os.path.splitext(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return "%s.locked-%s%s" % (root, stamp, ext)


def _prune_fallbacks(path, keep):
    """删掉同 stem 的旧降级文件，只留 ``keep``。

    每撞一次「目标被占用」就会多一个 ``.locked-<时间戳>`` 文件；无人值守日更
    如果长期撞上开着 Excel，目录会被同内容的副本堆满。留下最新那份即可
    （``resolve_actual`` 也只认最新那份）。

    删除失败**必须记账，不许静默吞**。2026-09-22 实测：本机环境的
    safe-delete 钩子会把 ``os.remove`` 转成回收站操作，并在部分文件上直接
    失败（``trash-failed``）。原先这里 `except OSError: pass`，结果
    「清理被拦住了」和「本来就没什么可清」在外部看起来一模一样 ——
    排查时误判成代码逻辑缺陷，白花了一轮。失败的路径记进 ``PRUNE_FAILURES``，
    由披露环节显式说出来。
    """
    d = os.path.dirname(path) or "."
    stem, ext = os.path.splitext(os.path.basename(path))
    keep = os.path.abspath(keep)
    if not os.path.isdir(d):
        return []
    removed = []
    for f in os.listdir(d):
        if not (f.startswith(stem + ".locked-") and f.endswith(ext)):
            continue
        p = os.path.abspath(os.path.join(d, f))
        if p == keep:
            continue
        try:
            os.remove(p)
            removed.append(p)
        except OSError as e:
            PRUNE_FAILURES.append({"path": p, "reason": str(e)[:160]})
    return removed


def resolve_actual(path):
    """返回**实际生效**的同名文件路径。

    目标被 WPS/Excel 占用时会退写 ``<名>.locked-<时间戳><ext>``（见
    ``atomic_write_bytes``），此时**标准名上留的是上一轮的旧内容**。任何
    「回读落盘产物」的校验（对账、验收）都必须先过这里，否则会拿过期数据
    去校验、报出「列缺失」这种假故障 —— 2026-09-22 实测踩到。

    判定：标准名与所有同 stem 的 ``.locked-*`` 比 mtime，取最新那份。
    返回 ``(实际路径, 是否发生了降级)``；都不存在时返回 ``(path, False)``。
    """
    d = os.path.dirname(path) or "."
    stem, ext = os.path.splitext(os.path.basename(path))
    cands = [path]
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.startswith(stem + ".locked-") and f.endswith(ext):
                cands.append(os.path.join(d, f))
    cands = [c for c in cands if os.path.exists(c)]
    if not cands:
        return path, False
    # mtime 相同时优先取**降级文件**。
    # 降级文件的存在本身说明「写标准名那次失败了」，所以它是更近一次写入的产物；
    # 而 `max` 在同值时返回列表里靠前的元素（= 标准名），会把过期内容当成最新的。
    # 为什么同值是现实问题：本机 NTFS 时间戳粒度约 40ms，先后写出的两份文件
    # 实测有 50% 概率拿到完全相同的 mtime。
    newest = max(cands, key=lambda c: (os.path.getmtime(c), c != path))
    return newest, newest != path


def promote_fallback(path):
    """把上一轮因占用而退写的 ``<名>.locked-*`` 补写回标准名。

    为什么需要这一步：退写机制保证「数据不丢」，但**不保证标准名会追上**。
    若日更任务长期撞上「用户开着 WPS」，标准名会一直停在很旧的内容上，
    新数据全堆在 ``.locked-*`` 里 —— 按标准名读的下游与肉眼看到的都是过期数据
    （2026-09-22 实测：标准名停在 09:43 的旧内容，且那一版第 6 因子列还是空的）。

    所以每轮写盘前先做一次补写：只要标准名现在可写、且存在比它更新的降级文件，
    就把最新的那份挪正。标准名仍被占用时安全跳过（留待下一轮）。

    返回 ``(是否补写, 说明)``。
    """
    d = os.path.dirname(path) or "."
    if not os.path.isdir(d):
        return False, "目录不存在: %s" % d
    stem, ext = os.path.splitext(os.path.basename(path))
    fbs = [os.path.join(d, f) for f in os.listdir(d)
           if f.startswith(stem + ".locked-") and f.endswith(ext)]
    if not fbs:
        return False, "无降级文件"
    newest = max(fbs, key=os.path.getmtime)
    # mtime 相同也要补写（用 >=，不是 >）。
    # 理由：本函数只在**一轮写入开始之前**被调用（见 taco_monitor 产物环节），
    # 此刻还能看到降级文件，就说明「上一次把内容写到标准名的尝试失败了」，
    # 标准名顶多和降级文件一样新（同一时刻写出的两份），不可能更新。
    # 本机 NTFS 实测时间戳粒度约 40ms：连续写出的两个文件会拿到**完全相同**的
    # mtime（12 次实验里 6 次相同）—— 旧的 `>` 判定会在这种情况下拒绝补写，
    # 让真正更新的内容永远补不回标准名，正是本机制要防的事。
    if os.path.exists(path) and os.path.getmtime(path) > os.path.getmtime(newest):
        return False, "标准名已是最新"
    try:
        os.replace(newest, path)
    except OSError as e:
        return False, "标准名仍被占用（%s）" % type(e).__name__
    return True, "已补写 %s -> %s" % (os.path.basename(newest),
                                      os.path.basename(path))


def atomic_write_bytes(path, data, retries=4, backoff=(0.3, 0.8, 1.8, 3.0)):
    """尽量原子地写字节。三级策略，从最安全到最可用。

    无人值守纪律（2026-09-22 实测踩到）：目标文件被别人开着时，
    ``os.replace`` 抛 ``PermissionError[WinError 5]``，整个日更任务就此中断 ——
    而用户只是打开看了一眼 CSV。故按级降级，绝不中断整轮：

      1. 写同目录 ``.tmp`` 后 ``os.replace``（原子），带指数退避重试
         —— 多数占用是瞬时的，如另存/重算；
      2. **就地覆盖**（``r+b`` + truncate + fsync）。到这一档说明
         「rename-over 被拒但文件本身可写」。实测这是独立于「被独占」的一种
         状态：2026-09-22 本机 ``taco-latest.csv`` 反复 WinError 5，而同目录
         的 ``taco_index.xlsx`` 与新建文件都能正常 replace，且没有 WPS/Excel
         进程在跑；对前者 ``open('r+b')`` 明明成功。
         为什么必须补这一档：只降级成 ``.locked-*`` 会让**标准名长期停在旧
         内容上**，而标准名正是下游和肉眼读的那个（本轮实测标准名停在 09:43
         的 17 列旧版，而实际已是 27 列）。宁可牺牲原子性也不能让标准名是错的。
      3. 连就地写也失败，才退写成 ``<name>.locked-<时间戳><ext>``。

    记账：第 2 档记入 ``INPLACE_WRITES``，第 3 档记入 ``WRITE_FALLBACKS``，
    都由报告显式披露 —— 静默的降级等于没有降级。

    返回值是**实际写入的路径**（可能是降级路径），调用方不要假定等于入参。
    """
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)

    last = None
    for attempt in range(retries + 1):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError as e:
            last = e
            if attempt < retries:
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
        except OSError as e:
            last = e
            break

    # --- 第 2 档：就地覆盖（保住标准名，放弃原子性）---
    try:
        with open(path, "r+b") as f:
            f.seek(0)
            f.write(data)          # 一次 write，减少被撕裂的窗口
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
        INPLACE_WRITES.append({"path": path, "reason": str(last)[:160]})
        try:
            os.remove(tmp)
        except OSError:
            pass
        return path
    except OSError:
        pass                        # 落到第 3 档

    alt = _fallback_path(path)
    try:
        os.replace(tmp, alt)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise last
    # 只留最新那份降级文件，避免长期被占用时堆满同内容副本。
    # 被清掉的旧副本要同时从记账里摘掉，否则披露环节会引用一个已不存在的文件。
    removed = set(_prune_fallbacks(path, alt))
    if removed:
        WRITE_FALLBACKS[:] = [w for w in WRITE_FALLBACKS
                              if os.path.abspath(w["actual"]) not in removed]
    WRITE_FALLBACKS.append({"intended": path, "actual": alt,
                            "reason": str(last)[:160]})
    return alt


def atomic_write_text(path, text, encoding="utf-8-sig"):
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path, obj, indent=2):
    return atomic_write_text(path, json.dumps(obj, ensure_ascii=False,
                                              indent=indent), "utf-8")


# ----------------------------------------------------------------------------
# 内容判定（假 200 识别）
# ----------------------------------------------------------------------------

JS_CHALLENGE_MARKERS = (
    b"<html",
    b"<!doctype html",
    b"enable javascript",
    b"cf-browser-verification",
    b"challenge-platform",
    b"__cf_chl",
)


def looks_like_html(body):
    if not body:
        return False
    head = body[:2048].lower()
    return any(m in head for m in JS_CHALLENGE_MARKERS)


def looks_like_challenge(body):
    """Stooq 式的 JS 挑战页：每个符号返回同一个固定大小的 HTML。"""
    if not looks_like_html(body):
        return False
    head = body[:2048].lower()
    return (b"enable javascript" in head
            or b"cf-browser-verification" in head
            or b"challenge-platform" in head)


def validate_payload(body, kind):
    """按内容校验载荷是否可信。返回 (ok, reason)。"""
    if not body:
        return False, "empty-body"
    if looks_like_challenge(body):
        return False, "js-challenge-page"
    if kind == "csv":
        head = body[:4096].decode("utf-8", "replace")
        first = head.splitlines()[0] if head.splitlines() else ""
        if "," not in first:
            return False, "csv-no-comma-header"
        if looks_like_html(body):
            return False, "csv-is-html"
        return True, "csv-ok"
    if kind == "arcgis_json":
        try:
            d = json.loads(body.decode("utf-8"))
        except Exception as e:                               # noqa: BLE001
            return False, "json-parse:%s" % str(e)[:80]
        if "error" in d:
            return False, "arcgis-error:%s" % str(d["error"])[:160]
        if "features" not in d:
            return False, "json-no-features"
        return True, "arcgis-ok(%d features)" % len(d["features"])
    if kind == "sina_jsonp":
        text = body[:65536].decode("utf-8", "replace")
        if "var t=([" not in text:
            return False, "jsonp-envelope-missing(not-a-sina-daily-kline)"
        return True, "sina-jsonp-ok"
    if kind == "yahoo_chart":
        if looks_like_html(body):
            # 网关 / 地区拦截会返回一个 HTML 页面而不是 4xx。直连被拦时
            # 就是这个形态，必须显式指出来，否则会被当成「JSON 解析失败」
            # 而浪费半天去查解析器。
            head = body[:2048].decode("utf-8", "replace").lower()
            hint = ("yahoo-blocked-html(疑似网关/地区拦截，请检查出口代理)"
                    if ("yahoo" in head or "无法" in head)
                    else "yahoo-html-not-json")
            return False, hint
        try:
            d = json.loads(body.decode("utf-8"))
        except Exception as e:                               # noqa: BLE001
            return False, "json-parse:%s" % str(e)[:80]
        ch = d.get("chart")
        if not isinstance(ch, dict):
            return False, "yahoo-no-chart-key"
        if ch.get("error"):
            return False, "yahoo-error:%s" % str(ch["error"])[:160]
        res = ch.get("result") or []
        if not res:
            return False, "yahoo-empty-result"
        bars = res[0].get("timestamp") or []
        if not bars:
            return False, "yahoo-no-timestamps"
        return True, "yahoo-chart-ok(%d bars)" % len(bars)
    if kind == "json":
        try:
            json.loads(body.decode("utf-8"))
            return True, "json-ok"
        except Exception as e:                               # noqa: BLE001
            return False, "json-parse:%s" % str(e)[:80]
    return True, "unchecked"


def egress_report():
    """探测当前出口（供报告头部打印，别靠记忆，靠打印）。"""
    out = {"env_RM_PROXY": os.environ.get("RM_PROXY", "") or None}
    ph, ps = system_proxy()
    out["http_proxy"] = ph
    out["https_proxy"] = ps
    try:
        r = fetch("https://api.ipify.org?format=json", {"request": {
            "user_agent": "Mozilla/5.0", "timeout_sec": 15, "retries": 1,
            "min_interval_per_host_sec": 0}}, timeout=15, retries=1,
            min_interval=0)
        if r.ok:
            out["egress_ip"] = json.loads(r.body.decode("utf-8")).get("ip")
            out["egress_channel"] = r.channel
        else:
            out["egress_ip"] = None
            out["egress_error"] = r.error
    except Exception as e:                                   # noqa: BLE001
        out["egress_error"] = str(e)[:120]
    return out


if __name__ == "__main__":
    r = fetch(sys.argv[1] if len(sys.argv) > 1 else
              "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2026-09-01")
    print(r)
    if r.ok:
        print(r.body[:400].decode("utf-8", "replace"))
