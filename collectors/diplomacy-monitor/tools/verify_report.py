#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_report.py — diplomacy-monitor 对账：从落盘 jsonl 独立复算报告中的数字，
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
    p_rep = args.report or os.path.join(outdir, d, f"diplomacy-{d}.md")
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
    lbl = {"fact_event": "事实·事件", "opinion_statement": "观点·表态",
           "opinion_analysis": "观点·分析", "excluded": "已排除"}
    for k, v in kc.items():
        mm = re.search(re.escape(lbl[k]) + r"\s+(\d+)", sec1)
        if mm:
            n_checks += 1
            ck(f"kind 计数 {k}", int(mm.group(1)) == v, f"{mm.group(1)} vs {v}")
    # 4 桶×tier 交叉表行数 == 桶数
    n_checks += 1
    sec11 = slice_section(rep, "1.1") or ""
    xrows = [l for l in sec11.splitlines() if l.startswith("| ") and "—（" not in l]
    xrows = [l for l in xrows if re.match(r"\| [^|]+\| \d+ \|", l)]
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
    # 13 五组基线表行数 == config
    for sec, key, name in (("2.1", "mediator_baseline", "协调方基线"),
                           ("3.1", "trump_baseline", "特朗普基线"),
                           ("4.1", "memo_baseline", "备忘录基线"),
                           ("5.1", "military_baseline", "军事基线"),
                           ("6.1", "talks_baseline", "渠道基线")):
        n_checks += 1
        secx = slice_section(rep, sec) or ""
        rows = [l for l in secx.splitlines()
                if re.match(r"\| \d{4}-\d{2}(?:-\d{2})?[^|]*\|", l)]
        ck(f"§{sec} {name}行数 == config", len(rows) == len(cfg[key]),
           f"{len(rows)} vs {len(cfg[key])}")
    # 14 头部新增条数在场
    n_checks += 1
    ck("头部标新增条数", bool(re.search(r"本次新增 \*\*\d+\*\* 条", rep)))
    # 15 分节条数与 records 一致（§5.2 降温 / §5.3 再升级）
    n_checks += 1
    de = sum(1 for r in recs if "deescalation" in r.get("buckets", []))
    re_ = sum(1 for r in recs if "reescalation_mil" in r.get("buckets", []))
    m_de = re.search(r"### 5\.2 降温信号（(\d+) 条）", rep)
    m_re = re.search(r"### 5\.3 再升级信号（(\d+) 条）", rep)
    ck("§5.2/5.3 军事温度条数 == 复算",
       m_de and m_re and int(m_de.group(1)) == de and int(m_re.group(1)) == re_,
       f"{m_de.group(1) if m_de else '?'} vs {de} / {m_re.group(1) if m_re else '?'} vs {re_}")
    # 16 备忘录双口径在场（伊方条件 vs 美方新协议诉求）
    n_checks += 1
    memo_sec = slice_section(rep, "4.1") or ""
    ck("备忘录基线含伊方条件+美方诉求双口径",
       ("30 天" in memo_sec or "30天" in memo_sec) and ("新协议" in memo_sec or "更大范围" in memo_sec))
    # 17 TACO 定性警示在场（市场叙事≠降温证据）
    n_checks += 1
    ck("TACO 市场叙事警示在场", "市场叙事" in rep and "不构成降温证据" in rep)
    # 18 斡旋模糊性警示在场
    n_checks += 1
    ck("『谈判在进行/否认谈判』模糊性警示在场", "同时为真" in rep)
    # 19 T1 源在场说明（巴方 APP 唯一直连）
    n_checks += 1
    ck("T1 直连源说明在场", "APP" in rep)

    print(f"[verify] {len(fails)} 项不符 / 共 {n_checks} 项检查")
    with open(os.path.join(outdir, "_verify_last.json"), "w", encoding="utf-8") as f:
        json.dump({"ran_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "ok": not fails, "mismatch": len(fails), "checked": n_checks,
                   "scope": d, "files": [p_rep, p_rec, p_arc]}, f, ensure_ascii=False, indent=1)
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
