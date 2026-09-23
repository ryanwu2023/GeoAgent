#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_report.py — asset-transmission-monitor 对账：从落盘 jsonl 独立复算报告中的数字，
绕开渲染层。凡参与比对的路径一律可覆盖（--report/--records/--archive/--date/--out-dir），
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
    m = re.search(rf"^###? {re.escape(header)}[.:\s（x]", rep, re.M)
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
    p_rep = args.report or os.path.join(outdir, d, f"uaid-{d}.md")
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
        mm = re.search(re.escape(bcfg["label"]) + r"（?[^\d]*\s(\d+)", sec1)
        if mm:
            n_checks += 1
            ck(f"桶计数 {bid}", int(mm.group(1)) == bc.get(bid, 0),
               f"{mm.group(1)} vs {bc.get(bid, 0)}")
    # 3 kind 计数
    n_checks += 1
    kc = Counter(r["kind"] for r in recs)
    lbl = {"fact_attack": "事实·已完成事件", "opinion_statement": "观点·表态",
           "opinion_analysis": "观点·分析", "excluded": "已排除"}
    for k, v in kc.items():
        mm = re.search(re.escape(lbl[k]) + r"\s+(\d+)", sec1)
        if mm:
            n_checks += 1
            ck(f"kind 计数 {k}", int(mm.group(1)) == v, f"{mm.group(1)} vs {v}")
    # 4 桶×tier 交叉表行数 == 桶数
    n_checks += 1
    sec11 = slice_section(rep, "1.1") or ""
    xrows = [l for l in sec11.splitlines() if re.match(r"\| [^|]+\| \d+ \|", l)]
    ck("交叉表行数 == 桶数", len(xrows) == len(cfg["buckets"]),
       f"{len(xrows)} vs {len(cfg['buckets'])}")
    # 5 快照 ⊆ 累积档
    n_checks += 1
    arc_ids = {r["id"] for r in arc}
    ck("快照 ⊆ 累积档", all(r["id"] in arc_ids for r in recs),
       f"{sum(1 for r in recs if r['id'] not in arc_ids)} 条不在")
    # 6 cfg_ver 唯一
    n_checks += 1
    vers = {r.get("cfg_ver") for r in recs}
    ck("快照内 cfg_ver 唯一", len(vers) == 1, f"{len(vers)} 种")
    # 7 tier 合法
    n_checks += 1
    ck("记录 tier 合法", all(r.get("tier") in ("T1", "T2", "T3") for r in recs))
    # 8 kind 完整
    n_checks += 1
    ck("记录 kind 完整", all(r.get("kind") in lbl for r in recs))
    # 9 无日期不进累积档
    n_checks += 1
    ck("无日期不进累积档", all(not (r.get("date_unknown")) for r in arc))
    # 10 无纪元哨兵残留
    n_checks += 1
    body_no_note = re.sub(r".*1970.*纪元零值.*", "", rep)
    ck("无纪元哨兵残留", not re.search(r"Thu, 01 Ja|Jan 1970 00", body_no_note))
    # 11 无半截 RFC822
    n_checks += 1
    ck("无半截 RFC822 串", not re.search(r"\|\s+\w{3}, \d{2} \w{3}", rep))
    # 12 窗口内日期合法
    n_checks += 1
    d0 = date.fromisoformat(d)
    start = (d0 - timedelta(days=cfg["window_days"])).isoformat()
    bad = [r for r in recs if r.get("published") and not
           (start <= r["published"][:10] <= d)]
    ck("记录日期都在窗口内", not bad, f"{len(bad)} 条越窗")
    # 13 基线表行数 == config（资金/能力两组）
    for sec, key, name in (("2.1", "rows", "资金机制基线"),
                           ("2.2", "capability_rows", "能力与口径基线")):
        n_checks += 1
        secx = slice_section(rep, sec) or ""
        rows = [l for l in secx.splitlines()
                if re.match(r"\| \d{4}-\d{2}(?:-\d{2})?[^|]*\|", l)]
        ck(f"§{sec} {name}行数 == config", len(rows) == len(cfg["baseline"][key]),
           f"{len(rows)} vs {len(cfg['baseline'][key])}")
    # 14 头部新增条数在场
    n_checks += 1
    ck("头部标新增条数", bool(re.search(r"本次新增 \*\*\d+\*\* 条", rep)))
    # 15 分节条数与 records 一致（§3 援助包 / §5 武器）
    n_checks += 1
    ap_n = sum(1 for r in recs if "supply_loss" in r.get("buckets", []))
    wp_n = sum(1 for r in recs if "freight_insurance" in r.get("buckets", []))
    m_ap = re.search(r"## 3\. 供应损失与产能中断（(\d+) 条）", rep)
    m_wp = re.search(r"## 4\. 运费与保险（战争险）（(\d+) 条）", rep)
    ck("§3/§4 条数 == 复算",
       m_ap and m_wp and int(m_ap.group(1)) == ap_n and int(m_wp.group(1)) == wp_n,
       f"{m_ap.group(1) if m_ap else '?'} vs {ap_n} / {m_wp.group(1) if m_wp else '?'} vs {wp_n}")
    # 16 口径纪律警示在场（四层口径 + PDA/USAI）
    n_checks += 1
    ck("传导链纪律警示在场", "只列**证据**" in rep and "因果" in rep)
    n_checks += 1
    ck("单位纪律警示在场", "单位" in rep and "不可相加" in rep)
    # 17 宣布≠已发生 纪律在场
    n_checks += 1
    ck("宣布≠已发生 警示在场", "宣布 ≠ 已发生" in rep and "confirmed_action" in rep)
    # 18 新华社/中文窗口分节在场
    n_checks += 1
    ck("§7.x 新华社窗口分节在场", "新华社/中文窗口全部快照条目" in rep)
    # 19 无日期分节在场（§8）
    n_checks += 1
    ck("§8 无日期分节在场", "## 8. 无日期条目" in rep)
    # 20 Xinhua 停更留档在附录A（禁复活假活源）
    n_checks += 1
    ck("附录A 有 Xinhua worldrss 停更留档", bool(re.search(r"xinhua-worldrss.*停用", rep)))

    print(f"[verify] {len(fails)} 项不符 / 共 {n_checks} 项检查")
    with open(os.path.join(outdir, "_verify_last.json"), "w", encoding="utf-8") as f:
        json.dump({"ran_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "ok": not fails, "mismatch": len(fails), "checked": n_checks,
                   "scope": d, "files": [p_rep, p_rec, p_arc]}, f, ensure_ascii=False, indent=1)
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
