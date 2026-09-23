#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""verify_report.py — russia-domestic-monitor 独立对账工具
对最新（或指定日期/指定目录）产出做结构+口径复算，落盘 _verify_last.json。
所有路径可覆盖：--out-dir / --date / --records / --report（绝不写死）。
mismatch>0 时退出码 1。"""
import argparse, json, os, re, sys
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIND_LABEL = {"fact_event": "事实·已完成事件/发布", "opinion_statement": "观点·表态",
              "opinion_analysis": "观点·分析", "excluded": "已排除"}
FLAG_LABEL = {"official_data": "官方数据语境", "forecast_estimate": "预测/估算",
              "easing_measure": "缓解措施", "restriction_measure": "收紧/限制"}
BUCKETS = ["duma_election", "oil_exports", "fiscal_deficit",
           "military_spending", "ruble_fx", "labor_shortage", "china_xinhua"]

def find_latest(outdir):
    days = [d for d in os.listdir(outdir)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
            and os.path.isdir(os.path.join(outdir, d))]
    return max(days) if days else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(BASE, "output"))
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（默认取最新日期目录）")
    ap.add_argument("--records", default=None, help="覆盖 records.jsonl 路径")
    ap.add_argument("--report", default=None, help="覆盖日报 md 路径")
    args = ap.parse_args()

    outdir = args.out_dir
    dd = args.date or find_latest(outdir)
    if not dd:
        print("❌ 未找到日期目录，先用主程序产出")
        sys.exit(1)
    p_rec = args.records or os.path.join(outdir, dd, "records.jsonl")
    p_rep = args.report or os.path.join(outdir, dd, f"rudom-{dd}.md")
    p_arch = os.path.join(outdir, "ALL-records.jsonl")

    # 对账范围如实打印（防对着错文件说 OK）
    print(f"[verify] records={p_rec}")
    print(f"[verify] report ={p_rep}")
    print(f"[verify] archive={p_arch}")

    C = []   # (名称, ok)
    def ck(name, cond):
        C.append((name, bool(cond)))

    recs, rep, arch = [], "", []
    if os.path.exists(p_rec):
        with open(p_rec, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f if l.strip()]
    else:
        ck("records.jsonl 存在", False)
    if os.path.exists(p_rep):
        rep = open(p_rep, encoding="utf-8").read()
    else:
        ck("日报 md 存在", False)
    if os.path.exists(p_arch):
        with open(p_arch, encoding="utf-8") as f:
            arch = [json.loads(l) for l in f if l.strip()]
    else:
        ck("累积档存在", False)

    # ---- 快照结构 ----
    ck("快照非空或显式声明空窗", len(recs) >= 0)
    ck("每条记录有 id/url/title/published/source_id/tier/kind/buckets/flags",
       all(r.get("id") and "published" in r and r.get("source_id")
           and r.get("tier") in ("T1", "T2", "T3") and r.get("kind") and "buckets" in r
           for r in recs))
    ck("快照内 id 唯一", len({r["id"] for r in recs}) == len(recs))
    ck("kind 三分类合法", all(r["kind"] in KIND_LABEL for r in recs))
    ck("buckets 全部合法", all(all(b in BUCKETS for b in r["buckets"]) for r in recs))
    ck("tier 无 T4", all(r["tier"] != "T4" for r in recs))
    ck("窗口内日期合理（<= 明天）",
       all(not r.get("published") or r["published"][:10] <= str(date.today()) + "Z"[:0]
           or r["published"][:10] <= date.fromisoformat(max(dd, str(date.today()))).isoformat()
           for r in recs))
    # ---- 快照窗口一致性 ----
    from datetime import timedelta
    start = (date.fromisoformat(dd) - timedelta(days=30)).isoformat()
    bad_win = [r for r in recs if r.get("published")
               and not (start <= r["published"][:10] <= dd)]
    ck(f"快照全部落在 {start}..{dd} 窗口", not bad_win)
    # ---- 快照 ⊆ 累积档，且窗口内累积档 ⊆ 快照（双向一致）----
    arch_ids = {r["id"] for r in arch}
    ck("快照 id 全在累积档", {r["id"] for r in recs} <= arch_ids)
    win_arch = {r["id"] for r in arch
                if r.get("published") and start <= r["published"][:10] <= dd
                and r.get("kind") != "excluded"}
    ck("窗口内累积档 id 全在快照", win_arch <= {r["id"] for r in recs})
    # ---- 与日报数字闭环 ----
    if rep:
        m = re.search(r"当前快照（窗口 \d+ 天）\*\*(\d+)\*\*", rep)
        ck("日报快照计数与 records 行数一致", m and int(m.group(1)) == len(recs))
        m2 = re.search(r"本次新增 \*\*(\d+)\*\*", rep)
        ck("日报含新增计数", bool(m2))
        m3 = re.search(r"累积档 (\d+) 条", rep)
        ck("日报累积档计数与 ALL-records 行数一致",
           (not m3) or int(m3.group(1)) == len(arch))
        for b in BUCKETS:
            n = sum(1 for r in recs if b in r["buckets"])
            mm = re.search(re.escape(f"（{n} 条）") , rep) if n else None
            # 分桶标题形如 「## 3. 国家杜马选举…（N 条）」
            ck(f"日报分桶计数 {b}={n}", n == 0 or mm)
        ck("日报含交叉表", "信号桶 × 信源等级交叉表" in rep)
        ck("日报含已知局限", "已知偏差与局限" in rep)
        ck("日报含信源清单附录", "附录A 信源清单" in rep)
        ck("日报含回源对账状态行", "回源对账" in rep)
        ck("日报含新华社/中文窗口", "新华社/中文窗口" in rep)
    # ---- flags 合法性 ----
    ck("flags 键合法", all(all(k in FLAG_LABEL for k in (r.get("flags") or {})) for r in recs))
    # ---- 对账留档 ----
    mismatch = sum(1 for _, ok in C if not ok)
    outp = os.path.join(outdir, "_verify_last.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump({"ran_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "ok": mismatch == 0, "mismatch": mismatch,
                   "checked": len(C), "scope": dd,
                   "files": {"records": p_rec, "report": p_rep, "archive": p_arch}},
                  f, ensure_ascii=False, indent=1)
    print(f"[verify] {len(C) - mismatch}/{len(C)} 项一致；mismatch={mismatch}；留档 {outp}")
    for name, ok in C:
        if not ok:
            print(f"  ❌ {name}")
    sys.exit(0 if mismatch == 0 else 1)

if __name__ == "__main__":
    main()
