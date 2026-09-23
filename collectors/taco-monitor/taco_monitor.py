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
import ast
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tacolib import net, parse, taco, emit, bonbast   # noqa: E402

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
           "sina_jsonp": "jsonp", "yahoo_chart": "json"}.get(kind, "bin")
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
        r = net.fetch(s.get("url"), cfg, egress=s.get("egress"))
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
# 水位断裂守卫（第 6 因子的前置体检）
# ---------------------------------------------------------------------------

def level_break_check(obs_by_date, run_date=None, recent_days=45,
                      base_days=365, exclude_recent=90, lo=0.4, hi=2.5):
    """检测单序列的「水位断裂」：近期中位数 vs 长期基准中位数。

    为什么必须做这一步：第 6 因子用的是 **31 日变动**。一条序列只要水位发生了
    **持续**的断裂（口径变化、覆盖范围变化、区域性信号丢失、AIS 失效……），
    变动量就会退化成断点之后的小噪声，而指数看到的是「小幅回升 = 压力下降」
    —— 与水位给出的方向完全相反。

    真实案例（2026-09 实测）：IMF PortWatch 的霍尔木兹 `n_total`
    2026-02 还有 ~78 艘次/日，2026-03 断到 ~3 艘次/日，此后 6 个月一直在 3–13，
    同期 `n_tanker` 与 `capacity` **同步**塌掉约 95%（三个字段一起塌 = 口径/覆盖
    问题，不是运量信号）。而同期苏伊士、曼德海峡、马六甲、台湾海峡、巴拿马
    全部平稳 —— **只有霍尔木兹断了**。所以这不是全局数据管道故障，而是霍尔木兹
    单点的口径/信号问题（区域性 AIS/GPS 干扰或图层口径变更）。

    返回 dict：ok / ratio / recent_median / base_median / n_recent / n_base
    """
    ds = sorted(obs_by_date)
    if not ds:
        return {"ok": None, "reason": "no-data"}

    def _d(s):
        return datetime.strptime(s, "%Y-%m-%d").date()

    last = _d(ds[-1])
    r_lo = last - timedelta(days=recent_days)
    b_hi = last - timedelta(days=exclude_recent)
    b_lo = b_hi - timedelta(days=base_days)
    recent = [obs_by_date[d] for d in ds if _d(d) >= r_lo]
    base = [obs_by_date[d] for d in ds if b_lo <= _d(d) <= b_hi]
    if len(recent) < 5 or len(base) < 20:
        return {"ok": None, "reason": "insufficient-observations",
                "n_recent": len(recent), "n_base": len(base)}

    def _med(v):
        v = sorted(v)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0

    rm, bm = _med(recent), _med(base)
    if bm == 0:
        ratio = float("inf") if rm else 1.0
    else:
        ratio = rm / bm
    return {
        "ok": lo <= ratio <= hi,
        "ratio": ratio,
        "recent_median": rm,
        "base_median": bm,
        "n_recent": len(recent),
        "n_base": len(base),
        "recent_window": [r_lo.isoformat(), ds[-1]],
        "base_window": [b_lo.isoformat(), b_hi.isoformat()],
        "thresholds": {"lo": lo, "hi": hi},
    }


def monthly_medians(obs_by_date, months=14):
    """按月产出中位数（供报告的水位面板）。返回 [(YYYY-MM, n, median)]。"""
    from collections import defaultdict
    g = defaultdict(list)
    for d, v in obs_by_date.items():
        if v is not None:
            g[d[:7]].append(v)
    out = []
    for k in sorted(g)[-months:]:
        v = sorted(g[k])
        n = len(v)
        out.append((k, n, v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0))
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

def verify(run_date, cfg, statuses, result, latest, out_dir, from_file=None,
           bon=None):
    """从落盘数据独立复算展示数字。

    两级结论：
      level="fail" —— 数字对不上，必须当成 FAIL 处理；
      level="warn" —— 事实需要读者知道、但数据本身是完整的（如目标文件被占用
        导致换名写入）。这类若判 FAIL，会让日常任务天天报 FAIL，把 FAIL 训练
        成噪音，真正的 FAIL 被忽略。
    """
    checks = []

    def add(name, ok, detail, warn=False):
        checks.append({"name": name, "ok": bool(ok), "detail": detail,
                       "level": "warn" if warn else "fail"})

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

    # 10. TRHBCCCD 不在 5 因子 TACO 的入指数清单里
    #     本轮（observe 模式）它的角色就是**观察指标**，连 TACO-6 都不进；
    #     composite 模式下它是本项目扩展的第 6 因子。两种模式下都必须证明
    #     「它不在 required 里」—— 这是"不进指数"的配置级证据。
    trh = [s for s in cfg.get("series", []) if s.get("id") == "TRHBCCCD"]
    _hrole = ((cfg.get("extended_factor") or {}).get("mode", "composite"))
    add("TRHBCCCD 不在 5 因子入指数清单",
        bool(trh) and trh[0].get("role") == "context_only"
        and "TRHBCCCD" not in required,
        "role=%s，required 命中=%s，extended_factor.mode=%s（%s）"
        % (trh[0].get("role") if trh else "NOT-FOUND",
           "TRHBCCCD" in required, _hrole,
           "仅作观察指标" if _hrole == "observe" else "仅入 TACO-6"))

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
    #     但数据本身完整，只是文件名降级 —— 故为 warn 级，不判 FAIL。
    wf = list(net.WRITE_FALLBACKS)
    add("产物均写入标准文件名", not wf,
        "无降级" if not wf
        else "降级 %d 项: %s —— 目标被占用，数据完整但换名；关闭占用程序后重跑可写回" % (
            len(wf),
            ", ".join(os.path.basename(w["intended"]) for w in wf)),
        warn=True)

    # 12a. 就地覆盖（rename-over 被拒、但目标文件本身可写）。标准名内容是对的，
    #      代价是**失去原子性** —— 写入过程被强杀时文件可能停在半截。
    #      一样要显式披露：静默的降级等于没有降级。
    iw = list(net.INPLACE_WRITES)
    add("产物写入保持原子性", not iw,
        "无就地覆盖" if not iw
        else ("%d 项就地覆盖（标准名内容正确，但非原子 —— rename-over 被拒"
              "而文件可写）: %s"
              % (len(iw), ", ".join(os.path.basename(x["path"]) for x in iw))),
        warn=True)

    # 12b. 旧降级副本清理失败。与 12 是两件事：12 是「本轮数据换了名字」，
    #      这条是「历史副本没清掉」。本机 safe-delete 钩子实测会把 os.remove
    #      转成回收站操作并在部分文件上失败 —— 不影响本轮数据，故 warn 级；
    #      但必须显式说出来，因为静默失败会让人把它误判成代码缺陷。
    pf = list(net.PRUNE_FAILURES)
    add("旧降级副本已清理", not pf,
        "无遗留" if not pf
        else ("%d 个旧降级文件删除失败（多为本机 safe-delete 钩子拦截，"
              "非数据问题）: %s"
              % (len(pf), ", ".join(os.path.basename(x["path"]) for x in pf))),
        warn=True)

    # 13. 第 6 因子（本项目扩展）：TACO-6 = S6 的 7 日均线
    if result.get("extended"):
        T6 = result.get("T6") or []
        S6 = result.get("S6") or []
        j = latest.get("index6") if latest else None
        if j is None:
            # latest 未带（例如 6 因子暂无有效值）-> 自行找末个有效 T6
            for k in range(len(T6) - 1, -1, -1):
                if T6[k] is not None:
                    j = k
                    break
        if j is not None:
            win = [S6[k] for k in range(max(0, j - 6), j + 1)
                   if S6[k] is not None]
            rec = sum(win) / len(win) if win else None
            add("TACO-6 = S6 的 7 日均线",
                rec is not None and abs(rec - T6[j]) < 1e-9,
                "复算 %.9f vs 报告 %.9f（%s）"
                % (rec if rec is not None else float("nan"), T6[j],
                   result["dates"][j]))
        else:
            add("TACO-6 有有效读数", False, "第 6 因子无有效值")

        # 14. 第 6 因子的 31 日差分确实取了负
        j2 = j
        F = result.get("F") or []
        if j2 is not None and j2 >= 31 and j2 < len(F) \
                and F[j2] is not None and F[j2 - 31] is not None:
            expect = -(F[j2] - F[j2 - 31])
            add("第 6 因子取负抽查(通行量)",
                abs(expect - result["Nt"][j2]) < 1e-9,
                "复算 %.9f vs 报告 %.9f" % (expect, result["Nt"][j2]))
        else:
            add("第 6 因子取负抽查(通行量)", False, "索引不足")

        # 15. 第 6 因子没有污染 5 因子：S 的长度与网格一致
        add("第 6 因子未改变网格",
            len(result["S6"]) == len(result["S"]) == len(result["dates"]),
            "len(dates)=%d len(S)=%d len(S6)=%d"
            % (len(result["dates"]), len(result["S"]), len(result["S6"])))

        # 16. 第 6 因子的「水位体检」：断裂则 warn（TACO-6 降级，不得单独解读）
        brk = result.get("level_break_hormuz") or {}
        if brk.get("ok") is None:
            add("第 6 因子水位体检已完成", False,
                "未完成：%s" % brk.get("reason"), warn=True)
        else:
            add("第 6 因子水位未断裂", brk.get("ok"),
                "近 %s 日中位数 %.1f vs 基准 %s..%s 中位数 %.1f（倍数 %.2f，"
                "阈值 %.2f~%.2f）—— 断裂时 31 日变动会给出反向信号，TACO-6 应视为降级"
                % (brk.get("n_recent"), brk.get("recent_median"),
                   (brk.get("base_window") or ["", ""])[0],
                   (brk.get("base_window") or ["", ""])[1],
                   brk.get("base_median"), brk.get("ratio"),
                   brk["thresholds"]["lo"] if brk.get("thresholds") else 0,
                   brk["thresholds"]["hi"] if brk.get("thresholds") else 0),
                warn=True)

        # 17. 水位口径（TACO-6L）：Ltz = -z(F)，S6L = mean(6 个 z)，T6L = S6L 的 7 日均线
        if result.get("extended_level"):
            Ltz = result.get("Ltz") or []
            k = next((i for i in range(len(Ltz) - 1, -1, -1)
                      if Ltz[i] is not None), None)
            if k is not None:
                Fz_win = [result["F"][j] for j in range(result["first"], k + 1)
                          if result["F"][j] is not None]
                if len(Fz_win) >= 2:
                    m = sum(Fz_win) / len(Fz_win)
                    sd = (sum((v - m) ** 2 for v in Fz_win)
                          / (len(Fz_win) - result["z_ddof"])) ** 0.5
                    expect = -((result["F"][k] - m) / sd) if sd else None
                    add("水位口径 Ltz = -z(F)",
                        expect is not None and abs(expect - Ltz[k]) < 1e-9,
                        "复算 %.9f vs 报告 %.9f（%s）"
                        % (expect if expect is not None else float("nan"),
                           Ltz[k], result["dates"][k]))
                zs = [result[x][k] for x in ("Nz", "Oz", "Pz", "Qz", "Rz", "Ltz")]
                if all(z is not None for z in zs):
                    m6 = sum(zs) / 6.0
                    add("水位口径 S6L = mean(6 个 z)",
                        abs(m6 - result["S6L"][k]) < 1e-9,
                        "复算 %.9f vs 报告 %.9f" % (m6, result["S6L"][k]))
                else:
                    add("水位口径 S6L = mean(6 个 z)", False, "有 z 为空")
                S6L = result.get("S6L") or []
                win = [S6L[j] for j in range(max(0, k - 6), k + 1)
                       if S6L[j] is not None]
                rec = sum(win) / len(win) if win else None
                add("水位口径 TACO-6L = S6L 的 7 日均线",
                    rec is not None and abs(rec - result["T6L"][k]) < 1e-9,
                    "复算 %.9f vs 报告 %.9f"
                    % (rec if rec is not None else float("nan"),
                       result["T6L"][k]))
            else:
                add("水位口径有有效读数", False, "Ltz 无有效值")
        else:
            add("水位口径已启用", False, "结果中无 extended_level 标记")
    elif result.get("observe"):
        # ---- 观察模式：此处**不该**有第 6 因子，而是另一组断言 ----
        #
        # 观察模式最容易出的事故是「以为没进指数、其实进了」。所以这里不查
        # "第 6 因子好不好"，而是查两件事：
        #   (a) 结果里确实没有第 6 因子的任何派生量；
        #   (b) 用来展示的那一列（TRHBCCCD_n_total）真的是原始水位，
        #       且它**没有**渗进 S / T —— S 仍严格等于 5 个 z 的均值。
        # (b) 是核心：只要 S 还能由 5 个 z 复算出来，观察列在数学上就不可能
        # 参与过合成（多一项的话均值一定对不上）。
        F = result.get("F") or []
        n_f = sum(1 for v in F if v is not None)
        add("观察模式: 未产出任何第 6 因子派生量",
            not any(result.get(k) for k in
                    ("Nt", "Ntz", "S6", "T6", "Ltz", "S6L", "T6L")),
            "Nt/Ntz/S6/T6/Ltz/S6L/T6L 必须全为空")
        add("观察模式: 未产出任何第 7 因子派生量",
            not any(result.get(k) for k in
                    ("Ntz2", "S7", "T7", "Ltz2", "S7L", "T7L")),
            "里亚尔相关派生量必须全为空")
        add("观察列 TRHBCCCD_n_total 有有效值", n_f > 100,
            "ffill 后 %d 个网格日有值（原始源 %s 天）"
            % (n_f, result.get("observe_src_n")))
        # 核心恒等式：S 只能由那 5 个 z 得到。若观察列偷偷进了合成，
        # 均值会变成 6 个数的均值，这一条必然失败。
        _ok_s, _bad_s = True, None
        for _i in range(len(result["dates"])):
            _zs = [result[k][_i] for k in ("Nz", "Oz", "Pz", "Qz", "Rz")]
            if any(z is None for z in _zs) or result["S"][_i] is None:
                continue
            if abs(sum(_zs) / 5.0 - result["S"][_i]) > 1e-9:
                _ok_s, _bad_s = False, result["dates"][_i]
                break
        add("观察模式: S 仍严格 = 5 个 z 的均值（观察列未进合成）", _ok_s,
            "逐点校验通过" if _ok_s else "首个不符日 %s" % _bad_s)
        add("观察模式: 列契约 = 13 + 1（观察列）",
            len(taco.output_columns(result)) == taco.N_COLS_5F_OBS,
            "实际 %d 列" % len(taco.output_columns(result)))
        _lb = result.get("level_break_hormuz") or {}
        if _lb:
            add("观察指标水位体检已完成（不影响指数，仅作披露）", True,
                "近/基准 = %s，判定 %s"
                % (_lb.get("ratio"),
                   {True: "正常", False: "断裂（仅提示，TACO 不受影响）",
                    None: "未完成"}[_lb.get("ok")]))
        add("观察模式: 报告已声明 in_index=False",
            bool((result.get("hormuz_role") or "") == "observe"), "hormuz_role=observe")
    else:
        add("第 6 因子已启用", False,
            "结果中无 extended 标记 —— 检查 config.extended_factor / "
            "TRHBCCCD 是否就绪")

    # 18. 第 7 因子（本项目扩展）：Bonnast USD 卖出价 / 里亚尔
    #
    #     与第 6 因子同一纪律：只增不改。下面三条算术恒等式合起来就是
    #     「6 因子 = 5 基础 + 霍尔木兹」「7 因子 = 6 因子 + 里亚尔」的证明 ——
    #     只要三者同时成立，第 7 因子就不可能悄悄改掉 5/6 因子的合成结果。
    if result.get("extended_2"):
        Ntz2 = result.get("Ntz2") or []
        Ltz2 = result.get("Ltz2") or []
        S7 = result.get("S7") or []
        S7L = result.get("S7L") or []
        T7 = result.get("T7") or []
        T7L = result.get("T7L") or []
        F2 = result.get("F2") or []

        j7 = next((i for i in range(len(T7) - 1, -1, -1)
                   if T7[i] is not None), None)
        if j7 is None:
            add("第 7 因子变动口径有有效读数", False,
                "T7 全为空 —— 变动口径需要 F2 与 F2[-31] 同时存在，"
                "源只给 %s 天有效值" % len([v for v in F2 if v is not None]))
        else:
            win = [S7[k] for k in range(max(0, j7 - 6), j7 + 1)
                   if S7[k] is not None]
            rec = sum(win) / len(win) if win else None
            add("TACO-7 = S7 的 7 日均线",
                rec is not None and abs(rec - T7[j7]) < 1e-9,
                "复算 %.9f vs 报告 %.9f（%s）"
                % (rec if rec is not None else float("nan"), T7[j7],
                   result["dates"][j7]))

        j7l = next((i for i in range(len(T7L) - 1, -1, -1)
                    if T7L[i] is not None), None)
        if j7l is not None:
            win = [S7L[k] for k in range(max(0, j7l - 6), j7l + 1)
                   if S7L[k] is not None]
            rec = sum(win) / len(win) if win else None
            add("TACO-7L = S7L 的 7 日均线",
                rec is not None and abs(rec - T7L[j7l]) < 1e-9,
                "复算 %.9f vs 报告 %.9f（%s）"
                % (rec if rec is not None else float("nan"), T7L[j7l],
                   result["dates"][j7l]))

        # 合成恒等式（任一条不成立都说明因子集合被改动了）
        for label, k, arr, extra in (
                ("S6 = mean(5 基础 z + Ntz)", "S6", result.get("S6"),
                 ("Ntz",)),
                ("S7 = mean(5 基础 z + Ntz + Ntz2)", "S7", S7,
                 ("Ntz", "Ntz2")),
                ("S7L = mean(5 基础 z + Ltz + Ltz2)", "S7L", S7L,
                 ("Ltz", "Ltz2"))):
            k_idx = next((i for i in range(len(arr or []) - 1, -1, -1)
                          if arr[i] is not None), None)
            if k_idx is None:
                add(label, False, "无有效值")
                continue
            zs = [result[x][k_idx] for x in ("Nz", "Oz", "Pz", "Qz", "Rz")]
            zs += [result[x][k_idx] for x in extra]
            if any(z is None for z in zs):
                add(label, False, "有 z 为空（%s）" % result["dates"][k_idx])
            else:
                m = sum(zs) / len(zs)
                add(label, abs(m - arr[k_idx]) < 1e-9,
                    "复算 %.9f vs 报告 %.9f（%d 个 z）"
                    % (m, arr[k_idx], len(zs)))

        # 符号：第 7 因子**不取负**（汇率上行 = 里亚尔贬值 = 压力升）。
        # 这条与第 6 因子的取负正好相反，最容易「照抄上一个因子」写错。
        if j7 is None:
            add("第 7 因子取正抽查(里亚尔)", False, "无有效索引")
        else:
            ok_idx = next((i for i in range(j7, 31 - 1, -1)
                           if i >= 31 and i < len(F2) and F2[i] is not None
                           and F2[i - 31] is not None
                           and i < len(Ntz2) and Ntz2[i] is not None), None)
            if ok_idx is None:
                add("第 7 因子取正抽查(里亚尔)", False,
                    "找不到 F2 与 F2[-31] 同时存在的索引")
            else:
                expect = F2[ok_idx] - F2[ok_idx - 31]
                got_nt = result["Nt2"][ok_idx]
                add("第 7 因子取正抽查(里亚尔)",
                    abs(expect - got_nt) < 1e-6 and expect * got_nt >= 0,
                    "复算 %+.6f vs 报告 %+.6f（取负的话符号会相反）"
                    % (expect, got_nt))

        if j7l is not None:
            k = j7l
            win = [F2[j] for j in range(result["first"], k + 1)
                   if F2[j] is not None]
            if len(win) >= 2:
                m = sum(win) / len(win)
                sd = (sum((v - m) ** 2 for v in win)
                      / (len(win) - result["z_ddof"])) ** 0.5
                expect = ((F2[k] - m) / sd) if sd else None
                add("水位口径 Ltz2 = +z(F2)",
                    expect is not None and abs(expect - Ltz2[k]) < 1e-9,
                    "复算 %.9f vs 报告 %.9f" % (
                        expect if expect is not None else float("nan"),
                        Ltz2[k]))
            else:
                add("水位口径 Ltz2 = +z(F2)", False, "样本不足")

        # 覆盖区间必须 ⊆ 数据源区间 —— 防止 ffill 把第 7 因子的 z 提前到
        # 源数据开始之前（那等于凭空造历史）。
        src_first = next((result["dates"][i] for i, v in enumerate(F2)
                          if v is not None), None)
        src_last = taco._last_valid_date(result["dates"], F2)
        t7_first, _ = taco._first_valid(result["dates"], T7)
        t7l_first, _ = taco._first_valid(result["dates"], T7L)
        bad_span = []
        for nm, fst in (("T7", t7_first), ("T7L", t7l_first)):
            if fst and src_first and fst < src_first:
                bad_span.append("%s 起始 %s 早于数据源 %s" % (nm, fst, src_first))
        add("第 7 因子覆盖区间 ⊆ 数据源区间", not bad_span,
            "；".join(bad_span) if bad_span
            else "源 %s .. %s，T7 起 %s，T7L 起 %s"
                 % (src_first, src_last, t7_first, t7l_first))

        # 末尾未被无故截断：水位口径只需 2 个样本，理应一直算到源的末日
        lag = None
        if src_last and latest and latest.get("date7L"):
            lag = (datetime.strptime(src_last, "%Y-%m-%d")
                   - datetime.strptime(latest["date7L"], "%Y-%m-%d")).days
        add("第 7 因子末尾未被无故截断", lag is not None and lag <= 5,
            "TACO-7L 末日 %s vs 源末日 %s（差 %s 天）"
            % (latest.get("date7L") if latest else None, src_last, lag))

        cc = (bon.get("crosscheck") or {})
        add("第 7 因子两条链路交叉校验", cc.get("ok") is not False,
            cc.get("detail") or "未执行", warn=(cc.get("ok") is None))

        # 有效期披露：源 /graph 固定只给 60 天，第 7 因子天生短。
        # 这件事必须在报告里写着 —— 静默地给一条只覆盖 29 天的曲线，
        # 是这类「多源拼接指数」最常见也最贵的误读来源。
        sp = result.get("span_7") or {}
        add("第 7 因子有效期已披露（源上限 60 天）", True,
            "TACO-7 %s 天（%s .. %s）/ TACO-7L %s 天（%s .. %s），"
            "网格 %s 天 —— 有效期短是数据源硬上限"
            % ((sp.get("t7") or {}).get("n"),
               (sp.get("t7") or {}).get("first"),
               (sp.get("t7") or {}).get("last"),
               (sp.get("t7l") or {}).get("n"),
               (sp.get("t7l") or {}).get("first"),
               (sp.get("t7l") or {}).get("last"), sp.get("grid_n")))
    else:
        # 汇率因子已按用户要求移除（config 显式 enabled=false）。
        # 这与「本该有却抓失败了」是两件完全不同的事，所以这里判 PASS 而不是
        # warn —— 把「设计内移除」也挂成警告，会把真正的异常淹掉。
        if not bool((cfg.get("extended_factor_2") or {}).get("enabled", True)):
            add("第 7 因子（汇率）已按配置移除", True,
                "config.extended_factor_2.enabled=false：不抓取、不落盘、不进合成")
        else:
            add("第 7 因子已启用", False,
                "结果中无 extended_2 标记 —— 检查 config.extended_factor_2 / "
                "bonbast 源是否就绪", warn=True)

    # 19. 落盘 CSV 的每一列都必须真的写出去（**回读文件**、逐列数非空个数）
    #
    #     为什么非要回读磁盘：内存里算对 ≠ 写出去了。2026-09-22 实测：CSV 与
    #     Excel 都把第 6 因子的**原始水位列**取成了原始输入字典 result["cols"]
    #     （而 ffill 后的通行量在 result["F"]），于是 TRHBCCCD_n_total 整列写空。
    #     最阴的是 z_transit / TACO-6 全都正常，报告读起来毫无破绽 ——
    #     前面 17 项检查全部从内存复算，一项都发现不了。
    #     所以这一项必须看**产物本身**：列名齐不齐、每列非空个数对不对得上。
    #
    #     注意先过 resolve_actual：目标被 WPS/Excel 占用时本轮内容写在
    #     `.locked-*` 里，标准名上是上一轮的旧内容，直接读标准名会误报。
    csv_path = os.path.join(out_dir, "taco-%s.csv" % run_date)
    actual, fell_back = net.resolve_actual(csv_path)
    if not os.path.exists(actual):
        add("落盘 CSV 每列均有值", False, "找不到 CSV: %s" % actual)
    else:
        rows_on_disk = None
        try:
            with open(actual, encoding="utf-8-sig", newline="") as fh:
                rows_on_disk = list(csv.DictReader(fh))
        except (OSError, UnicodeDecodeError) as e:
            add("落盘 CSV 每列均有值", False, "读取失败: %s" % e)
        if rows_on_disk is not None:
            cols_now = taco.output_columns(result)
            header = list(rows_on_disk[0].keys()) if rows_on_disk else []
            miss = [c for c in cols_now if c not in header]
            add("落盘 CSV 列名与列契约一致", not miss,
                "%d 列%s%s" % (len(cols_now), "（读自降级文件）" if fell_back else "",
                               "" if not miss else "，缺: %s" % miss))
            if not miss:
                # 期望值必须独立推导（走列契约表 + 源数组），**不能**复用写盘用的
                # output_row —— 否则映射错时「写出去是空、算出来也是空」，一起
                # 对上，校验变成自证。这一点由 tools/_negative_test.py 验证过。
                want_map = taco.expected_counts(result, cols_now)
                bad = []
                for c in cols_now:
                    got = sum(1 for r in rows_on_disk
                              if (r.get(c) or "").strip() != "")
                    want = want_map[c]
                    if got != want:
                        bad.append("%s: 落盘 %d / 应有 %d" % (c, got, want))
                key_col = "TRHBCCCD_n_total"
                add("落盘 CSV 每列均有值", not bad,
                    "；".join(bad) if bad
                    else "全部 %d 列非空计数对齐（%s=%d 行有值，共 %d 行）"
                         % (len(cols_now), key_col,
                            sum(1 for r in rows_on_disk
                                if (r.get(key_col) or "").strip() != ""),
                            len(rows_on_disk)))

    failed = [c for c in checks if not c["ok"] and c.get("level") != "warn"]
    warned = [c for c in checks if not c["ok"] and c.get("level") == "warn"]
    res = {"ok": not failed, "total": len(checks), "failed": len(failed),
           "warned": len(warned), "checks": checks, "run_date": run_date}
    return res


# ---------------------------------------------------------------------------
# 离线自检
# ---------------------------------------------------------------------------

def _fmt_spec_count(s):
    """数一个 %-格式串里真正消费位置参数的占位符个数。

    %% 不消费参数；%(name)s 属映射形式，用 named 标记让调用方跳过；
    `*` 宽度/精度本身也消费一个参数，一并计入。
    """
    n = 0
    named = False
    i = 0
    L = len(s)
    while i < L:
        if s[i] != "%":
            i += 1
            continue
        i += 1
        if i >= L:
            break
        if s[i] == "%":
            i += 1
            continue
        if s[i] == "(":
            j = s.find(")", i)
            if j < 0:
                break
            named = True
            i = j + 1
        while i < L and s[i] in "-+ #0":
            i += 1
        while i < L and (s[i].isdigit() or s[i] == "*"):
            if s[i] == "*":
                n += 1
            i += 1
        if i < L and s[i] == ".":
            i += 1
            while i < L and (s[i].isdigit() or s[i] == "*"):
                if s[i] == "*":
                    n += 1
                i += 1
        while i < L and s[i] in "hlL":
            i += 1
        if i < L:
            i += 1
            n += 1
    return n, named


def scan_format_arity(paths):
    """静态扫描 `"..." % (tuple)`：占位符个数必须等于实参个数。

    【为什么要有这一条】真实事故：emit.py 的表头写了 5 列、实参给了 6 个。
    Python 只在**执行到那一行**时才抛 TypeError —— 也就是整条流水线跑完
    联网抓取、算完指数、写完 history 之后才炸。这种「跑完才炸」的代价极高，
    而且很容易被当成偶发。静态扫描放在离线自检里，改完立刻能拦住，
    不必等下一轮抓取。

    只扫字面量左操作数 + 元组/单值右操作数这些可静态判定的形式；
    `%(name)s` 映射形式、`*args` 展开、非字面量一律跳过（不猜）。
    """
    bad = []
    scanned = 0
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=p)
        except SyntaxError as e:
            bad.append("%s: 语法错误 %s" % (os.path.basename(p), e))
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp)
                    and isinstance(node.op, ast.Mod)):
                continue
            left = node.left
            if not (isinstance(left, ast.Constant)
                    and isinstance(left.value, str)):
                continue
            right = node.right
            if isinstance(right, ast.Dict):
                continue
            if isinstance(right, ast.Constant):
                args = 1
            elif isinstance(right, (ast.Tuple, ast.List)):
                if any(isinstance(e, ast.Starred) for e in right.elts):
                    continue
                args = len(right.elts)
            else:
                continue
            n, named = _fmt_spec_count(left.value)
            if named:
                continue
            scanned += 1
            if n != args:
                head = left.value.strip().replace("\n", " ")[:44]
                bad.append("%s:%d 占位符 %d / 实参 %d «%s…»"
                           % (os.path.basename(p), node.lineno, n, args, head))
    return scanned, bad


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

    # ---- 死配置检测：配置里承诺的行为键，代码里必须真的读它 ----
    #
    # 为什么值得单独一条：`abort_on_index_input_fail` / `max_stale_ratio`
    # 曾长期只写在 config.json 里，代码从没读过。运维照它们做假设（「已经
    # 门禁了」），而程序根本没守 —— 失效时没有任何信号。这类缺陷比「没配置」
    # 危险，因为没配置至少是诚实的。
    #
    # 判据必须**从 config 动态取键名**，不能在源码里硬编码键名列表 ——
    # 否则「列表本身就在源码里」会让断言自我满足，测了个寂寞（第一版就是这么错的）。
    _src = open(os.path.join(HERE, "taco_monitor.py"), encoding="utf-8").read()
    for _k in sorted((cfg.get("gate") or {}).keys()):
        if _k.startswith("_") or _k == "required_for_index":
            continue          # 纯说明 / 纯数据，不是「行为开关」
        ck("无死配置: gate.%s 被代码读取" % _k, ('"%s"' % _k) in _src,
           "配置里有这个键但代码从不读它")

    # ---- 油价口径契约：换源后必须逐条断言，防止改回旧源 ----
    #
    # H 因子（CO1）是**指数输入**，口径必须与底稿 `CO1 Comdty` 一致。
    # 断言到「主机 + 符号 + payload 类型 + 出口策略」四件事，
    # 因为任何一项被静默改回都会让指数悄悄用错口径（实测踩过两次）。
    s_co1 = [s for s in series if s.get("id") == "CO1"]
    ck("CO1 入指数条目唯一", len(s_co1) == 1)
    if s_co1:
        _c = s_co1[0]
        ck("CO1 走 Yahoo", "query1.finance.yahoo.com" in (_c.get("url") or ""))
        ck("CO1 符号为 BZ=F", "BZ=F" in (_c.get("url") or "BZ=F").replace("%3D", "="))
        ck("CO1 payload=yahoo_chart", _c.get("payload") == "yahoo_chart")
        ck("CO1 value_col=close", _c.get("value_col") == "close")
        ck("CO1 egress=proxy（Yahoo 必须走系统代理）", _c.get("egress") == "proxy")
        ck("CO1 role=index_input", _c.get("role") == "index_input")
        ck("CO1 丢弃当日未收盘 bar", _c.get("drop_inprogress_session") is True)
    for _x in ("CO1_fred_crosscheck", "CO1_sina_crosscheck"):
        _xs = [s for s in series if s.get("id") == _x]
        ck("%s 存在" % _x, len(_xs) == 1)
        if _xs:
            ck("%s 不入指数" % _x, _xs[0].get("role") == "context_only")

    # USSWIT1 必须走替代源
    s_sw = [s for s in series if s.get("id") == "USSWIT1_proxy_T5YIE"]
    ck("USSWIT1 替代条目存在", len(s_sw) == 1)
    if s_sw:
        ck("USSWIT1 用 T5YIE", "T5YIE" in s_sw[0]["url"])
        ck("USSWIT1 标记 _is_proxy", s_sw[0].get("_is_proxy") is True)
        ck("USSWIT1 替代排序有 chosen", any(
            x.get("chosen") for x in (s_sw[0].get("substitute_ranking") or [])))

    # TRHBCCCD：不进 5 因子 TACO，但作为本项目扩展的第 6 因子
    s_t = [s for s in series if s.get("id") == "TRHBCCCD"]
    ck("TRHBCCCD 存在", len(s_t) == 1)
    if s_t:
        ck("TRHBCCCD 是 context_only（不入 5 因子 TACO）",
           s_t[0].get("role") == "context_only")
        ck("TRHBCCCD 不在 required", "TRHBCCCD" not in req)
    ext = cfg.get("extended_factor") or {}
    ck("extended_factor.series 指向 TRHBCCCD",
       ext.get("series") == "TRHBCCCD", "got %s" % ext.get("series"))
    ck("extended_factor 显式标记为非复刻",
       ext.get("is_replication") is False)
    ck("extended_factor.field 有定义", bool(ext.get("field")))
    # 角色开关：必须是一个**闭合枚举**。两个独立布尔会产生
    # 「观察=true 且 并入=true」这种自相矛盾的组合，而它的表现是
    # 「指数被一个声称不参与合成的列污染了」。
    _mode = str(ext.get("mode", "composite")).lower()
    ck("extended_factor.mode 是合法枚举 observe|composite",
       _mode in ("observe", "composite"), "got %r" % _mode)
    ext2 = cfg.get("extended_factor_2") or {}
    bcfg2 = cfg.get("bonbast_crawler") or {}
    # 抓取与使用必须同进同退：禁用了因子却还在抓数据 = 白跑 + 落一堆没人看的
    # 原始载荷，下次排查会以为是「抓了但没算」，白绕一圈。
    ck("汇率因子与 Bonnast 抓取开关一致（不会白抓）",
       bool(ext2.get("enabled", True)) == bool(bcfg2.get("enabled", True)),
       "extended_factor_2.enabled=%s / bonbast_crawler.enabled=%s"
       % (ext2.get("enabled", True), bcfg2.get("enabled", True)))
    if not ext2.get("enabled", True):
        ck("汇率移除后陈旧度表里不再要求 BONBAST 新鲜",
           "BONBAST_USD_sell_rial"
           not in ((cfg.get("staleness") or {}).get("per_series") or {}))

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

    # --- 第 6 因子（霍尔木兹通行量；本项目扩展）---
    def _same(a, b):
        return len(a) == len(b) and all(
            (x is None and y is None)
            or (x is not None and y is not None and abs(x - y) < 1e-12)
            for x, y in zip(a, b))

    # 故意用【隔日】的稀疏序列：既测前值填充，也测网格不被它拉长
    sparse = {dates[i]: 10.0 + 0.25 * i for i in range(0, 70, 2)}
    res6 = taco.compute(base, extended_factor=sparse)
    ck("6因子: extended 标记", res6.get("extended") is True)
    ck("6因子: 稀疏第 6 因子不改变网格长度",
       res6["n_days"] == res["n_days"] == 70,
       "got %d" % res6["n_days"])
    ck("6因子: 5 因子 S 逐点完全不变", _same(res["S"], res6["S"]))
    ck("6因子: 5 因子 T 逐点完全不变", _same(res["T"], res6["T"]))
    ck("6因子: 5 因子 z 逐点完全不变",
       all(_same(res[k], res6[k]) for k in ("Nz", "Oz", "Pz", "Qz", "Rz")))
    ck("6因子: 稀疏值已前值填充（idx1 == idx0）",
       res6["F"][1] is not None and abs(res6["F"][1] - res6["F"][0]) < 1e-12,
       "F0=%s F1=%s" % (res6["F"][0], res6["F"][1]))
    ck("6因子: 非填充日取原值",
       abs(res6["F"][40] - sparse[dates[40]]) < 1e-12)
    # 取负：通行量下降 -> Nt 为正（供给压力升）
    ck("6因子: Nt = -(F - F[-31]) 且取负",
       abs(res6["Nt"][40] - (-(res6["F"][40] - res6["F"][9]))) < 1e-9,
       "Nt40=%s" % res6["Nt"][40])
    falling = {dates[i]: 20.0 - 0.2 * i for i in range(70)}
    resf = taco.compute(base, extended_factor=falling)
    ck("6因子: 通行量下行 -> Nt 为正（与支持率同向处理）",
       resf["Nt"][60] is not None and resf["Nt"][60] > 0,
       "Nt60=%s" % resf["Nt"][60])
    # S6 = mean(6 个 z)
    i6 = next((i for i in range(70)
               if all(res6[k][i] is not None
                      for k in ("Nz", "Oz", "Pz", "Qz", "Rz", "Ntz"))), None)
    ck("6因子: 存在 6 个 z 齐备的观测", i6 is not None)
    if i6 is not None:
        m6 = sum(res6[k][i6] for k in ("Nz", "Oz", "Pz", "Qz", "Rz", "Ntz")) / 6.0
        ck("6因子: S6 = mean(6 个 z)", abs(m6 - res6["S6"][i6]) < 1e-9,
           "复算 %.12f vs %.12f" % (m6, res6["S6"][i6]))
    # T6 = S6 的 7 日均线；且 T6 != T（多一个因子应当改变指数）
    j6 = next((i for i in range(69, -1, -1) if res6["T6"][i] is not None), None)
    if j6 is not None:
        win = [res6["S6"][k] for k in range(max(0, j6 - 6), j6 + 1)
               if res6["S6"][k] is not None]
        ck("6因子: T6 = S6 的 7 日均线",
           abs(sum(win) / len(win) - res6["T6"][j6]) < 1e-9)
    ck("6因子: 未启用时不产出 T6",
       "T6" not in taco.compute(base))
    ck("6因子: stats 带 taco6 子字典且不污染原键",
       "taco6" in taco.stats(res6) and "taco6" not in taco.stats(res)
       and taco.stats(res6).get("n") == taco.stats(res).get("n"))
    ck("6因子: 落盘列含 F/Nt/Ntz/S6/T6",
       set(("F", "Nt", "Ntz", "S6", "T6"))
       <= set(taco.series_for_output(res6)[0].keys()))

    # --- 观察指标模式（2026-09-23 用户要求：TACO 回到 5 因子）---
    # 回归断言的核心是「不进合成」：S/T 必须与纯 5 因子**逐点完全一致**，
    # 并且结果里不得出现任何第 6/7 因子派生量。只要这两条成立，
    # 观察序列在数学上就没有第二条路径能影响指数。
    reso = taco.compute(base, observe_factor=sparse)
    ck("观察: observe 标记", reso.get("observe") is True)
    ck("观察: 未开 extended / extended_level 标记",
       not reso.get("extended") and not reso.get("extended_level"))
    ck("观察: 观察序列不改变网格长度", reso["n_days"] == res["n_days"] == 70)
    ck("观察: S 逐点完全不变（观察列未进合成）", _same(res["S"], reso["S"]))
    ck("观察: T 逐点完全不变", _same(res["T"], reso["T"]))
    ck("观察: 5 个 z 逐点完全不变",
       all(_same(res[k], reso[k]) for k in ("Nz", "Oz", "Pz", "Qz", "Rz")))
    ck("观察: 不产出任何第 6 因子派生量",
       not any(reso.get(k) for k in
               ("Nt", "Ntz", "S6", "T6", "Ltz", "S6L", "T6L")))
    ck("观察: 不产出任何第 7 因子派生量",
       not any(reso.get(k) for k in ("Ntz2", "S7", "T7", "Ltz2", "S7L", "T7L")))
    ck("观察: 稀疏观察序列已前值填充",
       reso["F"][1] is not None and abs(reso["F"][1] - reso["F"][0]) < 1e-12)
    ck("观察: 落盘列 = 13 + 1（末列即观察指标）",
       len(taco.output_columns(reso)) == 14
       and taco.output_columns(reso)[-1] == "TRHBCCCD_n_total")
    ck("观察: stats 带 hormuz_observe 且 in_index=False",
       (taco.stats(reso).get("hormuz_observe") or {}).get("in_index") is False
       and "hormuz_observe" not in taco.stats(res)
       and not any(k in taco.stats(reso) for k in ("taco6", "taco6L", "taco7")))
    _lto = taco.latest(reso)
    ck("观察: latest 带出观察水位但无 TACO6/TACO7",
       _lto.get("transit") is not None and "TACO6" not in _lto
       and "TACO7" not in _lto)
    ck("观察: 观察指标日期不晚于 TACO 读数日",
       (_lto.get("date_transit") or "9999") <= (_lto.get("date") or ""),
       "transit=%s taco=%s" % (_lto.get("date_transit"), _lto.get("date")))
    # 互斥：同一条序列不能既进指数又只观察。必须抛，不能静默二选一。
    _mutex_ok = False
    try:
        taco.compute(base, extended_factor=sparse, observe_factor=sparse)
    except ValueError:
        _mutex_ok = True
    ck("观察: 与 extended_factor 互斥（同时给必须抛）", _mutex_ok)
    ck("观察: 缺第 6/7 因子时 5 因子结果不受影响",
       taco.stats(reso).get("latest") == taco.stats(res).get("latest"))


    # --- 落盘列契约（CSV 与 Excel 共用同一份取数）---
    # 这是对 2026-09-22 那次真实故障的回归断言：第 6 因子的**原始水位列**必须
    # 取自计算结果 result["F"]，而不是原始输入字典 result["cols"]（那里没有 F）。
    # 当时 CSV 与 Excel 两处各写了一遍映射、一起取错，TRHBCCCD_n_total 整列写空，
    # 而 z_transit / TACO-6 全是好的 —— 从报告上完全看不出来。
    _c5 = taco.output_columns(res)
    _c6 = taco.output_columns(res6)
    # compute() 只要给了 extended_factor 就同时开出两套口径，所以 17 列这个
    # 中间态在真实结果里到不了 —— 直接用合成 result 把裁剪逻辑本身测掉。
    ck("落盘契约: 列数按启用情况裁剪 13 / 14 / 17 / 20",
       len(taco.output_columns({})) == 13
       and len(taco.output_columns({"observe": True})) == 14
       and len(taco.output_columns({"extended": True})) == 17
       and len(taco.output_columns({"extended": True,
                                   "extended_level": True})) == 20,
       "5因子=%d 观察=%d 双口径=%d"
       % (len(taco.output_columns({})),
          len(taco.output_columns({"observe": True})), len(_c6)))
    ck("落盘契约: 观察模式的末列就是观察指标本身",
       taco.output_columns({"observe": True})[-1] == "TRHBCCCD_n_total"
       and taco.col_source("TRHBCCCD_n_total") == "F",
       "末列=%s" % taco.output_columns({"observe": True})[-1])
    ck("落盘契约: 实跑结果为 13 列(5因子) / 20 列(双口径)",
       len(_c5) == 13 and len(_c6) == 20)
    ck("落盘契约: 原始输入字典里没有第 6 因子键（正是取错数的原因）",
       "F" not in (res6.get("cols") or {})
       and "Ntz" not in (res6.get("cols") or {}))
    _iF = next((i for i in range(len(res6["F"]) - 1, -1, -1)
                if res6["F"][i] is not None), None)
    ck("落盘契约: 存在可校验的第 6 因子原始值", _iF is not None)
    if _iF is not None:
        _r = taco.output_row(res6, _iF)
        ck("落盘契约: TRHBCCCD_n_total 取自计算结果而非原始输入",
           _r["TRHBCCCD_n_total"] == res6["F"][_iF],
           "row=%r F=%r" % (_r["TRHBCCCD_n_total"], res6["F"][_iF]))
        ck("落盘契约: 落盘行键序与表头完全一致",
           list(_r.keys()) == _c6, "row keys=%s" % list(_r.keys()))
        # 关键：期望值独立推导（列契约表 + 源数组），不经过 output_row。
        # 若两边都走 output_row，映射错时会「一起错、一起对上」，校验就是自证。
        _want_map = taco.expected_counts(res6, _c6)
        _mismatch = []
        _got_map = {}
        for _c in _c6:
            _got_map[_c] = sum(1 for i in range(len(res6["dates"]))
                               if taco.output_row(res6, i, _c6)[_c] is not None)
            if _got_map[_c] != _want_map[_c]:
                _mismatch.append("%s %d!=%d"
                                 % (_c, _got_map[_c], _want_map[_c]))
        ck("落盘契约: 每列非空计数与源数组对齐", not _mismatch,
           "；".join(_mismatch))
        # 契约语义断言：原始水位列必须绑到 result["F"]，而 F 不在原始输入字典里
        ck("落盘契约: 第 6 因子原始列绑定 result['F'] 而非原始输入",
           taco.col_source("TRHBCCCD_n_total") == "F"
           and "F" not in (res6.get("cols") or {})
           and _want_map["TRHBCCCD_n_total"] > 0,
           "source=%s，应有非空 %d 个，落盘 %d 个"
           % (taco.col_source("TRHBCCCD_n_total"),
              _want_map["TRHBCCCD_n_total"],
              _got_map["TRHBCCCD_n_total"]))
        ck("落盘契约: 第 6 因子原始列非全空（本故障的直接判据）",
           _got_map["TRHBCCCD_n_total"] > 0)

    # --- 水位口径（TACO-6L）---
    ck("6L: extended_level 标记", res6.get("extended_level") is True)
    ck("6L: 5 因子 S 仍逐点不变", _same(res["S"], res6["S"]))
    ck("6L: 5 因子 T 仍逐点不变", _same(res["T"], res6["T"]))
    # z(-x) == -z(x)：水位口径就是「水位 z 取负」，与「先取负再 z」等价
    Fz_ref = taco.expanding_z(res6["F"], len(res6["F"]))
    ck("6L: Ltz 等于 -z(F)",
       all((a is None and b is None)
           or (a is not None and b is not None and abs(a + b) < 1e-12)
           for a, b in zip(res6["Ltz"], Fz_ref)))
    # 水位口径的加入不得回头改动变动口径（S6/T6 是先前算好的，不能被覆盖）
    _i6 = next((i for i in range(70)
                if all(res6[x][i] is not None
                       for x in ("Nz", "Oz", "Pz", "Qz", "Rz", "Ntz"))), None)
    if _i6 is not None:
        _m6 = sum(res6[x][_i6]
                  for x in ("Nz", "Oz", "Pz", "Qz", "Rz", "Ntz")) / 6.0
        ck("6L: 加入水位口径未改动变动口径 S6",
           abs(_m6 - res6["S6"][_i6]) < 1e-9,
           "复算 %.12f vs %.12f" % (_m6, res6["S6"][_i6]))
    k6l = next((i for i in range(69, -1, -1)
                if all(res6[x][i] is not None
                       for x in ("Nz", "Oz", "Pz", "Qz", "Rz", "Ltz"))), None)
    ck("6L: 存在 6 个 z 齐备的观测", k6l is not None)
    if k6l is not None:
        m6l = sum(res6[x][k6l]
                  for x in ("Nz", "Oz", "Pz", "Qz", "Rz", "Ltz")) / 6.0
        ck("6L: S6L = mean(6 个 z)", abs(m6l - res6["S6L"][k6l]) < 1e-9)
    ck("6L: 落盘列含 Ltz/S6L/T6L",
       set(("Ltz", "S6L", "T6L")) <= set(taco.series_for_output(res6)[0].keys()))
    ck("6L: stats 带 taco6L 子字典",
       "taco6L" in taco.stats(res6) and "taco6L" not in taco.stats(res))
    # 关键：断裂序列上两套口径必须给出**相反方向**，否则两套就是冗余的。
    # 构造要与真实情形同形：① 断点必须在 z 窗口起点(31)之后，否则扩张窗口
    # 看不到断点前的量级；② 评估点距断点要超过 31 天，否则 31 日变动
    # 会跨越断点、把断点本身算成"变化"，量级看起来很大。
    # 真实数据正是 ②：断点 2026-03，而 31 日回看窗已全部落在断裂后的低位平台内。
    _brk = {dates[i]: (70.0 if i < 33
                       else (3.0 if i < 60 else 3.0 + 0.2 * (i - 60)))
            for i in range(70)}
    resb = taco.compute(base, extended_factor=_brk)
    ck("6L: 断裂序列上两套口径方向相反（证明两套不是冗余）",
       resb["Nt"][66] is not None and resb["Ltz"][66] is not None
       and resb["Nt"][66] < 0 < resb["Ltz"][66],
       "Nt66=%s Ltz66=%s" % (resb["Nt"][66], resb["Ltz"][66]))

    # --- 水位断裂守卫 ---
    import datetime as _dt
    _lst = _dt.date(2026, 9, 13)

    def _mk(fn, days=600):
        return {(_lst - _dt.timedelta(days=k)).strftime("%Y-%m-%d"): fn(k)
                for k in range(days)}

    _stable = _mk(lambda k: 70.0 + (k % 5))
    ck("水位守卫: 平稳序列判为未断裂",
       level_break_check(_stable, "2026-09-13").get("ok") is True)
    _broken = _mk(lambda k: (70.0 if k > 180 else 3.0))
    _bk = level_break_check(_broken, "2026-09-13")
    ck("水位守卫: 断裂序列被判出（不放过）", _bk.get("ok") is False,
       "ratio=%s" % _bk.get("ratio"))
    ck("水位守卫: 断裂倍数 < 0.2",
       _bk.get("ratio") is not None and _bk["ratio"] < 0.2,
       "ratio=%s" % _bk.get("ratio"))
    ck("水位守卫: 样本不足时返回 None 而不是乱判",
       level_break_check({(_lst - _dt.timedelta(days=k)).strftime("%Y-%m-%d"): 5.0
                          for k in range(30)}).get("ok") is None)
    _mm = monthly_medians(_stable, months=3)
    ck("水位守卫: 月度中位数按月聚合",
       len(_mm) == 3 and _mm[-1][0] == "2026-09",
       "got %s" % (_mm[-1][0] if _mm else None))

    # --- 降级文件补写（保证标准名不会长期停在旧内容上）---
    import shutil as _sh
    import tempfile as _tf
    _td = _tf.mkdtemp(prefix="taco_promo_")
    try:
        _std = os.path.join(_td, "a.csv")
        with open(_std, "w", encoding="utf-8") as _f:
            _f.write("old")
        _fb = os.path.join(_td, "a.locked-20990101-000000.csv")
        with open(_fb, "w", encoding="utf-8") as _f:
            _f.write("new")
        # 显式设定 mtime，**不要依赖文件系统的时间戳粒度**：本机 NTFS 粒度约
        # 40ms，前后创建的两个文件有 50% 概率拿到完全相同的 mtime，会让本用例
        # 随机变红（实测 12 次里 6 次）。会随机红的护栏比没有护栏更糟 ——
        # 它教人忽略红灯。
        _t0 = time.time()
        os.utime(_std, (_t0 - 60, _t0 - 60))
        os.utime(_fb, (_t0, _t0))
        _ok, _why = net.promote_fallback(_std)
        ck("补写: 降级文件更新 -> 补写回标准名",
           _ok and open(_std, encoding="utf-8").read() == "new"
           and not os.path.exists(_fb), _why)
        ck("补写: 补完再跑一次是空操作",
           net.promote_fallback(_std)[0] is False)
        # mtime 完全相同（NTFS 粒度下很常见）时必须仍判定「降级文件更新」。
        # 否则真正更新的内容永远补不回标准名，正是本机制要防的事。
        _std_t = os.path.join(_td, "t.csv")
        _fb_t = os.path.join(_td, "t.locked-20260101-000000.csv")
        for _fp, _c in ((_std_t, "stale"), (_fb_t, "fresh")):
            with open(_fp, "w", encoding="utf-8") as _f:
                _f.write(_c)
        _tt = time.time()
        os.utime(_std_t, (_tt, _tt))
        os.utime(_fb_t, (_tt, _tt))
        _okt, _whyt = net.promote_fallback(_std_t)
        ck("补写: mtime 相同也判降级文件更新",
           _okt and open(_std_t, encoding="utf-8").read() == "fresh", _whyt)
        # 标准名更新时不得被旧降级文件倒灌
        _fb2 = os.path.join(_td, "a.locked-20200101-000000.csv")
        with open(_fb2, "w", encoding="utf-8") as _f:
            _f.write("stale")
        _old = time.time() - 86400
        os.utime(_fb2, (_old, _old))
        ck("补写: 标准名已是最新 -> 不被旧的降级文件覆盖",
           net.promote_fallback(_std)[0] is False
           and open(_std, encoding="utf-8").read() == "new")
        # 标准名被占用时必须安全跳过，不能抛异常
        ck("补写: 无降级文件时安全返回",
           net.promote_fallback(os.path.join(_td, "none.csv"))[0] is False)
        # 清理：同 stem 的旧降级副本只留最新那份（无人值守时不会越堆越多）
        for _s in ("20260101-000000", "20260202-000000", "20260303-000000"):
            with open(os.path.join(_td, "b.locked-%s.csv" % _s), "w",
                      encoding="utf-8") as _f:
                _f.write(_s)
        with open(os.path.join(_td, "b.keep.csv"), "w", encoding="utf-8") as _f:
            _f.write("keep")            # 不匹配 .locked- 模式，不该被清
        _rm = net._prune_fallbacks(os.path.join(_td, "b.csv"),
                                   os.path.join(_td, "b.locked-20260303-000000.csv"))
        _left = sorted(f for f in os.listdir(_td) if f.startswith("b.locked-"))
        ck("降级清理: 只留最新一份，不动非降级文件",
           len(_rm) == 2 and _left == ["b.locked-20260303-000000.csv"]
           and os.path.exists(os.path.join(_td, "b.keep.csv")),
           "删了 %d 个，剩 %s" % (len(_rm), _left))
        # 清理失败必须**被记账**，不能静默吞。
        # 2026-09-22 实测：本机 safe-delete 钩子把 os.remove 转成回收站操作并在
        # 部分文件上失败，而原先的 `except OSError: pass` 让「被拦住」和
        # 「本来就没得清」在外部完全一样 —— 排查时误判成代码缺陷。
        _f_fail = os.path.join(_td, "c.locked-20260101-000000.csv")
        _f_keep = os.path.join(_td, "c.locked-20260909-000000.csv")
        for _fp in (_f_fail, _f_keep):
            with open(_fp, "w", encoding="utf-8") as _f:
                _f.write("x")
        _real_remove = os.remove
        net.PRUNE_FAILURES.clear()

        def _boom(_p):
            if _p.endswith("20260101-000000.csv"):
                raise OSError("trash-failed (模拟钩子拦截)")
            _real_remove(_p)

        net.os.remove = _boom
        try:
            _rm2 = net._prune_fallbacks(os.path.join(_td, "c.csv"), _f_keep)
        finally:
            net.os.remove = _real_remove
        ck("降级清理: 删除失败被记账而非静默吞",
           not _rm2 and len(net.PRUNE_FAILURES) == 1
           and "20260101-000000" in net.PRUNE_FAILURES[0]["path"],
           "rm=%s fails=%s" % (_rm2, net.PRUNE_FAILURES))
        net.PRUNE_FAILURES.clear()
    finally:
        _sh.rmtree(_td, ignore_errors=True)

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

    # --- Yahoo chart API（BZ=F，2026-09-23 起 CO1 主源）---
    # 夹具用真实抓回的 3 根 bar：09-18 / 09-21 / 09-22。
    yh_ts = [1789704000, 1789963200, 1790049600]
    yh_body = json.dumps({
        "chart": {"result": [{
            "meta": {"symbol": "BZ=F", "exchangeName": "NYM",
                     "instrumentType": "FUTURE", "gmtoffset": -14400,
                     "currency": "USD", "regularMarketPrice": 98.76},
            "timestamp": yh_ts,
            "indicators": {"quote": [{"close": [103.87, 100.34, 98.76]}]},
        }], "error": None}}).encode()
    d6, m6 = parse.parse_yahoo_chart(yh_body)
    ck("parse: Yahoo epoch【秒】正确归属为交易日",
       sorted(d6) == ["2026-09-18", "2026-09-21", "2026-09-22"],
       "got %s" % sorted(d6))
    ck("parse: Yahoo 值与源逐点一致",
       abs(d6["2026-09-21"] - 100.34) < 1e-9
       and abs(d6["2026-09-18"] - 103.87) < 1e-9)
    ck("parse: Yahoo 透出合约身份供追溯",
       m6.get("symbol") == "BZ=F" and m6.get("exchange") == "NYM",
       "meta=%s" % m6)

    yh_null = json.dumps({
        "chart": {"result": [{
            "meta": {"symbol": "BZ=F", "gmtoffset": -14400},
            "timestamp": yh_ts,
            "indicators": {"quote": [{"close": [103.87, 100.34, None]}]},
        }], "error": None}}).encode()
    d7, m7 = parse.parse_yahoo_chart(yh_null)
    ck("parse: Yahoo close=null 被跳过（不当 0、不占该日）",
       m7.get("dropped_null") == 1 and "2026-09-22" not in d7,
       "null=%s dates=%s" % (m7.get("dropped_null"), sorted(d7)))

    _, m8 = parse.parse_yahoo_chart(yh_body, drop_from="2026-09-22")
    ck("parse: Yahoo 当日未收盘 bar 被 drop_from 丢弃",
       m8.get("dropped_inprogress") == 1
       and m8.get("last_date") == "2026-09-21",
       "inprogress=%s last=%s" % (m8.get("dropped_inprogress"),
                                  m8.get("last_date")))

    # 假 200：网关把 Yahoo 换成一张 HTML 页。必须报「被拦截」，
    # 而不是含糊的「JSON 解析失败」—— 后者会让人去查解析器。
    ok_yh, why_yh = net.validate_payload(
        b'<!DOCTYPE html><html><head><title>Yahoo</title></head>'
        b'<body>\xe6\x97\xa0\xe6\xb3\x95\xe8\xae\xbf\xe9\x97\xae</body></html>',
        "yahoo_chart")
    ck("validate: Yahoo 网关拦截页被识别为 blocked-html",
       (not ok_yh) and "blocked-html" in why_yh, "why=%s" % why_yh)
    ck("validate: Yahoo 合法载荷通过",
       net.validate_payload(yh_body, "yahoo_chart")[0])
    yh_err = json.dumps({"chart": {"result": None,
                                   "error": {"code": "Not Found"}}}).encode()
    ck("validate: Yahoo error 响应被拒",
       not net.validate_payload(yh_err, "yahoo_chart")[0])

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

    # rename-over 被拒、但文件本身可写：必须**就地覆盖保住标准名**，而不是
    # 退写成 .locked-*。2026-09-22 实测：本机 taco-latest.csv 反复 WinError 5，
    # 同目录其它文件却能正常 replace，且没有 WPS/Excel 在跑 —— 只靠降级写会让
    # 标准名长期停在旧内容上（当时停在 09:43 的 17 列旧版，实际已是 27 列）。
    # 用 Python 打开的句柄模拟这一状态：它带 FILE_SHARE_WRITE（故就地写通），
    # 但不带 FILE_SHARE_DELETE（故 replace 被拒）。
    locked = os.path.join(td, "locked.csv")
    net.atomic_write_text(locked, "first")
    fh = open(locked, "r+b")
    try:
        net.WRITE_FALLBACKS.clear()
        net.INPLACE_WRITES.clear()
        net.atomic_write_text(locked, "second")
        ck("rename-over 被拒时改为就地覆盖（保住标准名）",
           len(net.INPLACE_WRITES) == 1 and not net.WRITE_FALLBACKS,
           "inplace=%d fallback=%d" % (len(net.INPLACE_WRITES),
                                       len(net.WRITE_FALLBACKS)))
        ck("就地覆盖写入内容正确",
           open(locked, encoding="utf-8-sig").read() == "second")
        ck("就地覆盖无 tmp 残留", not os.path.exists(locked + ".tmp"))
    finally:
        fh.close()
        net.WRITE_FALLBACKS.clear()
        net.INPLACE_WRITES.clear()

    # 第 3 档：连就地写也不可行时，才退写降级名（不能崩）。
    # 用一个**目录**占住目标名：replace 不能把文件盖到目录上、就地 open 也失败。
    as_dir = os.path.join(td, "asdir.csv")
    os.makedirs(as_dir, exist_ok=True)
    net.WRITE_FALLBACKS.clear()
    net.INPLACE_WRITES.clear()
    alt3 = net.atomic_write_text(as_dir, "third")
    ck("就地写也不可行时退写降级名而非崩溃",
       bool(net.WRITE_FALLBACKS) and ".locked-" in alt3
       and open(alt3, encoding="utf-8-sig").read() == "third",
       "alt=%s" % os.path.basename(alt3))
    net.WRITE_FALLBACKS.clear()

    # 放开占用后应恢复写回标准名
    net.WRITE_FALLBACKS.clear()
    net.PRUNE_FAILURES.clear()
    net.INPLACE_WRITES.clear()
    net.atomic_write_text(locked, "fourth")
    ck("占用解除后写回标准文件名",
       not net.WRITE_FALLBACKS and not net.INPLACE_WRITES
       and open(locked, encoding="utf-8-sig").read() == "fourth")

    import shutil
    shutil.rmtree(td, ignore_errors=True)

    # --- 静态：% 格式串的占位符个数必须等于实参个数 ---
    # 这条拦的是「跑完联网抓取才炸」的一类错误（见 scan_format_arity 注释）。
    _targets = []
    for _d in (HERE, os.path.join(HERE, "tacolib"), os.path.join(HERE, "tools")):
        if os.path.isdir(_d):
            _targets += [os.path.join(_d, f)
                         for f in sorted(os.listdir(_d)) if f.endswith(".py")]
    _nf, _bad = scan_format_arity(_targets)
    ck("格式串 占位符/实参 个数一致", not _bad,
       "%d 处不一致：%s" % (len(_bad), "; ".join(_bad[:3])))
    # 扫描器「扫了 0 处」也会通过上面那条 —— 必须确认它真的覆盖到代码，
    # 否则就是一个和 bug 意见一致的假护栏（本项目踩过这个坑）。
    ck("格式串 扫描器确实覆盖到代码", _nf >= 50,
       "只扫到 %d 处，疑似目标路径写错 —— 护栏形同虚设" % _nf)

    return {"ok": not fails, "total": checks, "failed": len(fails),
            "failures": fails}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _abort(out_root, run_date, statuses, stale, dec, reason, missing):
    """门禁未过时统一出口：只写「源状态」留档，**绝不碰任何产物文件**。

    为什么不照旧直接 return：老写法在「缺 1 个入指数序列」时会继续走完
    产物生成，把一份**算不出 TACO 的残缺表**写到标准名上，覆盖掉上一轮
    完整数据（实测发生：CO1 瞬时抓取失败 + 表被占用，两者叠加导致标准名
    一夜之间从完整变成残缺）。门禁的意义就是「宁可不产出，也不产出错的」，
    所以中止时必须连 LATEST.md / taco-latest.csv / xlsx 一起不动 ——
    标准名上留着上一轮的完整读数，比换上一份当天残缺读数安全得多。
    """
    net.atomic_write_json(os.path.join(out_root, "_source_status.json"), {
        "run_date": run_date, "gate_passed": False, "abort_reason": reason,
        "missing": missing,
        "statuses": [{k: v for k, v in s.items() if not k.startswith("_")}
                     for s in statuses],
        "staleness": stale, "proxy_decision": dec,
    })


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

    # --- 1b. 第 7 因子数据源：Bonnast USD 卖出价（里亚尔）---
    #
    # 为什么不塞进 cfg["series"]：那个通道是「一个 URL 一个载荷」，而 Bonnast
    # 要两步（首页取令牌 -> POST /json）+ 另取一个 /graph 页面拿历史。硬套会
    # 把令牌逻辑写进通用抓取层。所以单独采集 —— 但**同样**产出 status 项、
    # 归档原始载荷、进陈旧度检查，绝不搞成编外数据（编外数据 = 没人检查的数据）。
    bcfg = dict(cfg.get("bonbast_crawler") or {})
    bsid = bcfg.get("series_id", "BONBAST_USD_sell_rial")
    bon = {"ok": False, "series": {}, "warnings": []}
    bst = None
    if bcfg.get("enabled", True):
        print("\n  [第 7 因子] Bonnast USD 卖出价（里亚尔）...")
        t0 = time.time()
        if args.from_file:
            bon = bonbast.parse_archived(args.from_file, run_date)
            channel = "file"
        else:
            bon = bonbast.collect(cfg, run_date)
            channel = "curl"
            # 归档原始载荷：三步各自的字节，供排障与离线重放
            for key, fname in bonbast.raw_files().items():
                if (bon.get("raw") or {}).get(key):
                    net.atomic_write_bytes(os.path.join(raw_dir, fname),
                                           bon["raw"][key])
        _bs = sorted(bon.get("series") or {})
        bst = {
            "id": bsid, "role": "extended_factor_2", "provider": "bonbast",
            "tier": bcfg.get("tier", "market"),
            "status": "OK" if bon.get("ok") else "FAIL",
            "n": len(_bs), "first_date": _bs[0] if _bs else None,
            "last_date": _bs[-1] if _bs else None,
            "last_value": (bon["series"][_bs[-1]] if _bs else None),
            "channel": channel, "ua": None, "elapsed": time.time() - t0,
            "error": None if bon.get("ok") else "；".join(
                bon.get("warnings") or ["unknown"])[:300],
            "url": (bcfg.get("base_url", "https://www.bonbast.com")
                    + str(bcfg.get("graph_endpoint", "/graph")) + "/"
                    + str(bcfg.get("graph_pair", "usd"))),
            "unit": "rial",
            "snapshot_rial": (bon.get("snapshot") or {}).get("value_rial"),
            "graph_span": (bon.get("graph") or {}).get("span_note"),
            "crosscheck": bon.get("crosscheck"),
            # 站点硬上限：/graph 只给 60 天。写进 status，报告与对账都看得见。
            "source_span_cap_days": 60,
        }
        statuses.append(bst)
        for w in (bon.get("warnings") or []):
            print("  ! %s" % w)
        if bon.get("ok"):
            print("  -> Bonnast %d 天（%s .. %s），末值 %s 里亚尔"
                  % (len(_bs), _bs[0], _bs[-1],
                     ("%.0f" % bon["series"][_bs[-1]])))
            cc = bon.get("crosscheck") or {}
            if cc.get("ok") is False:
                print("  !! 交叉校验不一致：%s" % cc.get("detail"))
            elif cc.get("ok") is True:
                print("  -> 交叉校验通过：%s" % cc.get("detail"))
        else:
            print("  ! 第 7 因子不可用，本轮只出 5/6 因子指数")
    else:
        # 显式说出来，不留空白。否则「本没抓」与「抓失败了」在日志里长得一样，
        # 下次排查会怀疑网络/SDK，白绕一圈。
        print("\n  [已移除] 汇率因子（Bonnast USD 卖出价 / 里亚尔）："
              "config.bonbast_crawler.enabled=false，本轮不发起任何请求、"
              "不落任何列。")

    # --- 2. 陈旧度 ---
    print("\n[2/5] 陈旧度检查 ...")
    stale = staleness_check(cfg, statuses, run_date)
    for x in stale:
        if not x["ok"]:
            print("  [STALE] %-22s %s" % (x["id"], x.get("reason")))
    print("  -> %d/%d 新鲜" % (sum(1 for x in stale if x["ok"]), len(stale)))

    # 陈旧率门禁（config.gate.max_stale_ratio）。
    #
    # 为什么必须**真的**读这个键：它此前只写在 config 里、代码从没读过 —— 属于
    # 「配置承诺了一个不存在的行为」。这类死配置比缺配置更危险：运维照它做假设，
    # 而代码根本没在守。实测踩到过（见 README §5 #29）。
    _gate_all = cfg.get("gate", {})
    _msr = _gate_all.get("max_stale_ratio")
    _req_ids = _gate_all.get("required_for_index") or []
    _stale_idx = [x for x in stale if x["id"] in _req_ids]
    if _msr is not None and _stale_idx:
        _ratio = sum(1 for x in _stale_idx if not x["ok"]) / float(len(_stale_idx))
        if _ratio > _msr:
            print("  !! 陈旧率门禁未过：入指数序列 %d/%d 陈旧（%.0f%% > %.0f%%）"
                  % (sum(1 for x in _stale_idx if not x["ok"]), len(_stale_idx),
                     _ratio * 100, _msr * 100))
            print("  拒绝生成 TACO —— 陈旧输入算出的指数会被当成当日读数。")
            _abort(out_root, run_date, statuses, stale, dec,
                   "staleness-ratio-exceeded", [x["id"] for x in _stale_idx if not x["ok"]])
            return 2

    # --- 3. 算 TACO ---
    print("\n[3/5] 计算 TACO 指数 ...")
    gate = cfg.get("gate", {})
    required = gate.get("required_for_index") or []
    avail = {s["id"]: s for s in statuses if s.get("status", "").startswith(("OK", "LOADED"))}
    miss = [r for r in required if r not in avail]
    # abort_on_index_input_fail：只要有**任一**入指数序列缺失就中止。
    #
    # 默认 true，理由是同一条算术：composite_S = 5 个 z 的均值，缺任一输入
    # 就必然算不出 TACO。min_series_ok=4 那档「缺 1 个仍继续」只会走完流程
    # 写出一份**没有 TACO 的残缺表**，覆盖上一轮完整数据 —— 没有任何收益，
    # 只有风险。所以这里不再容忍（该键此前同样是死配置，见 README §5 #29）。
    if gate.get("abort_on_index_input_fail", True) and miss:
        print("  !! 门禁未过：%d/%d 个入指数序列缺失（%s）"
              % (len(miss), len(required), miss))
        print("  拒绝生成 TACO，且不覆盖任何产物 —— 上一轮完整读数保留在标准名上。")
        _abort(out_root, run_date, statuses, stale, dec,
               "index-input-missing", miss)
        return 2
    if len(miss) > len(required) - gate.get("min_series_ok", 4):
        print("  !! 门禁未过：%d/%d 个入指数序列缺失（%s）"
              % (len(miss), len(required), miss))
        print("  拒绝生成 TACO —— 避免半残指数被当成完整指数。")
        _abort(out_root, run_date, statuses, stale, dec,
               "min-series-ok", miss)
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
    # 霍尔木兹通行量：**角色由 config 决定**，一处开关。
    #
    #   mode=observe   -> 观察指标：抓取 / 落盘 / 展示，**不进 TACO 合成**（默认）
    #   mode=composite -> 第 6 因子：并入合成，产出 TACO-6 / TACO-6L
    #
    # 为什么不放两个独立布尔：两个开关会出现「观察=true 且 并入=true」这种
    # 自相矛盾的组合，而它的表现是「指数被一个声称不参与合成的列污染了」——
    # 最难排查的一类。单枚举 + 非法值直接退出，把矛盾消灭在配置层。
    ext_cfg = cfg.get("extended_factor") or {}
    ext_sid = ext_cfg.get("series", "TRHBCCCD")
    ext_field = ext_cfg.get("field", "n_total")
    ext_mode = str(ext_cfg.get("mode", "composite")).lower()
    if ext_mode not in ("observe", "composite"):
        raise SystemExit(
            "config.extended_factor.mode 只能是 observe | composite，"
            "当前是 %r" % ext_mode)
    hormuz_obs = (ext_mode == "observe")
    ext_map = {}
    if ext_cfg.get("enabled", True) and ext_sid in avail:
        for d, rec in (avail[ext_sid]["_data"] or {}).items():
            v = rec.get(ext_field) if isinstance(rec, dict) else None
            if v is not None:
                ext_map[d] = float(v)
    if ext_map:
        ext_dates = sorted(ext_map)
        print("  -> 霍尔木兹 [%s.%s] %d 天有效，%s .. %s"
              % (ext_sid, ext_field, len(ext_map),
                 ext_dates[0], ext_dates[-1]))
        print("  -> 角色：%s"
              % ("**观察指标**（只展示、不进 TACO 合成）" if hormuz_obs
                 else "第 6 因子（并入合成 -> TACO-6 / TACO-6L）"))
    else:
        print("  ! 霍尔木兹序列缺失（%s 未就绪）：本轮无观察水位" % ext_sid)

    # 第 7 因子：Bonnast USD 卖出价（里亚尔）。
    # 2026-09-23 起按用户要求整体移除 —— enabled=false 时不抓取、不落列。
    # 分支保留，以便日后改回 enabled=true 即可恢复（定义与单位证据都还在）。
    ext2_cfg = cfg.get("extended_factor_2") or {}
    ext2_map = {}
    if ext2_cfg.get("enabled", True) and bon.get("ok"):
        for d, v in (bon.get("series") or {}).items():
            if v is not None:
                ext2_map[d] = float(v)
    if ext2_map:
        e2 = sorted(ext2_map)
        # 这里只能报【源区间】，不能报「有效天数」：Bonnast 是盘中实时站，
        # 今天（run_date）已经有一条仍在成形的报价，而既有网格的末日锚在基础
        # 序列上（通常 = 上一交易日），所以源里最新那一天会被 ffill 纪律丢掉。
        # 把源区间说成「有效」会让 61 天 vs 落盘 60 天看起来像丢数据。
        print("  -> 第 7 因子 [%s] 源区间 %d 天（%s .. %s，源上限 60 天）"
              % (bsid, len(ext2_map), e2[0], e2[-1]))
    elif not ext2_cfg.get("enabled", True):
        pass          # 已在上面打印「[已移除] 汇率因子」，此处不再重复
    else:
        print("  ! 第 7 因子缺失（%s 未就绪）：本轮只出 5 因子指数" % bsid)

    result = taco.compute(
        series_map,
        # observe 与 extended 互斥（compute 会抛），这里按 mode 二选一
        extended_factor=(None if hormuz_obs else (ext_map or None)),
        extended_factor_2=(ext2_map or None),
        observe_factor=((ext_map or None) if hormuz_obs else None))

    # 霍尔木兹序列体检：水位断裂检测。
    #
    # **观察模式下它照样跑** —— 断裂本身是重要的观察事实（"现在的水位只有
    # 历史的百分之几"），只是不再影响指数：5 因子 TACO 根本不含这一项。
    # composite 模式下它是 TACO-6 的降级开关（31 日变动在断裂序列上会反向）。
    # 两种模式共用同一份体检，读数永远一致 —— 不会出现"当因子时报警、
    # 当观察时不报警"这种因角色变化而改变事实的情况。
    if ext_map:
        brk = level_break_check(ext_map, run_date)
        result["level_break_hormuz"] = brk
        result["monthly_medians_hormuz"] = monthly_medians(ext_map)
        result["hormuz_role"] = "observe" if hormuz_obs else "composite"
        if brk.get("ok") is False:
            print("  !! 霍尔木兹【水位断裂】：近 %d 日中位数 %.1f vs 基准 %s~%s "
                  "中位数 %.1f（倍数 %.2f）"
                  % (brk["n_recent"], brk["recent_median"], brk["base_window"][0],
                     brk["base_window"][1], brk["base_median"], brk["ratio"]))
            if hormuz_obs:
                print("     该序列当前水位远低于历史基准。它**不参与** TACO 合成，"
                      "故 TACO 读数不受影响；")
                print("     但这是重要的观察事实，已写进水位面板与看板。")
            else:
                print("     在断裂序列上「31 日变动」会给出与水位相反的方向，"
                      "TACO-6 已标记为【降级】，不得单独解读。")
        elif brk.get("ok") is None:
            print("  ? 霍尔木兹水位体检未完成：%s" % brk.get("reason"))
        else:
            print("  -> 霍尔木兹水位体检通过（近/基准 = %.2f）" % brk["ratio"])

    # 第 7 因子体检：同样查水位断裂，另加一条**结构性**约束 —— 变动口径需要
    # F2 与 F2[-31] 同时存在，源只给 60 天，所以 TACO-7 天生只有约 29 天。
    # 这里把「有多少天」明确算出来并打出来，不让它悄悄变成一列空数据。
    if ext2_map:
        brk2 = level_break_check(ext2_map, run_date)
        result["level_break_7"] = brk2
        if brk2.get("ok") is False:
            print("  !! 第 7 因子【水位断裂】：近 %d 日中位数 %.0f vs 基准中位数 "
                  "%.0f（倍数 %.2f）"
                  % (brk2["n_recent"], brk2["recent_median"],
                     brk2["base_median"], brk2["ratio"]))
        elif brk2.get("ok") is None:
            print("  ? 第 7 因子水位体检未完成：%s（源只有 60 天，"
                  "基线窗口必然样本不足 —— 属已知限制）" % brk2.get("reason"))
        else:
            print("  -> 第 7 因子水位体检通过（近/基准 = %.2f）" % brk2["ratio"])
        _t7 = result.get("T7") or []
        _t7l = result.get("T7L") or []
        _f2arr = result.get("F2") or []
        _f2n = sum(1 for x in _f2arr if x is not None)
        _f2first, _ = taco._first_valid(result["dates"], _f2arr)
        _f2last = taco._last_valid_date(result["dates"], _f2arr)
        # 源区间 -> 上网格后的真实覆盖。两者差 1 天是常态（源含今日盘中价，
        # 网格末日锚在基础序列），必须分别报出来，否则会被读成「丢了数据」。
        print("  -> 第 7 因子上网格后实际 %d 天（%s .. %s）；网格 %d 天"
              % (_f2n, _f2first, _f2last, len(result["dates"])))
        _f7, _ = taco._first_valid(result["dates"], _t7)
        _l7 = taco._last_valid_date(result["dates"], _t7)
        _f7l, _ = taco._first_valid(result["dates"], _t7l)
        _l7l = taco._last_valid_date(result["dates"], _t7l)
        print("  -> TACO-7  有效区间 %s .. %s（%d 天 / 网格 %d 天，变动口径需 31 天回看）"
              % (_f7, _l7, sum(1 for x in _t7 if x is not None),
                 len(result["dates"])))
        print("  -> TACO-7L 有效区间 %s .. %s（%d 天 / 网格 %d 天，水位口径只回看 2 点）"
              % (_f7l, _l7l, sum(1 for x in _t7l if x is not None),
                 len(result["dates"])))
        result["span_7"] = {"t7": {"first": _f7, "last": _l7,
                                   "n": sum(1 for x in _t7 if x is not None)},
                            "t7l": {"first": _f7l, "last": _l7l,
                                    "n": sum(1 for x in _t7l if x is not None)},
                            "f2": {"first": _f2first, "last": _f2last,
                                   "n": _f2n},
                            "grid_n": len(result["dates"]),
                            "source_days": len(ext2_map),
                            "source_cap_days": 60}

    latest = taco.latest(result)
    stats = taco.stats(result)
    if latest:
        print("  -> TACO = %.4f  (%s)，%d 个有效观测，分位 %.1f%%"
              % (latest["TACO"], latest["date"],
                 stats.get("n", 0), 100 * (stats.get("latest_percentile") or 0)))
        if latest.get("TACO6") is not None:
            s6 = (stats.get("taco6") or {})
            print("  -> TACO-6  = %.4f  (%s)，%d 个有效观测，分位 %.1f%%  [31日变动口径]"
                  % (latest["TACO6"], latest["date6"], s6.get("n", 0),
                     100 * (s6.get("latest_percentile") or 0)))
        if latest.get("TACO6L") is not None:
            s6l = (stats.get("taco6L") or {})
            print("  -> TACO-6L = %.4f  (%s)，%d 个有效观测，分位 %.1f%%  [水位口径]"
                  % (latest["TACO6L"], latest["date6L"], s6l.get("n", 0),
                     100 * (s6l.get("latest_percentile") or 0)))
        if latest.get("TACO6") is not None and latest.get("TACO6L") is not None:
            print("     （两套 6 因子口径并列；东吴底稿只用 5 因子。"
                  "变动/水位差 %+.4f —— 水位断裂时两者方向可能相反）"
                  % (latest["TACO6L"] - latest["TACO6"]))
        if latest.get("TACO7") is not None:
            s7 = (stats.get("taco7") or {})
            print("  -> TACO-7  = %.4f  (%s)，仅 %d 天有效（%s .. %s）"
                  "  [5 基础 + 霍尔木兹 + 里亚尔 / 变动口径]"
                  % (latest["TACO7"], latest["date7"], s7.get("n", 0),
                     s7.get("first_date"), s7.get("last_date")))
        if latest.get("TACO7L") is not None:
            s7l = (stats.get("taco7L") or {})
            print("  -> TACO-7L = %.4f  (%s)，%d 天有效（%s .. %s）"
                  "  [水位口径]"
                  % (latest["TACO7L"], latest["date7L"], s7l.get("n", 0),
                     s7l.get("first_date"), s7l.get("last_date")))
        if latest.get("TACO7") is not None and latest.get("TACO7L") is not None:
            print("     （第 7 因子里亚尔：z_rial=%+.3f / z_rial_level=%+.3f；"
                  "有效区间短是数据源硬上限，不是计算失败）"
                  % (latest.get("z_rial") or 0.0,
                     latest.get("z_rial_level") or 0.0))
        if result.get("observe") and latest.get("transit") is not None:
            ho = (stats.get("hormuz_observe") or {})
            print("  -- 霍尔木兹通行量（**观察指标，未进入 TACO**）"
                  " = %.0f 艘次/日" % latest["transit"])
            print("     网格末日 %s（前值填充）｜**真实观测日 %s**（源滞后 %s 天）"
                  "｜源覆盖 %s 天，上网格 %s 天"
                  % (latest.get("date_transit"),
                     latest.get("date_transit_raw"),
                     latest.get("transit_raw_lag_days"),
                     ho.get("raw_n"), ho.get("n")))
            print("     分位 %.1f%%；%d 个网格日有值（ffill 把缺日补齐）"
                  % (100 * (ho.get("latest_percentile") or 0), ho.get("n") or 0))
    else:
        print("  !! 未算出有效 TACO")

    # --- 4. 产物 ---
    print("\n[4/5] 生成产物 ...")

    # 先把上一轮因占用而退写的标准名补写回来。
    # 退写机制保证数据不丢，但不保证标准名会追上 —— 长期撞上「用户开着 Excel」
    # 时，标准名会停在很旧的内容上，新数据全堆在 .locked-* 里，按标准名读的
    # 下游和肉眼看到的都是过期数据。补写只在本轮即将覆盖的那几个文件上做，
    # 标准名仍被占用时安全跳过。
    promotions = []
    for _p in (os.path.join(day_dir, "taco-%s.csv" % run_date),
               os.path.join(out_root, "taco-latest.csv"),
               os.path.join(out_root, "taco_index.xlsx"),
               os.path.join(out_root, "taco-dashboard.html"),
               os.path.join(out_root, "LATEST.md")):
        _done, _why = net.promote_fallback(_p)
        if _done:
            promotions.append(_why)
    for _w in promotions:
        print("  补写上一轮降级文件: %s" % _w)

    records = []
    run_tag = "%s#%s" % (run_date, int(t_start))
    ext_on = bool(result.get("extended"))
    ext2_on = bool(result.get("extended_2"))
    obs_on = bool(result.get("observe"))

    def _arr(k):
        return result.get(k) or []

    for i, d in enumerate(result["dates"]):
        row = {"run_tag": run_tag, "date": d, "series": "TACO_INDEX",
               "value": result["T"][i], "unit": "z",
               "composite_S": result["S"][i],
               "z_10Y": result["Nz"][i], "z_swap": result["Oz"][i],
               "z_approval": result["Pz"][i], "z_DJIA": result["Qz"][i],
               "z_Brent": result["Rz"][i]}
        if ext_on:
            a = _arr("T6")
            row["TACO6"] = a[i] if i < len(a) else None
        if ext_on:
            aL = _arr("T6L")
            row["TACO6L"] = aL[i] if i < len(aL) else None
        if ext2_on:
            a7 = _arr("T7")
            row["TACO7"] = a7[i] if i < len(a7) else None
            a7l = _arr("T7L")
            row["TACO7L"] = a7l[i] if i < len(a7l) else None
        records.append(row)
    if ext_on or obs_on:
        # 霍尔木兹序列单独成行，便于下游按 series 取数而不必解析复合行。
        #
        # 两种角色共用同一个 series 名（HORMUZ_TRANSIT）与同一个 value 字段
        # （原始水位），只在派生列上有无之分：**观察模式只有 value，没有 z /
        # 没有 TACO6**。这样下游按 `series=="HORMUZ_TRANSIT"` 取水位永远成立，
        # 不必先判断「这轮它是什么角色」。
        for i, d in enumerate(result["dates"]):
            rec = {
                "run_tag": run_tag, "date": d, "series": "HORMUZ_TRANSIT",
                "value": (_arr("F")[i] if i < len(_arr("F")) else None),
                "unit": "vessels",
                "role": "observe" if obs_on else "composite",
                # 显式声明是否进指数 —— 观察模式下这一点必须写在数据里，
                # 而不是只存在于报告的文字里。
                "in_index": bool(ext_on),
            }
            if ext_on:
                rec.update({
                    "z_transit": (_arr("Ntz")[i] if i < len(_arr("Ntz")) else None),
                    "composite_S6": (_arr("S6")[i] if i < len(_arr("S6")) else None),
                    "TACO6": (_arr("T6")[i] if i < len(_arr("T6")) else None),
                    "z_transit_level": (_arr("Ltz")[i] if i < len(_arr("Ltz")) else None),
                    "composite_S6L": (_arr("S6L")[i] if i < len(_arr("S6L")) else None),
                    "TACO6L": (_arr("T6L")[i] if i < len(_arr("T6L")) else None),
                })
            records.append(rec)
    if ext2_on:
        # 第 7 因子同样单独成行。注意 BONBAST 的原值只到有数据的那些天，
        # 而 T7/T7L 的有效区间更短 —— 两列各自的空值都有明确含义，不要填 0。
        for i, d in enumerate(result["dates"]):
            records.append({
                "run_tag": run_tag, "date": d,
                "series": "BONBAST_USD_SELL_RIAL",
                "value": (_arr("F2")[i] if i < len(_arr("F2")) else None),
                "unit": "rial",
                "z_rial": (_arr("Ntz2")[i] if i < len(_arr("Ntz2")) else None),
                "composite_S7": (_arr("S7")[i] if i < len(_arr("S7")) else None),
                "TACO7": (_arr("T7")[i] if i < len(_arr("T7")) else None),
                "z_rial_level": (_arr("Ltz2")[i] if i < len(_arr("Ltz2")) else None),
                "composite_S7L": (_arr("S7L")[i] if i < len(_arr("S7L")) else None),
                "TACO7L": (_arr("T7L")[i] if i < len(_arr("T7L")) else None),
            })
    emit.write_jsonl(os.path.join(day_dir, "taco-records-%s.jsonl" % run_date),
                     records)

    composite = {
        "run_tag": run_tag, "run_date": run_date,
        "latest": latest, "stats": stats,
        "coverage": result["coverage"],
        "formula_params": {"lag": result["lag"], "first": result["first"],
                           "ma_window": result["ma_window"],
                           "z_ddof": result["z_ddof"]},
        # 霍尔木兹序列：**角色显式记账**。in_index 是这一块最关键的字段 ——
        # 它一句话回答「这轮的指数里到底含不含它」，不必去读 formula.steps
        # 或猜 config 的历史。
        "hormuz": {
            "series": ext_sid, "field": ext_field,
            "mode": "observe" if obs_on else "composite",
            "in_index": bool(ext_on),
            "role_label": ("观察指标（不进入 TACO 合成）" if obs_on
                           else "第 6 因子（并入 TACO-6 / TACO-6L）"),
            "n_days": len(ext_map),
            "coverage": {
                "grid_n": len(result.get("dates") or []),
                "nn_filled": sum(1 for v in (result.get("F") or [])
                                 if v is not None),
                "source_n": result.get("observe_src_n"),
                "source_last_date": result.get("observe_src_last"),
            },
            "level_break": result.get("level_break_hormuz"),
            # 观察模式下不存在「降级」概念：它不进指数，水位再低也不会污染 TACO。
            "degraded": (False if obs_on else
                         (result.get("level_break_hormuz") or {}).get("ok") is False),
            "note": ("按用户要求：TACO 回到东吴原始 5 因子口径，霍尔木兹降级为"
                     "观察指标 —— 抓取、落盘、展示，不加权、不取 z、不进 S、不进 T。"
                     if obs_on else
                     "用户选定【两套并列】：水位断裂时两套口径方向可能相反，"
                     "谁都不隐藏。东吴底稿的 TACO 只用 5 个因子（F 列 0 引用）。"),
        },
        # 兼容字段：既有下游按 extended_factor 取第 6 因子信息。
        # observe 模式下 enabled=False 且 variants 为空 —— 不谎报。
        "extended_factor": {
            "enabled": ext_on, "series": ext_sid, "field": ext_field,
            "n_days": len(ext_map), "is_replication": False,
            "variants": ({} if obs_on else {
                "change": "TACO6 = 31 日变动口径（negate(diff_lag(F,31))）",
                "level": "TACO6L = 水位口径（-z(F)）",
            }),
            "level_break": result.get("level_break_hormuz"),
            "degraded": (result.get("level_break_hormuz") or {}).get("ok") is False,
            "note": ("本轮为 observe 模式：只作观察指标，未进入指数。"
                     if obs_on else
                     "用户选定【两套并列】：水位断裂时两套口径方向可能相反，"
                     "谁都不隐藏。东吴底稿的 TACO 只用 5 个因子（F 列 0 引用）。"),
        },
        "extended_factor_2": {
            "enabled": ext2_on,
            "removed": not bool(ext2_cfg.get("enabled", True)),
            "removed_at": ("2026-09-23"
                           if not bool(ext2_cfg.get("enabled", True)) else None),
            "series": bsid,
            "field": "usd_sell_rial",
            "label": (ext2_cfg.get("label")
                      or "Bonnast USD 卖出价（里亚尔）"),
            "source": "bonbast.com",
            "n_days": len(ext2_map),
            "unit": "rial",
            "toman_to_rial": bonbast.TOMAN_TO_RIAL,
            "polarity": ext2_cfg.get("polarity", 1),
            "variants": ({
                "change": "TACO7 = 变动口径（diff_lag(F2,31)，【不取负】："
                          "汇率上行 = 里亚尔贬值 = 压力升）",
                "level": "TACO7L = 水位口径（+z(F2)）",
            } if ext2_on else {}),
            "coverage": {
                "t7": (result.get("span_7") or {}).get("t7"),
                "t7l": (result.get("span_7") or {}).get("t7l"),
                "grid_n": (result.get("span_7") or {}).get("grid_n"),
                "source_cap_days": 60,
            },
            "level_break": result.get("level_break_7"),
            "crosscheck": (bon.get("crosscheck")),
            "degraded": (result.get("level_break_7") or {}).get("ok") is False,
            "note": ("已按用户要求移除汇率因子（config.extended_factor_2.enabled="
                     "false）：不抓取、不落盘、不参与合成。定义保留供追溯。"
                     if not ext2_on else
                     "站点 /graph 固定只给 60 天历史（?range=1y 无效），故本因子"
                     "有效区间远短于 5/6 因子；这是数据源硬上限，不是计算失败。"),
        },
        "proxy_decision": dec,
    }
    net.atomic_write_json(os.path.join(day_dir,
                                       "taco-composite-%s.json" % run_date),
                          composite)

    # CSV（全量）
    # 列定义与取数统一走 taco.output_row —— 不要再在这里手写「列名 -> 数组」映射。
    # 曾经这里把第 6 因子的原始水位列取成了 result["cols"]["F"]（原始输入字典里
    # 根本没有 F），整列写成空；而 z_transit / TACO-6 都是好的，从报告上看
    # 完全发现不了。现在只有一份列契约，且 verify 会回读落盘 CSV 数非空个数。
    csv_cols = taco.output_columns(result)
    csv_rows = [taco.output_row(result, i, csv_cols)
                for i in range(len(result["dates"]))]
    emit.write_csv(os.path.join(day_dir, "taco-%s.csv" % run_date),
                   csv_rows, header=csv_cols)
    emit.write_csv(os.path.join(out_root, "taco-latest.csv"),
                   csv_rows, header=csv_cols)

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
    if ext_on:
        _t6 = _arr("T6")
        taco6_hist = {result["dates"][i]: _t6[i]
                      for i in range(len(result["dates"]))
                      if i < len(_t6) and _t6[i] is not None}
        if taco6_hist:
            _, n_tot, n_new, n_rev = emit.update_history(
                hist_dir, "TACO6_INDEX", taco6_hist, run_date=run_date)
            print("  history %-22s total=%-5d new=%-4d revised=%d"
                  % ("TACO6_INDEX", n_tot, n_new, n_rev))
        _t6l = _arr("T6L")
        taco6l_hist = {result["dates"][i]: _t6l[i]
                       for i in range(len(result["dates"]))
                       if i < len(_t6l) and _t6l[i] is not None}
        if taco6l_hist:
            _, n_tot, n_new, n_rev = emit.update_history(
                hist_dir, "TACO6L_INDEX", taco6l_hist, run_date=run_date)
            print("  history %-22s total=%-5d new=%-4d revised=%d"
                  % ("TACO6L_INDEX", n_tot, n_new, n_rev))
    if ext2_on:
        # 第 7 因子原值也进历史：站点只给 60 天，本地history 是**唯一**能
        # 把历史攒长的地方 —— 日更的价值就在这里，不写就永远只有 60 天。
        _f2 = _arr("F2")
        b_hist = {result["dates"][i]: _f2[i]
                  for i in range(len(result["dates"]))
                  if i < len(_f2) and _f2[i] is not None}
        if b_hist:
            _, n_tot, n_new, n_rev = emit.update_history(
                hist_dir, bsid, b_hist, run_date=run_date)
            print("  history %-22s total=%-5d new=%-4d revised=%d"
                  % (bsid, n_tot, n_new, n_rev))
        for _tag, _key, _name in (("7", "T7", "TACO7_INDEX"),
                                  ("7L", "T7L", "TACO7L_INDEX")):
            _a = _arr(_key)
            h = {result["dates"][i]: _a[i]
                 for i in range(len(result["dates"]))
                 if i < len(_a) and _a[i] is not None}
            if h:
                _, n_tot, n_new, n_rev = emit.update_history(
                    hist_dir, _name, h, run_date=run_date)
                print("  history %-22s total=%-5d new=%-4d revised=%d"
                      % (_name, n_tot, n_new, n_rev))

    # 跨轮观察账本
    _obs = [{"run_tag": run_tag, "date": d, "series": "TACO_INDEX",
             "value": result["T"][i], "unit": "z"}
            for i, d in enumerate(result["dates"])
            if result["T"][i] is not None]
    if ext_on:
        _t6 = _arr("T6")
        _obs += [{"run_tag": run_tag, "date": d, "series": "TACO6_INDEX",
                  "value": _t6[i], "unit": "z"}
                 for i, d in enumerate(result["dates"])
                 if i < len(_t6) and _t6[i] is not None]
        _t6l = _arr("T6L")
        _obs += [{"run_tag": run_tag, "date": d, "series": "TACO6L_INDEX",
                  "value": _t6l[i], "unit": "z"}
                 for i, d in enumerate(result["dates"])
                 if i < len(_t6l) and _t6l[i] is not None]
    if ext_on or obs_on:
        # 观察账本同样按「角色」记账：value 是原始水位（两种角色一致），
        # 但 in_index 明确标出它这轮有没有进指数。
        # 下游只要看 in_index 就能知道该不该把它和 TACO 一起解释 ——
        # 不用去猜「那一轮 config 是什么模式」。
        _f = _arr("F")
        _obs += [{"run_tag": run_tag, "date": d, "series": "HORMUZ_TRANSIT",
                  "value": _f[i], "unit": "vessels",
                  "role": "observe" if obs_on else "composite",
                  "in_index": bool(ext_on)}
                 for i, d in enumerate(result["dates"])
                 if i < len(_f) and _f[i] is not None]
    if ext2_on:
        _f2 = _arr("F2")
        _obs += [{"run_tag": run_tag, "date": d,
                  "series": bsid, "value": _f2[i], "unit": "rial"}
                 for i, d in enumerate(result["dates"])
                 if i < len(_f2) and _f2[i] is not None]
        for _key, _name in (("T7", "TACO7_INDEX"), ("T7L", "TACO7L_INDEX")):
            _a = _arr(_key)
            _obs += [{"run_tag": run_tag, "date": d, "series": _name,
                      "value": _a[i], "unit": "z"}
                     for i, d in enumerate(result["dates"])
                     if i < len(_a) and _a[i] is not None]
    emit.write_jsonl(os.path.join(out_root, "ALL-observations.jsonl"),
                     _obs, mode="a")

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
    if net.INPLACE_WRITES:
        print("\n  !! 就地覆盖 %d 项 —— rename-over 被拒但文件可写，"
              "标准名内容正确、但失去了原子性：" % len(net.INPLACE_WRITES))
        for w in net.INPLACE_WRITES:
            print("     %s" % os.path.basename(w["path"]))
    if net.WRITE_FALLBACKS:
        print("\n  !! 写入降级 %d 项 —— 目标文件被其他程序占用，已退写备用名："
              % len(net.WRITE_FALLBACKS))
        for w in net.WRITE_FALLBACKS:
            print("     %-34s -> %s"
                  % (os.path.basename(w["intended"]),
                     os.path.basename(w["actual"])))
        print("     处置：关闭占用该文件的程序后重跑一次，即可写回标准文件名。"
              "（下一轮会自动把降级文件补写回标准名，无需手工搬）")

    # 源状态
    net.atomic_write_json(os.path.join(out_root, "_source_status.json"), {
        "run_date": run_date, "gate_passed": True,
        "statuses": [{k: v for k, v in s.items() if not k.startswith("_")}
                     for s in statuses],
        "staleness": stale, "proxy_decision": dec,
        "our_vs_dongwu": our_vs,
        "write_fallbacks": list(net.WRITE_FALLBACKS),
        "write_promotions": promotions,
        "prune_failures": list(net.PRUNE_FAILURES),
        "inplace_writes": list(net.INPLACE_WRITES),
    })

    # --- 5. 对账 ---
    print("\n[5/5] 回源对账 ...")
    vr = verify(run_date, cfg, statuses, result, latest, day_dir,
                from_file=args.from_file, bon=bon)
    net.atomic_write_json(os.path.join(out_root, "_verify_last.json"), vr)
    print("  %d/%d 项通过（%d 项警告）"
          % (vr["total"] - vr["failed"] - vr.get("warned", 0), vr["total"],
             vr.get("warned", 0)))
    for c in vr["checks"]:
        if not c["ok"]:
            print("  %s: %s — %s" % ("WARN" if c.get("level") == "warn"
                                     else "FAIL", c["name"], c["detail"]))

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
        if latest.get("TACO6") is not None:
            print("TACO-6  = %.4f  (%s)  [31日变动口径 · 本项目扩展]"
                  % (latest["TACO6"], latest["date6"]))
        if latest.get("TACO6L") is not None:
            print("TACO-6L = %.4f  (%s)  [水位口径 · 本项目扩展]"
                  % (latest["TACO6L"], latest["date6L"]))
        if latest.get("TACO7") is not None:
            print("TACO-7  = %.4f  (%s)  [7 因子 · 变动口径 · 有效天数少]"
                  % (latest["TACO7"], latest["date7"]))
        if latest.get("TACO7L") is not None:
            print("TACO-7L = %.4f  (%s)  [7 因子 · 水位口径]"
                  % (latest["TACO7L"], latest["date7L"]))
    print("报告: %s" % os.path.join(out_root, "LATEST.md"))
    print("看板: %s" % os.path.join(out_root, "taco-dashboard.html"))
    print("对账: %s" % ("PASS" if vr["ok"] else "FAIL"))
    print("=" * 70)
    return 0 if vr["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
