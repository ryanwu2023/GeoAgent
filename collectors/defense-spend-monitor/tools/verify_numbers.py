# -*- coding: utf-8 -*-
"""回源核对：把落盘指标与原始 API 返回值逐项对账。

只做只读验证，不修改任何产出。

⚠️ 取哪个快照来核对，**绝不写死日期**。
教训：本文件早期把路径硬编码成 output/2026-09-17/metrics-2026-09-17.json，
跨天后它仍然「全绿」，但核对的是前一天的旧快照 —— 而 LATEST.md 早已是
新一天的数字。一个对着错文件说 OK 的校验工具，比没有校验更危险。
现在默认取**最新日期目录**，并把实际核对的文件打在输出顶部；
也可以 `--date YYYY-MM-DD` 或 `--metrics <路径>` 显式指定。

⚠️ 代理端口也会漂移（实测同一天内 7897 → 63299 → 7897）。
本工具用系统自动探测的代理，并**在首行打印实际出口** ——
不打印的话，「所有源突然全挂」和「被 CDN 风控拦截」症状完全一样，无法区分。

覆盖三类核对：
  ① 财政部 MTS 逐科目 + 逐月序列           （实际支出 outlay）
  ② USAspending 弹药 PSC 精确聚合 + 逐月序列（合同义务 obligation）
  ③ 派生指标复算 + 月度序列加总闭环
"""
import argparse
import datetime
import json
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent


def resolve_metrics(explicit_date=None, explicit_path=None) -> pathlib.Path:
    """定位要核对的指标文件：显式路径 > 显式日期 > 最新日期目录。"""
    if explicit_path:
        p = pathlib.Path(explicit_path)
        if not p.is_absolute():
            p = ROOT / p
        return p
    out = ROOT / "output"
    if explicit_date:
        return out / explicit_date / f"metrics-{explicit_date}.json"
    days = sorted([d.name for d in out.iterdir()
                   if d.is_dir() and (d / f"metrics-{d.name}.json").exists()],
                  reverse=True)
    if not days:
        sys.exit(f"未找到任何 metrics 快照，请先运行 spend_monitor.py（查找目录：{out}）")
    return out / days[0] / f"metrics-{days[0]}.json"


_ap = argparse.ArgumentParser(description="回源核对落盘指标与官方 API")
_ap.add_argument("--date", help="指定核对的快照日期，如 2026-09-18（默认取最新）")
_ap.add_argument("--metrics", help="直接指定 metrics-*.json 路径")
_ap.add_argument("--skip-months", action="store_true",
                 help="跳过逐月序列回源（省请求；加总闭环也随之跳过）")
_args = _ap.parse_args()

METRICS = resolve_metrics(_args.date, _args.metrics)
if not METRICS.exists():
    sys.exit(f"指定的指标文件不存在：{METRICS}")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Accept": "application/json"}

ok = 0
bad = 0

print("=" * 88)
print(f"核对对象：{METRICS.relative_to(ROOT)}")
_proxies = urllib.request.getproxies()
print(f"出口代理：{_proxies.get('https') or _proxies.get('http') or '无（直连）'}")
print("=" * 88)


def num(v):
    """API 返回的金额是字符串，统一转 float；None/空串返回 None。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cmp(label, local, remote, tol=0.01):
    """比较落盘值与回源值。tol 为允许的绝对误差（美元）。"""
    global ok, bad
    remote = num(remote)
    if remote is None:
        print(f"  [跳过] {label}: 回源无数值")
        return
    d = abs(float(local) - remote)
    flag = "✅" if d <= tol else "❌"
    if d <= tol:
        ok += 1
    else:
        bad += 1
    print(f"  {flag} {label}\n      落盘 = {float(local):,.2f}\n"
          f"      回源 = {remote:,.2f}\n      差 = {d:,.2f}")


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**UA, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


FY_MONTH_TO_CAL = {1: 10, 2: 11, 3: 12, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6,
                   10: 7, 11: 8, 12: 9}


def period_to_ym(p):
    """与主程序同一套换算：财年月份 → 日历月。两边不一致就会整体偏移。"""
    if not isinstance(p, dict):
        return None
    if p.get("calendar_year") and p.get("month"):
        return f"{int(p['calendar_year']):04d}-{int(p['month']):02d}"
    if p.get("fiscal_year") and p.get("month"):
        fm, fy = int(p["month"]), int(p["fiscal_year"])
        return f"{fy - 1 if fm <= 3 else fy:04d}-{FY_MONTH_TO_CAL[fm]:02d}"
    return None


CAT_URL = "https://api.usaspending.gov/api/v2/search/spending_by_category/psc/"
OT_URL = "https://api.usaspending.gov/api/v2/search/spending_over_time/"

# ── 1. 财政部 MTS 对账 ─────────────────────────────────────────────
print("=" * 88)
print("① 财政部 MTS（Monthly Treasury Statement）—— 逐科目回源核对")
print("=" * 88)
local = json.loads(METRICS.read_text("utf-8"))
t = local["treasury"]
names = [e["full"] for e in t["lines"].values()]
# 从落盘序列的最早月份起查，避免把作为上下文保留的 FY2025 月份漏在过滤条件之外
earliest = t["months"][0]
filt = (f"record_date:gte:{earliest},record_date:lte:{t['latest_month']},"
        f"classification_desc:in:({','.join(names)})")
url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
       "/v1/accounting/mts/mts_table_5?sort=record_date&page[size]=200&filter="
       + urllib.parse.quote(filt)
       + "&fields=record_date,classification_desc,current_month_net_outly_amt,"
         "current_month_gross_outly_amt,current_fytd_net_outly_amt,current_fytd_gross_outly_amt")
data = get_json(url)["data"]
print(f"\n回源拿到 {len(data)} 行（落盘 {len(t['months'])} 个月 × {len(names)} 科目）\n")

# 只挑落盘记录的最后一个月的《净支出》逐项对账
for label, entry in t["lines"].items():
    src = [r for r in data
           if r["classification_desc"] == entry["full"]
           and r["record_date"] == t["latest_month"]]
    if not src:
        print(f"  [缺失] {label}: 回源未找到 {t['latest_month']} 行")
        bad += 1
        continue
    r = src[0]
    print(f"── {label} ──")
    cmp("本月净支出 current_month_net_outly_amt", entry["latest"],
        r.get("current_month_net_outly_amt"))
    cmp("本财年累计净支出 current_fytd_net_outly_amt", entry["fytd"],
        r.get("current_fytd_net_outly_amt"))

# 月度序列完整性
print("\n── 月度历史序列核对（取国防部合计）──")
dod = t["lines"]["Total--Department of Defense--Military Programs"]
mismatch = 0
for dt, val in dod["history"]:
    m = [r for r in data if r["classification_desc"] == dod["full"]
         and r["record_date"] == dt]
    if not m:
        print(f"  ❌ {dt}: 回源缺该月")
        mismatch += 1
        continue
    if abs(num(m[0]["current_month_net_outly_amt"]) - float(val)) > 0.01:
        print(f"  ❌ {dt}: 落盘 {val:,.2f} vs 回源 "
              f"{num(m[0]['current_month_net_outly_amt']):,.2f}")
        mismatch += 1
if mismatch == 0:
    print(f"  ✅ 国防部合计 {len(dod['history'])} 个月序列逐月一致")
    ok += 1
else:
    bad += 1

# ── 2. USAspending 弹药 PSC 精确聚合 ────────────────────────────────
print()
print("=" * 88)
print("② USAspending 弹药类 PSC 精确聚合（合同义务 obligation）—— 回源核对")
print("=" * 88)
us = local["usaspending"]


def agency_filter(t0, t1):
    return {
        "agencies": [{"type": "awarding", "tier": "toptier",
                      "name": "Department of Defense"}],
        "time_period": [{"start_date": t0, "end_date": t1}],
        "award_type_codes": ["A", "B", "C", "D"],
    }


codes = us.get("psc_codes_queried") or []
print(f"\n落盘记录的 PSC 代码集：{len(codes)} 个")
resp = post_json(CAT_URL, {"filters": {**agency_filter(*us["window"]),
                                       "psc_codes": codes},
                           "limit": 100, "page": 1})
rows = resp.get("results", [])
remote = {str(r["code"]): float(r["amount"]) for r in rows}
print(f"回源返回 {len(rows)} 个 PSC 类别（落盘弹药明细 {len(us['munitions_rows'])} 个）\n")

for m in us["munitions_rows"]:
    cmp(f"PSC {m['code']} {m['name']}", m["amount"], remote.get(m["code"]))

# 回源里出现但落盘没有的代码（会说明落盘漏了一类弹药）
missing = sorted(set(remote) - {m["code"] for m in us["munitions_rows"]})
if missing:
    print(f"  ❌ 回源有、落盘缺的 PSC 代码：{missing}")
    bad += 1
else:
    print("  ✅ 回源返回的弹药代码在落盘中无遗漏")
    ok += 1

print("\n── 内部一致性 ──")
calc = sum(m["amount"] for m in us["munitions_rows"])
cmp("弹药类合计 = 各明细行之和", us["munitions_total"], calc)
print(f"  回源合计（独立求和） = {sum(remote.values()):,.2f}"
      f"（用以交叉验证落盘总额）")
cmp("弹药类合计 vs 回源独立求和", us["munitions_total"], sum(remote.values()))

# 参考口径：未过滤时返回的前 N 个类别 —— 明确标为下界，避免被当成总额
print("\n── 参考口径（未过滤，仅作背景）──")
resp_all = post_json(CAT_URL, {"filters": agency_filter(*us["window"]),
                               "limit": 100, "page": 1})
all_rows = resp_all.get("results", [])
all_sum = sum(float(r["amount"]) for r in all_rows)
print(f"  金额最大的前 {len(all_rows)} 个 PSC 类别合计（**下界**，非国防部合同总额）")
cmp("前 N 类合计", us.get("all_psc_top_sum") or 0, all_sum)

# ── 3. 逐月序列与加总闭环 ──────────────────────────────────────────
print()
print("=" * 88)
print("③ 逐月序列回源 + 加总闭环（趋势数字必须能加总回区间总额）")
print("=" * 88)
if _args.skip_months:
    print("  （--skip-months：跳过）")
else:
    mw = us.get("monthly_window")
    monthly = us.get("monthly") or []
    if not mw or not monthly:
        print("  落盘中没有逐月序列，跳过。")
    else:
        hours = post_json(OT_URL, {
            "group": "month",
            "filters": {**agency_filter(mw[0], mw[1]), "psc_codes": codes}})
        mine: dict = {}
        for r in hours.get("results", []):
            ym = period_to_ym(r.get("time_period") or {})
            if ym:
                mine[ym] = mine.get(ym, 0.0) + float(r.get("aggregated_amount") or 0)

        print(f"\n窗口 {mw[0]} → {mw[1]}，落盘 {len(monthly)} 个月\n")
        mm = 0
        for row in monthly:
            ym = row["month"]
            got = num(mine.get(ym, 0.0))
            want = float(row.get("munitions") or 0.0)
            if abs(got - want) > 0.01:
                print(f"  ❌ {ym}: 落盘 {want:,.2f} vs 回源 {got:,.2f}")
                mm += 1
        if mm == 0:
            print(f"  ✅ {len(monthly)} 个月的弹药义务额逐月一致")
            ok += 1
        else:
            bad += 1

        # 加总闭环：月度之和 == 独立区间聚合
        s = sum(float(r.get("munitions") or 0.0) for r in monthly)
        ref = us.get("monthly_window_total")
        if ref is None:
            ref = us.get("munitions_total")
        print("\n── 加总闭环 ──")
        cmp("月度序列之和 vs 独立区间聚合总额", s, ref)
        cmp("月度之和 vs 本次回源逐月求和", s, sum(mine.values()))

        # 分阶段合计
        war = (us.get("war_start") or mw[0])[:7]
        base = sum(float(r.get("munitions") or 0.0) for r in monthly
                   if r["month"] < war)
        war_s = sum(float(r.get("munitions") or 0.0) for r in monthly
                    if r["month"] >= war)
        print(f"  战前(＜{war}) 合计 = {base:,.2f}；战后(≥{war}) 合计 = {war_s:,.2f}")
        cmp("战前+战后 = 序列总和", base + war_s, s)

    # 全合同口径
    con_mw = post_json(OT_URL, {"group": "month",
                                "filters": agency_filter(mw[0], mw[1])})
    con: dict = {}
    for r in con_mw.get("results", []):
        ym = period_to_ym(r.get("time_period") or {})
        if ym:
            con[ym] = con.get(ym, 0.0) + float(r.get("aggregated_amount") or 0)
    print("\n── 口径声明核对 ──")
    print(f"  落盘 metrics 字段名：{us.get('metrics_api_field')}")
    print(f"  回源接口返回的字段名（应为义务 obligaton，不是 outlay）："
          f"{sorted({k for r in con_mw.get('results', [])[:1] for k in r if 'Obligation' in k or 'Outlay' in k})}")
    print("  ⚠️ 财政部（outlay 实际支出）与 USAspending（obligation 合同义务）"
          "口径不同，本工具**不做两者相加**的比对，也不应有人去做。")

# ── 4. 落盘趋势报告与序列文件 ───────────────────────────────────────
print()
print("=" * 88)
print("④ 趋势报告与序列文件落盘核对")
print("=" * 88)
date_str = METRICS.parent.name
root = METRICS.parent.parent
series_path = root / "history" / "series.jsonl"
trend_path = root / "history" / f"trend-{date_str}.md"
latest_trend = root / "LATEST-trend.md"
for p, label in ((series_path, "序列长表 series.jsonl"),
                 (trend_path, f"趋势报告 trend-{date_str}.md"),
                 (latest_trend, "LATEST-trend.md")):
    if p.exists():
        print(f"  ✅ 存在：{p.relative_to(root)}")
        ok += 1
    else:
        print(f"  ❌ 缺失：{p.relative_to(root)}")
        bad += 1

if series_path.exists():
    rows_s = [json.loads(l) for l in series_path.read_text("utf-8").splitlines()
              if l.strip()]
    have = {r["metric"] for r in rows_s}
    need = {"usaspending::munitions", "treasury::Total--Department of Defense--Military Programs"}
    print(f"\n  序列条数 {len(rows_s)}，指标 {len(have)} 个")
    miss = need - have
    if miss:
        print(f"  ❌ 序列缺少必需指标：{sorted(miss)}")
        bad += 1
    else:
        print("  ✅ 序列含弹药与财政部两个必需指标")
        ok += 1
    # 口径与等级字段必须齐全，否则序列会被误读
    for fld in ("caliber", "unit", "source_id", "tier", "kind"):
        n = sum(1 for r in rows_s if r.get(fld) in (None, ""))
        if n:
            print(f"  ❌ 序列有 {n} 行缺字段 `{fld}`")
            bad += 1
        else:
            print(f"  ✅ 序列每行都有 `{fld}`")
            ok += 1
    # 趋势报告里的加总结论必须写明「差 0.00」
    if trend_path.exists():
        td = trend_path.read_text("utf-8")
        if "0.00" in td and "✅ 一致" in td:
            print("  ✅ 趋势报告内含加总一致性结论（差 0.00）")
            ok += 1
        else:
            print("  ❌ 趋势报告缺少加总一致性结论")
            bad += 1
        if "启发式" in td and "不可相加" in td:
            print("  ✅ 趋势报告含口径与启发式声明")
            ok += 1
        else:
            print("  ❌ 趋势报告缺少口径/启发式声明")
            bad += 1

# ── 5. 采购占比与派生指标 ──────────────────────────────────────────
print()
print("=" * 88)
print("⑤ 派生指标复算")
print("=" * 88)
proc = t["lines"]["Total--Procurement"]
share_p = 100 * proc["latest"] / dod["latest"]
print(f"  采购占国防部本月支出 = {share_p:.4f}%  "
      f"（落盘 {t['procurement_share']:.4f}%）"
      f"  {'✅' if abs(share_p - t['procurement_share']) < 1e-6 else '❌'}")
if abs(share_p - t["procurement_share"]) < 1e-6:
    ok += 1
else:
    bad += 1

mo3 = proc["avg3"]
mo3p = proc["avg3_prior"]
print(f"  采购 近3月均值/前3月均值-1 = {100*(mo3/mo3p-1):.4f}%"
      f"  （月均 {mo3:,.0f} vs {mo3p:,.0f}）")

print()
print("=" * 88)
print(f"对账结果：{ok} 项一致 / {bad} 项不一致")
print("=" * 88)

# 落一份结果留档，供日报引用「最近一次回源对账」。
# 为什么必须落盘而不是让日报写死：核对项数会随覆盖范围（月份数、科目数）变化，
# 写死的「26 项差 0.00」必然过期，而**过期的「已验证」比不写更误导**。
try:
    _rec = {
        "checked_at": datetime.datetime.now(datetime.timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics_file": METRICS.relative_to(ROOT).as_posix(),
        "ok": ok, "mismatch": bad,
        "skip_months": bool(_args.skip_months),
        "months_checked": 0 if _args.skip_months else None,
        "exit_code": 1 if bad else 0,
    }
    (ROOT / "output" / "_verify_last.json").write_text(
        json.dumps(_rec, ensure_ascii=False, indent=1), "utf-8")
    print(f"结果留档：output/_verify_last.json（{ok} 项一致 / {bad} 项不一致）")
except Exception as _e:                                            # noqa: BLE001
    print(f"[!] 结果留档写入失败（不影响退出码）：{type(_e).__name__}: {_e}")

sys.exit(1 if bad else 0)
