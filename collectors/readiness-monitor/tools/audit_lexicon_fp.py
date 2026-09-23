#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""词表假阳性审计：列出每个词条在**真实语料**里实际匹配到的完整词形。

为什么需要它
------------
匹配规则会在词条**末词**上追加词形族 `(?:s|es|ed|ing|d)?`。这个后缀是必要的
（否则 `destroys` 漏检 `destroyed`），但它会**悄悄造出字典里不存在的词**：
`war` + `d` → 匹配 `ward`；`air` + `d` → 匹配 `aired`。

这类错误在程序里完全看不出来：日志绿、条数正常、上下文片段也是原文里真实存在的
字符串 —— 只是它被算成了「战备信号」。所以必须把**实际词形**打印出来人工过一眼。

用法
----
    python tools/audit_lexicon_fp.py                 # 默认读最新日期目录快照
    python tools/audit_lexicon_fp.py --records accumulated
    python tools/audit_lexicon_fp.py --file <path.jsonl>
    python tools/audit_lexicon_fp.py --top 60
    python tools/audit_lexicon_fp.py --suspicious    # 只打印可疑词形

退出码：始终 0（这是人工复核工具，不是门禁；门禁在 --selftest 里）。
"""
import argparse
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import readiness_monitor as R  # noqa: E402

SUSPICIOUS_RE = re.compile(
    r"(?:ward|wards|warded|airing|aired|airs|raids?d|"
    r"[\w]{3,}(?:eee|dd|sss|iiing))$", re.I)


def load_cfg(path):
    cfg = json.load(open(path, encoding="utf-8"))
    R.CFG = cfg
    R.SIG_CFG = cfg.get("signals", {})
    R.CLAIM_CFG = cfg.get("claims", {})
    R.REL_CFG = cfg.get("relevance", {})
    R.CROSS_ANCHORS = cfg.get("cross_anchors", [])
    R.OUTLET_MAP = cfg.get("outlet_map", {})
    R.SOURCE_BY_ID = {s["id"]: s for s in cfg.get("sources", [])}
    return cfg


def pick_records(records_arg, file_arg):
    if file_arg:
        p = file_arg
    elif records_arg == "accumulated":
        p = os.path.join(R.OUTPUT_DIR, "ALL-records.jsonl")
    else:
        # 默认取**最新日期目录**快照 —— 绝不写死日期（写死会对着旧快照说 OK）
        dirs = sorted(d for d in os.listdir(R.OUTPUT_DIR)
                      if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))
        if not dirs:
            dirs = sorted(d for d in os.listdir(R.OUTPUT_DIR)
                          if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))
        p = os.path.join(R.OUTPUT_DIR, dirs[-1], f"records-{dirs[-1]}.jsonl")
    if not os.path.exists(p):
        print(f"❌ 找不到记录文件：{p}")
        return None, p
    recs = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs, p


def all_terms(cfg):
    """收集所有**单拉丁**词条（含 ` + ` 复合条件的各分量单独看）。"""
    terms = set()
    for spec in cfg.get("signals", {}).values():
        terms.update(spec.get("phrases", []) or [])
    for v in (cfg.get("claims") or {}).values():
        if isinstance(v, list):
            terms.update(v)
    rel = cfg.get("relevance") or {}
    for v in rel.values():
        if isinstance(v, list):
            terms.update(v)
    for a in (cfg.get("cross_anchors") or []):
        if isinstance(a, dict) and a.get("re"):
            pass                       # anchor 走独立正则，不在此列
    out = set()
    for t in terms:
        if not t or " + " in t:
            for part in t.split(" + "):
                part = part.strip()
                if part and R._is_ascii(R.norm_text(part)):
                    out.add(part)
            continue
        if R._is_ascii(R.norm_text(t)):
            out.add(t)
    return out


def main():
    ap = argparse.ArgumentParser(description="词表假阳性审计")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--records", choices=["snapshot", "accumulated"],
                    default="snapshot")
    ap.add_argument("--file", default=None, help="直接指定 jsonl 路径")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--suspicious", action="store_true",
                    help="只打印可疑词形（疑似词形族造出的非词）")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    recs, path = pick_records(args.records, args.file)
    if recs is None:
        return 2
    print(f"# 词表假阳性审计  （代码 {R.VERSION}）")
    print(f"# 读取文件：{path}")
    print(f"# 语料条数：{len(recs)}")
    print()

    corpus = " \n ".join((r.get("title") or "") + " . " + (r.get("summary") or "")
                         for r in recs)
    hay = R.norm_text(corpus)
    terms = all_terms(cfg)
    print(f"# 参与扫描的单拉丁词条：{len(terms)}")
    print()

    rows = []
    for t in sorted(terms):
        rx = R._phrase_pattern(R.norm_text(t))
        forms = collections.Counter()
        for m in rx.finditer(hay):
            forms[m.group(0).lower()] += 1
        if forms:
            rows.append((sum(forms.values()), t, forms))

    if args.suspicious:
        print("=== 疑似「词形族造出的非词」命中 ===")
        print("（若某条确实不该命中，应把该词条改为更精确的形式或加排除项）")
        print()
        n_found = 0
        for n, t, forms in rows:
            weird = {k: v for k, v in forms.items() if SUSPICIOUS_RE.search(k)}
            if weird:
                n_found += 1
                print(f"  {t:26s} ({n} 次) 可疑词形 -> {weird}")
        if not n_found:
            print("  ✅ 未发现可疑词形")
        print()
        return 0

    rows.sort(reverse=True)
    print(f"=== 命中量前 {args.top} 的词条及其**实际匹配词形** ===")
    print("（左=总命中次数，中=词表里的基础形，右=语料中实际匹配到的完整词形）")
    print()
    for n, t, forms in rows[: args.top]:
        flag = " ⚠️" if any(SUSPICIOUS_RE.search(k) for k in forms) else ""
        print(f"{n:6d}  {t:26s} -> {dict(forms)}{flag}")
    print()

    never = sorted(terms - {t for _, t, _ in rows})
    print(f"=== 零命中词条（{len(never)} 个）===")
    print("（零命中不一定是错：可能是该话题本窗口没出现。但要确认词条本身没写错）")
    print("  " + ", ".join(never[:80]) + (" …" if len(never) > 80 else ""))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
