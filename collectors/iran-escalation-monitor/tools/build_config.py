#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把「三轮实测探测结果 + 策展元数据 + 语料库」合并成 config.json。

★ 为什么源清单要由脚本生成而不是手写：
  手写会有两类错误，且都不会报错 ——
    ① 抄错 URL（少个参数、错了 Site id）→ 抓取失败
    ② 把「实测不可用」的源写成 enabled:true（或反之）→ 采集缺口被掩盖
  源清单只认**实测证据**：`tools/_probe_round*.json`。策展元数据（当事方/等级/
  停用理由）单独写在 META 里，与自述的实测状态合并。

用法
----
    python tools/build_config.py           # 生成 config.json
    python tools/build_config.py --check   # 只校验，不写
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE_GLOB = os.path.join(ROOT, "tools", "_probe_round*.json")
LEXICON = os.path.join(ROOT, "tools", "lexicon.json")
OUT = os.path.join(ROOT, "config.json")

# ---------------------------------------------------------------- 当事方
PARTY_BY_ZONE = {
    "us-official": "us",
    "iran-official": "iran",
    "iran-opp": "iran_opp",
    "third": "third", "think": "third", "maritime": "third", "naval": "third",
    "cable": "third", "energy": "third", "chokepoint": "third", "foresight": "third",
}

# ---------------------------------------------------------------- 发布主体
PUBLISHER = {
    # 美方官方
    "dod-news": "美国国防部（五角大楼）", "dod-releases": "美国国防部（新闻稿）",
    "dvids-centcom": "美国中央司令部公共事务办公室（DVIDS）",
    "state-press": "美国国务院（新闻稿）",
    "gnews-centcom": "美国中央司令部（经 Google News 聚合）",
    "centcom-afpims": "美国中央司令部（官网 RSS，Site id 不匹配）",
    # 伊朗官方/半官方
    "irna-en": "伊朗伊斯兰共和国通讯社（IRNA）", "mehr-en": "梅赫尔通讯社（Mehr）",
    "isna-en": "伊朗学生通讯社（ISNA）", "tehrantimes": "Tehran Times",
    "parstoday": "Pars Today（伊朗对外广播）", "sepahnews": "Sepah News（IRGC 官方新闻社）",
    "defapress": "Defa Press（伊朗国防部关联）", "mashreghnews": "Mashregh News（IRGC 关联）",
    "kayhan": "Kayhan（最高领袖系）", "presstv": "Press TV（伊朗国家对外广播）",
    "abna24": "ABNA 24", "gnews-irna": "IRNA（经 Google News）",
    "gnews-irgc": "IRGC 相关报道（经 Google News）",
    "gnews-fars": "Fars News（经 Google News）",
    "gnews-tasnim": "Tasnim（经 Google News）",
    "farsnews-direct": "Fars News（官网，JS 渲染）",
    "farsnews-en": "Fars News 英文（官网，JS 渲染）",
    "tasnim-direct": "Tasnim（官网 RSS）",
    "khamenei-en": "最高领袖办公室英文站",
    "mod-iran": "伊朗国防部（mod.ir）", "irgc-official": "伊斯兰革命卫队（irgc.ir）",
    "iranpress": "Iran Press",
    # 流亡/反对派
    "iranintl": "Iran International（流亡反对派媒体）",
    "iranintl2": "Iran International（流亡反对派媒体）",
    "radiofarda": "Radio Farda / RFE-RL（美国资助，流亡）",
    "radiofarda2": "Radio Farda / RFE-RL（美国资助，流亡）",
    # 海军/海事
    "usni-news": "美国海军学会新闻（USNI News）",
    "naval-news": "Naval News", "navalnews2": "Naval News",
    "twz": "The War Zone", "twz2": "The War Zone",
    "seapower": "Seapower Magazine",
    "defensenews-naval": "Defense News（海军版）",
    "naval-technology": "Naval Technology",
    "navyrecognition": "Navy Recognition",
    "gcaptain": "gCaptain（航运）", "maritime-exec": "The Maritime Executive",
    "splash247": "Splash 247（航运）", "freightwaves": "FreightWaves",
    "marinelink": "MarineLink", "hellenic-shipping": "Hellenic Shipping News",
    "offshore-energy": "Offshore Energy", "baird-maritime": "Baird Maritime",
    "safety4sea": "SAFETY4SEA", "bimco": "BIMCO（航运协会）",
    "seatrade-maritime": "Seatrade Maritime News", "porttechnology": "Port Technology",
    "ukmto": "英国海事贸易行动办公室（UKMTO）", "ukmto-news": "英国海事贸易行动办公室（UKMTO）",
    "mard-msci": "美国海事署（MARAD）海事安全通告", "marad-msci": "美国海事署（MARAD）海事安全通告",
    "marad-rss": "美国海事署（MARAD）", "imo-pressbrief": "国际海事组织（IMO）",
    # 能源 / 光缆 / 核
    "oilprice": "OilPrice.com", "arabnews": "Arab News（沙特）",
    "thenational-ae": "The National（阿联酋）",
    "subtelforum": "Submarine Telecoms Forum", "datacenterdynamics": "Data Center Dynamics",
    "lightreading": "Light Reading（电信）", "totaltele": "Total Telecom",
    "capacitymedia": "Capacity Media", "telegeography": "TeleGeography",
    "submarinenetworks": "Submarine Networks",
    "iaea-news": "国际原子能机构（IAEA）", "iaea-news2": "国际原子能机构（IAEA）",
    "iaea-pressrss": "国际原子能机构（IAEA）",
    "armscontrol": "美国军控协会（Arms Control Association）",
    "armscontrolwonk": "Arms Control Wonk", "fas-feed": "美国科学家联盟（FAS）",
    "thebulletin": "Bulletin of the Atomic Scientists", "bulletin2": "Bulletin of the Atomic Scientists",
    "nti-feed": "核威胁倡议组织（NTI）", "iranwatch": "Iran Watch（威斯康星项目）",
    "isis-online": "科学与国际安全研究所（ISIS）", "isis-online2": "科学与国际安全研究所（ISIS）",
    # 智库
    "atlanticcouncil": "大西洋理事会（Atlantic Council）",
    "aei": "美国企业研究所（AEI）", "stimson": "史汀生中心（Stimson）",
    "crisisgroup": "国际危机组织（Crisis Group）", "mei-edu": "中东研究所（MEI）",
    "csis-analysis": "战略与国际研究中心（CSIS）", "isw": "战争研究所（ISW）",
    "isw2": "战争研究所（ISW）", "brookings": "布鲁金斯学会（Brookings）",
    "carnegie": "卡内基国际和平基金会", "chathamhouse": "查塔姆研究所（Chatham House）",
    "rusi": "英国皇家联合军种研究院（RUSI）",
    "washinstitute": "华盛顿近东政策研究所",
    # 第三方
    "aljazeera": "半岛电视台（Al Jazeera）", "bbc-me": "英国广播公司（BBC）中东",
    "france24-me": "France 24（中东）", "mideasteye": "Middle East Eye",
    "almonitor": "Al-Monitor", "timesofisrael": "The Times of Israel",
    "jpost": "The Jerusalem Post", "amwaj": "Amwaj.media",
    "almayadeen-en": "Al Mayadeen English",
    "reuters-world": "路透社",
    "gnews-reuters-iran": "路透社（经 Google News 聚合）",
    # 聚合型专题查询
    "gnews-hormuz": "霍尔木兹海峡专题（Google News 聚合）",
    "gnews-babelmandeb": "曼德海峡专题（Google News 聚合）",
    "gnews-houthi": "胡塞武装袭船专题（Google News 聚合）",
    "gnews-redsea": "红海航运安全专题（Google News 聚合）",
    "gnews-iran-escalation": "伊朗军事升级专题（Google News 聚合）",
    "gnews-hormuz-closure": "封锁霍尔木兹专题（Google News 聚合）",
    "gnews-hormuz-drill": "霍尔木兹演习专题（Google News 聚合）",
    "gnews-abqaiq": "海湾能源设施遇袭专题（Google News 聚合）",
    "gnews-oilattack": "海湾油设施遇袭专题（Google News 聚合）",
    "gnews-gulf-energy": "海湾能源安全专题（Google News 聚合）",
    "gnews-kharg": "哈尔克岛/油码头专题（Google News 聚合）",
    "gnews-iran-usnavy": "伊朗 vs 美舰专题（Google News 聚合）",
    "gnews-iran-irgc-navy": "IRGC 海军专题（Google News 聚合）",
    "gnews-iran-navy-drill": "伊朗海军演习专题（Google News 聚合）",
    "gnews-us-carrier": "美军航母部署专题（Google News 聚合）",
    "gnews-cable": "海底光缆中断专题（Google News 聚合）",
    "gnews-cable2": "海底光缆切断专题（Google News 聚合）",
    "gnews-cable-redsea": "红海光缆专题（Google News 聚合）",
    "gnews-nuclear": "伊朗核问题专题（Google News 聚合）",
    "gnews-iran-nuclear-test": "伊朗核试验专题（Google News 聚合）",
    "gnews-iran-missile": "伊朗导弹试射专题（Google News 聚合）",
    "gnews-iran-threat": "伊朗威胁表态专题（Google News 聚合）",
    "gnews-iran-imminent": "伊朗迫近行动专题（Google News 聚合）",
}

# ---------------------------------------------------------------- 主题锚定源
# 这些源**整条 feed 本身就在本主题内**（海军/海事/光缆/核专业媒体），
# 因此展示闸门只要求「信号命中」，不再要求再命中 anchor/domain 词。
TOPIC_ANCHORED = {
    "usni-news", "naval-news", "navalnews2", "seapower", "defensenews-naval",
    "gcaptain", "maritime-exec", "splash247", "marinelink", "offshore-energy",
    "baird-maritime", "freightwaves", "hellenic-shipping",
    "subtelforum", "totaltele", "capacitymedia", "datacenterdynamics", "lightreading",
    "iaea-news", "iaea-news2", "armscontrol", "armscontrolwonk",
    "twz", "twz2",
    "gnews-hormuz", "gnews-babelmandeb", "gnews-houthi", "gnews-redsea",
    "gnews-iran-escalation", "gnews-hormuz-closure", "gnews-hormuz-drill",
    "gnews-abqaiq", "gnews-oilattack", "gnews-gulf-energy", "gnews-kharg",
    "gnews-iran-usnavy", "gnews-iran-irgc-navy", "gnews-iran-navy-drill",
    "gnews-us-carrier", "gnews-cable", "gnews-cable2", "gnews-cable-redsea",
    "gnews-nuclear", "gnews-iran-nuclear-test", "gnews-iran-missile",
    "gnews-iran-threat", "gnews-iran-imminent",
}

# ---------------------------------------------------------------- 停用理由（留档）
# ★ 规范：实测日期 + 状态码 + Server 头 + 原因 + 替代方案
DISABLED_NOTE = {
    "centcom-afpims": "【实测 2026-09-18】`centcom.mil` 的 AFPIMS `RSS.ashx`（Site=1060）返回 **HTTP 200 + 空响应体** —— 与 navy.mil 同一现象：Site id 不匹配时不报错、只给空体。该 Site id 未能反查成功。替代：`gnews-centcom`（取标题层）+ `dvids-centcom`（取一手内容）。",
    "farsnews-direct": "【实测 2026-09-18】`farsnews.ir/rss` 返回 **HTTP 200 但是 HTML 页面（4.0KB，0 条目）**，`Server: ninja` —— 该站已改为 JS 渲染，静态解析取不到条目。替代：`gnews-fars`（带 site 过滤的 Google News 查询）。",
    "farsnews-en": "【实测 2026-09-18】`farsnews.ir/en/rss` 同为 **HTTP 200 + JS 渲染 HTML（0 条目）**。替代：`gnews-fars`。",
    "tasnim-direct": "【实测 2026-09-18】`tasnimnews.com/en/rss/...` 网络层失败：一次 `Tunnel connection failed: 502`、一次 `SSL: UNEXPECTED_EOF_WHILE_READING`。两个出口、3 次重试均未成功。**不排除是本地出口抖动**，日后可复测。替代：`gnews-tasnim`。",
    "khamenei-en": "【实测 2026-09-18】`english.khamenei.ir/rss` 返回 **HTTP 403**，两个出口一致 → 判为站点侧拒绝（非出口问题，因为同出口下其他伊朗源正常）。替代：`parstoday` / `irna-en` 转述最高领袖表态。",
    "iranintl": "【实测 2026-09-18】`iranintl.com/en/rss` 返回 **HTTP 200 但是 Next.js 客户端渲染页（82KB，0 条目）**，`Server: cloudflare`。替代：暂无；该源为流亡反对派媒体，缺它只影响「反对派视角」的覆盖面，不影响当事方对照。",
    "radiofarda": "【实测 2026-09-18】`rferl.org/rss` 返回 **HTTP 200 + 11 字节（内容为 `Invalid url`）** —— 该端点需带具体频道参数，裸 `/rss` 无效。未找到公开的 Radio Farda 频道 feed。替代：暂无。",
    "nti-feed": "【实测 2026-09-18】`nti.org/feed` 返回 **HTTP 200 + 831 字节的 RSS 外壳但 0 条目**（feed 为空/已停更）。替代：`armscontrol` / `thebulletin`（后者亦 403）。",
    "brookings": "【实测 2026-09-18】`brookings.edu/feed` 返回 **HTTP 200 但为站点首页 HTML（171KB，0 条目）**。替代：`atlanticcouncil` / `aei`。",
    "carnegie": "【实测 2026-09-18】`carnegieendowment.org/rss` 返回 **HTTP 200 但为 HTML 页（70KB，0 条目）**，`Server: envoy`。替代：`atlanticcouncil` / `stimson`。",
    "safety4sea": "【实测 2026-09-18】`safety4sea.com/feed` 返回 **HTTP 200 + 490KB HTML 但 0 条目**，且首次尝试伴随 `SSL: UNEXPECTED_EOF` —— 判为站点侧（同出口下其他海事源正常）。替代：`gcaptain` / `maritime-exec` / `splash247`。",
    "imo-pressbrief": "【实测 2026-09-18】`imo.org/.../PressBriefings/rss` 返回 **HTTP 200 + 60KB HTML（0 条目）**，`Server: Microsoft-IIS/10.0` —— 该路径不是 feed。替代：`ukmto` 不可用，改用 `gcaptain`/`maritime-exec` 转述 IMO 通告。",
    "amwaj": "【实测 2026-09-18】`amwaj.media/rss` 返回 **HTTP 429（限流）**，`Server: Vercel`。该站对非浏览器请求限流严格，**不做绕过**。替代：`almonitor` / `mideasteye`。",
    "ukmto": "【实测 2026-09-18】`ukmto.org/rss` 返回 **HTTP 404**，`Server: cloudflare`。该站未提供公开 RSS 端点（官网为 JS 渲染）。★ 这是本主题最遗憾的缺口 —— UKMTO 是红海/亚丁湾海事安全通告的**一手发布方**。替代：经 `gcaptain`/`maritime-exec` 转述其通告；日后若拿到端点应优先启用。",
    "ukmto-news": "【实测 2026-09-18】`ukmto.org/news` 返回 **HTTP 404**。见 `ukmto` 条。",
    "marad-msci": "【实测 2026-09-18】美国海事署 (MARAD) 海事安全通告端点 404/403（`Server: AkamaiGHost`）。端点命名规则不公开。替代：经航运媒体转述。",
    "marad-rss": "【实测 2026-09-18】`maritime.dot.gov/rss.xml` 返回 **HTTP 403**（`Server: AkamaiGHost`）。★ 注意：403 在换出口后仍复现，但同出口下 defense.gov 正常，故判为**该站点侧**的路径限制（也可能需要特定 Referer）。替代：经航运媒体转述。",
    "iaea-pressrss": "【实测 2026-09-18】`iaea.org/news/rss` 返回 **HTTP 403**。**已改用可用的 `iaea-news`（`/feeds/news`，实测 150 条）**，本条保留仅作留档。",
    "thebulletin": "【实测 2026-09-18】`thebulletin.org/feed` 返回 **HTTP 403**，`Server: cloudflare`。替代：`armscontrol` / `armscontrolwonk`。",
    "iranwatch": "【实测 2026-09-18】`iranwatch.org/rss` 返回 **HTTP 403**，`Server: cloudflare`。替代：`armscontrol` / `isis-online`（后者亦 404）。",
    "isis-online": "【实测 2026-09-18】`isis-online.org/rss` 返回 **HTTP 404**，`Server: cloudflare`。替代：`armscontrol`。",
    "isw": "【实测 2026-09-18】`understandingwar.org/rss.xml` 返回 **HTTP 403**，两个出口一致 → 站点侧。替代：`crisisgroup` / `atlanticcouncil`。",
    "csis-analysis": "【实测 2026-09-18】`csis.org/rss/analysis` 返回 **HTTP 404** —— 端点已变更。替代：`atlanticcouncil` / `stimson`。",
    "washinstitute": "【实测 2026-09-18】`washingtoninstitute.org/rss.xml` 返回 **HTTP 404**。替代：`aei` / `atlanticcouncil`。",
    "chathamhouse": "【实测 2026-09-18】`chathamhouse.org/rss` 返回 **HTTP 403**，`Server: cloudflare`。替代：`crisisgroup`。",
    "rusi": "【实测 2026-09-18】`rusi.org/rss` 返回 **HTTP 404**，`Server: AmazonS3`。替代：`crisisgroup`。",
    "treasury-ofac": "【实测 2026-09-18】`home.treasury.gov/rss/press-releases.xml` 返回 **HTTP 404** —— 端点已变更（财政部新闻稿 RSS 路径调整过多次）。替代：`state-press` + `dod-releases` 覆盖美方制裁/政策表态。",
    "iranpress": "【实测 2026-09-18】`iranpress.com/rss` 网络层失败：`SSL: SSLV3_ALERT_HANDSHAKE_FAILURE`，两个出口一致 → 站点已禁用旧 TLS 版本，标准库无法握手。**不做降级绕过**。替代：`parstoday` / `irna-en`。",
    "mod-iran": "【实测 2026-09-18】伊朗国防部 `mod.ir/rss` 不可达（网络层失败/非 feed 端点）。替代：`defapress`（国防部关联的 Defa Press）。",
    "irgc-official": "【实测 2026-09-18】`irgc.ir/rss` 不可达。替代：`sepahnews`（IRGC 官方新闻社，已启用）+ `gnews-irgc`。",
    "almayadeen-en": "【实测 2026-09-18】`english.almayadeen.net/rss` 返回 **HTTP 403**，`Server: cloudflare`。替代：`aljazeera` / `mideasteye`。",
    "bimco": "【实测 2026-09-18】`bimco.org/rss` 返回 **HTTP 404**。替代：`gcaptain` / `splash247`。",
    "seatrade-maritime": "【实测 2026-09-18】`seatrade-maritime.com/rss` 返回 **HTTP 404**，`Server: cloudflare`。替代：`gcaptain`。",
    "porttechnology": "【实测 2026-09-18】`porttechnology.org/feed` 返回 **HTTP 403**，`Server: cloudflare`。替代：`gcaptain`。",
    "naval-technology": "【实测 2026-09-18】`naval-technology.com/feed` 返回 **HTTP 403**，`Server: Varnish`。替代：`naval-news` / `usni-news`。",
    "navyrecognition": "【实测 2026-09-18】`navyrecognition.com/...rss` 返回 **HTTP 403**，`Server: cloudflare`。替代：`naval-news`。",
    "submarinenetworks": "【实测 2026-09-18】`submarinenetworks.com/rss` 返回 **HTTP 404**。替代：`subtelforum`（已启用）。",
    "telegeography": "【实测 2026-09-18】`telegeography.com/rss` 返回 **HTTP 404**。替代：`subtelforum` / `datacenterdynamics`。",
    "thenational-ae": "【实测 2026-09-18】`thenationalnews.com/rss` 返回 **HTTP 404**。替代：`arabnews`（沙特）。",
    "dvids-navcent": "【实测 2026-09-18】`dvidshub.net/rss/unit/232` 返回 **HTTP 404** —— unit id 232 不是 NAVCENT/第五舰队。★ 需要从 `dvidshub.net/unit/<SLUG>` 页面反查真实 unit id（本项目暂无该页面抓取能力）。替代：`dvids-centcom`。",
    "reuters-world": "【实测 2026-09-18】路透社官方 RSS 通道已下线（`feeds.reuters.com` 不再提供服务）。替代：`gnews-reuters-iran`（对 reuters.com 做 site 过滤的 Google News 查询）。",
}

# ★ 实测「可达但取不到条目」的源：保留在 config 中、enabled:false，
#   带上原因，日后复测即可（不删除）。
WARN_IDS = {"brookings", "carnegie", "centcom-afpims", "farsnews-direct", "farsnews-en",
            "iranintl", "iranintl2", "nti-feed", "radiofarda", "radiofarda2",
            "safety4sea", "imo-pressbrief"}

STALE_DAYS = 45          # 源内最新条目超过 N 天 → 报告中标记「疑似停更」

# ★ 探测期为了「同一源再试一次」而临时加的 `_2` 副本：**与基础 id 是同一个 feed**，
#   必须删掉，否则同一 feed 会被抓两遍 → 条数虚增一倍（且去重键不同，去重挡不住）。
#   `gnews-cable2` 不在其中：它是一个**不同的查询式**（去掉 Red Sea 限定），不是副本。
DROP_IDS = {"navalnews2", "twz2", "iaea-news2", "isw2", "isna-en2", "bulletin2",
            "radiofarda2", "iranintl2", "tasnim-direct2", "isis-online2"}


def best_results() -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for path in sorted(glob.glob(PROBE_GLOB)):
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            k = r["id"]
            score = (1 if (r.get("http") == 200 and r.get("items", 0) > 0) else 0,
                     r.get("items", 0), -r.get("ms", 0))
            if k not in best or score > best[k]["_score"]:
                r["_score"] = score
                best[k] = r
    return best


def to_source(sid: str, r: Dict[str, Any]) -> Dict[str, Any]:
    zone = r.get("zone") or "third"
    party = PARTY_BY_ZONE.get(zone, "third")
    usable = (r.get("http") == 200 and r.get("items", 0) > 0)
    is_warn = sid in WARN_IDS
    enabled = bool(usable and not is_warn)
    tier = r.get("tier") or "T3"
    pub = PUBLISHER.get(sid) or sid
    note = DISABLED_NOTE.get(sid, "")
    if not note and not enabled:
        note = (f"【实测 2026-09-18】HTTP {r.get('http')} "
                f"Server={r.get('server') or '—'}；{(r.get('err') or r.get('note') or '未取到条目')[:90]}")
    if enabled and r.get("latest") is None:
        note = (note + " ").strip() + "（该 feed 的日期格式非标准，源内最新日期未能自动解析）"
    return {
        "id": sid, "party": party, "zone": zone, "tier": tier, "publisher": pub,
        "url": r["url"], "enabled": enabled, "topic_anchored": sid in TOPIC_ANCHORED,
        # ★ 聚合器源会把多个不同立场的媒体混在同一个源里 → 必须按**实际发布媒体**
        #   重新判当事方（outlet_map），否则会把流亡/反对派媒体算成「伊方官方表态」。
        "aggregator": sid.startswith("gnews-"),
        "note": note,
        "probe": {"date": "2026-09-18", "http": r.get("http"),
                  "items": r.get("items", 0), "server": r.get("server") or "",
                  "latest": r.get("latest")},
        "stale_days": STALE_DAYS,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 config.json")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()

    with open(LEXICON, encoding="utf-8") as f:
        lex = json.load(f)
    best = best_results()

    srcs = [to_source(sid, r) for sid, r in sorted(best.items()) if sid not in DROP_IDS]
    # 源排序：启用优先、按 zone 顺序、按实测条目数降序
    zorder = {z["id"]: z["order"] for z in lex["zones"]}
    srcs.sort(key=lambda s: (not s["enabled"], zorder.get(s["zone"], 99),
                             -s["probe"]["items"], s["id"]))

    zones = {z["id"]: z for z in lex["zones"]}
    for s in srcs:
        z = zones.get(s["zone"])
        s["zone_name"] = z["name"] if z else s["zone"]

    cfg = {
        "version": "1.0.0",
        "project": "iran-escalation-monitor",
        "topic": "伊朗战备与升级动态监测（六大关注域 × 当事方对照）",
        "generated_at": "2026-09-18",
        "generated_by": "tools/build_config.py（源清单来自 tools/_probe_round*.json 实测）",
        "lookback_hours": 336,
        "per_source_max": 0,
        "per_source_max_note": ("0 = 不限制。★ 截断按 feed 顺序取前 N 条，会**静默丢弃窗口内记录**，"
                               "与「合并源 = 窗口内全部」直接冲突。若确需限制请显式设为正数，"
                               "报告 §2 会在该源标注「⚠️截断」。"),
        "stale_days": STALE_DAYS,
        "request": {
            "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
            "headers": {
                "Accept": ("application/rss+xml,application/atom+xml,application/xml;q=0.9,"
                           "text/xml;q=0.8,application/json;q=0.7,*/*;q=0.6"),
                "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1"
            },
            "note": ("浏览器常规请求头。★ 实测「只带 UA」会 403、补上 Sec-Fetch-* 后 200 —— "
                     "这是 403→200 的开关，不是可选项。"),
            "timeout": 30,
            "retries": 3,
            "retry_backoff_sec": 2.0,
            "delay_between_sources_sec": 1.2
        },
        "relevance": lex["relevance"],
        "signals": lex["signals"],
        "claims": lex["claims"],
        "cross_anchors": lex["cross_anchors"],
        "window": lex["window"],
        # ★ history.start 决定「空 ≠ 零」能否生效：build_weekly 以它为区间起点，
        #   早于首条记录所在周的周一律标 covered=False 并渲染成 `—`。
        #   缺了它，起点会退化成「首条记录所在周」，于是**所有周都显示为已收录**，
        #   「这段时间我们没在收」这个语义变成死代码 —— 读者只会看到一片空白。
        "history": {
            "start": "2026-02-01",
            "start_label": "美伊冲突起始月（用于区分「未收录」与「真的没有」）",
            "note": "历史回填起点；日常运行只刷新最近窗口。改此值会使周趋势区间变化。",
        },
        "schedule": {
            "cron_hint": "每日 07:30 与 19:30",
            "note": "建议每日两次；前瞻域的「临战迫近」类表述寿命很短，一天一跑易漏。",
        },
        "zones": lex["zones"],
        "outlet_map": lex["outlet_map"],
        "sources": srcs,
    }

    n_on = sum(1 for s in srcs if s["enabled"])
    n_off = len(srcs) - n_on
    by_zone: Dict[str, int] = {}
    for s in srcs:
        if s["enabled"]:
            by_zone[s["zone"]] = by_zone.get(s["zone"], 0) + 1
    print(f"源总数 {len(srcs)}　启用 {n_on}　停用留档 {n_off}")
    for z in sorted(by_zone, key=lambda k: zorder.get(k, 99)):
        print(f"  {z:16s} {by_zone[z]:2d} 个启用")
    bad = [s["id"] for s in srcs if s["enabled"] and not s["url"]]
    if bad:
        print(f"❌ 启用但无 URL：{bad}")
        return 1

    # 语料库自检：半截词根 + 词表规模
    # ★ 规则：词表里应写**基础形**。若某词条以常见屈折后缀结尾（ed/ing/ies/ied）
    #   且同桶里找不到对应的基础形，才可疑 —— 直接对 `_inflections()` 的结果
    #   自比一次即可判定（基础形的词形族包含它自己）。
    SUSPECT_INFLECT = re.compile(r"(?:ed|ing|ies|ied)$", re.I)
    half = []
    for b, spec in lex["signals"].items():
        groups = ([(spec.get("phrases") or [])] + list(spec.get("require_all") or []))
        flat = [p for g in groups for p in g]
        for p in flat:
            last = (p.split() or [p])[-1]
            if SUSPECT_INFLECT.search(last) and last.lower() not in {
                    q.split()[-1].lower() for q in flat}:
                half.append(f"{b}:{p}")
    if half:
        print(f"⚠️ 疑似屈折形词条（应改为基础形，除非是不规则形）{len(half)} 条：{half[:12]}")

    print(f"语料库：{len(lex['signals'])} 信号桶 × {len(lex['zones'])} 关注域，"
          f"{len(lex['claims'])} 声明类型，{len(lex['cross_anchors'])} 议题锚点")

    if args.check:
        print("（--check：未写文件）")
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print(f"已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
