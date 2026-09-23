#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""verify_report.py — ru-diplomacy-monitor 独立对账工具
复算日报与快照/累积档的一致性；结果写 _verify_last.json（报告头部动态引用）。
路径一个都不写死：默认取最新日期目录，全部可用参数覆盖。
（3.9/3.12 纪律：对账工具自身路径必须可覆盖、打印实际核对的文件路径。）"""
import argparse, glob, json, os, re, sys
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

KIND_LABEL = {"fact_event": "事实·已完成事件/发布", "opinion_statement": "观点·表态",
              "opinion_analysis": "观点·分析", "excluded": "已排除"}
BUCKET_ORDER = ["us_envoy", "talks_process", "eu_stance", "ua_stance", "anchorage", "china_xinhua"]

def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(BASE, "output"))
    ap.add_argument("--date", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--records", default=None)
    ap.add_argument("--archive", default=None)
    args = ap.parse_args()
    outdir = args.out_dir

    # ---- 路径解析（默认=最新日期目录，绝不写死）----
    if args.report and args.records and args.archive:
        rep_path, rec_path, arch_path = args.report, args.records, args.archive
        run_date = args.date or ""
    else:
        if args.date:
            dd = os.path.join(outdir, args.date)
        else:
            dated = sorted(glob.glob(os.path.join(
                outdir, "[0-9]" * 4 + "-" + "[0-9]" * 2 + "-" + "[0-9]" * 2)))
            # 排除验证隔离目录（非 YYYY-MM-DD 形态的目录天然不会被匹配）
            if not dated:
                print("FATAL: output 下无日期目录"); sys.exit(2)
            dd = dated[-1]
        run_date = os.path.basename(dd)
        rep_path = args.report or os.path.join(dd, f"rudip-{run_date}.md")
        rec_path = args.records or os.path.join(dd, "records.jsonl")
        arch_path = args.archive or os.path.join(outdir, "ALL-records.jsonl")

    print(f"[verify] 实际核对: report={rep_path}")
    print(f"[verify]           records={rec_path}")
    print(f"[verify]           archive={arch_path}")

    C = []  # (name, ok)
    def ck(name, cond):
        C.append((name, bool(cond)))

    import ru_diplomacy_monitor as m
    cfg = m.load_cfg()
    eng = m.Engine(cfg)

    rep = ""
    if os.path.exists(rep_path):
        with open(rep_path, "r", encoding="utf-8") as f:
            rep = f.read()
    ck("日报存在", bool(rep))
    ck("日报含运行日期", (not run_date) or (run_date in rep))

    recs = load_jsonl(rec_path)
    ck("快照 records.jsonl 存在且非空", len(recs) > 0)
    arch = load_jsonl(arch_path)
    ck("累积档存在且非空", len(arch) > 0)

    # ---- 快照字段完整性 ----
    req_fields = ("id", "url", "title", "published", "source_id", "publisher",
                  "tier", "buckets", "kind", "stance", "flags", "cfg_ver", "run_date")
    bad = [r.get("id", "?")[:8] for r in recs
           if any(r.get(k) in (None, "", [], {}) and k not in ("stance",) for k in req_fields)]
    ck(f"快照记录字段完整（缺字段 {len(bad)} 条）", not bad)
    kinds_bad = [r["kind"] for r in recs if r["kind"] not in KIND_LABEL]
    ck("kind 全部合法", not kinds_bad)
    tiers_bad = [r["tier"] for r in recs if r["tier"] not in ("T1", "T2", "T3")]
    ck("tier 全部合法", not tiers_bad)
    bk_bad = [b for r in recs for b in r["buckets"] if b not in BUCKET_ORDER]
    ck("buckets 全部合法", not bk_bad)
    ck("cfg_ver 与当前配置一致", all(r.get("cfg_ver") == eng.cfg_ver for r in recs))

    # ---- 窗口一致性 ----
    win = int(cfg["window_days"])
    as_of = date.fromisoformat(run_date) if run_date else date.today()
    start = (as_of - timedelta(days=win)).isoformat()
    out_win = [r["id"] for r in recs if r.get("published")
               and not (start <= r["published"][:10] <= as_of.isoformat())]
    ck(f"全部快照记录落在 {start}..{run_date} 窗口内", not out_win)

    # ---- 计数复算 vs 日报 ----
    def count_pat(label, expected, label_set=None):
        pat = re.escape(label) + r"\s+(\d+)"
        mt = re.search(pat, rep)
        if not mt:
            return False, "报告未找到该计数"
        return int(mt.group(1)) == expected, f"报告={mt.group(1)} 实际={expected}"

    from collections import Counter
    bc = Counter(b for r in recs for b in r["buckets"])
    sec_ok, sec_msg = True, ""
    for bid in BUCKET_ORDER:
        n_actual = sum(1 for r in recs if bid in r["buckets"])
        mt = re.search(re.escape(cfg["buckets"][bid]["label"]) + r"[^。\n]*?（(\d+) 条）", rep)
        if not mt or int(mt.group(1)) != n_actual:
            sec_ok = False
            sec_msg = f"{bid}: 报告={mt.group(1) if mt else '缺失'} 实际={n_actual}"
            break
    ck("各桶分节计数与快照一致", sec_ok and (sec_msg or True) and sec_ok)
    kc = Counter(r["kind"] for r in recs)
    k_ok = True
    for k, n in kc.items():
        mt = re.search(re.escape(KIND_LABEL[k]) + r"\s+(\d+)", rep)
        if not mt or int(mt.group(1)) != n:
            k_ok = False
            break
    ck("事实/观点计数与快照一致", k_ok)
    ck("快照头部含新增条数", re.search(r"本次新增 \*\*\d+\*\* 条", rep) is not None)
    ck("快照头部含快照总数", re.search(r"\*\*\d+\*\* 条", rep) is not None)

    # ---- 交叉表 ----
    cross_ok = True
    for bid in BUCKET_ORDER:
        cc = Counter(r["tier"] for r in recs if bid in r["buckets"])
        row = re.search(re.escape(cfg["buckets"][bid]["label"]) + r"\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)", rep)
        if not row or (int(row.group(1)), int(row.group(2)), int(row.group(3))) != \
           (cc.get("T1", 0), cc.get("T2", 0), cc.get("T3", 0)):
            cross_ok = False
            break
    ck("信号桶×等级交叉表与快照一致", cross_ok)

    # ---- 累积档一致性 ----
    amap = {r["id"]: r for r in arch}
    ck("累积档 id 唯一", len(amap) == len(arch))
    missing = [r["id"] for r in recs if r["id"] not in amap]
    ck("快照记录全部在累积档", not missing)
    drift = [r["id"] for r in recs
             if r["id"] in amap and
             (amap[r["id"]].get("buckets"), amap[r["id"]].get("kind")) !=
             (r["buckets"], r["kind"])]
    ck("快照与累积档桶/kind 无漂移", not drift)
    ck("累积档无 excluded 残留进快照", all(r["kind"] != "excluded" for r in recs))

    # ---- 结构性内容 ----
    ck("§0 阅读须知存在", "阅读须知" in rep)
    ck("基线表存在", "结构性基线" in rep and "安克雷奇" in rep)
    ck("附录A 信源清单行数与配置一致",
       rep.count("| T1 |") + rep.count("| T2 |") + rep.count("| T3 |") >= len(cfg["sources"]))
    ck("附录B 等级定义存在", "T4" in rep and "不采用" in rep)
    for sid in ("xinhua-home", "gn-xinhua", "gn-china"):
        ck(f"中文窗口 {sid} 在配置中", any(s["id"] == sid for s in cfg["sources"]))

    ok = sum(1 for _, c in C if c)
    for name, c in C:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"[verify] {ok}/{len(C)} 通过")

    with open(os.path.join(outdir, "_verify_last.json"), "w", encoding="utf-8") as f:
        json.dump({"ran_at": m.now_utc().isoformat(timespec="seconds"),
                   "ok": ok == len(C), "checked": len(C), "mismatch": len(C) - ok,
                   "scope": f"{run_date or 'latest'} snapshot={len(recs)} archive={len(arch)}",
                   "files": {"report": rep_path, "records": rec_path, "archive": arch_path}},
                  f, ensure_ascii=False, indent=1)
    sys.exit(0 if ok == len(C) else 1)

if __name__ == "__main__":
    main()
