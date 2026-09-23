# -*- coding: utf-8 -*-
"""audit_content.py — 内容准确性审计（承 ua-front/mideast 审计轮方法论）

重放分类（classify 是确定性函数，输入=落盘的 title+summary，输出必须与存档一致）：
  1. 桶不一致（双向都是 bug：确定性函数不应漂移）
  2. kind / stance / flags 不一致
  3. 完成态可疑：fact_attack 但标题含意向语态词（will/consider/plan to/expected to...）
  4. flags 语义张力（信息性，非硬错误）：delivered & delay_or_halt（交付后又暂停）、
     pledge_only & delay_or_halt（承诺后搁置）——逐条打印供人工复核
  5. 结构完整性：空桶但 kind!=excluded、重复 id、摘要触顶截断（1200）
  6. 每桶抽样打印（人工复核入口）
任一硬错误退出码非零。
"""
import json, re, sys, os
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ukraine_aid_monitor as m

def main(out_dir=None, date=None):
    base = out_dir or "output"
    d = date or max(x for x in os.listdir(base) if re.match(r"\d{4}-\d{2}-\d{2}", x))
    snap_path = os.path.join(base, d, "records.jsonl")
    recs = [json.loads(l) for l in open(snap_path, encoding="utf-8") if l.strip()]
    cfg = m.load_cfg()
    eng = m.Engine(cfg)
    intent_scan = re.compile(
        r"\b(?:will|to|consider\w*|could|should|would|if|whether|plan\w*|seek\w*|"
        r"offer\w*|propose\w*|expect\w*|aim\w*|set to|due to)\b", re.I)
    print(f"审计对象: {snap_path}  ({len(recs)} 条)")
    hard = []
    n_replay = n_bucket_mm = n_kind_mm = n_flag_mm = n_stance_mm = 0
    intent_susp = []
    flag_tension = []
    trunc = 0
    for r in recs:
        cls = eng.classify(r.get("title", ""), r.get("summary", ""))
        got = cls.get("buckets", []) if cls and not cls.get("anchor_only") else []
        n_replay += 1
        if set(got) != set(r.get("buckets") or []):
            n_bucket_mm += 1
            if len(hard) < 15:
                hard.append(f"桶不一致 [{r['source_id']}] {r['title'][:70]}\n"
                            f"    存档={r.get('buckets')} 重算={got}")
        if r.get("kind") == "excluded":
            continue
        ck_kind = cls.get("kind") if cls and not cls.get("anchor_only") else "excluded"
        if ck_kind != r.get("kind"):
            n_kind_mm += 1
            hard.append(f"kind不一致 [{r['source_id']}] {r['title'][:60]}: "
                        f"存档={r.get('kind')} 重算={ck_kind}")
        fl = (cls or {}).get("flags") or {}
        if {k: v for k, v in fl.items() if v} != {k: v for k, v in (r.get("flags") or {}).items() if v}:
            n_flag_mm += 1
            hard.append(f"flags不一致 [{r['source_id']}] {r['title'][:60]}")
        if (cls or {}).get("stance") != r.get("stance"):
            n_stance_mm += 1
            hard.append(f"stance不一致 [{r['source_id']}] {r['title'][:60]}: "
                        f"存档={r.get('stance')} 重算={(cls or {}).get('stance')}")
        # 完成态可疑：fact_attack + 标题意向语态
        if r.get("kind") == "fact_attack":
            if intent_scan.search(r["title"]):
                intent_susp.append(r)
        # flags 语义张力（信息性）
        f = r.get("flags") or {}
        if f.get("delivered") and f.get("delay_or_halt"):
            flag_tension.append(("delivered&delay", r))
        if f.get("pledge_only") and f.get("delay_or_halt"):
            flag_tension.append(("pledge&delay", r))
        # 截断边界
        if len(r.get("summary") or "") >= 1200:
            trunc += 1
    print(f"\n[1] 重放 {n_replay} 条：桶不一致 {n_bucket_mm}，kind {n_kind_mm}，"
          f"flags {n_flag_mm}，stance {n_stance_mm}")
    for h in hard[:15]:
        print("  ✗", h)
    print(f"\n[2] 完成态可疑（fact_attack + 标题意向语态）: {len(intent_susp)}")
    for r in intent_susp[:8]:
        print(f"  ? [{r['published']}] {r['title'][:90]}")
    print(f"\n[3] flags 语义张力（信息性，人工复核）: {len(flag_tension)}")
    for k, r in flag_tension[:5]:
        print(f"  ? [{k}] {r['title'][:90]}")
    print(f"\n[4] 摘要触顶 1200 字符（截断外命中风险）: {trunc}")
    ids = Counter(r["id"] for r in recs)
    dup = [i for i, c in ids.items() if c > 1]
    print(f"[5] 重复 id: {len(dup)}")
    empty_b = [r for r in recs if not r.get("buckets") and r.get("kind") != "excluded"]
    print(f"[6] 空桶但非 excluded: {len(empty_b)}")
    # 每桶抽样（人工复核）
    print("\n[7] 每桶最新 2 条（人工复核）:")
    seen = {}
    for r in sorted(recs, key=lambda x: x.get("published") or "", reverse=True):
        for b in r["buckets"]:
            seen.setdefault(b, 0)
            if seen[b] < 2:
                seen[b] += 1
                fl = [k for k, v in (r.get("flags") or {}).items() if v]
                print(f"  [{b}] {r['published']} ({r['tier']}/{r['kind']}/{r['stance']}) {r['title'][:88]}"
                      + (f" | {fl}" if fl else ""))
    bad = n_bucket_mm + n_kind_mm + n_flag_mm + n_stance_mm + len(dup) + len(empty_b)
    print(f"\n结论: {'✗ 硬错误 ' + str(bad) + ' 项' if bad else '✓ 重放全一致，无硬错误'}")
    return 1 if bad else 0

if __name__ == "__main__":
    out_dir = date = None
    args = sys.argv[1:]
    if "--out-dir" in args:
        out_dir = args[args.index("--out-dir") + 1]
    if "--date" in args:
        date = args[args.index("--date") + 1]
    sys.exit(main(out_dir, date))
