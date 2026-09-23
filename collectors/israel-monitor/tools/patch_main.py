#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""patch_main.py — 把 opinion-monitor 骨架域内化为 israel-monitor。
每条替换都 assert 命中，改完 AST 验证。"""
import io, ast

P = "israel_monitor.py"
src = io.open(P, encoding="utf-8").read()

def rep(old, new, n=1):
    global src
    assert old in src, "MISS: " + old[:80]
    src = src.replace(old, new, n)

# ---------- P1 docstring ----------
rep("opinion-monitor 对账", "israel-monitor 对账") if "opinion-monitor 对账" in src else None

# ---------- P2 anchor ----------
rep('''        self.anchor = term_re("iran")  # 占位，下面重编
        alts = ["iran(?:ian|ians)?", "tehran", "hormuz", r"middle\\s+east", "mideast"]''',
'''        self.anchor = term_re("israel")  # 占位，下面重编
        alts = ["israel(?:i|is)?", "idf", "jerusalem", r"tel\\s+aviv", "knesset",
                "netanyahu", "hezbollah", "hamas", r"west\\s+bank", "gaza",
                r"leban(?:on|ese)", r"syri(?:a|an)", "iran(?:ian)?", "tehran",
                r"middle\\s+east", "mideast"]''')

# ---------- P3/P4 stance keys ----------
rep('''        self.antiwar = [term_re(t) for t in cfg["stance"]["antiwar"]]
        self.prowar = [term_re(t) for t in cfg["stance"]["pro_war"]]''',
'''        self.anti = [term_re(t) for t in cfg["stance"]["anti"]]
        self.pro = [term_re(t) for t in cfg["stance"]["pro"]]''')
rep('lex += " " + " ".join(cfg["stance"]["antiwar"] + cfg["stance"]["pro_war"])',
    'lex += " " + " ".join(cfg["stance"]["anti"] + cfg["stance"]["pro"])')

# ---------- P5 stance_of ----------
rep('''        a = any(r.search(text) for r in self.antiwar)
        p = any(r.search(text) for r in self.prowar)
        if a and not p: return "antiwar_signal"
        if p and not a: return "pro_war_signal"''',
'''        a = any(r.search(text) for r in self.anti)
        p = any(r.search(text) for r in self.pro)
        if a and not p: return "anti_gov_signal"
        if p and not a: return "pro_gov_signal"''')

# ---------- P6 event_re + results_re ----------
rep('''            r"subpoena|indicts?|arrested?|confab|march(?:es|ed|ing)?|rall(?:y|ied|ies))\\b", re.I)''',
'''            r"subpoena|indicts?|arrests?|arrested|confab|march(?:es|ed|ing)?|rall(?:y|ied|ies)|"
            r"strikes?|struck|airstrikes?|shelling|shelled|launch(?:es|ed|ing)?|"
            r"killed|injur(?:e|es|ed)|detain(?:s|ed)?|dismantl(?:e|es|ed)|"
            r"seiz(?:e|es|ed)|raid(?:ed|s)?|operat(?:e|es|ed|ing)|stormed|"
            r"evacuat(?:e|es|ed)|blockade[sd]?|clamped)\\b", re.I)
        self.results_re = re.compile(
            r"\\b(?:exit\\s+polls?|final\\s+results?|results?|ballots?\\s+(?:counted|tallied)|"
            r"seat\\s+projections?|mandates?|coalition\\s+(?:talks|agreement|agreements|negotiations)|"
            r"concedes?|conceded)\\b", re.I)''')

# ---------- P8 anti_num ----------
rep('''self.anti_num = re.compile(r"\\b(?:oppose|opposed|disapprove|against|wrong|mistake|blunder)\\b", re.I)''',
    '''self.anti_num = re.compile(r"\\b(?:oppose|opposed|disapprove|against|wrong|mistake|blunder|resign|corruption|distrust)\\b", re.I)''')

# ---------- P9 pollsters ----------
rep('''        self.pollsters = ["Reuters/Ipsos", "Ipsos", "AP-NORC", "NORC", "Quinnipiac",
                          "Gallup", "YouGov", "Marist", "Schar", "RealClearPolitics",
                          "Morning Consult", "Emerson", "Monmouth", "Pew Research",
                          "Fox News", "CNN", "Reuters"]''',
'''        self.pollsters = ["Lazar", "Channel 12", "Channel 13", "Channel 14", "Kan",
                          "Maariv", "Israel Democracy Institute", "Ynet", "Walla",
                          "Mano", "Panel4U", "Smith Consulting"]''')

# ---------- P10 classify ----------
rep('''        if "polling" in buckets and polls:
            kind = "fact_poll"
        elif "congress" in buckets and self.vote_re.search(text):
            kind = "fact_vote"''',
'''        if "election_polls" in buckets and polls:
            kind = "fact_poll"
        elif ("election_polls" in buckets or "election_campaign" in buckets) \\
                and self.results_re.search(text):
            kind = "fact_vote"''')

# ---------- P11 labels ----------
rep('''KIND_LABEL = {"fact_poll": "事实·民调", "fact_vote": "事实·表决", "fact_event": "事实·事件",
              "opinion_statement": "观点·表态", "opinion_analysis": "观点·分析"}
STANCE_LABEL = {"antiwar_signal": "反战", "pro_war_signal": "挺战",
                "mixed_signal": "多空混存", "unclassified": "未分类"}''',
'''KIND_LABEL = {"fact_poll": "事实·民调", "fact_vote": "事实·结果", "fact_event": "事实·事件",
              "opinion_statement": "观点·表态", "opinion_analysis": "观点·分析",
              "excluded": "已排除"}
STANCE_LABEL = {"anti_gov_signal": "反内塔/倒阁", "pro_gov_signal": "挺内/挺政府",
                "mixed_signal": "双向混存", "unclassified": "未分类"}''')

# ---------- P12 filename ----------
rep('opinion-digest-{as_of.isoformat()}', 'israel-digest-{as_of.isoformat()}')

# ---------- 整段替换 render_report ----------
i0 = src.index("def render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir):")
i1 = src.index("# ---------------------------------------------------------------- main")
NEW_RENDER = r'''def render_report(cfg, records, undated, audit, stats, as_of, verify_note, outdir):
    W = []
    dt = as_of.isoformat()
    by_bucket = Counter(b for r in records for b in r["buckets"])
    by_kind = Counter(r["kind"] for r in records)
    by_stance = Counter(r["stance"] for r in records if r.get("stance") != "unclassified")
    polls_n = sum(len(r.get("polls") or []) for r in records)
    W.append(f"# 美伊冲突·以色列动向监测日报（{dt}）")
    W.append("")
    W.append("## 0. 阅读须知（先读，再读数字）")
    W.append("")
    W.append("- **事实/观点四分类**：每条记录标注 `kind`：")
    W.append("  - `fact_poll` 民调/席位预测数字=**事实**（发布值，程序只定位不加权）；")
    W.append("  - `fact_vote` 开票/组阁结果=**事实**（10-27 投票日之前不存在结果，只有预测）；")
    W.append("  - `fact_event` 可核验事件=**事实**（打击/突袭/逮捕/会晤/宣判）；")
    W.append("  - `opinion_statement` 表态=**『他说了X』是事实，X 本身是观点**，逐条附人物归属与上下文；")
    W.append("  - `opinion_analysis` 分析解读=**观点**。")
    W.append("- **信源等级**：T1=原始出处（UN 新闻稿等官方一手）；T3=媒体转述（含 IDF 声明的转述层）；")
    W.append("  T4（社交平台自媒体）不采集——不可核验。IDF 官网为 JS 渲染站无法直连，其声明均经 T3 转述。")
    W.append("- **伤亡/损失数字是当事方口径**：以方（IDF 声明）与黎方（卫生部长）/巴方（哈马斯卫生部）")
    W.append("  可差数倍，逐条标注归属；本程序不做核实只做并列。")
    W.append("- **立场词表是启发式**：只看方向不看强度（反内塔/倒阁 vs 挺内/挺政府），逐条附上下文可复核。")
    W.append(f"- 本轮配置指纹 `cfg_ver={stats['cfg_ver'][:12]}…`；快照内唯一（闸门断言）。")
    W.append("")
    W.append(f"## 1. 快照头部（{dt}）")
    W.append("")
    W.append(f"- 抓取源 {stats['n_enabled']} 个（T1 {stats['n_t1']} / T3 {stats['n_t3']}；停用留档 {stats['n_disabled']}）")
    W.append(f"- 本次新增 **{stats['n_new']}** 条；当前快照（窗口 {cfg['window_days']} 天）**{len(records)}** 条")
    W.append(f"- 窗口内信号桶计数：`" + "、".join(
        f"{cfg['buckets'][b]['label']} {n}" for b, n in by_bucket.most_common()) + "`")
    W.append(f"- 事实/观点：`" + "、".join(f"{KIND_LABEL[k]} {n}" for k, n in by_kind.most_common() if k in KIND_LABEL) + "`")
    W.append(f"- 立场分布（启发式）：`" + "、".join(f"{STANCE_LABEL[s]} {n}" for s, n in by_stance.most_common()) + "`")
    W.append(f"- 民调/席位数字抽取：**{polls_n}** 个（§2 逐个列示）")
    W.append(f"- 回源对账：{verify_note}")
    W.append("")
    # §2 民调
    W.append("## 2. 民调/席位预测证据板（fact_poll）")
    W.append("")
    W.append("| 日期 | 机构 | 数值·方向 | 口径句（context） | 来源/等级 | 原文 |")
    W.append("|---|---|---|---|---|---|")
    for r in sorted([x for x in records if x["kind"] == "fact_poll"],
                    key=lambda x: x.get("published") or "", reverse=True):
        for p in (r.get("polls") or []):
            ctx = p["context"].replace("|", "/")
            W.append(f"| {r['published'][:10] if r['published'] else '—'} "
                     f"| {p['pollster'] or r['publisher']} "
                     f"| **{p['value']}%** { {'anti':'反内塔/倒阁','pro':'挺内/挺政府','other':'—'}[p['direction']] } "
                     f"| {ctx} | {r['publisher']} / {r['tier']} | {_row_link(r)} |")
    if not any(r["kind"] == "fact_poll" for r in records):
        W.append("| — | 本窗口未捕获民调报道 | — | — | — | — |")
    W.append("")
    # §3 大选
    W.append("## 3. 大选与组阁（第 26 届议会，2026-10-27 投票）")
    W.append("")
    W.append("### 3.1 选举基线（手工快照，含民调均值与关键事实）")
    W.append("")
    W.append("| 日期 | 事项 | 数值/内容 | 等级 | 来源 | 备注 |")
    W.append("|---|---|---|---|---|---|")
    for b in cfg.get("election_baseline", []):
        W.append(f"| {b['date']} | {b['item']} | {b['value']} | {b['tier']} "
                 f"| {b['src'][:60]} | {b.get('note','')} |")
    W.append("")
    W.append("> ⚠️ 基线为 T3 转写快照（聚合/媒体渠道，2026-09-19 录入）；")
    W.append("> 投票日后须以中央选举委员会官方结果替换，逐条回源。")
    W.append("")
    W.append("### 3.2 窗口内新增大选/组阁动向")
    W.append("")
    elec = [r for r in records if r["buckets"] and
            ("election_campaign" in r["buckets"] or r["kind"] == "fact_vote")]
    if elec:
        for r in sorted(elec, key=lambda x: x.get("published") or "", reverse=True)[:30]:
            ents = "、".join(e for g in r["entities"].values() for e in g) or "—"
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"**[{KIND_LABEL[r['kind']]}·{st}]** {r['title']} — 人物: {ents} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无大选/组阁条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §4 内塔尼亚胡
    W.append("## 4. 内塔尼亚胡：司法与政治处境")
    W.append("")
    W.append("### 4.1 审判/法律基线（手工快照）")
    W.append("")
    W.append("| 日期 | 事项 | 内容 | 等级 | 备注 |")
    W.append("|---|---|---|---|---|")
    for b in cfg.get("netanyahu_baseline", []):
        W.append(f"| {b['date']} | {b['item']} | {b['value']} | {b['tier']} | {b.get('note','')} |")
    W.append("")
    W.append("### 4.2 窗口内新增司法/政治动向")
    W.append("")
    trial = [r for r in records if "netanyahu_trial" in r["buckets"]]
    if trial:
        for r in sorted(trial, key=lambda x: x.get("published") or "", reverse=True)[:25]:
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无司法/政治条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §5 四域军事
    W.append("## 5. 四域军事动向")
    W.append("")
    W.append("### 5.1 军事基线（四域关键事实快照）")
    W.append("")
    W.append("| 域 | 日期 | 事项 | 内容 | 等级 | 备注 |")
    W.append("|---|---|---|---|---|---|")
    DOMAINS = [("lebanon", "黎巴嫩"), ("syria", "叙利亚"), ("westbank", "约旦河西岸"), ("gaza", "加沙")]
    for b in cfg.get("military_baseline", []):
        dn = dict(DOMAINS).get(b["domain"], b["domain"])
        W.append(f"| {dn} | {b['date']} | {b['item']} | {b['value']} | {b['tier']} | {b.get('note','')} |")
    W.append("")
    for did, dlabel in DOMAINS:
        W.append(f"### 5.{DOMAINS.index((did, dlabel))+2} {dlabel}（窗口内新增）")
        W.append("")
        rows = [r for r in records if did in r["buckets"]]
        if rows:
            for r in sorted(rows, key=lambda x: x.get("published") or "", reverse=True)[:25]:
                st = STANCE_LABEL.get(r.get("stance"), "—")
                W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                         f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} "
                         f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        else:
            W.append(f"（本窗口无{dlabel}条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
        W.append("")
    # §6 对伊第二战线
    W.append("## 6. 对伊第二战线（伊朗/胡塞方向）")
    W.append("")
    irf = [r for r in records if "iran_front" in r["buckets"]]
    if irf:
        for r in sorted(irf, key=lambda x: x.get("published") or "", reverse=True)[:25]:
            st = STANCE_LABEL.get(r.get("stance"), "—")
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` "
                     f"[{KIND_LABEL[r['kind']]}·{st}] {r['title']} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无对伊条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §7 街头
    W.append("## 7. 街头行动/抗议（以色列国内）")
    W.append("")
    prot = [r for r in records if "protest" in r["buckets"]]
    if prot:
        for r in sorted(prot, key=lambda x: x.get("published") or "", reverse=True)[:25]:
            W.append(f"- `{r['published'][:10] if r['published'] else '—'}` {r['title']} "
                     f"｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
    else:
        W.append("（本窗口无街头行动条目——『无条目≠无活动』，历史见累积档 ALL-records.jsonl）")
    W.append("")
    # §8 无日期
    W.append("## 8. 无日期条目（不跨轮累积，仅本轮披露）")
    W.append("")
    if undated:
        for r in undated[:40]:
            W.append(f"- （日期未知）{r['title']} ｜ {r['publisher']}({r['tier']}) ｜ {_row_link(r)}")
        W.append(f"\n共 {len(undated)} 条；`published=Thu, 01 Jan 1970` 类纪元零值已按『日期未知』处理。")
    else:
        W.append("（无）")
    W.append("")
    # §9 已知偏差
    W.append("## 9. 已知偏差与局限")
    W.append("")
    for i, x in enumerate(cfg["report"]["known_limitations"], 1):
        W.append(f"{i}. {x}")
    W.append(f"{len(cfg['report']['known_limitations'])+1}. 停用源留档见附录A；"
             f"被锚定拦截的候选 {len(audit.get('anchor_no_bucket', []))} 条已留档 `_filter_audit.json`。")
    W.append("")
    # 附录A
    W.append("## 附录A 信源清单（含停用源与等级）")
    W.append("")
    W.append("| id | 等级 | publisher | 状态 | 说明 |")
    W.append("|---|---|---|---|---|")
    for s in cfg["sources"]:
        st = "启用" if s.get("enabled") else "停用"
        W.append(f"| {s['id']} | {s['tier']} | {s['publisher']} | {st} | {(s.get('note') or '')[:120]} |")
    W.append("")
    W.append("## 附录B 等级定义与分类学")
    W.append("")
    W.append("- **T1** 原始出处（UN 新闻稿等官方一手）；**T2** 官方/机构估算（本项目暂无）；")
    W.append("  **T3** 媒体转述（含 Google News 聚合、IDF/真主党声明的转述层）；**T4** 自媒体——不采用。")
    W.append("- 事实（发布值/结果/事件）≠ 机械抽取（百分比定位）≠ 启发式判断（立场词表）。")
    W.append("- 『某人物主张X』是关于该人物的**事实**；X 是**观点**。本报告同时保留两层。")
    W.append("")
    return "\n".join(W)

'''
src = src[:i0] + NEW_RENDER + src[i1:]

# ---------- 整段替换 selftest ----------
j0 = src.index("def run_selftest(cfg, eng):")
NEW_ST = r'''def run_selftest(cfg, eng):
    P = []
    def ck(name, cond):
        P.append((name, bool(cond)))

    R = term_re
    # --- 词形族 ---
    for w, forms in [("strike", ["strikes", "striking"]),
                     ("launch", ["launches", "launched", "launching"]),
                     ("coalition", ["coalitions"]),
                     ("protest", ["protests", "protested", "protesting", "protester"]),
                     ("oppose", ["opposes", "opposed", "opposing"]),
                     ("condemn", ["condemns", "condemned", "condemnation"]),
                     ("resign", ["resigns", "resigned", "resignation"]),
                     ("march", ["marches", "marched", "marching"])]:
        rx = R(w)
        ck(f"词形族 {w}", all(rx.search(f) for f in [w] + forms))
    ck("无半截词根", not any(re.search(r"(ed|ing)$", t) for t in
       cfg["stance"]["anti"] + cfg["stance"]["pro"]))
    # --- 实体陷阱 ---
    ck("Yair Golan 不误中 Golan Heights", not alias_re("Yair Golan").search("IDF operates near the Golan Heights"))
    ck("Eisenkot 双拼写都命中", any(r.search("Eizenkot said") for r in
       [alias_re(a) for a in cfg["entities"]["campaign"]["Gadi Eisenkot"]]))
    ck("Netanyahu 命中所有格", any(r.search("Netanyahu's coalition") for r in
       [alias_re(a) for a in cfg["entities"]["campaign"]["Benjamin Netanyahu"]]))
    ck("President Aoun 命中", any(r.search("President Aoun urged Washington") for r in
       [alias_re(a) for a in cfg["entities"]["officials"]["Joseph Aoun"]]))
    ck("disapprove 不误中 approve", not alias_re("approve").search("43% disapprove"))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("美式 Mon d, YYYY", parse_any_date("September 5, 2026")[0] == "2026-09-05")
    ck("年份离谱判失败", parse_any_date("13 Sep 99 10:00:00 +0000") == (None, False))
    ck("垃圾判失败", parse_any_date("not a date") == (None, False))
    # --- 民调抽取 ---
    t1 = "45% of Israelis want Netanyahu to resign while 27% back him, the Lazar poll found."
    ex = eng.extract_polls(t1)
    ck("民调抽取 2 个", len(ex) == 2)
    ck("方向 anti（resign 最近）", ex and ex[0]["direction"] == "anti" and ex[0]["value"] == 45)
    ck("方向 pro（back 最近）", len(ex) > 1 and ex[1]["direction"] == "pro" and ex[1]["value"] == 27)
    ck("机构识别 Lazar", ex and ex[0]["pollster"] == "Lazar")
    t2 = "The poll of 800 respondents had a margin of error of 3 percentage points."
    ck("误差边际排除", all(p["value"] != 3 for p in eng.extract_polls(t2)) or not eng.extract_polls(t2))
    # --- 分类 ---
    r1 = eng.classify("Israel poll: 45% want Netanyahu to resign, 27% back him",
                      "The Lazar survey found 45 percent want him to go.")
    ck("大选民调→fact_poll", r1 and r1["kind"] == "fact_poll")
    ck("民调记录实体含 Netanyahu", r1 and "Benjamin Netanyahu" in r1["entities"].get("campaign", []))
    r2 = eng.classify("Netanyahu tells court the cases against him are fabricated",
                      "In the trial hearing, the PM said the prosecution relies on flawed evidence.")
    ck("审判听证→opinion_statement", r2 and r2["kind"] == "opinion_statement"
       and "netanyahu_trial" in r2["buckets"])
    r3 = eng.classify("IDF strikes Hezbollah infrastructure in southern Lebanon after drone attack",
                      "The military said the strikes targeted weapons storage in Nabatieh.")
    ck("黎巴嫩打击→fact_event", r3 and r3["kind"] == "fact_event" and "lebanon" in r3["buckets"])
    r4 = eng.classify("Israel operates in security zone near Golan Heights, Syrian media report",
                      "IDF troops operated near Mount Hermon overnight.")
    ck("叙利亚→fact_event", r4 and r4["kind"] == "fact_event" and "syria" in r4["buckets"])
    ck("Golan Heights 不进人物实体", r4 and "Yair Golan" not in r4["entities"].get("campaign", []))
    r5 = eng.classify("Israel warns Iran over nuclear rebuild",
                      "Officials vowed to prevent Tehran from reconstituting enrichment.")
    ck("对伊表态→opinion_statement", r5 and r5["kind"] == "opinion_statement"
       and "iran_front" in r5["buckets"])
    r6 = eng.classify("Thousands rally in Tel Aviv against the government ahead of the election",
                      "Protesters gathered demanding early elections.")
    ck("集会→fact_event·反政府", r6 and r6["kind"] == "fact_event"
       and r6["stance"] == "anti_gov_signal" and "protest" in r6["buckets"])
    r7 = eng.classify("Exit poll: Likud wins 30 seats in Israel election", "Channel 12 exit poll published.")
    ck("开票→fact_vote", r7 and r7["kind"] == "fact_vote")
    r8 = eng.classify("IDF raid in the West Bank village of Talfit",
                      "Weapons were seized and three suspects arrested, the military said.")
    ck("西岸突袭→fact_event", r8 and "westbank" in r8["buckets"] and r8["kind"] == "fact_event")
    r9 = eng.classify("Gaza ceasefire holds as IDF controls buffer zone",
                      "The army said it struck a Hamas cell after an alleged violation.")
    ck("加沙停火→fact_event", r9 and "gaza" in r9["buckets"])
    r10 = eng.classify("Fed minutes signal rate path", "Inflation and employment discussion.")
    ck("无锚定=不收", r10 is None)
    r11 = eng.classify("Middle East envoy discusses regional security", "Talks continued in Doha.")
    ck("锚定无桶=拦截候选", r11 and r11.get("anchor_only") is True)
    # --- 桶结构不变量 ---
    ck("election_polls 二维", len(cfg["buckets"]["election_polls"]["require_all"]) == 2)
    ck("所有桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每源有 tier", all(s.get("tier") in ("T1", "T2", "T3") for s in cfg["sources"]))
    ck("无 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"]))
    ck("停用源有留档", all("note" in s and s["note"] for s in cfg["sources"] if not s.get("enabled")))
    ck("选举基线字段完整", all(b.get("date") and b.get("item") and b.get("tier") in ("T1", "T2", "T3")
       and b.get("src") for b in cfg["election_baseline"]))
    ck("司法基线字段完整", all(b.get("date") and b.get("item") and b.get("tier") in ("T1", "T2", "T3")
       for b in cfg["netanyahu_baseline"]))
    ck("军事基线四域合法", all(b["domain"] in ("lebanon", "syria", "westbank", "gaza")
       and b.get("date") and b.get("item") for b in cfg["military_baseline"]))
    # --- title_key 折叠 ---
    ck("gnews 后缀折叠", title_key("Likud leads poll - Reuters") ==
       title_key("Likud leads poll - The Business Standard"))
    ck("链接归一", norm_link("https://x.com/a?utm_source=1&ocid=2") == "https://x.com/a")
    # --- 窗口/合并 ---
    arch = {"a": {"id": "a", "published": "2026-09-01", "date_unknown": False}}
    recs = [{"id": "a", "published": "2026-09-01", "date_unknown": False, "run_date": "2026-09-19"},
            {"id": "b", "published": "2026-09-18", "date_unknown": False, "run_date": "2026-09-19"},
            {"id": "u", "published": None, "date_unknown": True, "run_date": "2026-09-19"}]
    n = merge_archive(arch, recs)
    ck("合并新增=1（无日期不携带）", n == 1 and "u" not in arch and "b" in arch and "a" in arch)
    win, und = window_filter(list(arch.values()), date(2026, 9, 19), 14)
    ck("窗口过滤（09-01 掉出 14 天窗）", {x["id"] for x in win} == {"b"} and len(und) == 0)
    # --- 月份 March ≠ 抗议动词 march（opinion-monitor 实测假阳性，守卫迁移）---
    rm = eng.classify("Deaths reported in March 2026 during the Israel-Iran war",
                      "A service member died of undetermined causes on March at a base.")
    ck("March 月份不进抗议桶",
       rm is None or rm.get("anchor_only") or "protest" not in rm["buckets"])
    rm2 = eng.classify("Thousands march against the government in Tel Aviv",
                       "Protesters rally demanding elections now.")
    ck("动词 march 进抗议桶",
       rm2 is not None and not rm2.get("anchor_only") and "protest" in rm2["buckets"])
    # --- gnews summary 复读导致同数字双抽 → 去重 ---
    dd = eng.extract_polls("Poll: 71% distrust Netanyahu. Poll: 71% distrust the government.")
    ck("同数字复读去重", len(dd) == 1)
    # --- 数字实体解码 ---
    ck("数字实体解码", strip_tags("died &#8230; later") == "died … later")
    # --- y→ied 变位 ---
    ck("justify 词形族", term_re("justify").search("justified") and
       term_re("justify").search("justifies"))
    # --- 回填掉桶 → excluded，不进快照（防『保留旧桶只刷指纹』的混龄档案）---
    arch2 = {"x": {"id": "x", "published": "2026-09-15", "date_unknown": False,
                   "title": "Regional discussion in March 2026",
                   "summary": "Middle East policy talk continued.",
                   "kind": "fact_event", "buckets": ["protest"], "cfg_ver": "OLD"},
             "y": {"id": "y", "published": "2026-09-16", "date_unknown": False,
                   "title": "Central bank meeting minutes",
                   "summary": "Rates discussed.",
                   "kind": "fact_event", "buckets": ["protest"], "cfg_ver": "OLD"}}
    backfill_archive(cfg, eng, arch2)
    ck("回填掉桶(anchor_only)→excluded", arch2["x"]["kind"] == "excluded" and arch2["x"]["buckets"] == [])
    ck("回填掉桶(无锚定)→excluded", arch2["y"]["kind"] == "excluded" and arch2["y"]["buckets"] == [])
    win2, _ = window_filter(list(arch2.values()), date(2026, 9, 19), 14)
    ck("excluded 不进快照", len(win2) == 0)
    # --- 核心结论能产出（防『全绿但结论恒空』）---
    ck("核心结论: 民调/表态/事件/结果 四类都能产出",
       r1["kind"] == "fact_poll" and r2["kind"] == "opinion_statement"
       and r3["kind"] == "fact_event" and r7["kind"] == "fact_vote")
    ok = sum(1 for _, c in P if c)
    for name, c in P:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"[selftest] {ok}/{len(P)} 通过")
    return 0 if ok == len(P) else 1

'''
src = src[:j0] + NEW_ST

io.open(P, "w", encoding="utf-8").write(src)
ast.parse(src)
print("patched + AST OK")
