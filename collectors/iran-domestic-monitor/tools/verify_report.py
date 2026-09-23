#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_report.py — iran-domestic-monitor 对账：从落盘 jsonl 独立复算报告中的数字，
绕开渲染层。凡参与比对的路径一律可覆盖（--report/--records/--archive/--date），
实际核对的文件路径打印在输出首行。任一项不符退出码非零。
"""
import argparse, json, os, re, sys
from collections import Counter
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def newest_date_dir(outdir):
    cands = [d for d in os.listdir(outdir) if re.match(r"^\d{4}-\d{2}-\d{2}$", d)]
    return max(cands) if cands else None

def load_jsonl(p):
    out = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

def slice_section(rep, header):
    m = re.search(rf"^###? {re.escape(header)}[.:\s（]", rep, re.M)
    if not m:
        return None
    rest = rep[m.end():]
    nxt = re.search(r"^#{2,3} \d", rest, re.M)
    return rest[: nxt.start()] if nxt else rest

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(BASE, "output"))
    ap.add_argument("--date", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--records", default=None)
    ap.add_argument("--archive", default=None)
    args = ap.parse_args()
    outdir = args.out_dir

    d = args.date or newest_date_dir(outdir) or date.today().isoformat()
    p_rep = args.report or os.path.join(outdir, d, f"iran-domestic-{d}.md")
    p_rec = args.records or os.path.join(outdir, d, "records.jsonl")
    p_arc = args.archive or os.path.join(outdir, "ALL-records.jsonl")
    print(f"[verify] 报告: {p_rep}")
    print(f"[verify] 记录: {p_rec}")
    print(f"[verify] 累积档: {p_arc}")

    fails = []
    def ck(name, cond, detail=""):
        print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail else ""))
        if not cond:
            fails.append(name)

    if not (os.path.exists(p_rep) and os.path.exists(p_rec)):
        print(f"[verify] ❌ 文件缺失，无法对账")
        sys.exit(2)
    rep = open(p_rep, encoding="utf-8").read()
    recs = load_jsonl(p_rec)
    arc = load_jsonl(p_arc) if os.path.exists(p_arc) else []
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    n_checks = 0

    # 1 头部快照条数 == records 行数
    n_checks += 1
    m = re.search(r"当前快照（窗口 \d+ 天）\*\*(\d+)\*\*", rep)
    ck("头部快照条数 == records 行数", m and int(m.group(1)) == len(recs),
       f"{m.group(1) if m else '?'} vs {len(recs)}")
    # 2 桶计数 == 独立复算
    n_checks += 1
    sec1 = slice_section(rep, "1") or ""
    bc = Counter(b for r in recs for b in r.get("buckets", []))
    for bid, bcfg in cfg["buckets"].items():
        mm = re.search(re.escape(bcfg["label"]) + r"\s+(\d+)", sec1)
        if mm:
            n_checks += 1
            ck(f"桶计数 {bid}", int(mm.group(1)) == bc.get(bid, 0),
               f"{mm.group(1)} vs {bc.get(bid, 0)}")
    # 3 kind 计数
    n_checks += 1
    kc = Counter(r["kind"] for r in recs)
    lbl = {"fact_data": "事实·数据", "fact_event": "事实·事件",
           "opinion_statement": "观点·表态", "opinion_analysis": "观点·分析",
           "excluded": "已排除"}
    for k, v in kc.items():
        mm = re.search(re.escape(lbl[k]) + r"\s+(\d+)", sec1)
        if mm:
            n_checks += 1
            ck(f"kind 计数 {k}", int(mm.group(1)) == v, f"{mm.group(1)} vs {v}")
    # 4 数值抽取个数
    n_checks += 1
    mn = sum(len(r.get("metrics") or []) for r in recs)
    mm = re.search(r"数值机械抽取：\*\*(\d+)\*\*", rep)
    if mm:
        ck("数值抽取个数", int(mm.group(1)) == mn, f"{mm.group(1)} vs {mn}")
    # 5 §2 数值表行数 == metrics 总数
    n_checks += 1
    sec2 = slice_section(rep, "2") or ""
    rows = [l for l in sec2.splitlines() if l.startswith("| ") and re.match(r"\| \d{4}-", l)]
    ck("§2 表行数 == 数值抽取数", len(rows) == mn, f"{len(rows)} vs {mn}")
    # 6 桶×tier 交叉表行数 == 桶数
    n_checks += 1
    sec11 = slice_section(rep, "1.1") or ""
    xrows = [l for l in sec11.splitlines() if l.startswith("| ") and "—（" not in l]
    xrows = [l for l in xrows if re.match(r"\| [^|]+\| \d+ \|", l)]
    ck("交叉表行数 == 桶数", len(xrows) == len(cfg["buckets"]),
       f"{len(xrows)} vs {len(cfg['buckets'])}")
    # 7 快照 ⊆ 累积档
    n_checks += 1
    arc_ids = {r["id"] for r in arc}
    ck("快照 ⊆ 累积档", all(r["id"] in arc_ids for r in recs),
       f"{sum(1 for r in recs if r['id'] not in arc_ids)} 条不在")
    # 8 cfg_ver 唯一
    n_checks += 1
    vers = {r.get("cfg_ver") for r in recs}
    ck("快照内 cfg_ver 唯一", len(vers) == 1, f"{len(vers)} 种")
    # 9 tier 合法
    n_checks += 1
    ck("记录 tier 合法", all(r.get("tier") in ("T1", "T2", "T3") for r in recs))
    # 10 kind 完整
    n_checks += 1
    ck("记录 kind 完整", all(r.get("kind") in lbl for r in recs))
    # 11 无日期不进累积档
    n_checks += 1
    ck("无日期不进累积档", all(not (r.get("date_unknown")) for r in arc))
    # 12 无纪元哨兵残留
    n_checks += 1
    body_no_note = re.sub(r".*1970.*纪元零值.*", "", rep)
    ck("无纪元哨兵残留", not re.search(r"Thu, 01 Ja|Jan 1970 00", body_no_note))
    # 13 无半截 RFC822
    n_checks += 1
    ck("无半截 RFC822 串", not re.search(r"\|\s+\w{3}, \d{2} \w{3}", rep))
    # 14 窗口内日期合法
    n_checks += 1
    d0 = date.fromisoformat(d)
    start = (d0 - timedelta(days=cfg["window_days"])).isoformat()
    bad = [r for r in recs if r.get("published") and not
           (start <= r["published"][:10] <= d)]
    ck("记录日期都在窗口内", not bad, f"{len(bad)} 条越窗")
    # 15 六组基线表行数 == config
    for sec, key, name in (("3.1", "fx_baseline", "汇率基线"),
                           ("4.1", "cpi_baseline", "物价基线"),
                           ("5.1", "jobs_baseline", "就业基线"),
                           ("6.1", "oil_baseline", "出口基线"),
                           ("7.1", "fuel_baseline", "燃料基线"),
                           ("8.1", "power_baseline", "权势基线")):
        n_checks += 1
        secx = slice_section(rep, sec) or ""
        rows = [l for l in secx.splitlines() if re.match(r"\| \d{4}-\d{2}(?:-\d{2})?[^|]*\|", l)]
        ck(f"§{sec} {name}行数 == config", len(rows) == len(cfg[key]),
           f"{len(rows)} vs {len(cfg[key])}")
    # 16 头部新增条数在场
    n_checks += 1
    ck("头部标新增条数", bool(re.search(r"本次新增 \*\*\d+\*\* 条", rep)))
    # 17 汇率基线含官方+自由市场双口径（口径纪律）
    n_checks += 1
    fx_sec = slice_section(rep, "3.1") or ""
    ck("汇率基线双口径在场", ("官方" in fx_sec and "自由市场" in fx_sec))

    print(f"[verify] {len(fails)} 项不符 / 共 {n_checks} 项检查")
    with open(os.path.join(outdir, "_verify_last.json"), "w", encoding="utf-8") as f:
        json.dump({"ran_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "ok": not fails, "mismatch": len(fails), "checked": n_checks,
                   "scope": d, "files": [p_rep, p_rec, p_arc]}, f, ensure_ascii=False, indent=1)
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
