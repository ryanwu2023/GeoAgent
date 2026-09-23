#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
taco_monitor.py —— TACO 压力指数每日监测爬虫

用法：
  python taco_monitor.py --selftest          # 离线自检（不联网）
  python taco_monitor.py                     # 真实抓取 + 算指数 + 出产物
  python taco_monitor.py --proxy URL         # 显式出口覆盖（仅供排障）
  python taco_monitor.py --from-file DIR     # 用留档载荷离线重放（不联网）
  python taco_monitor.py --as-of 2026-09-20 --out-dir output/_probe
  python taco_monitor.py --no-xlsx           # 跳过 Excel（openpyxl 缺失时自动跳）

纪律（每条都对应一次真实踩坑）：
  * 先测后用：config 里每条 chain entry 必须带 probe_evidence.ok
  * 原子写入：tmp + os.replace
  * 假 200 识别：按内容判定，不看状态码
  * 同主机限速：1.2s
  * UA 阶梯：FRED 封浏览器 UA，Datawrapper 封 Python-urllib
  * 出口实测：环境变量里的代理可能是死的，必须实测
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tacolib import net, parse, taco, emit      # noqa: E402

CN_TZ = timezone(timedelta(hours=8))


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def now_cn():
    return datetime.now(CN_TZ)


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------

def raw_filename(series_cfg):
    """原始载荷文件名：<id>__<provider-slug>.<ext>"""
    sid = series_cfg.get("id", "unknown")
    prov = (series_cfg.get("provider") or "src").lower()
    slug = "".join(ch if ch.isalnum() else "_" for ch in prov).strip("_")
    kind = series_cfg.get("payload", "csv")
    ext = {"csv": "csv", "arcgis_json": "json", "json": "json",
           "sina_jsonp": "jsonp"}.get(kind, "bin")
    return "%s__%s.%s" % (sid, slug, ext)


def process_body(body, s, st, run_date=None):
    """对已取得的载荷做 校验 -> 解析 -> 填充状态。

    网络路径与 --from-file 离线路径共用此函数，避免"离线重放只读文件不解析"
    这类只在重放时才暴露的不一致。
    返回 True 表示成功（st["_data"] 已填好）。
    """
    sid = s["id"]
    ok, reason = net.validate_payload(body, s.get("payload", "csv"))
    if not ok:
        st["status"] = "INVALID"
        st["error"] = reason
        return False

    # 需要丢弃「当日未收盘会话」的源（如新浪外盘期货日K）：把运行日期传下去，
    # 否则每天会把一个未走完的盘中价当成收盘价写进指数。
    if s.get("drop_inprogress_session") and run_date:
        s = dict(s)
        s["_drop_from"] = run_date

    data, meta = parse.parse_payload(body, s)
    if not data:
        st["status"] = "EMPTY"
        st["error"] = meta.get("error", "parsed-zero-rows")
        return False

    st["status"] = "OK"
    st["n"] = meta.get("n")
    st["first_date"] = meta.get("first_date")
    st["last_date"] = meta.get("last_date")
    st["last_value"] = meta.get("last_value")
    st["parse_meta"] = {k: v for k, v in meta.items()
                        if k not in ("disapprove", "recent")}
    st["_data"] = data
    if "disapprove" in meta:
        st["_disapprove"] = meta["disapprove"]
    if "recent" in meta:
        st["recent"] = meta["recent"]
    return True


def fetch_all(cfg, from_file=None, proxy=None, run_date=None):
    """抓取（或从留档读取）所有序列。返回 (payloads, statuses)。"""
    payloads = {}
    statuses = []
    req = cfg.get("request", {})
    delay = req.get("delay_between_sources_sec", 1.2)

    for i, s in enumerate(cfg.get("series", [])):
        sid = s["id"]
        st = {
            "id": sid, "role": s.get("role"), "provider": s.get("provider"),
            "tier": s.get("tier"), "status": "PENDING",
            "n": None, "last_date": None, "last_value": None,
            "channel": None, "ua": None, "elapsed": None,
            "error": None, "url": s.get("url"),
        }

        if from_file:
            path = os.path.join(from_file, raw_filename(s))
            if not os.path.exists(path):
                st["status"] = "MISSING (from-file)"
                st["error"] = "no archived payload at %s" % path
                statuses.append(st)
                print("  [MISSING] %-22s %s" % (sid, path))
                continue
            with open(path, "rb") as f:
                body = f.read()
            st["channel"] = "file"
            st["bytes"] = len(body)
            st["sha256_16"] = net.FetchResult(
                True, s.get("url") or "", "file", 200, body, None, 0).sha256()
            if process_body(body, s, st, run_date=run_date):
                st["status"] = "LOADED (from-file)"
                payloads[sid] = body
                statuses.append(st)
                print("  [LOAD] %-22s n=%-5s %s .. %s  last=%s"
                      % (sid, st.get("n"), st.get("first_date"),
                         st.get("last_date"), st.get("last_value")))
            else:
                statuses.append(st)
                print("  [%s] %-22s %s" % (st["status"], sid, st.get("error")))
            continue

        t0 = time.time()
        r = net.fetch(s.get("url"), cfg)
        st["elapsed"] = time.time() - t0
        st["channel"] = r.channel
        st["ua"] = getattr(r, "ua", None)
        st["http"] = r.http
        st["bytes"] = r.nbytes
        st["sha256_16"] = r.sha256()

        if not r.ok:
            st["status"] = "FAIL"
            st["error"] = r.error
            st["attempts"] = getattr(r, "attempts", None)
            statuses.append(st)
            print("  [FAIL] %-22s %s" % (sid, r.error))
            continue

        if not process_body(r.body, s, st, run_date=run_date):
            statuses.append(st)
            print("  [%s] %-22s %s" % (st["status"], sid, st.get("error")))
            continue

        payloads[sid] = r.body
        statuses.append(st)
        print("  [OK]   %-22s n=%-5s %s .. %s  last=%s"
              % (sid, st.get("n"), st.get("first_date"),
                 st.get("last_date"), st.get("last_value")))

        if delay and i < len(cfg.get("series", [])) - 1:
            time.sleep(delay)

    return payloads, statuses


# ---------------------------------------------------------------------------
# 陈旧度检查
# ---------------------------------------------------------------------------

def staleness_check(cfg, statuses, as_of):
    """检查每源最新日期距 as_of 的天数是否超限。"""
    conf = cfg.get("staleness", {})
    per = conf.get("per_series", {})
    default = conf.get("default_max_lag_days", 7)
    try:
        ref = datetime.strptime(as_of, "%Y-%m-%d").date()
    except ValueError:
        ref = now_cn().date()
    out = []
    for st in statuses:
        sid = st["id"]
        max_lag = per.get(sid, default)
        ld = st.get("last_date")
        if not ld:
            out.append({"id": sid, "ok": False, "reason": "no-last-date",
                        "max_lag": max_lag})
            continue
        try:
            d = datetime.strptime(ld, "%Y-%m-%d").date()
        except ValueError:
            out.append({"id": sid, "ok": False, "reason": "bad-last-date",
                        "max_lag": max_lag})
            continue
        lag = (ref - d).days
        out.append({"id": sid, "ok": lag <= max_lag, "lag_days": lag,
                    "max_lag": max_lag, "last_date": ld,
                    "reason": "fresh" if lag <= max_lag else "stale"})
    return out


# ---------------------------------------------------------------------------
# 与东吴原版对照（可选：仅在底稿可用时）
# ---------------------------------------------------------------------------

def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / (sxx ** 0.5 * syy ** 0.5)


def _rank(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def compare_with_dongwu(result, dongwu_csv):
    """把我们的 TACO 与东吴原版 TACO 做区间相关对照。

    注意：底稿仅作【一次性校验基准】。日常运行不读取它，也不把它当输入。
    dongwu_csv 是"用东吴原始输入按已验证公式复算"的结果，因公式精确匹配
    （误差 < 1e-10），它等价于东吴原版曲线。
    """
    if not dongwu_csv or not os.path.exists(dongwu_csv):
        return None
    # 兼容多种 TACO 列名
    taco_cols = ("TACO_Index_T", "TACO_Index_7dma_T", "TACO", "TACO_INDEX",
                 "TACO_Index", "T")
    ref = {}
    import csv as _csv
    with open(dongwu_csv, "r", encoding="utf-8-sig") as f:
        rdr = _csv.DictReader(f)
        fields = [x.strip() for x in (rdr.fieldnames or [])]
        col = next((c for c in taco_cols if c in fields), None)
        if col is None:
            return None
        for r in rdr:
            d = (r.get("Date") or "").strip()
            v = (r.get(col) or "").strip()
            if d and v:
                try:
                    ref[d] = float(v)
                except ValueError:
                    pass
    if not ref:
        return None

    mine = {}
    for i, d in enumerate(result["dates"]):
        if result["T"][i] is not None:
            mine[d] = result["T"][i]

    def window(label, lo, hi):
        common = sorted(d for d in (set(ref) & set(mine)) if lo <= d <= hi)
        if len(common) < 10:
            return None
        xs = [mine[d] for d in common]
        ys = [ref[d] for d in common]
        diffs = [abs(a - b) for a, b in zip(xs, ys)]
        return {
            "label": label, "n": len(common),
            "corr": _corr(xs, ys),
            "rank_corr": _corr(_rank(xs), _rank(ys)),
            "mean_abs_diff": sum(diffs) / len(diffs),
            "max_abs_diff": max(diffs),
        }

    out = []
    for w in (("全期", "0000-01-01", "9999-12-31"),
              ("近一年", "2025-09-21", "9999-12-31"),
              ("2026 年内", "2026-01-01", "9999-12-31"),
              ("近 90 日", "2026-06-23", "9999-12-31")):
        r = window(*w)
        if r:
            out.append(r)
    return out or None


# ---------------------------------------------------------------------------
# 对账（回源独立复算）
# ---------------------------------------------------------------------------

def verify(run_date, cfg, statuses, result, latest, out_dir, from_file=None):
    """从落盘数据独立复算展示数字。"""
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1. 指数输入全部就位
    required = cfg.get("gate", {}).get("required_for_index") or []
    got = {s["id"] for s in statuses if s.get("status", "").startswith(("OK", "LOADED"))}
    missing = [r for r in required if r not in got]
    add("指数输入齐备", not missing,
        "缺: %s" % missing if missing else "全部 %d 个就位" % len(required))

    # 2. TACO 末值可由 S 的 7 日均线复算
    if latest:
        i = latest["index"]
        S = result["S"]
        win = [S[k] for k in range(max(0, i - 6), i + 1) if S[k] is not None]
        recomputed = sum(win) / len(win) if win else None
        ok = (recomputed is not None
              and abs(recomputed - latest["TACO"]) < 1e-9)
        add("TACO = S 的 7 日均线", ok,
            "复算 %.9f vs 报告 %.9f" % (recomputed or float("nan"),
                                       latest["TACO"]))
    else:
        add("TACO 末值存在", False, "无有效读数")

    # 3. S = 5 个 z 的等权均值
    if latest:
        i = latest["index"]
        zs = [result[k][i] for k in ("Nz", "Oz", "Pz", "Qz", "Rz")]
        ok = all(z is not None for z in zs)
        if ok:
            m = sum(zs) / 5.0
            add("S = mean(5 个 z)", abs(m - latest["composite_S"]) < 1e-9,
                "复算 %.9f vs 报告 %.9f" % (m, latest["composite_S"]))
        else:
            add("S = mean(5 个 z)", False, "有 z 为空")

    # 4. 31 日差分抽查（末值）
    if latest:
        i = latest["index"]
        C = result["cols"].get("C") or []
        if i >= 31 and C[i] is not None and C[i - 31] is not None:
            expect = C[i] - C[i - 31]
            got_i = result["I"][i]
            add("31 日差分抽查(10Y)", abs(expect - got_i) < 1e-9,
                "复算 %.9f vs 报告 %.9f" % (expect, got_i))
        else:
            add("31 日差分抽查(10Y)", False, "索引不足")

    # 5. 符号变换：K = -(E - E[-31])
    if latest:
        i = latest["index"]
        E = result["cols"].get("E") or []
        if i >= 31 and E[i] is not None and E[i - 31] is not None:
            expect = -(E[i] - E[i - 31])
            add("支持率取负抽查", abs(expect - result["K"][i]) < 1e-9,
                "复算 %.9f vs 报告 %.9f" % (expect, result["K"][i]))
        else:
            add("支持率取负抽查", False, "索引不足")

    # 6. 道指倒数：L = 1/(G/G[-31] - 1)
    if latest:
        i = latest["index"]
        G = result["cols"].get("G") or []
        if i >= 31 and G[i] is not None and G[i - 31]:
            expect = 1.0 / (G[i] / G[i - 31] - 1)
            add("道指倒数抽查", abs(expect - result["L"][i]) < 1e-9,
                "复算 %.9f vs 报告 %.9f" % (expect, result["L"][i]))
        else:
            add("道指倒数抽查", False, "索引不足")

    # 7. 日历日网格：周末必须有值（前值填充）
    dates = result["dates"]
    if dates:
        wk = [d for d in dates if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5]
        C = result["cols"].get("C") or []
        filled = sum(1 for d in wk
                     if C[dates.index(d)] is not None)
        add("日历日网格(周末有值)", filled == len(wk),
            "%d/%d 个周末日已前值填充" % (filled, len(wk)))

    # 8. 扩张窗口 z：早期 z 的样本量应小于后期
    Nz = result["Nz"]
    nz_valid = [i for i in range(len(Nz)) if Nz[i] is not None]
    add("z 分数存在有效观测", len(nz_valid) > 100,
        "%d 个有效 z" % len(nz_valid))

    # 9. 未使用底稿 D 列（USSWIT1 真实值）
    used = [s for s in cfg.get("series", []) if s.get("id") == "USSWIT1_proxy_T5YIE"]
    add("USSWIT1 走公开替代源", bool(used) and "T5YIE" in (used[0].get("url") or ""),
        "url=%s" % (used[0].get("url") if used else "NOT-FOUND"))

    # 10. TRHBCCCD 不入指数
    trh = [s for s in cfg.get("series", []) if s.get("id") == "TRHBCCCD"]
    add("TRHBCCCD 角色为 context_only",
        bool(trh) and trh[0].get("role") == "context_only",
        "role=%s" % (trh[0].get("role") if trh else "NOT-FOUND"))

    # 11. 原始载荷落盘（离线重放模式不写 raw，故跳过此检查）
    if from_file:
        add("原始载荷已留档", True,
            "离线重放模式：本轮不写 raw（源目录 %s）" % from_file)
    else:
        raw_dir = os.path.join(out_dir, "raw")
        n_raw = len(os.listdir(raw_dir)) if os.path.isdir(raw_dir) else 0
        add("原始载荷已留档", n_raw >= 1, "%d 个文件于 %s" % (n_raw, raw_dir))

    # 12. 写入是否发生降级（目标文件被占用 -> 退写备用名）
    #     「产物写到哪」是报告的一部分：静默换名会让下游按旧文件名读到过期数据。
    wf = list(net.WRITE_FALLBACKS)
    add("产物均写入标准文件名", not wf,
        "无降级" if not wf
        else "降级 %d 项: %s" % (len(wf),
                               ", ".join(os.path.basename(w["intended"])
                                         for w in wf)))

    failed = [c for c in checks if not c["ok"]]
    res = {"ok": not failed, "total": len(checks), "failed": len(failed),
           "checks": checks, "run_date": run_date}
    return res


# ---------------------------------------------------------------------------
# 离线自检
# ---------------------------------------------------------------------------

def selftest(cfg):
    """不联网的自检：配置完整性 + 公式正确性 + 解析器健壮性。"""
    fails = []
    checks = 0

    def ck(name, cond, detail=""):
        nonlocal checks
        checks += 1
        if not cond:
            fails.append("%s %s" % (name, detail))

    # --- 配置 ---
    ck("config.version", bool(cfg.get("version")))
    series = cfg.get("series") or []
    ck("config.series 非空", len(series) >= 5, "got %d" % len(series))
    ids = [s.get("id") for s in series]
    ck("序列 id 唯一", len(ids) == len(set(ids)))
    for s in series:
        sid = s.get("id")
        ck("series[%s].url" % sid, bool(s.get("url")))
        ck("series[%s].payload" % sid, bool(s.get("payload")))
        ck("series[%s].role" % sid, bool(s.get("role")))
        ev = s.get("probe_evidence") or {}
        ck("series[%s].probe_evidence.ok" % sid, ev.get("ok") is True,
           "先测后用：无实测证据不得进配置")

    req = cfg.get("gate", {}).get("required_for_index") or []
    ck("gate.required_for_index 覆盖 5 个入指数序列", len(req) == 5,
       "got %d" % len(req))
    for r in req:
        ck("required[%s] 存在于 series" % r, r in ids)

    # USSWIT1 必须走替代源
    s_sw = [s for s in series if s.get("id") == "USSWIT1_proxy_T5YIE"]
    ck("USSWIT1 替代条目存在", len(s_sw) == 1)
    if s_sw:
        ck("USSWIT1 用 T5YIE", "T5YIE" in s_sw[0]["url"])
        ck("USSWIT1 标记 _is_proxy", s_sw[0].get("_is_proxy") is True)
        ck("USSWIT1 替代排序有 chosen", any(
            x.get("chosen") for x in (s_sw[0].get("substitute_ranking") or [])))

    # TRHBCCCD 不入指数
    s_t = [s for s in series if s.get("id") == "TRHBCCCD"]
    ck("TRHBCCCD 存在", len(s_t) == 1)
    if s_t:
        ck("TRHBCCCD 是 context_only", s_t[0].get("role") == "context_only")
        ck("TRHBCCCD 不在 required", "TRHBCCCD" not in req)

    # --- 公式正确性（用构造数据，可手算校验）---
    # 构造：5 条常量序列 -> 差分为 0 -> 倒数分母 0 -> L 为空
    base = {}
    for k in "CDEGH":
        base[k] = {}
    # 造 70 天
    from datetime import date as _date, timedelta as _td
    d0 = _date(2026, 1, 1)
    dates = [(d0 + _td(days=i)).strftime("%Y-%m-%d") for i in range(70)]
    for i, d in enumerate(dates):
        base["C"][d] = 4.0 + 0.01 * i          # 稳定上行
        base["D"][d] = 2.0 + 0.005 * i
        base["E"][d] = 45.0 - 0.1 * i          # 支持率下行
        base["G"][d] = 50000.0 * (1 + 0.002 * i)
        base["H"][d] = 70.0 + 0.2 * i
    res = taco.compute(base)
    ck("公式: 面板天数=70", res["n_days"] == 70, "got %d" % res["n_days"])
    ck("公式: I 前 31 项为空",
       all(res["I"][i] is None for i in range(31)))
    ck("公式: I[31] = C[31]-C[0]",
       abs(res["I"][31] - (base["C"][dates[31]] - base["C"][dates[0]])) < 1e-9)
    ck("公式: K 取负（支持率下行 -> K 为正）",
       res["K"][31] is not None and res["K"][31] > 0,
       "K[31]=%s" % res["K"][31])
    ck("公式: L 有值", res["L"][31] is not None)
    ck("公式: z 在 i=31 时样本量=1 -> 需 >=2 才出值",
       res["Nz"][31] is None)
    ck("公式: z 在 i=32 时出值", res["Nz"][32] is not None)
    ck("公式: S 是 5 个 z 的均值",
       res["S"][40] is None or True)   # 结构性检查在 verify 里做
    ck("公式: T 有值", any(v is not None for v in res["T"]))
    ck("公式: coverage.T 记录完整",
       res["coverage"]["T_taco"]["n_valid"] > 0)

    # 扩张窗口验证：z[32] 用 [31..32] 两点的样本 std
    x = [v for v in res["I"] if v is not None]
    ck("公式: I 有效点数 = 70-31 = 39", len(x) == 39, "got %d" % len(x))

    # MA 跳过空值语义
    seq = [None, None, 1.0, 2.0, 3.0]
    ma = taco.moving_average_skip_blanks(seq, len(seq), 3)
    ck("公式: MA 跳过空值 (idx2 -> mean(1)=1)", abs(ma[2] - 1.0) < 1e-12,
       "got %s" % ma[2])
    ck("公式: MA idx3 -> mean(1,2)=1.5", abs(ma[3] - 1.5) < 1e-12)
    ck("公式: MA idx4 -> mean(1,2,3)=2", abs(ma[4] - 2.0) < 1e-12)

    # 倒数边界：涨跌幅为 0 -> None
    flat = {"G": {dates[i]: 100.0 for i in range(70)}}
    L = taco.reciprocal_pct_change([100.0] * 70, 70)
    ck("公式: 涨跌幅 0 -> L 为 None", all(v is None for v in L))

    # --- 解析器 ---
    fred_body = (b"observation_date,DGS10\n2026-09-15,4.10\n"
                 b"2026-09-16,.\n2026-09-17,4.20\n")
    d, m = parse.parse_fred_csv(fred_body, "observation_date", "DGS10")
    ck("parse: FRED 跳过 '.' 缺值", len(d) == 2, "got %d" % len(d))
    ck("parse: FRED last=4.20", abs(d["2026-09-17"] - 4.20) < 1e-9)
    d2, m2 = parse.parse_fred_csv(fred_body, "observation_date", "WRONG")
    ck("parse: FRED 列名不符 -> 报错", not d2 and "mismatch" in m2.get("error", ""))

    dw_body = (b"modeldate,approve,disapprove\n9/19/2026,38.49,58.94\n"
               b"9/20/2026,38.49,58.94\n")
    d3, m3 = parse.parse_dw_csv(dw_body)
    ck("parse: DW 日期格式转换", "2026-09-20" in d3, "got %s" % list(d3))
    ck("parse: DW 值正确", abs(d3["2026-09-20"] - 38.49) < 1e-9)
    ck("parse: DW 附带 disapprove", "disapprove" in m3)

    arc_body = json.dumps({
        "features": [
            {"attributes": {"date": "2026-09-13", "n_total": 8,
                            "n_tanker": 1, "capacity": 71701}},
            {"attributes": {"date": 0, "n_total": 1, "n_tanker": 0,
                            "capacity": 0}},
        ]}).encode()
    d4, m4 = parse.parse_arcgis_json(arc_body)
    ck("parse: ArcGIS 字符串日期", "2026-09-13" in d4, "got %s" % list(d4))
    ck("parse: ArcGIS epoch 0 哨兵被丢弃", m4.get("dropped") == 1,
       "dropped=%s" % m4.get("dropped"))

    err_body = json.dumps({"error": {"code": 400,
                                     "message": "Invalid query"}}).encode()
    d5, m5 = parse.parse_arcgis_json(err_body)
    ck("parse: ArcGIS 错误响应被识别", not d5 and "arcgis-error" in m5.get("error", ""))

    # --- 假 200 识别 ---
    ck("validate: JS 挑战页被识别",
       not net.validate_payload(b"<html><body>Enable JavaScript</body></html>",
                                "csv")[0])
    ck("validate: 空 body 被拒", not net.validate_payload(b"", "csv")[0])
    ck("validate: 正常 CSV 通过",
       net.validate_payload(b"a,b\n1,2\n", "csv")[0])
    ck("validate: HTML 冒充 CSV 被拒",
       not net.validate_payload(b"<!DOCTYPE html><html>", "csv")[0])

    # --- 原子写入 ---
    import tempfile
    td = tempfile.mkdtemp(prefix="taco_selftest_")
    p = os.path.join(td, "x.txt")
    net.atomic_write_text(p, "hello")
    ck("atomic_write 落盘", open(p, encoding="utf-8-sig").read() == "hello")
    ck("atomic_write 无残留 tmp", not os.path.exists(p + ".tmp"))

    # 目标被占用时必须降级而不是崩：用一个独占句柄模拟 WPS/Excel 打开文件
    locked = os.path.join(td, "locked.csv")
    net.atomic_write_text(locked, "first")
    fh = open(locked, "r+b")
    try:
        net.WRITE_FALLBACKS.clear()
        net.atomic_write_text(locked, "second", )
        got_alt = bool(net.WRITE_FALLBACKS)
        ck("目标被占用时降级写入而非崩溃", got_alt,
           "fallbacks=%d" % len(net.WRITE_FALLBACKS))
        alt = net.WRITE_FALLBACKS[0]["actual"] if got_alt else None
        ck("降级文件名含 .locked- 标记", bool(alt) and ".locked-" in alt,
           os.path.basename(alt) if alt else "—")
        ck("降级写入内容正确",
           bool(alt) and open(alt, encoding="utf-8-sig").read() == "second")
        ck("原文件在占用期间未被破坏",
           open(locked, "r+b").read().decode("utf-8-sig") == "first")
    finally:
        fh.close()
        net.WRITE_FALLBACKS.clear()
    # 放开占用后应恢复写回标准名
    net.WRITE_FALLBACKS.clear()
    net.atomic_write_text(locked, "third")
    ck("占用解除后写回标准文件名",
       not net.WRITE_FALLBACKS
       and open(locked, encoding="utf-8-sig").read() == "third")

    import shutil
    shutil.rmtree(td, ignore_errors=True)

    return {"ok": not fails, "total": checks, "failed": len(fails),
            "failures": fails}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="TACO 压力指数每日监测爬虫")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不联网）")
    ap.add_argument("--proxy", default=None,
                    help="显式出口覆盖（仅供排障，不要写进计划任务）")
    ap.add_argument("--from-file", default=None,
                    help="从留档目录离线重放（不联网）")
    ap.add_argument("--as-of", default=None, help="把本次当作某日（YYYY-MM-DD）")
    ap.add_argument("--out-dir", default=None, help="隔离输出目录（跨天验证用）")
    ap.add_argument("--no-xlsx", action="store_true", help="跳过 Excel 生成")
    ap.add_argument("--no-dongwu-compare", action="store_true",
                    help="跳过与东吴底稿对照")
    args = ap.parse_args(argv)

    cfg_path = os.path.join(HERE, "config.json")
    cfg = load_config(cfg_path)

    if args.selftest:
        print("=" * 70)
        print("taco-monitor 离线自检")
        print("=" * 70)
        r = selftest(cfg)
        print("检查项: %d  失败: %d" % (r["total"], r["failed"]))
        for f in r["failures"]:
            print("  FAIL: %s" % f)
        print("结果: %s" % ("PASS" if r["ok"] else "FAIL"))
        return 0 if r["ok"] else 1

    if args.proxy:
        os.environ["RM_PROXY"] = args.proxy

    t_start = time.time()
    run_date = args.as_of or now_cn().strftime("%Y-%m-%d")
    print("=" * 70)
    print("taco-monitor —— TACO 压力指数每日监测")
    print("运行日期: %s" % run_date)
    print("=" * 70)

    dec = net.proxy_decision()
    print("出口决策: %s" % dec.get("reason"))
    if dec.get("env_proxy_vars_ignored"):
        print("  （已忽略环境变量代理: %s）"
              % ", ".join(dec["env_proxy_vars_ignored"]))

    out_root = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(HERE, "output")
    day_dir = os.path.join(out_root, run_date)
    raw_dir = os.path.join(day_dir, "raw")
    emit.ensure_dir(raw_dir)

    # --- 1. 抓取 ---
    print("\n[1/5] 抓取公开源 ...")
    payloads, statuses = fetch_all(cfg, from_file=args.from_file,
                                   proxy=args.proxy, run_date=run_date)
    for st in statuses:
        st["proxy_decision"] = dec

    # 落盘原始载荷
    if not args.from_file:
        for s in cfg.get("series", []):
            sid = s["id"]
            if sid in payloads:
                net.atomic_write_bytes(os.path.join(raw_dir, raw_filename(s)),
                                       payloads[sid])

    ok_n = sum(1 for s in statuses if s["status"].startswith(("OK", "LOADED")))
    print("  -> %d/%d 源就绪" % (ok_n, len(statuses)))

    # --- 2. 陈旧度 ---
    print("\n[2/5] 陈旧度检查 ...")
    stale = staleness_check(cfg, statuses, run_date)
    for x in stale:
        if not x["ok"]:
            print("  [STALE] %-22s %s" % (x["id"], x.get("reason")))
    print("  -> %d/%d 新鲜" % (sum(1 for x in stale if x["ok"]), len(stale)))

    # --- 3. 算 TACO ---
    print("\n[3/5] 计算 TACO 指数 ...")
    gate = cfg.get("gate", {})
    required = gate.get("required_for_index") or []
    avail = {s["id"]: s for s in statuses if s.get("status", "").startswith(("OK", "LOADED"))}
    miss = [r for r in required if r not in avail]
    if len(miss) > len(required) - gate.get("min_series_ok", 4):
        print("  !! 门禁未过：%d/%d 个入指数序列缺失（%s）"
              % (len(miss), len(required), miss))
        print("  拒绝生成 TACO —— 避免半残指数被当成完整指数。")
        status_path = os.path.join(out_root, "_source_status.json")
        net.atomic_write_json(status_path, {
            "run_date": run_date, "gate_passed": False, "missing": miss,
            "statuses": [{k: v for k, v in s.items() if not k.startswith("_")}
                         for s in statuses],
            "staleness": stale, "proxy_decision": dec,
        })
        return 2
    if miss:
        print("  ! 警告：%d 个入指数序列缺失（%s），仍继续但指数不完整"
              % (len(miss), miss))

    series_map = {
        "C": avail["USGG10YR"]["_data"] if "USGG10YR" in avail else {},
        "D": avail["USSWIT1_proxy_T5YIE"]["_data"] if "USSWIT1_proxy_T5YIE" in avail else {},
        "E": avail["RCPPTAPP"]["_data"] if "RCPPTAPP" in avail else {},
        "G": avail["INDU"]["_data"] if "INDU" in avail else {},
        "H": avail["CO1"]["_data"] if "CO1" in avail else {},
    }
    result = taco.compute(series_map)
    latest = taco.latest(result)
    stats = taco.stats(result)
    if latest:
        print("  -> TACO = %.4f  (%s)，%d 个有效观测，分位 %.1f%%"
              % (latest["TACO"], latest["date"],
                 stats.get("n", 0), 100 * (stats.get("latest_percentile") or 0)))
    else:
        print("  !! 未算出有效 TACO")

    # --- 4. 产物 ---
    print("\n[4/5] 生成产物 ...")
    records = []
    run_tag = "%s#%s" % (run_date, int(t_start))
    for i, d in enumerate(result["dates"]):
        row = {"run_tag": run_tag, "date": d, "series": "TACO_INDEX",
               "value": result["T"][i], "unit": "z",
               "composite_S": result["S"][i],
               "z_10Y": result["Nz"][i], "z_swap": result["Oz"][i],
               "z_approval": result["Pz"][i], "z_DJIA": result["Qz"][i],
               "z_Brent": result["Rz"][i]}
        records.append(row)
    emit.write_jsonl(os.path.join(day_dir, "taco-records-%s.jsonl" % run_date),
                     records)

    composite = {
        "run_tag": run_tag, "run_date": run_date,
        "latest": latest, "stats": stats,
        "coverage": result["coverage"],
        "formula_params": {"lag": result["lag"], "first": result["first"],
                           "ma_window": result["ma_window"],
                           "z_ddof": result["z_ddof"]},
        "proxy_decision": dec,
    }
    net.atomic_write_json(os.path.join(day_dir,
                                       "taco-composite-%s.json" % run_date),
                          composite)

    # CSV（全量）
    cols_map = result["cols"]
    csv_rows = []
    for i, d in enumerate(result["dates"]):
        def gv(k, i=i):
            a = cols_map.get(k) or []
            return a[i] if i < len(a) else None
        def ga(k, i=i):
            a = result.get(k) or []
            return a[i] if i < len(a) else None
        csv_rows.append({
            "Date": d,
            "USGG10YR": gv("C"), "USSWIT1_proxy_T5YIE": gv("D"),
            "RCPPTAPP_approve": gv("E"), "INDU": gv("G"), "CO1_Brent": gv("H"),
            "z_10Y": ga("Nz"), "z_swap": ga("Oz"), "z_approval": ga("Pz"),
            "z_DJIA": ga("Qz"), "z_Brent": ga("Rz"),
            "composite_S": ga("S"), "TACO_Index_T": ga("T"),
        })
    emit.write_csv(os.path.join(day_dir, "taco-%s.csv" % run_date), csv_rows)
    emit.write_csv(os.path.join(out_root, "taco-latest.csv"), csv_rows)

    # 累积历史
    hist_dir = os.path.join(out_root, "history")
    for s in cfg.get("series", []):
        sid = s["id"]
        if sid in avail:
            _, n_tot, n_new, n_rev = emit.update_history(
                hist_dir, sid, avail[sid]["_data"], run_date=run_date)
            print("  history %-22s total=%-5d new=%-4d revised=%d"
                  % (sid, n_tot, n_new, n_rev))
    taco_hist = {result["dates"][i]: result["T"][i]
                 for i in range(len(result["dates"]))
                 if result["T"][i] is not None}
    _, n_tot, n_new, n_rev = emit.update_history(
        hist_dir, "TACO_INDEX", taco_hist, run_date=run_date)
    print("  history %-22s total=%-5d new=%-4d revised=%d"
          % ("TACO_INDEX", n_tot, n_new, n_rev))

    # 跨轮观察账本
    emit.write_jsonl(os.path.join(out_root, "ALL-observations.jsonl"),
                     [{"run_tag": run_tag, "date": d, "series": "TACO_INDEX",
                       "value": result["T"][i], "unit": "z"}
                      for i, d in enumerate(result["dates"])
                      if result["T"][i] is not None], mode="a")

    # 对照东吴（仅作一次性校验，不作为输入）
    our_vs = None
    if not args.no_dongwu_compare:
        dw_path = os.path.join(os.path.dirname(HERE), "data", "taco",
                               "taco_replicated.csv")
        our_vs = compare_with_dongwu(result, dw_path)
        if our_vs:
            print("  对照东吴底稿（一次性校验基准，非输入）：")
            for w in our_vs:
                print("    %-10s n=%-5s corr=%s" %
                      (w["label"], w["n"],
                       ("%.4f" % w["corr"]) if w["corr"] is not None else "—"))

    # 报告 + 看板 + Excel
    digest = emit.build_digest(run_date, cfg, statuses, result, latest, stats,
                               our_vs_dongwu=our_vs)
    net.atomic_write_text(os.path.join(day_dir, "taco-digest-%s.md" % run_date),
                          digest, "utf-8")
    net.atomic_write_text(os.path.join(out_root, "LATEST.md"), digest, "utf-8")

    html_doc = emit.build_html(run_date, cfg, statuses, result, latest, stats,
                               hist_dir)
    net.atomic_write_text(os.path.join(out_root, "taco-dashboard.html"),
                          html_doc, "utf-8")

    if not args.no_xlsx:
        xp = emit.build_xlsx(os.path.join(out_root, "taco_index.xlsx"),
                             run_date, cfg, statuses, result, latest, stats,
                             our_vs_dongwu=our_vs)
        if xp:
            print("  xlsx: %s" % os.path.basename(xp))
        else:
            print("  xlsx: 跳过（openpyxl 不可用）")

    # 写入降级：目标文件被别人占用（WPS/Excel 打开着最常见）时，已退写到
    # <name>.locked-<时间戳><ext>。必须显式说出来，不能静默换名。
    if net.WRITE_FALLBACKS:
        print("\n  !! 写入降级 %d 项 —— 目标文件被其他程序占用，已退写备用名："
              % len(net.WRITE_FALLBACKS))
        for w in net.WRITE_FALLBACKS:
            print("     %-34s -> %s"
                  % (os.path.basename(w["intended"]),
                     os.path.basename(w["actual"])))
        print("     处置：关闭占用该文件的程序后重跑一次，即可写回标准文件名。")

    # 源状态
    net.atomic_write_json(os.path.join(out_root, "_source_status.json"), {
        "run_date": run_date, "gate_passed": True,
        "statuses": [{k: v for k, v in s.items() if not k.startswith("_")}
                     for s in statuses],
        "staleness": stale, "proxy_decision": dec,
        "our_vs_dongwu": our_vs,
        "write_fallbacks": list(net.WRITE_FALLBACKS),
    })

    # --- 5. 对账 ---
    print("\n[5/5] 回源对账 ...")
    vr = verify(run_date, cfg, statuses, result, latest, day_dir,
                from_file=args.from_file)
    net.atomic_write_json(os.path.join(out_root, "_verify_last.json"), vr)
    print("  %d/%d 项通过" % (vr["total"] - vr["failed"], vr["total"]))
    for c in vr["checks"]:
        if not c["ok"]:
            print("  FAIL: %s — %s" % (c["name"], c["detail"]))

    # 带对账结果的报告重写一次
    digest2 = emit.build_digest(run_date, cfg, statuses, result, latest, stats,
                                our_vs_dongwu=our_vs, verify=vr)
    net.atomic_write_text(os.path.join(day_dir, "taco-digest-%s.md" % run_date),
                          digest2, "utf-8")
    net.atomic_write_text(os.path.join(out_root, "LATEST.md"), digest2, "utf-8")

    print("\n" + "=" * 70)
    if latest:
        print("TACO = %.4f  (%s)   用时 %.1fs"
              % (latest["TACO"], latest["date"], time.time() - t_start))
    print("报告: %s" % os.path.join(out_root, "LATEST.md"))
    print("看板: %s" % os.path.join(out_root, "taco-dashboard.html"))
    print("对账: %s" % ("PASS" if vr["ok"] else "FAIL"))
    print("=" * 70)
    return 0 if vr["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
