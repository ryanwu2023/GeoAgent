#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_report.py — mideast-monitor 对账：从落盘 jsonl 独立复算报告中的数字，
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
    p_rep = args.report or os.path.join(outdir, d, f"mideast-digest-{d}.md")
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

    # 1 头部快照条数 == records 行数
    m = re.search(r"当前快照（窗口 \d+ 天）\*\*(\d+)\*\*", rep)
    ck("头部快照条数 == records 行数", m and int(m.group(1)) == len(recs),
       f"{m.group(1) if m else '?'} vs {len(recs)}")
    # 2 桶计数 == 独立复算
    sec1 = slice_section(rep, "1") or ""
    bc = Counter(b for r in recs for b in r.get("buckets", []))
    for bid, bcfg in cfg["buckets"].items():
        mm = re.search(re.escape(bcfg["label"]) + r"\s+(\d+)", sec1)
        if mm:
            ck(f"桶计数 {bid}", int(mm.group(1)) == bc.get(bid, 0),
               f"{mm.group(1)} vs {bc.get(bid, 0)}")
    # 3 kind 计数
    kc = Counter(r["kind"] for r in recs)
    lbl = {"fact_pact": "事实·协议", "fact_attack": "事实·袭击", "fact_event": "事实·事件",
           "opinion_statement": "观点·表态", "opinion_analysis": "观点·分析",
           "excluded": "已排除"}
    for k, v in kc.items():
        mm = re.search(re.escape(lbl[k]) + r"\s+(\d+)", sec1)
        if mm:
            ck(f"kind 计数 {k}", int(mm.group(1)) == v, f"{mm.group(1)} vs {v}")
    # 4 民调数字抽取个数
    pn = sum(len(r.get("polls") or []) for r in recs)
    mm = re.search(r"民调数字抽取：\*\*(\d+)\*\*", rep)
    if mm:
        ck("民调抽取个数", int(mm.group(1)) == pn, f"{mm.group(1)} vs {pn}")
    # 5 §2 民调表行数 == fact_poll 记录的 poll 总数
    sec2 = slice_section(rep, "2") or ""
    rows = [l for l in sec2.splitlines() if l.startswith("| ") and re.match(r"\| \d{4}-", l)]
    ck("§2 表行数 == 民调抽取数", len(rows) == pn, f"{len(rows)} vs {pn}")
    # 6 §2 中的数值都能在 records.polls 里找到
    vals_rep = set()
    for l in rows:
        mm = re.search(r"\*\*(\d{1,3})%\*\*", l)
        if mm:
            vals_rep.add(int(mm.group(1)))
    vals_rec = {p["value"] for r in recs for p in (r.get("polls") or [])}
    ck("§2 数值 ⊆ records.polls", vals_rep <= vals_rec, f"{sorted(vals_rep)} ⊆ {sorted(vals_rec)}")
    # 7 快照 ⊆ 累积档
    arc_ids = {r["id"] for r in arc}
    ck("快照 ⊆ 累积档", all(r["id"] in arc_ids for r in recs),
       f"{sum(1 for r in recs if r['id'] not in arc_ids)} 条不在")
    # 8 cfg_ver 唯一
    vers = {r.get("cfg_ver") for r in recs}
    ck("快照内 cfg_ver 唯一", len(vers) == 1, f"{len(vers)} 种")
    # 9 每条记录有 tier 且合法
    ck("记录 tier 合法", all(r.get("tier") in ("T1", "T2", "T3") for r in recs))
    # 10 每条记录有 kind
    ck("记录 kind 完整", all(r.get("kind") in lbl for r in recs))
    # 11 无日期条目不进累积档
    ck("无日期不进累积档", all(not (r.get("date_unknown")) for r in arc))
    # 12 报告无纪元哨兵垃圾（排除说明性文字那一行）
    body_no_note = re.sub(r".*1970.*纪元零值.*", "", rep)
    ck("无纪元哨兵残留", not re.search(r"Thu, 01 Ja|Jan 1970 00", body_no_note))
    # 13 无半截 RFC822 日期
    ck("无半截 RFC822 串", not re.search(r"\|\s+\w{3}, \d{2} \w{3}", rep))
    # 14 窗口内日期合法
    d0 = date.fromisoformat(d)
    start = (d0 - timedelta(days=cfg["window_days"])).isoformat()
    bad = [r for r in recs if r.get("published") and not
           (start <= r["published"][:10] <= d)]
    ck("记录日期都在窗口内", not bad, f"{len(bad)} 条越窗")
    # 15-18 四组基线表行数 == config
    for sec, key, name in (("3.1", "pact_baseline", "协议基线"),
                           ("4.1", "houthi_baseline", "也门基线"),
                           ("5.1", "iraq_baseline", "伊拉克基线"),
                           ("6.1", "gulf_baseline", "海湾基线")):
        secx = slice_section(rep, sec) or ""
        rows = [l for l in secx.splitlines() if re.match(r"\| \d{4}-\d{2}(?:-\d{2})? \|", l)]
        ck(f"§{sec} {name}行数 == config", len(rows) == len(cfg[key]),
           f"{len(rows)} vs {len(cfg[key])}")
    # 16 头部新增条数在场
    ck("头部标新增条数", bool(re.search(r"本次新增 \*\*\d+\*\* 条", rep)))

    print(f"[verify] {len(fails)} 项不符 / 共 18+ 项检查")
    with open(os.path.join(outdir, "_verify_last.json"), "w", encoding="utf-8") as f:
        json.dump({"ran_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "ok": not fails, "mismatch": len(fails), "checked": 18,
                   "scope": d, "files": [p_rep, p_rec, p_arc]}, f, ensure_ascii=False, indent=1)
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
