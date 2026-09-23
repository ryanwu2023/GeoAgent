# -*- coding: utf-8 -*-
"""bonbast.py —— Bonnast（德黑兰自由市场）USD 卖出价的专项抓取。

范围纪律
--------
本项目**只**取一件东西：``Bonbast_USD卖出_里亚尔``（USD/IRR 卖出价）。
同站还有 EUR/CNY/金币/比特币等几十个字段，一律不取 —— 取多了就会有人
拿「金币托曼」当「汇率里亚尔」用，单位混用是这类多字段接口的经典事故。

抓取链路（两步，均已实测）
--------------------------
1. ``GET https://www.bonbast.com/``
   首页内嵌一次性令牌::

       $.post('/json', {param: "<hash>,<tok>,<YYYY-MM-DD-HH-MM-SS>"})

   令牌每次加载都变，必须先取首页再正则提取。
2. ``POST https://www.bonbast.com/json``  表单 ``param=<令牌>``
   返回 80 个字段的 JSON，其中 ``usd1`` = USD **卖出价**、``usd2`` = 买入价。

历史（这一步决定了本因子的可用长度）
------------------------------------
``GET https://www.bonbast.com/graph/usd`` 页面内嵌 ``labels`` / ``data``
两条数组，固定 **60 天**日频。实测 ``?range=1y`` / ``?period=1y`` 无效
（仍返回 60 天）、``/graph/usd/365`` 直接 404 —— **没有更长的免费历史**。
所以第 7 因子天然只有近 60 天，报告里必须显式披露，不能让它看起来像
「和 5 因子一样长」。

单位（极容易错，故三重固化）
----------------------------
页面明写 ``All prices are in Iranian Toman (1 Toman = 10 Rials)``。
接口返回的是**托曼**，而本因子口径是**里亚尔**，故一律 ``× 10``。
三重防线：常量 ``TOMAN_TO_RIAL``、``_sanity`` 值域带、以及与图序列的
同源交叉校验（两条链路都错成同一倍数的概率极低）。

令牌失效的坑（比参考实现多修一处）
----------------------------------
令牌失效时服务端返回 ``{"rest":"1"}`` —— 注意是 **rest** 而不是 ``reset``。
只判 ``"reset"`` 会把它当成一份「合法但没有币种」的载荷：重试逻辑完全不
触发，最终只报一句「解析后没有任何币种数据」，看起来像站点改版。故本模块
两个键都判。
"""

from __future__ import annotations

import json
import os
import re
import tempfile

from . import net

# 页面明写 1 Toman = 10 Rials
TOMAN_TO_RIAL = 10

# 令牌：主正则锚在 $.post('/json', {param: "..."
_TOKEN_RE = re.compile(
    r"\$\.post\(\s*['\"]/json['\"]\s*,\s*\{\s*param\s*:\s*['\"]([^'\"]+)['\"]")
# 兜底：任何 param: "..." 形式的较长字符串
_TOKEN_FALLBACK_RE = re.compile(r"param\s*:\s*['\"]([^'\"]{8,})['\"]")
_LABELS_RE = re.compile(r"labels\s*:\s*\[(.*?)\]", re.S)
_DATA_RE = re.compile(r"data\s*:\s*\[([^\]]+)\]", re.S)
_LABEL_DATE_RE = re.compile(r"new Date\('([\d\-]+)'\)")

# 令牌失效响应：实测站点写的是 "rest"（站点侧拼写），旧实现只判 "reset" 会漏。
_TOKEN_RESET_KEYS = ("rest", "reset")

# 值域护栏（托曼）—— 两级，故意分开，因为两者的失败含义不同：
#
# 硬区间（越界 = FAIL）：只拦「解析出 0 / 1 / 负数 / 抓到非汇率字段 / 站点改版」。
#   区间必须留得极宽，否则会随里亚尔贬值而误杀。
# 量级区间（越界 = WARN，不阻断）：USD/IRR 自由市场的经验量级。
#   实测锚点（同日两条独立链路互相印证，2026-09-22）：
#     Bonbast 快照 usd1 = 229600 托曼
#     本项目参考实现 iran-condition-monitor 的 fx_daily
#       -> bonbast_usd_sell_toman = 229600 / bonbast_usd_sell_rial = 2296000
#     页面自述：All prices are in Iranian Toman (1 Toman = 10 Rials)
#   任一链路把托曼当里亚尔（×10）都会掉出这个区间 —— 这正是硬区间
#   拦不住、必须靠量级区间提示的那类错误。
_VALUE_FLOOR_TOMAN = 1_000.0
_VALUE_CEIL_TOMAN = 100_000_000.0
_MAG_FLOOR_TOMAN = 50_000.0        # = 50 万里亚尔
_MAG_CEIL_TOMAN = 1_000_000.0      # = 1000 万里亚尔

# 图序列与快照的同源交叉校验容差。两条链路取的是同一个市场的同一个报价，
# 日频值允许小幅跳动，但不该差出一个量级。
_CROSSCHECK_TOL = 0.05

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"))}
_TS_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s+(\d{1,2}):(\d{2})")


def _num(value):
    """把接口值转成 float。兼容千分位、波斯数字、空值与占位符 '-'。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in ("-", "—", "N/A", "n/a", "null"):
        return None
    # 波斯/阿拉伯数字 -> ASCII
    text = text.translate({ord(c): str(i) for i, c in enumerate(
        "۰۱۲۳۴۵۶۷۸۹")})
    text = text.translate({ord(c): str(i) for i, c in enumerate(
        "٠١٢٣٤٥٦٧٨٩")})
    text = text.replace(",", "").replace(" ", "")
    try:
        return float(text)
    except ValueError:
        return None


def _extract_token(html):
    """从首页 HTML 提取一次性令牌。"""
    if not html:
        return None
    m = _TOKEN_RE.search(html)
    if m:
        return m.group(1)
    m = _TOKEN_FALLBACK_RE.search(html)
    return m.group(1) if m else None


def _parse_ts(text):
    """``September 22, 2026 02:41`` -> ``2026-09-22T02:41``（原样，不做时区猜测）。"""
    if not text:
        return None
    m = _TS_RE.search(str(text))
    if not m:
        return None
    mon = _MONTHS.get(m.group(1))
    if not mon:
        return None
    return "%s-%02d-%02dT%02d:%02d" % (m.group(3), mon, int(m.group(2)),
                                       int(m.group(4)), int(m.group(5)))


def _sanity(value_toman, where):
    """硬护栏（越界 = FAIL）。返回 (ok, reason)。"""
    if value_toman is None:
        return False, "%s: 数值为空" % where
    if not (_VALUE_FLOOR_TOMAN <= value_toman <= _VALUE_CEIL_TOMAN):
        return False, ("%s: 数值 %.0f 托曼落在硬区间 [%.0f, %.0f] 之外，"
                       "疑为解析错误或站点结构变更"
                       % (where, value_toman, _VALUE_FLOOR_TOMAN,
                          _VALUE_CEIL_TOMAN))
    return True, "ok"


def _magnitude_warn(value_toman, where):
    """量级护栏（越界 = WARN，不阻断）。返回告警文案或 None。

    单独成级的原因：硬区间刻意留得极宽，拦不住「整条链路一起差 10 倍」
    （托曼当里亚尔）这种同口径偏移；而量级区间能立刻提示它。
    但它也可能随汇率长期单边走弱而误报，所以只告警、不阻断出数。
    """
    if value_toman is None:
        return None
    if not (_MAG_FLOOR_TOMAN <= value_toman <= _MAG_CEIL_TOMAN):
        return ("%s: 数值 %.0f 托曼落在经验量级 [%.0f, %.0f] 之外 —— "
                "请核对单位（托曼 vs 里亚尔，差 10 倍）或数据源口径"
                % (where, value_toman, _MAG_FLOOR_TOMAN, _MAG_CEIL_TOMAN))
    return None


def fetch_snapshot(cfg):
    """抓当日快照，只取 USD 卖出价。

    返回 dict：
      ok / value_toman / value_rial / quote_ts / fetched_date / warnings
      raw: {"home": bytes, "json": bytes}（供归档留证）
    """
    out = {"ok": False, "value_toman": None, "value_rial": None,
           "quote_ts": None, "fetched_date": None, "warnings": [],
           "raw": {}, "attempts": 0}
    base = str(cfg.get("base_url", "https://www.bonbast.com")).rstrip("/")
    home_url = base + "/"
    json_url = base + str(cfg.get("json_endpoint", "/json"))
    ua = str(cfg.get("user_agent") or
             "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
    attempts = int(cfg.get("max_reset_retries", 4))

    # cookie 必须跨两步保留：不带 jar 时 /json 会直接回令牌失效。
    fd, jar = tempfile.mkstemp(prefix="bonbast_ck_", suffix=".txt")
    os.close(fd)
    try:
        for i in range(1, attempts + 1):
            out["attempts"] = i
            r = net.fetch(home_url, cfg, ua=ua, cookie_jar=jar,
                          cookie_write=jar, retries=1)
            if not r.ok:
                out["warnings"].append("bonbast: 首页请求失败 %s" % r.error)
                continue
            out["raw"]["home"] = r.body

            token = _extract_token(r.body.decode("utf-8", "replace"))
            if not token:
                out["warnings"].append(
                    "bonbast: 第 %d 次未能在首页找到令牌" % i)
                continue

            resp = net.fetch(
                json_url, cfg, ua=ua, cookie_jar=jar, cookie_write=jar,
                method="POST", data="param=" + token, retries=1,
                headers={"Origin": base, "Referer": home_url,
                         "X-Requested-With": "XMLHttpRequest",
                         "Content-Type":
                             "application/x-www-form-urlencoded; charset=UTF-8"})
            if not resp.ok:
                out["warnings"].append("bonbast: POST /json 失败 %s" % resp.error)
                continue
            out["raw"]["json"] = resp.body

            try:
                data = json.loads(resp.body.decode("utf-8"))
            except ValueError:
                out["warnings"].append("bonbast: /json 响应不是合法 JSON")
                continue
            if not isinstance(data, dict):
                out["warnings"].append("bonbast: /json 响应不是对象")
                continue

            # 令牌失效：站点返回 {"rest": "1"}（另兼容 "reset" 写法）
            if any(k in data for k in _TOKEN_RESET_KEYS):
                out["warnings"].append(
                    "bonbast: 第 %d 次令牌失效（%s），重取首页"
                    % (i, ",".join(k for k in _TOKEN_RESET_KEYS if k in data)))
                continue

            sell = _num(data.get("usd1"))
            ok, why = _sanity(sell, "bonbast 快照 usd1")
            if not ok:
                out["warnings"].append(why)
                continue
            mw = _magnitude_warn(sell, "bonbast 快照 usd1")
            if mw:
                out["warnings"].append(mw)

            out["value_toman"] = sell
            out["value_rial"] = sell * TOMAN_TO_RIAL
            out["quote_ts"] = _parse_ts(data.get("last_modified"))
            out["fetched_date"] = _parse_ts(data.get("last_modified") or "")
            if out["fetched_date"]:
                out["fetched_date"] = out["fetched_date"][:10]
            out["ok"] = True
            return out

        out["warnings"].append(
            "bonbast: 快照取数失败（已尝试 %d 次）" % attempts)
    finally:
        for p in (jar,):
            try:
                os.remove(p)
            except OSError:
                pass
    return out


def fetch_graph_usd(cfg):
    """抓 ``/graph/usd`` 的近 60 天日频卖出价。

    返回 dict：ok / series {date: value_toman} / span_note / warnings / raw
    """
    out = {"ok": False, "series": {}, "span_note": None, "warnings": [],
           "raw": {}}
    base = str(cfg.get("base_url", "https://www.bonbast.com")).rstrip("/")
    pair = str(cfg.get("graph_pair", "usd"))
    url = "%s%s/%s" % (base, str(cfg.get("graph_endpoint", "/graph")), pair)

    r = net.fetch(url, cfg, retries=int(cfg.get("graph_retries", 3)))
    if not r.ok:
        out["warnings"].append("bonbast_graph: 请求失败 %s" % r.error)
        return out
    out["raw"]["graph"] = r.body
    html = r.body.decode("utf-8-sig", "replace")

    m_labels = _LABELS_RE.search(html)
    if not m_labels:
        out["warnings"].append(
            "bonbast_graph: 未找到 labels 序列（站点结构可能已变）")
        return out
    m_data = _DATA_RE.search(html, m_labels.end())
    if not m_data:
        out["warnings"].append("bonbast_graph: 未找到 data 序列")
        return out

    dates = _LABEL_DATE_RE.findall(m_labels.group(1))
    values = [_num(v) for v in m_data.group(1).split(",")]
    if len(dates) != len(values):
        out["warnings"].append(
            "bonbast_graph: 日期数(%d)与数值数(%d)不一致，按较短者对齐"
            % (len(dates), len(values)))

    series = {}
    bad = 0
    off_mag = 0
    for d, v in zip(dates, values):
        ok, why = _sanity(v, "bonbast_graph %s" % d)
        if not ok:
            bad += 1
            continue
        if _magnitude_warn(v, "bonbast_graph %s" % d):
            off_mag += 1
        series[d] = v
    if bad:
        out["warnings"].append(
            "bonbast_graph: %d 个点未通过硬值域护栏，已剔除" % bad)
    if off_mag:
        out["warnings"].append(
            "bonbast_graph: %d 个点落在经验量级之外（单位/口径待核对）"
            % off_mag)
    if not series:
        out["warnings"].append("bonbast_graph: 解析后无有效点")
        return out

    keys = sorted(series)
    out["series"] = series
    out["ok"] = True
    out["span_note"] = {
        "first": keys[0], "last": keys[-1], "n": len(keys),
        "cap_days": 60,
        "note": "站点 /graph 固定给 %d 天，无 range 参数可用 —— "
                "%s 之前的历史取不到。" % (60, keys[0]),
    }
    return out


def collect(cfg, run_date):
    """抓取 Bonbast USD 卖出价（里亚尔），返回统一结构。

    返回 dict：
      ok / series {date: value_rial} / snapshot / graph / warnings
      / crosscheck / unit / source / label
    失败时 ok=False，warnings 说明原因；调用方应降级为「不出第 7 因子」，
    绝不拿空序列进指数。
    """
    bcfg = dict(cfg.get("bonbast_crawler") or {})
    out = {"ok": False, "series": {}, "snapshot": None, "graph": None,
           "warnings": [], "crosscheck": None, "unit": "rial",
           "source": "bonbast", "label": bcfg.get("label"),
           "toman_to_rial": TOMAN_TO_RIAL, "raw": {}}

    if not bcfg.get("enabled", True):
        out["warnings"].append("bonbast: config 中已禁用")
        return out

    snap = fetch_snapshot(bcfg)
    graph = fetch_graph_usd(bcfg)
    out["snapshot"] = {k: v for k, v in snap.items() if k != "raw"}
    out["graph"] = {k: v for k, v in graph.items() if k != "raw"}
    out["warnings"] += snap["warnings"] + graph["warnings"]
    out["raw"].update(snap.get("raw") or {})
    out["raw"].update(graph.get("raw") or {})

    # 图序列（托曼 -> 里亚尔），快照按日期覆盖同一天（快照更权威：它带分钟级
    # 时间戳，且是「当日」口径；图序列是日终序列，可能滞后一天）。
    series = {d: v * TOMAN_TO_RIAL for d, v in (graph.get("series") or {}).items()}
    snap_date = (snap.get("quote_ts") or "")[:10] or run_date
    if snap["ok"]:
        series[snap_date] = snap["value_rial"]

    # 同源交叉校验：两条链路取的是同一市场同一报价。差异过大说明其中一条
    # 抓错了（单位/币种/陈旧页）—— 这是本模块最重要的一道静默错误防线。
    out["crosscheck"] = _crosscheck(graph.get("series") or {}, snap)

    if not series:
        out["warnings"].append(
            "bonbast: 快照与图序列都没有可用数据，第 7 因子不参与本轮")
        return out

    keys = sorted(series)
    out["series"] = series
    out["ok"] = True
    out["span"] = {"first": keys[0], "last": keys[-1], "n": len(keys)}
    return out


def _crosscheck(graph_toman, snap):
    """快照 vs 图序列末值 的同源校验（同在托曼口径比较）。"""
    res = {"ok": None, "detail": None}
    if not snap.get("ok") or not graph_toman:
        res["detail"] = "任一侧缺失，跳过交叉校验"
        return res
    last_date = max(graph_toman)
    last_val = graph_toman[last_date]
    snap_val = snap["value_toman"]
    if not last_val or not snap_val:
        res["detail"] = "任一侧为空，跳过"
        return res
    diff = abs(snap_val - last_val) / last_val
    res["graph_last_date"] = last_date
    res["graph_last_toman"] = last_val
    res["snapshot_toman"] = snap_val
    res["rel_diff"] = diff
    res["ok"] = diff <= _CROSSCHECK_TOL
    res["detail"] = ("快照 %.0f vs 图序列(%s) %.0f 托曼，相对差 %.2f%%"
                     % (snap_val, last_date, last_val, 100 * diff))
    if not res["ok"]:
        res["detail"] += " —— 超过 %.0f%% 容差，两条链路可能不在同一口径" \
                         % (100 * _CROSSCHECK_TOL)
    return res


# ---------------------------------------------------------------------------
# 原始载荷留档 / 离线重放
# ---------------------------------------------------------------------------
# 与 cfg["series"] 里的普通源不同：Bonnast 要两步抓取，载荷不是一个 URL 一个
# 文件。故单独给三个固定名，--from-file replay 时按名读回，走**同一套解析器**
# （不与在线路径分叉 —— 否则离线重放通过的代码上线就崩）。
RAW_HOME = "BONBAST_USD_sell_rial__bonbast_home.html"
RAW_JSON = "BONBAST_USD_sell_rial__bonbast_json.json"
RAW_GRAPH = "BONBAST_USD_sell_rial__bonbast_graph.html"


def raw_files():
    """返回落盘用的 {键: 文件名}。"""
    return {"home": RAW_HOME, "json": RAW_JSON, "graph": RAW_GRAPH}


def parse_archived(raw_dir, run_date):
    """从留档载荷离线重放（不联网）。**复用** collect() 里的同一套解析。"""
    out = {"ok": False, "series": {}, "snapshot": None, "graph": None,
           "warnings": [], "crosscheck": None, "unit": "rial",
           "source": "bonbast", "toman_to_rial": TOMAN_TO_RIAL,
           "label": None, "raw": {}, "from_file": raw_dir}
    home_p = os.path.join(raw_dir, RAW_HOME)
    json_p = os.path.join(raw_dir, RAW_JSON)
    graph_p = os.path.join(raw_dir, RAW_GRAPH)

    snap = {"ok": False, "value_toman": None, "value_rial": None,
            "quote_ts": None, "warnings": [], "attempts": 0}
    if os.path.exists(json_p):
        with open(json_p, "rb") as fh:
            body = fh.read()
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            data = None
        if isinstance(data, dict) and not any(k in data
                                              for k in _TOKEN_RESET_KEYS):
            sell = _num(data.get("usd1"))
            ok, why = _sanity(sell, "bonbast 归档 usd1")
            if ok:
                snap.update(ok=True, value_toman=sell,
                            value_rial=sell * TOMAN_TO_RIAL,
                            quote_ts=_parse_ts(data.get("last_modified")))
            else:
                snap["warnings"].append(why)
        else:
            snap["warnings"].append("bonbast 归档: json 非对象或为令牌失效响应")
    else:
        snap["warnings"].append("bonbast 归档: 缺 %s" % RAW_JSON)

    graph = {"ok": False, "series": {}, "warnings": [], "span_note": None}
    if os.path.exists(graph_p):
        with open(graph_p, "rb") as fh:
            html = fh.read().decode("utf-8-sig", "replace")
        ml = _LABELS_RE.search(html)
        md = _DATA_RE.search(html, ml.end()) if ml else None
        if ml and md:
            series = {}
            for d, v in zip(_LABEL_DATE_RE.findall(ml.group(1)),
                            [_num(x) for x in md.group(1).split(",")]):
                if _sanity(v, "归档 graph %s" % d)[0]:
                    series[d] = v
            if series:
                keys = sorted(series)
                graph.update(ok=True, series=series,
                             span_note={"first": keys[0], "last": keys[-1],
                                        "n": len(keys), "cap_days": 60,
                                        "note": "站点 /graph 固定给 60 天"})
        if not graph["ok"]:
            graph["warnings"].append("bonbast 归档: graph 解析失败")
    else:
        graph["warnings"].append("bonbast 归档: 缺 %s" % RAW_GRAPH)

    out["snapshot"] = snap
    out["graph"] = graph
    out["warnings"] += snap["warnings"] + graph["warnings"]
    out["crosscheck"] = _crosscheck(graph.get("series") or {}, snap)

    series = {d: v * TOMAN_TO_RIAL
              for d, v in (graph.get("series") or {}).items()}
    if snap["ok"]:
        series[(snap.get("quote_ts") or "")[:10] or run_date] = snap["value_rial"]
    if series:
        keys = sorted(series)
        out.update(ok=True, series=series,
                   span={"first": keys[0], "last": keys[-1], "n": len(keys)})
    else:
        out["warnings"].append("bonbast 归档: 无可用数据")
    return out


def selftest():
    """离线自检：解析器 / 正则 / 单位 / 值域护栏 / 令牌失效键。"""
    failures = []

    def ck(label, actual, expected):
        if actual != expected:
            failures.append("%s: 期望 %r，实际 %r" % (label, expected, actual))

    # 数值解析
    ck("_num 千分位", _num("229,600"), 229600.0)
    ck("_num 波斯数字", _num("۲۲۹۶۰۰"), 229600.0)
    ck("_num 字符串", _num("229600"), 229600.0)
    ck("_num 数字型", _num(229600), 229600.0)
    ck("_num 空", _num(None), None)
    ck("_num 占位符", _num("-"), None)

    # 单位：托曼 -> 里亚尔
    ck("单位换算", 229600 * TOMAN_TO_RIAL, 2296000)

    # 令牌提取
    home = ('<script>function get_data(){ $.post(\'/json\', '
            '{param: "963d2d40cd6ae66972475d4709232641,fqSoY,'
            '2026-09-22-02-42-26"}, function(json){')
    ck("令牌提取", _extract_token(home),
       "963d2d40cd6ae66972475d4709232641,fqSoY,2026-09-22-02-42-26")
    ck("令牌缺失", _extract_token("<html>nothing</html>"), None)

    # 令牌失效键：站点写的是 rest，旧实现只判 reset 会漏
    for key in ("rest", "reset"):
        d = {key: "1"}
        if not any(k in d for k in _TOKEN_RESET_KEYS):
            failures.append("令牌失效键 %s 未被识别" % key)

    # 时间戳
    ck("时间戳解析", _parse_ts("September 22, 2026 02:41"),
       "2026-09-22T02:41")
    ck("时间戳解析(单数字日)", _parse_ts("July 4, 2026 9:05"),
       "2026-07-04T09:05")
    ck("时间戳坏输入", _parse_ts("garbage"), None)

    # 硬护栏：拦住解析出 0 / 负数 / 站点改版（区间刻意极宽，不误杀）
    ck("硬护栏 通过", _sanity(229600, "t")[0], True)
    ck("硬护栏 拦住 0", _sanity(0, "t")[0], False)
    ck("硬护栏 拦住 None", _sanity(None, "t")[0], False)
    ck("硬护栏 拦住整数序列号", _sanity(7, "t")[0], False)

    # 量级护栏：硬护栏拦不住「整条链路一起差 10 倍」，必须由它提示
    ck("量级护栏 正常值无告警", _magnitude_warn(229600, "t"), None)
    ck("量级护栏 抓到里亚尔误当托曼",
       bool(_magnitude_warn(2296000, "t")), True)
    ck("量级护栏 抓到托曼误当里亚尔",
       bool(_magnitude_warn(22960, "t")), True)

    # 图序列解析（取自 2026-09-22 实测归档的真实结构）
    graph = ("var chart = new Chart(ctx, { data: { labels: "
             "[new Date('2026-07-24'),new Date('2026-07-25')], "
             "datasets: [{ data: [192200, 188900] }] } });")
    ml = _LABELS_RE.search(graph)
    md = _DATA_RE.search(graph, ml.end()) if ml else None
    ck("图 labels 命中", bool(ml), True)
    ck("图日期数", len(_LABEL_DATE_RE.findall(ml.group(1))) if ml else 0, 2)
    ck("图数值数", len(md.group(1).split(",")) if md else 0, 2)

    # 交叉校验：同源一致 / 差一个量级
    same = _crosscheck({"2026-09-21": 229600.0},
                       {"ok": True, "value_toman": 229600.0})
    ck("交叉校验 一致", same["ok"], True)
    off = _crosscheck({"2026-09-21": 229600.0},
                      {"ok": True, "value_toman": 2296000.0})
    ck("交叉校验 差一个量级", off["ok"], False)

    return failures


if __name__ == "__main__":
    errs = selftest()
    if errs:
        print("bonbast 自检失败：")
        for e in errs:
            print("  ✗", e)
    else:
        print("bonbast 自检通过：解析、令牌、单位、值域护栏、交叉校验均正常。")
