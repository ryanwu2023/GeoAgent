# -*- coding: utf-8 -*-
"""回扫审计：用**真实语料**检查 claims 词表是否漏检。

为什么要单独做这个：词表是「末词加词形变化」，所以词表里写**屈折形**
（`destroyed` / `struck` / `neutraliz`）会导致只匹配该形态本身，
`Destroys` / `strike` / `neutralize` 全部落空 —— 而且日志、条数、状态码全绿。

用法：python tools/audit_claims.py [--kind action_claim] [--show 4]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import readiness_monitor as R  # noqa: E402

PROBES = {
    "action_claim": [
        r"\bdestroy(?:s|ed|ing)?\b", r"\bstrik(?:e|es|ing)\b", r"\bstruck\b",
        r"\bdown(?:s|ed|ing)?\b", r"\bshot down\b", r"\bshoots? down\b",
        r"\bneutraliz(?:e|es|ed|ing)\b", r"\bannihilat(?:e|es|ed|ing)\b",
        r"\bintercept(?:s|ed|ing)?\b", r"\brepel(?:s|led|ling)?\b",
        r"\bengag(?:e|es|ed|ing)\b", r"\bhit(?:s|ting)?\b", r"\btarget(?:s|ed|ing)\b",
        r"\blaunch(?:es|ed|ing)?\b", r"\bfir(?:e|es|ed|ing)\b",
        r"\bsuccessfully (?:hit|struck|targeted|engaged)\b",
    ],
    "denial": [
        r"\bden(?:y|ies|ied)\b", r"\brefut(?:e|es|ed|ing)\b", r"\breject(?:s|ed|ing)?\b",
        r"\bdismiss(?:es|ed|ing)?\b", r"\bdisput(?:e|es|ed)\b", r"\bfalse\b",
        r"\bunfounded\b", r"\bbaseless\b", r"\bno evidence\b", r"\bdenounce(?:s|d)?\b",
    ],
    "acknowledgement": [
        r"\bconfirm(?:s|ed|ing)?\b", r"\backnowledg(?:e|es|ed|ing)\b",
        r"\binvestigat(?:e|es|ed|ing|ion)\b", r"\bassess(?:es|ed|ing|ment)?\b",
        r"\badmit(?:s|ted|ting)?\b",
    ],
    "readiness_declaration": [
        r"\breadiness\b", r"\bcombat[- ]ready\b", r"\bready to\b", r"\bprepared for\b",
        r"\bhigh alert\b", r"\bfull alert\b", r"\bmobiliz(?:e|es|ed|ing|ation)\b",
        r"\bstate of readiness\b",
    ],
    "threat_warning": [
        r"\bwill respond\b", r"\bretaliat(?:e|es|ed|ing|ion|ory)\b",
        r"\bconsequences\b", r"\bwill pay\b", r"\bpromis(?:e|es|ed|ing)\b",
        r"\bthreat(?:s|en|ened|ening)?\b", r"\bwarn(?:s|ed|ing)?\b",
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default=None, help="只审某一类 claim")
    ap.add_argument("--show", type=int, default=4, help="每探针最多列几条漏检样本")
    ap.add_argument("--records", default=None, help="默认 output/ALL-records.jsonl")
    a = ap.parse_args()

    # ★ 关键：模块被 import 时 SIG_CFG/CLAIM_CFG 等全局量是**空的**
    #   （只在 main() 里赋值）。不显式注入配置，审计会拿着一份空词表跑，
    #   然后报告「全部漏检」—— 又是一次「工具本身没被验证」。
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    R.CLAIM_CFG = cfg.get("claims", {})
    R.SIG_CFG = cfg.get("signals", {})
    print("配置:", os.path.abspath(cfg_path),
          "| claim 类别:", {k: len(v) for k, v in R.CLAIM_CFG.items()})

    path = a.records or os.path.join(R.OUTPUT_DIR, "ALL-records.jsonl")
    print("审计文件:", os.path.abspath(path))
    if not os.path.exists(path):
        print("文件不存在")
        return 1
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    pool = [r for r in recs if r.get("signals")]
    print(f"语料 {len(recs)} 条，其中含战备信号 {len(pool)} 条\n")

    def hay(r):
        return R.norm_text((r.get("title") or "") + " \n " + (r.get("summary") or ""))

    kinds = [a.kind] if a.kind else list(PROBES)
    total_missed = 0
    for kind in kinds:
        lexicon = R.CLAIM_CFG.get(kind, [])
        covered = {id(r) for r in pool if R.find_hits(hay(r), lexicon)}
        print("=" * 80)
        print(f"### {kind}   词表 {len(lexicon)} 条   当前覆盖 {len(covered)} 篇")
        print("=" * 80)
        for p in PROBES[kind]:
            rx = re.compile(p, re.I)
            hits = [r for r in pool if rx.search(hay(r))]
            missed = [r for r in hits if id(r) not in covered]
            if not hits:
                continue
            total_missed += len(missed)
            flag = "★漏检" if missed else "  ok "
            print(f"{flag} {p:<50} 命中{len(hits):>3}  未覆盖{len(missed):>3}")
            for r in missed[:a.show]:
                m = rx.search(hay(r))
                print(f"        [{m.group(0)}] {r.get('party')} {r.get('source_id')} "
                      f"| {(r.get('title') or '')[:86]}")
    print("\n" + "-" * 80)
    print(f"合计未覆盖（探针粒度，含重叠计数）：{total_missed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
