#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
compare_taco_sources.py —— TACO 五因子「源头是否一致」实证核对

背景：`taco-monitor`（另一个项目，东吴宏观 TACO 压力指数 5 因子复刻）已经抓了
五因子。现在要把这批数据并进 `finance-front-monitor`。**并之前必须先核对源头**：
同一个指标两边用的是不是同一个源？如果不是，差多少？差异是「版本/口径差」还是
「抓错了」？

纪律（本工作区既有约定）：
  1. 只用**双方已落盘的原始载荷**对表，不用任何中间结果或记忆里的数字。
     → 证据可回放：谁都能用同一批字节复算一遍。
  2. 解析器**复用 finance_monitor.py 的**，不在这里重写一遍。
     两处各写一套解析是「对账工具自己和自己对账」的经典陷阱。
  3. 逐日对齐、逐日比。只报「最新值差多少」会掩盖「历史天天差 3%」这种情况。
  4. 差异定性按三条判据（见 check() 的注释），不轻易下「谁错了」的结论：
     同源 → 应当逐日**完全相等**；不同口径（期货 vs 现货）→ 差额应当**平稳**；
     版本差 → 差额**高度集中在少数日子**。

用法：
  python tools/compare_taco_sources.py
  python tools/compare_taco_sources.py --taco-root "D:/.../taco-monitor" --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import finance_monitor as fm  # noqa: E402

DEFAULT_TACO_ROOT = os.path.join(os.path.dirname(ROOT), "taco-monitor")

# 五因子的「本侧（finance）↔ 对侧（taco）」载荷配对。
# raw 通配符按**任一轮**的最新文件取，'*' 覆盖 provider 命名差异。
FACTORS = [
    dict(key="C", name="10Y 美债收益率",
         taco_series="USGG10YR", taco_src="FRED DGS10（fredgraph.csv）",
         fin_series="dgs10", fin_src="美国财政部 par yield CSV",
         fin_glob="dgs10__treasury__*.csv", fin_parse="treasury",
         taco_glob="USGG10YR__fred.csv", taco_parse="fred",
         note="不同源。FRED 的 DGS10 本身就是从财政部 par yield 派生的，理论上应逐日贴合。"),
    dict(key="D", name="1Y 通胀互换（代理：5Y 盈亏平衡通胀）",
         taco_series="USSWIT1_proxy_T5YIE", taco_src="FRED T5YIE",
         fin_series="taco_breakeven5y", fin_src="FRED T5YIE（2026-09-23 补爬）",
         fin_glob="taco_breakeven5y__fred__*.csv", fin_parse="fred",
         taco_glob="USSWIT1_proxy_T5YIE__fred.csv", taco_parse="fred",
         note="两侧使用**完全相同的 series id（FRED T5YIE）** → 应逐日完全相等。"
              "注意这只是「代理源相同」：1Y 通胀互换公开源不可得，两侧都用 5Y 盈亏平衡代理，"
              "「5Y 盈亏平衡 ≠ 1Y 互换」这件事两边都一样地不精确。"),
    dict(key="E", name="特朗普净支持率",
         taco_series="RCPPTAPP", taco_src="Silver Bulletin / Datawrapper kSCt4.csv",
         fin_series="taco_trump_approval", fin_src="Silver Bulletin / Datawrapper kSCt4.csv（2026-09-23 补爬）",
         fin_glob="taco_trump_approval__datawrapper__*.csv", fin_parse="dw",
         taco_glob="RCPPTAPP__silver*.csv", taco_parse="dw",
         note="两侧**URL 与解析方式完全相同**（同一份 kSCt4.csv、都按列名取 approve）→ "
              "应逐日完全相等。但两侧的**指标定义**不同：对侧是净支持率，这里只是 approve 单侧，"
              "所以「数据一致」不等于「因子可直接互换」。"),
    dict(key="G", name="道琼斯工业指数",
         taco_series="INDU", taco_src="FRED DJIA",
         fin_series="djia", fin_src="Yahoo ^DJI",
         fin_glob="djia__yahoo__*.json", fin_parse="yahoo",
         taco_glob="INDU__fred.csv", taco_parse="fred",
         note="不同源。都是道指日收盘，理论上应几乎相同（指数点位，不是价格）。"),
    dict(key="H", name="布伦特原油",
         taco_series="CO1", taco_src="Yahoo BZ=F（NYMEX Brent Last Day）",
         fin_series="brent", fin_src="Yahoo BZ=F（同一 symbol）",
         fin_glob="brent__yahoo__*.json", fin_parse="yahoo",
         taco_glob="CO1__yahoo*.json", taco_parse="yahoo",
         note="两边同一个 provider、同一个 symbol，只是 range 参数不同（2y/5y）→ 应逐日完全相等。"
              "★ 若只差**最新一天**，那不是源的问题：Yahoo 日线的最后一根在交易时段内是"
              "**实时变动的盘中价**，两份载荷抓取时刻不同，值自然不同。"
              "本工程已在解析阶段用 drop_inprogress_session 丢弃该会话（口径与 taco-monitor 对齐），"
              "所以这里对原始载荷做差异统计时，允许最后一天不一致，**不要当故障**。"),
]


def parse_file(path: str, kind: str):
    """用 finance_monitor 的解析器解析一份原始载荷。"""
    with open(path, "rb") as f:
        body = f.read()
    if kind == "yahoo":
        rows, why = fm.parse_yahoo_chart(body)
    elif kind == "fred":
        rows, why = fm.parse_fred_csv(body)
    elif kind == "treasury":
        rows, why = fm.parse_treasury_csv(body, "10 Yr")
    elif kind == "dw":
        rows, why = fm.parse_named_csv(body, "modeldate", "approve", "%m/%d/%Y")
    else:
        raise ValueError(kind)
    return rows, why


def latest_glob(pattern: str) -> str | None:
    hits = glob.glob(pattern)
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def align(a, b):
    """取双方共有日期的并集/交集，返回逐日对照列表。"""
    da, db = dict(a), dict(b)
    common = sorted(set(da) & set(db))
    return [{"date": d, "fin": da[d], "taco": db[d], "diff": db[d] - da[d]}
            for d in common]


def summarize(pairs):
    if not pairs:
        return {}
    diffs = [abs(p["diff"]) for p in pairs]
    rels = [abs(p["diff"]) / abs(p["fin"]) if abs(p["fin"]) > 1e-12 else None
            for p in pairs]
    rels = [r for r in rels if r is not None]
    exact = sum(1 for d in diffs if d < 1e-9)
    big = [p for p in pairs if abs(p["diff"]) > max(0.01, 3 * (sum(diffs) / len(diffs)))]
    return {
        "common_days": len(pairs),
        "exact_equal_days": exact,
        "max_abs_diff": max(diffs),
        "mean_abs_diff": round(sum(diffs) / len(diffs), 6),
        "max_rel_diff": (round(max(rels), 6) if rels else None),
        "mean_rel_diff": (round(sum(rels) / len(rels), 6) if rels else None),
        # 差额集中度：少数「大差日」贡献了多少绝对差。
        #   >3 倍 → 版本/快照差；各日均匀 → 口径差。
        "big_day_count": len(big),
        "big_day_share": (round(sum(abs(p["diff"]) for p in big) /
                                 max(sum(diffs), 1e-12), 3) if big else 0.0),
        "big_days": [{"date": p["date"],
                      "fin": round(p["fin"], 6),
                      "taco": round(p["taco"], 6),
                      "diff": round(p["diff"], 6)} for p in big[:8]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="TACO 五因子源头一致性核对")
    ap.add_argument("--taco-root", default=DEFAULT_TACO_ROOT,
                    help="taco-monitor 项目目录（含 output/ 的那一层）")
    ap.add_argument("--fin-raw", default=os.path.join(ROOT, "output"),
                    help="本侧 output 根（会在其下找 **/raw/）")
    ap.add_argument("--json", action="store_true", help="把结果写到 tools/_taco_source_check.json")
    a = ap.parse_args()

    taco_raw_root = os.path.join(a.taco_root, "output")
    if not os.path.isdir(taco_raw_root):
        print(f"!! 找不到 taco-monitor 的 output：{taco_raw_root}")
        return 1

    out = []
    print("=" * 100)
    print("TACO 五因子 · 源头一致性核对（双方均用**已落盘的原始载荷**，解析器复用 finance_monitor.py）")
    print("=" * 100)

    for f in FACTORS:
        print(f"\n【{f['key']}】{f['name']}")
        print(f"   本侧 finance : {f['fin_series'] or '—'}  ← {f['fin_src']}")
        print(f"   对侧 taco    : {f['taco_series']}  ← {f['taco_src']}")

        taco_path = latest_glob(os.path.join(taco_raw_root, "*", "raw", f["taco_glob"]))
        if not taco_path:
            print("   !! 对侧载荷缺失，无法核对")
            out.append({"key": f["key"], "name": f["name"], "status": "taco_raw_missing"})
            continue
        taco_rows, twhy = parse_file(taco_path, f["taco_parse"])
        print(f"   对侧载荷 : {os.path.relpath(taco_path, a.taco_root)}"
              f"  rows={len(taco_rows)} latest={taco_rows[-1] if taco_rows else None}")
        if not taco_rows:
            print(f"   !! 对侧解析失败：{twhy}")
            out.append({"key": f["key"], "name": f["name"], "status": "taco_parse_fail",
                        "why": twhy})
            continue

        if not f["fin_glob"]:
            print("   本侧       : 原本**没有**这条序列 → 结论 = 缺爬虫，需要补。")
            out.append({"key": f["key"], "name": f["name"],
                        "status": "missing_in_finance",
                        "taco_source": f["taco_src"],
                        "taco_latest": {"date": taco_rows[-1][0],
                                        "value": taco_rows[-1][1]},
                        "note": f["note"]})
            continue

        fin_path = latest_glob(os.path.join(a.fin_raw, "*", "raw", f["fin_glob"]))
        if not fin_path:
            print("   !! 本侧载荷缺失")
            out.append({"key": f["key"], "name": f["name"], "status": "fin_raw_missing"})
            continue
        fin_rows, fwhy = parse_file(fin_path, f["fin_parse"])
        print(f"   本侧载荷 : {os.path.relpath(fin_path, ROOT)}"
              f"  rows={len(fin_rows)} latest={fin_rows[-1] if fin_rows else None}")
        if not fin_rows:
            print(f"   !! 本侧解析失败：{fwhy}")
            out.append({"key": f["key"], "name": f["name"], "status": "fin_parse_fail",
                        "why": fwhy})
            continue

        pairs = align(fin_rows, taco_rows)
        s = summarize(pairs)
        same_source = (f["fin_parse"] == f["taco_parse"] and
                       f["fin_src"].split("（")[0] == f["taco_src"].split("（")[0])
        if same_source and s.get("exact_equal_days") == s.get("common_days"):
            verdict = "同源·逐日完全相等（源头一致）"
        elif same_source:
            verdict = "同源但存在差异（需查抓取/解析）"
        elif s.get("exact_equal_days", 0) == 0:
            verdict = "不同源·无一日完全相同 → 口径差或版本差，需按判据定性"
        else:
            verdict = "不同源·部分日期完全相同 → 大概率同一底层数据的不同发布途径"

        print(f"   共有日期 : {s['common_days']} 天；逐日完全相同 {s['exact_equal_days']} 天")
        print(f"   绝对差   : 最大 {s['max_abs_diff']:.6f}  平均 {s['mean_abs_diff']:.6f}")
        print(f"   相对差   : 最大 {s['max_rel_diff']}  平均 {s['mean_rel_diff']}")
        print(f"   差额集中 : {s['big_day_count']} 个大差日贡献 {s['big_day_share']:.1%} 的绝对差")
        print(f"   ★ 结论   : {verdict}")
        print(f"   说明     : {f['note']}")

        out.append({"key": f["key"], "name": f["name"], "status": "compared",
                    "same_source": same_source, "verdict": verdict,
                    "fin_source": f["fin_src"], "taco_source": f["taco_src"],
                    "summary": {k: v for k, v in s.items() if k != "big_days"},
                    "big_days": s.get("big_days", [])})

    if a.json:
        p = os.path.join(HERE, "_taco_source_check.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"checked_at": fm.now_utc().isoformat(timespec="seconds"),
                       "taco_root": a.taco_root, "factors": out},
                      fh, ensure_ascii=False, indent=1)
        print(f"\n证据 → {os.path.relpath(p, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
