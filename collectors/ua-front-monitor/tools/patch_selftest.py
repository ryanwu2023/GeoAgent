# -*- coding: utf-8 -*-
"""patch_selftest.py — 用战线版自检替换 ua_front_monitor.py 的 run_selftest 函数"""
import io, re, sys

SRC = "ua_front_monitor.py"
src = io.open(SRC, encoding="utf-8").read()
assert chr(8) not in src.replace("\\b", ""), "源码含退格符"

start = src.index("# ---------------------------------------------------------------- selftest")
end = src.index('if __name__ == "__main__":')

NEW = '''# ---------------------------------------------------------------- selftest
def run_selftest(cfg, eng):
    P = []
    def ck(name, cond):
        P.append((name, bool(cond)))

    # --- 词形族 ---
    for w, forms in [("storm", ["storms", "stormed", "storming"]),
                     ("intercept", ["intercepts", "intercepted", "intercepting", "interception"]),
                     ("encircl", ["encircle", "encircled", "encirclement"]),
                     ("liberat", ["liberate", "liberated", "liberation"]),
                     ("recaptur", ["recapture", "recaptured", "recapturing"]),
                     ("infiltrat", ["infiltrate", "infiltrated", "infiltration"]) ]:
        rx = term_re(w)
        ck(f"词形族 {w}", all(rx.search(f) for f in [w] + forms))
    # 连字符/变体
    ck("ceasefire 连字符双词", term_re("ceasefire").search("ceasefire") and term_re("cease-fire").search("cease-fire"))
    ck("front line 分合写并列", term_re("front line").search("front line") and term_re("frontline").search("frontline"))
    ck("air defense 英美拼写并列", term_re("air defense").search("air defense") and term_re("air defence").search("air defence"))
    ck("Odesa 敖德萨拼写", term_re("odesa").search("Odesa port"))
    # --- 日期 ---
    ck("RFC822 全称", parse_any_date("Sat, 13 Sep 2026 09:00:00 +0000") == ("2026-09-13", False))
    ck("两位数年", parse_any_date("Wed, 29 Jul 26 12:37:14 +0200") == ("2026-07-29", False))
    ck("纪元零值=未知", parse_any_date("Thu, 01 Jan 1970 00:00:00 +0000") == (None, True))
    ck("ISO", parse_any_date("2026-09-12T10:00:00Z")[0] == "2026-09-12")
    ck("年份离谱判失败", parse_any_date("13 Sep 99 10:00:00 +0000") == (None, False))
    # --- 桶命中：地面战线 ---
    r1 = eng.classify("Russian forces stormed Pokrovsk outskirts, fighting reaches Dobropillia",
                      "Assault groups advanced toward the fortress belt; shelling reported overnight.")
    ck("波克罗夫斯克→donbas_front（二维）", r1 and "donbas_front" in r1["buckets"])
    r2 = eng.classify("Russian assault near Kupiansk repelled, bridgehead holds on Oskil river",
                      "Ukrainian defenders repelled attacks toward Borova and Vovchansk, General Staff said.")
    ck("库皮扬斯克→kharkiv_front", r2 and "kharkiv_front" in r2["buckets"])
    r3 = eng.classify("Zaporizhzhia sector shelled as Kursk border incursion continues",
                      "Artillery struck settlements near Sumy and Belgorod, officials said.")
    ck("南部/边境带→other_fronts", r3 and "other_fronts" in r3["buckets"])
    # --- 桶命中：空中战役 ---
    r4 = eng.classify("Russia launches 453 Shahed drones overnight, 405 downed by air defenses",
                      "Jet-powered Geran drones attacked Kyiv and Odesa; kamikaze drones intercepted en masse.")
    ck("无人机战→drone_war", r4 and "drone_war" in r4["buckets"])
    ck("拦截命中→air_defense", r4 and "air_defense" in r4["buckets"])
    r5 = eng.classify("Iskander ballistic missile strike hits Kharkiv district",
                      "Glide bombs and KN-23 missiles targeted infrastructure, authorities said.")
    ck("导弹/滑翔弹→long_range", r5 and "long_range" in r5["buckets"])
    r6 = eng.classify("Naval drone attacks Black Sea fleet near Sevastopol, Odesa port hit",
                      "Sea Baby drones struck shipping; the Sea of Azov launch areas monitored.")
    ck("黑海/亚速海→black_sea", r6 and "black_sea" in r6["buckets"])
    r7 = eng.classify("Ukraine air defense intercepts 90 percent of drones with Patriot batteries",
                      "NASAMS and mobile fire groups suppressed the rest, Air Force reported.")
    ck("防空能力→air_defense", r7 and "air_defense" in r7["buckets"])
    r8 = eng.classify("Milbloggers claimed encirclement near Vovchansk; ISW assesses no evidence",
                      "Russian military bloggers asserted gains; geolocated footage confirmed otherwise.")
    ck("声称核实→claims_intel", r8 and "claims_intel" in r8["buckets"])
    # --- flags 声称/核实 ---
    rf1 = eng.classify("Pokrovsk almost surrounded, Russian MoD says",
                       "According to the Russian Ministry of Defence, its forces seized the airfield.")
    ck("俄方声称 ru_claim", rf1 and rf1["flags"].get("ru_claim") is True)
    rf2 = eng.classify("General Staff reported 230 combat engagements over past day",
                       "Ukraine says its forces repelled assaults in the Pokrovsk sector.")
    ck("乌方声称 ua_claim", rf2 and rf2["flags"].get("ua_claim") is True)
    rf3 = eng.classify("Both sides claim gains near Kostiantynivka, claims not independently verified",
                       "The claims could not be verified; ISW assessed the front unchanged.")
    ck("未经核实 unverified", rf3 and rf3["flags"].get("unverified") is True)
    rf4 = eng.classify("Ukrainian brigade warns of encirclement risk at Pokrovsk",
                       "Soldiers described pockets forming; commanders denied troops are cut off.")
    ck("包围表述 encirclement_alert", rf4 and rf4["flags"].get("encirclement_alert") is True)
    # --- 排除与锚定 ---
    rg3 = eng.classify("Morning recap", "Russian missiles struck Kharkiv; drones downed over Odesa; shelling continued.")
    ck("Morning recap 不进任何桶", rg3 is None or rg3.get("anchor_only") or not rg3["buckets"])
    r15 = eng.classify("Quarterly earnings beat expectations", "Tech shares rallied.")
    ck("无锚定=不收", r15 is None)
    r16 = eng.classify("Kyiv hosted a film festival with European guests", "The program included premieres and panels.")
    ck("锚定无桶=拦截候选", r16 and r16.get("anchor_only") is True)
    # --- kind 顺序 ---
    re1 = eng.classify("Russian missiles struck Kharkiv, authorities said", "Three people were wounded; fires broke out.")
    ck("完成态打击→fact_attack", re1 and re1["kind"] == "fact_attack")
    rs1 = eng.classify("Zelensky says the front is holding despite pressure", "He warned that Russia massed troops near Pokrovsk.")
    ck("表态→opinion_statement", rs1 and rs1["kind"] == "opinion_statement")
    ra1 = eng.classify("The Pokrovsk front appears increasingly fragile", "Analysts see a war of attrition grinding on.")
    ck("分析→opinion_analysis", ra1 and ra1["kind"] == "opinion_analysis")
    # --- 意向语态守卫（承 diplomacy 审计轮教训）---
    ri1 = eng.classify("Russia expected to launch new missile wave this winter", "Officials said the attack could target energy grid.")
    ck("expected to launch ≠ 完成态", ri1 is None or ri1["kind"] != "fact_attack")
    ri2 = eng.classify("Ukraine plans to strike Russian refineries with Flamingo missiles", "The campaign aims to cut diesel supply.")
    ck("plans to strike ≠ 完成态", ri2 is None or ri2["kind"] != "fact_attack")
    ri3 = eng.classify("Russia shelled Zaporizhzhia overnight, 405 drones downed", "Geran drones hit energy sites.")
    ck("真实完成态不被意向守卫误杀", ri3 and ri3["kind"] == "fact_attack")
    # --- 立场/否定 ---
    ck("立场降温 ceasefire", eng.stance_of("The two sides observed a brief ceasefire before shelling resumed.") == "deescalation_signal")
    ck("立场升级 massive strike", eng.stance_of("Russia launched a massive strike on the energy grid.") == "escalation_signal")
    ck("否定翻转 no truce", eng.stance_of("Officials insist there will be no truce before winter.") == "escalation_signal")
    ck("双向混存", eng.stance_of("Massive strikes continued even as both sides discussed a pause.") == "mixed_signal")
    # --- gnews 后缀折叠/链接归一 ---
    ck("gnews 后缀折叠", title_key("Pokrovsk battle rages - Reuters") ==
       title_key("Pokrovsk battle rages - The Business Standard"))
    ck("链接归一", norm_link("https://x.com/a?utm_source=1&ocid=2") == "https://x.com/a")
    # --- 窗口/合并 ---
    arch = {"a": {"id": "a", "published": "2026-08-01", "date_unknown": False}}
    recs = [{"id": "a", "published": "2026-08-01", "date_unknown": False, "run_date": "2026-09-19"},
            {"id": "b", "published": "2026-09-18", "date_unknown": False, "run_date": "2026-09-19"},
            {"id": "u", "published": None, "date_unknown": True, "run_date": "2026-09-19"}]
    n = merge_archive(arch, recs)
    ck("合并新增=1（无日期不携带）", n == 1 and "u" not in arch and "b" in arch and "a" in arch)
    win, und = window_filter(list(arch.values()), date(2026, 9, 19), 30)
    ck("窗口过滤（08-01 掉出 30 天窗）", {x["id"] for x in win} == {"b"} and len(und) == 0)
    # --- 修剪备份 ---
    _tmp_prune = {"a": {"published": "2020-01-01", "t": "x"},
                  "b": {"published": "2026-09-01"}, "c": {}}
    _bp = os.path.join(os.environ.get("TEMP", "/tmp"), "_prune_test_uafront.jsonl")
    ck("prune 超龄删除（含备份）",
       prune_archive(_tmp_prune, date(2026, 9, 19), 120, backup_path=_bp) == 1
       and _tmp_prune["b"]["published"] == "2026-09-01"
       and "2020-01-01" in open(_bp, encoding="utf-8").read())
    # --- 回填掉桶 → excluded ---
    arch2 = {"x": {"id": "x", "published": "2026-09-15", "date_unknown": False,
                   "title": "Chess tournament recap",
                   "summary": "The Kyiv Open concluded with an upset.",
                   "kind": "fact_attack", "buckets": ["donbas_front"], "cfg_ver": "OLD"},
             "y": {"id": "y", "published": "2026-09-16", "date_unknown": False,
                   "title": "Regional weather report",
                   "summary": "Rain expected across Kharkiv oblast.",
                   "kind": "fact_attack", "buckets": ["kharkiv_front"], "cfg_ver": "OLD"}}
    backfill_archive(cfg, eng, arch2)
    ck("回填掉桶(anchor_only)→excluded", arch2["x"]["kind"] == "excluded" and arch2["x"]["buckets"] == [])
    ck("回填掉桶(无锚定)→excluded", arch2["y"]["kind"] == "excluded" and arch2["y"]["buckets"] == [])
    win2, _ = window_filter(list(arch2.values()), date(2026, 9, 19), 30)
    ck("excluded 不进快照", len(win2) == 0)
    # --- 配置不变量 ---
    ck("战线八桶全二维", all(len(b["require_all"]) == 2 for b in cfg["buckets"].values()))
    ck("所有桶有标签", all(b.get("label") for b in cfg["buckets"].values()))
    ck("每启用源 tier 合法", all(s.get("tier") in ("T1", "T2", "T3")
       for s in cfg["sources"] if s.get("enabled")))
    ck("无启用 T4 源", all(s.get("tier") != "T4" for s in cfg["sources"] if s.get("enabled")))
    ck("停用源有留档", all(s.get("note") for s in cfg["sources"] if not s.get("enabled")))
    ck("baseline rows 字段完整",
       all(b.get("date") and b.get("event") and b.get("tier") in ("T1", "T2", "T3")
           and b.get("source") for b in cfg["baseline"].get("rows", [])
           + cfg["baseline"].get("air_rows", [])))
    ck("flags 键全部可编译", all(k in eng.flag_res for k in
       ("ru_claim", "ua_claim", "unverified", "encirclement_alert")))
    ck("TASS/俄源在场（官方立场窗口）", any(s["id"] == "tass" and s.get("enabled")
       for s in cfg["sources"]))
    # --- 核心结论能产出（防全绿但结论恒空）---
    ck("核心结论: 事件/表态/分析 三类都能产出",
       re1["kind"] == "fact_attack" and rs1["kind"] == "opinion_statement"
       and ra1["kind"] == "opinion_analysis")

    ok = sum(1 for _, c in P if c)
    for name, c in P:
        print(f"  {'✅' if c else '❌'} {name}")
    print(f"[selftest] {ok}/{len(P)} 通过")
    return 0 if ok == len(P) else 1

'''

io.open(SRC, "w", encoding="utf-8").write(src[:start] + NEW + src[end:])
print("自检函数已替换；断言 chr(8) 已过")
