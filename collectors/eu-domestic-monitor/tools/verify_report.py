#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""verify_report.py — eu-domestic-monitor 独立对账（复算报告与记录一致性）。
不留死路径：全部路径有覆盖参数，输出首行打印实际核对的文件。"""
import argparse, json, os, re, sys
from collections import Counter
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def find_latest(outdir):
    dates = sorted((d for d in os.listdir(outdir)
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)
                    and os.path.isdir(os.path.join(outdir, d))), reverse=True)
    return dates[0] if dates else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(BASE, "output"))
    ap.add_argument("--date", default=None, help="YYYY-MM-DD 覆盖最新目录")
    ap.add_argument("--report", default=None, help="覆盖报告路径")
    ap.add_argument("--records", default=None, help="覆盖快照 records.jsonl 路径")
    ap.add_argument("--archive", default=None, help="覆盖累积档路径")
    args = ap.parse_args()
    outdir = args.out_dir
    dd = args.date or find_latest(outdir)
    if not dd:
        print("❌ 无日期目录可核对"); return 1
    p_rep = args.report or os.path.join(outdir, dd, f"eudom-{dd}.md")
    p_rec = args.records or os.path.join(outdir, dd, "records.jsonl")
    p_arc = args.archive or os.path.join(outdir, "ALL-records.jsonl")
    print(f"[verify] 核对文件: report={p_rep} | records={p_rec} | archive={p_arc}")

    C, M = 0, 0
    def ck(name, cond):
        nonlocal C, M
        C += 1
        if not cond:
            M += 1
            print(f"  ❌ {name}")

    with open(p_rep, "r", encoding="utf-8") as f:
        rep = f.read()
    recs = [json.loads(l) for l in open(p_rec, encoding="utf-8") if l.strip()]
    arch = [json.loads(l) for l in open(p_arc, encoding="utf-8") if l.strip()]

    # 1) 报告头部计数与记录一致
    m = re.search(r"当前快照（窗口 (\d+) 天）\*\*(\d+)\*\* 条", rep)
    ck("快照计数一致", m and int(m.group(2)) == len(recs))
    m2 = re.search(r"本次新增 \*\*(\d+)\*\* 条", rep)
    ck("新增计数存在", m2 is not None)
    # 2) 桶计数
    for bid, label_pat in [("france_2027", r"法国2027总统大选预备（勒庞/国民联盟民调） (\d+)"),
                           ("italy_election", r"意大利大选（2027-12 前）与执政联盟 (\d+)"),
                           ("ua_aid_attitude", r"各国对乌军事援助态度 (\d+)"),
                           ("russia_policy", r"各国对俄政策与战争立场 (\d+)"),
                           ("eu_politics", r"欧盟机构运作与成员国博弈 (\d+)"),
                           ("china_xinhua", r"中国视角（新华社/中欧关系） (\d+)")]:
        n_b = sum(1 for r in recs if bid in r["buckets"])
        mm = re.search(label_pat, rep)
        ck(f"桶计数 {bid}", mm and int(mm.group(1)) == n_b)
    # 3) 每条快照记录在累积档且字段齐全
    aid2 = {r["id"]: r for r in arch}
    miss = [r["id"] for r in recs if r["id"] not in aid2]
    ck("快照全部在累积档", not miss)
    field_ok = all(r.get("title") and r.get("url") and r.get("publisher")
                   and r.get("tier") and r.get("kind") and "buckets" in r
                   and "flags" in r and r.get("cfg_ver") for r in recs)
    ck("快照字段齐全", field_ok)
    # 4) excluded 不进快照
    ck("快照无 excluded", all(r["kind"] != "excluded" for r in recs))
    # 5) 窗口一致
    cutoff = (date.fromisoformat(dd) - timedelta(days=30)).isoformat()
    win = [r for r in arch
           if r.get("published") and not r.get("date_unknown")
           and r["kind"] != "excluded"
           and cutoff <= r["published"][:10] <= dd]
    ck("窗口重算=快照", Counter(r["id"] for r in win) == Counter(r["id"] for r in recs))
    # 6) cfg_ver 快照内唯一且与累积档一致
    cvs = {r.get("cfg_ver") for r in recs}
    ck("快照 cfg_ver 唯一", len(cvs) == 1)
    cvs2 = {r.get("cfg_ver") for r in arch if r["id"] in {x["id"] for x in recs}}
    ck("累积档对应记录 cfg_ver 一致", cvs == cvs2)
    # 7) 报告含对账提示与附录
    ck("报告含回源对账行", "回源对账" in rep)
    ck("报告含信源清单", "附录A" in rep)
    ck("报告含已知偏差", "已知偏差" in rep)
    ck("报告含等级定义", "附录B" in rep)
    # 8) tiers 合法
    ck("tier 全法", all(r.get("tier") in ("T1", "T2", "T3") for r in recs))

    scope = f"{dd}({len(recs)}条)"
    with open(os.path.join(outdir, "_verify_last.json"), "w", encoding="utf-8") as f:
        json.dump({"ran_at": dd, "checked": C, "mismatch": M, "ok": M == 0,
                   "scope": scope, "files": [p_rep, p_rec, p_arc]},
                  f, ensure_ascii=False, indent=1)
    print(f"[verify] {C} 项检查，{M} 项不符；范围 {scope}")
    return 0 if M == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
