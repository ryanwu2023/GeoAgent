"""Build a standalone, source-backed HTML explaining GeoAgent's data lineage."""
from __future__ import annotations

import ast
import html
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit, unquote_plus


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "collectors" / "collectors.json"
DB = ROOT / "data" / "research.sqlite3"
OUTPUT = ROOT / "docs" / "GeoAgent数据源与处理链路说明.html"
KEYWORD_TRANSLATIONS = ROOT / "config" / "keyword-translations.json"
SEARCH_QUERY_TRANSLATIONS = ROOT / "config" / "search-query-translations.json"


# Business-facing scope for every registered collector.  This is deliberately
# separate from discovery keywords: it explains what the collector is meant to
# observe, while the keyword section below shows the exact submitted queries.
COLLECTOR_RESEARCH_SCOPE = {
    "asset-transmission-monitor": {
        "dimensions": ["美伊·金融战线", "俄乌·军事/内政/外交", "跨主题·资产传导"],
        "scope": "汇集影响权益、利率债、信用债、商品和外汇的事件线索，重点识别能源与运输、通胀与政策、增长与贸易、风险偏好、财政与产业需求等传导渠道，并保留成立条件、反证和下一观察项。",
    },
    "defense-spend-monitor": {
        "dimensions": ["美伊·美军战备"],
        "scope": "采集美国军费支出、国防合同、弹药与导弹采购、补库和扩产代理数据，观察中央司令部、五角大楼和国防部相关信息，为美军后勤补给能力和持续作战能力提供量化背景；合同与支出不自动归因于单一冲突。",
    },
    "diplomacy-monitor": {
        "dimensions": ["美伊·外交斡旋"],
        "scope": "跟踪巴基斯坦、卡塔尔、阿曼等协调方，美国和伊朗的会谈条件、备忘录执行意愿、特朗普政府的谈判意愿与约束，以及军事行动是否出现可验证降温。",
    },
    "eu-domestic-monitor": {
        "dimensions": ["俄乌·欧盟内政"],
        "scope": "跟踪法国、意大利等欧洲国家选举、政党民调和对乌援助立场，观察国内政治变化是否削弱欧盟制裁、军援、财政援助和长期支持能力。",
    },
    "eu-gas-storage-monitor": {
        "dimensions": ["俄乌·欧洲能源", "跨主题·资产传导"],
        "scope": "从固定官方渠道采集欧洲天然气库存率、库存量、注气量和取气量，评估俄乌冲突、供应扰动和季节性需求对欧洲能源安全、通胀及相关资产的压力。",
    },
    "eu-ukraine-flow-monitor": {
        "dimensions": ["俄乌·乌克兰后援", "俄乌·欧盟内政"],
        "scope": "采集欧盟与乌克兰贸易、谷物和能源产品流量，以及欧盟援乌工具的承诺与实际拨款，区分政策宣布、预算承诺和资金实际到位。",
    },
    "finance-front-monitor": {
        "dimensions": ["美伊·金融战线"],
        "scope": "采集霍尔木兹海峡与曼德海峡通行量、布伦特和WTI原油、美国战略石油储备、美债收益率、美股主要指数及货币政策预期等指标，用于检验冲突经能源、通胀、利率和风险偏好传导的市场表现。",
    },
    "iran-domestic-monitor": {
        "dimensions": ["美伊·伊朗内政"],
        "scope": "跟踪伊朗食品与总体物价、里亚尔汇率、青年就业、原油出口、汽油短缺、限电，以及文官政府与革命卫队权力关系、最高领袖公开活动和国内稳定性。",
    },
    "iran-escalation-monitor": {
        "dimensions": ["美伊·伊朗战备"],
        "scope": "跟踪伊朗军事行动和有序升级线索，包括对美军海上平台的行动、霍尔木兹与曼德海峡双线施压、对美国盟友关键能源设施的对等打击、海底光缆风险、核活动与武器试验相关信号；单方表态与已发生行动分开记录。",
    },
    "israel-monitor": {
        "dimensions": ["美伊·以色列动向"],
        "scope": "跟踪以色列政府和内塔尼亚胡动向、选举与国内政治，以及以色列在叙利亚、约旦河西岸、加沙和黎巴嫩的军事行动及其对美伊局势的外溢影响。",
    },
    "market-context-monitor": {
        "dimensions": ["跨主题·资产传导"],
        "scope": "补充高收益债与投资级债利差、盈亏平衡通胀、主要利率和汇率等市场背景序列，为美伊和俄乌事件提供同期市场验证；同步变化不单独视为冲突因果。",
    },
    "meeting-calendar-monitor": {
        "dimensions": ["美伊·外交斡旋", "俄乌·外交斡旋", "多边政治日程"],
        "scope": "跟踪相关领导人、外长、特使和关键协调方未来120天的公开会晤计划、日期精度、地点、参与人和确认状态；将多源报道归并为一项日程，并保留安排、确认、推迟、取消、否认、已举行和过期未证实等状态变化。计划地点不代表人物实时位置。",
    },
    "mideast-monitor": {
        "dimensions": ["美伊·中东各国"],
        "scope": "跟踪麦加共同防务协议及潜在新成员、沙特与胡塞武装战事、伊拉克亲伊朗武装对驻伊美军行动，以及海合会和周边国家的安全合作与冲突外溢。",
    },
    "naval-monitor": {
        "dimensions": ["美伊·美军战备"],
        "scope": "采集美国及相关国家海军舰艇、航母编队、两栖力量、公开部署和补给动态。母港、文章地点提及和可核验部署位置分别标记，不将离散报道拼成真实航迹。",
    },
    "opinion-monitor": {
        "dimensions": ["美伊·美国民意"],
        "scope": "跟踪路透/益普索、美联社、华盛顿邮报等民调中的参战与反战态度，关注塔克·卡尔森、乔·肯特等意见领袖，以及兰德·保罗、莉萨·穆尔科斯基、苏珊·柯林斯等涉及战争权力约束的政治动向。",
    },
    "readiness-monitor": {
        "dimensions": ["美伊·美军战备", "美伊·伊朗战备"],
        "scope": "对照美国中央司令部、五角大楼、国防部和伊朗革命卫队等主体的战备表态、威慑声明与行动证据，跟踪美军基地后勤能力、潜在打击方案以及镐山等关键核设施相关行动是否启动或收尾。",
    },
    "ru-diplomacy-monitor": {
        "dimensions": ["俄乌·外交斡旋"],
        "scope": "跟踪美国特使参与斡旋的意愿、俄罗斯与乌克兰谈判条件、欧盟和乌克兰对和谈的态度，以及是否回到安克雷奇框架或形成可验证的停火和执行安排。",
    },
    "russia-domestic-monitor": {
        "dimensions": ["俄乌·俄罗斯内政"],
        "scope": "跟踪俄罗斯国家杜马选举及政治变化、油品出口、财政赤字、军费开支、卢布汇率和劳动力短缺，评估国内财政与社会约束对持续作战能力的影响。",
    },
    "russia-financial-pressure-monitor": {
        "dimensions": ["俄乌·俄罗斯内政", "跨主题·资产传导"],
        "scope": "联合采集美元兑卢布、俄罗斯央行关键利率、布伦特原油和俄罗斯联邦预算执行数据，观察能源收入、汇率、利率与财政压力是否共同恶化或缓和。",
    },
    "stcn-usiran-monitor": {
        "dimensions": ["美伊·综合动态", "俄乌·综合动态", "跨主题·资产传导"],
        "scope": "从证券时报固定栏目补充过去半年及最新的美伊冲突、胡塞武装、霍尔木兹海峡、俄乌冲突和大类资产传导报道，提供中文财经媒体视角并保留原文链接与发布时间。",
    },
    "ua-front-monitor": {
        "dimensions": ["俄乌·军事战线"],
        "scope": "跟踪顿巴斯特别是顿涅茨克、哈尔科夫等方向的战线变化，无人机、导弹等远程打击，黑海与亚速海海上战线，以及乌克兰防空能力、设施损失和作战强度。",
    },
    "ukraine-aid-monitor": {
        "dimensions": ["俄乌·乌克兰后援"],
        "scope": "跟踪美国和欧洲对乌贷款、财政援助、武器援助、弹药与防空系统实际交付、训练支持和情报分享，区分宣布、批准、拨款与交付。",
    },
    "ukraine-external-sector-monitor": {
        "dimensions": ["俄乌·乌克兰后援", "跨主题·资产传导"],
        "scope": "从乌克兰国家银行固定接口采集经常账户、资本与金融账户、国际收支和外部融资数据，观察援助、贸易与资本流对乌克兰外部支付能力的支撑。",
    },
    "ukraine-grain-monitor": {
        "dimensions": ["俄乌·军事战线", "跨主题·资产传导"],
        "scope": "采集乌克兰小麦、玉米等粮食的产量、出口、消费和期末库存估计，观察战线、港口和黑海运输变化向全球粮价与贸易供给的传导。",
    },
    "ukraine-opinion-monitor": {
        "dimensions": ["俄乌·俄罗斯内政", "俄乌·外交斡旋"],
        "scope": "采集以俄罗斯受访者为主的俄乌相关民调，跟踪对军事行动、继续作战、谈判和停火的态度。样本对象、问题措辞和调查时间随每项观测保留，不能外推为全部公众意见。",
    },
    "xinhua-monitor": {
        "dimensions": ["美伊·八类维度", "俄乌·五类维度"],
        "scope": "从新华网固定国际新闻渠道补充美伊和俄乌最新进展，覆盖军事、外交、政策、能源运输、援助和内政。报道中的官方说法仍按声明处理，不自动升级为已核验事实。",
    },
    "taco-monitor": {
        "dimensions": ["美伊·金融战线", "跨主题·资产传导"],
        "scope": "采集并计算TACO资产压力指数所需的市场与政治因子，用于观察特朗普政策反复、风险资产和避险资产之间的综合压力变化。该指数是标准化合成观察工具，不等同于冲突概率，也不能单独解释资产涨跌。",
    },
}


RESEARCH_FRAMEWORK = [
    ("美伊局势", "美军战备", "美国军舰与公开部署、弹药库存代理、军费与采购、中央司令部/五角大楼/国防部表态及伊朗革命卫队交叉信息、美军基地后勤补给能力、镐山等关键设施行动进度。"),
    ("美伊局势", "伊朗战备", "伊朗有序升级路线、对美军海上平台行动、霍尔木兹与曼德海峡施压、美国盟友能源设施、海底光缆、核活动与武器试验等升级线索。"),
    ("美伊局势", "金融战线", "霍尔木兹与曼德海峡通行量、布伦特/WTI、美国战略石油储备、美债收益率、美股主要指数及货币政策预期。"),
    ("美伊局势", "美国民意", "参战与反战民调、意见领袖、战争权力约束议员及相关政治动向。"),
    ("美伊局势", "以色列动向", "以色列政府、选举与国内政治，以及叙利亚、约旦河西岸、加沙和黎巴嫩军事行动。"),
    ("美伊局势", "中东各国", "地区共同防务、沙特—胡塞战事、伊拉克亲伊朗武装与驻伊美军、海合会及周边安全合作。"),
    ("美伊局势", "伊朗内政", "食品与总体物价、里亚尔、青年就业、原油出口、燃料短缺、限电、文官政府与革命卫队权力结构、最高领袖权威。"),
    ("美伊局势", "外交斡旋", "巴基斯坦、卡塔尔、阿曼等协调方，特朗普政府谈判意愿与能力、军事降温和备忘录执行。"),
    ("俄乌冲突", "军事战线", "顿巴斯、顿涅茨克、哈尔科夫等方向，无人机与导弹远程打击，黑海/亚速海海上战线和乌克兰防空能力。"),
    ("俄乌冲突", "乌克兰后援", "美欧援乌贷款、实际拨款、武器与弹药交付、训练和美国情报分享。"),
    ("俄乌冲突", "俄罗斯内政", "国家杜马选举、油品出口、财政赤字、军费、卢布、劳动力短缺和国内民意。"),
    ("俄乌冲突", "欧盟内政", "法国、意大利等选举和政党立场变化，对欧盟援乌、制裁与持续支持能力的影响。"),
    ("俄乌冲突", "外交斡旋", "美国特使参与意愿、欧盟和乌克兰和谈态度、俄罗斯条件、安克雷奇框架及停火执行。"),
]

SPECIAL_SOURCES = {
    "russia-financial-pressure-monitor": [
        ("俄罗斯央行美元兑卢布", "https://www.cbr.ru/scripts/XML_dynamic.asp"),
        ("俄罗斯央行关键利率", "https://www.cbr.ru/hd_base/KeyRate/"),
        ("Brent", "https://finance.yahoo.com/quote/BZ%3DF/"),
        ("俄罗斯联邦预算执行", "https://roskazna.gov.ru/ispolnenie-byudzhetov/federalnyj-byudzhet/"),
    ],
    "eu-gas-storage-monitor": [("GIE AGSI", "https://agsi.gie.eu/")],
    "ukraine-grain-monitor": [("USDA PS&D", "https://apps.fas.usda.gov/psdonline/")],
    "ukraine-external-sector-monitor": [("乌克兰国家银行国际收支", "https://bank.gov.ua/NBUStatService/v1/statdirectory/balanceofpayments?json")],
    "eu-ukraine-flow-monitor": [
        ("Eurostat：欧盟自乌克兰进口", "https://ec.europa.eu/eurostat/databrowser/view/DS-045409/"),
        ("Eurostat：欧盟向乌克兰出口", "https://ec.europa.eu/eurostat/databrowser/view/DS-045409/"),
        ("Eurostat：欧盟自乌克兰进口谷物", "https://ec.europa.eu/eurostat/databrowser/view/DS-045409/"),
        ("Eurostat：欧盟向乌克兰出口能源产品", "https://ec.europa.eu/eurostat/databrowser/view/DS-045409/"),
        ("欧盟 Ukraine Facility", "https://commission.europa.eu/topics/eu-solidarity-ukraine/eu-assistance-ukraine/ukraine-facility_en"),
    ],
    "xinhua-monitor": [("新华网中文国际动态", "https://www.news.cn/world/jsxw/index.html")],
    "stcn-usiran-monitor": [("证券时报地缘冲突动态", "https://www.stcn.com/")],
}


def clean_text(value: object) -> str:
    return str(value or "").replace("基金经理", "用户").replace("研究员", "用户")


def safe_url(value: object) -> str:
    try:
        parsed = urlsplit(str(value or ""))
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    query = [
        (key, val)
        for key, vals in parse_qs(parsed.query, keep_blank_values=True).items()
        if key.lower() not in {"api_key", "apikey", "key", "token", "authorization"}
        for val in vals
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def latest_snapshot() -> dict:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT value FROM workspace_state WHERE key='working_snapshot_id'").fetchone()
    if row:
        sid = row[0]
    else:
        sid = con.execute("SELECT id FROM snapshots ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    snap = con.execute("SELECT payload,created_at FROM snapshots WHERE id=?", (sid,)).fetchone()
    con.close()
    payload = json.loads(snap["payload"])
    payload["id"], payload["created_at"] = sid, snap["created_at"]
    return payload


def source_config_map(collector_id: str) -> dict[str, dict]:
    path = ROOT / "collectors" / collector_id / "config.json"
    if not path.is_file():
        return {}
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        return {}
    result: dict[str, dict] = {}
    for item in config.get("sources", []):
        if not isinstance(item, dict):
            continue
        source_id = clean_text(item.get("id") or item.get("source_id") or item.get("name"))
        if not source_id:
            continue
        result[source_id] = {
            "name": clean_text(item.get("publisher") or item.get("name_zh") or item.get("name") or source_id),
            "url": safe_url(item.get("url") or item.get("endpoint") or item.get("feed_url")),
            "tier": clean_text(item.get("tier")),
        }
    return result


SEARCH_VARIABLE = re.compile(r"^(SEARCH_TERMS|SEARCH_QUERIES|QUERY_TERMS|DISCOVERY_TERMS)$", re.I)


def keyword_translations() -> dict[str, str]:
    result: dict[str, str] = {}
    # The broad cache is machine generated.  The smaller search-query file is
    # human reviewed and intentionally wins for currently submitted queries.
    for path in (KEYWORD_TRANSLATIONS, SEARCH_QUERY_TRANSLATIONS):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            continue
        result.update({str(key): clean_text(value) for key, value in payload.items() if clean_text(value)})
    return result


def search_rule(raw: str, origin: str, translations: dict[str, str]) -> dict:
    raw = clean_text(raw).strip()
    has_foreign = bool(re.search(r"[A-Za-z\u0400-\u04ff\u0600-\u06ff]", raw))
    translated = translations.get(raw, "") if has_foreign else raw
    time_match = re.search(r"\bwhen:(\d+)([hdwmy])\b", raw, flags=re.I)
    time_scope = ""
    if time_match:
        number, unit = time_match.groups()
        unit_zh = {"h": "小时", "d": "天", "w": "周", "m": "个月", "y": "年"}[unit.lower()]
        time_scope = f"最近 {number} {unit_zh}"
        # The translation model may transliterate the operator as “时间: 7d”.
        # Keep the exact operator in `raw` and explain its meaning separately.
        translated = re.sub(r"(?:when|时间|时)\s*[:：]\s*\d+\s*[hdwmy]\b", "", translated, flags=re.I).strip()
        translated = translated.replace('"', "").strip()
    return {
        "raw": raw,
        "origin": origin,
        "zh": translated,
        "time_scope": time_scope,
        "needs_translation": has_foreign and raw not in translations,
    }


def config_search_queries(collector_id: str, translations: dict[str, str]) -> list[dict]:
    """Return only values that are actually submitted to a search endpoint.

    Do not infer search terms from bucket, signal, anchor, include/exclude or label
    configuration.  Those values classify/filter fetched documents, but are not
    necessarily sent to any search service.
    """
    path = ROOT / "collectors" / collector_id / "config.json"
    if not path.is_file():
        return []
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        return []
    values: list[dict] = []

    # Exact q parameters used by Google News/site search sources.
    for source in config.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_name = clean_text(source.get("name_zh") or source.get("publisher") or source.get("name") or source.get("id") or "来源")
        try:
            query = parse_qs(urlsplit(str(source.get("url") or "")).query).get("q", [])
        except ValueError:
            query = []
        values.extend(search_rule(unquote_plus(value), f"{source_name}：网址 q 参数", translations) for value in query if value)

        # A small number of API sources construct requests from an explicit
        # `queries` list instead of embedding q in the configured URL.
        for item in source.get("queries", []):
            if isinstance(item, str):
                raw = item
            elif isinstance(item, dict):
                raw = item.get("term") or item.get("query") or item.get("q")
            else:
                raw = None
            if isinstance(raw, str) and raw.strip():
                values.append(search_rule(raw, f"{source_name}：queries 配置", translations))
    return list({value["raw"]: value for value in values}.values())


def code_search_queries(collector_id: str, entrypoint: str, translations: dict[str, str]) -> list[dict]:
    """Read explicitly named search-term constants from the active entrypoint."""
    path = (ROOT / "collectors" / collector_id / entrypoint).resolve()
    if not path.is_file() or ROOT not in path.parents:
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeError):
        return []
    values: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not names or not any(SEARCH_VARIABLE.fullmatch(name) for name in names):
            continue
        value = node.value
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        try:
            literal = ast.literal_eval(value)
        except (ValueError, TypeError):
            continue
        parts = literal if isinstance(literal, (list, tuple, set)) else [literal]
        variable = " / ".join(names)
        for part in parts:
            if isinstance(part, str) and 1 < len(part) <= 1000:
                values.append(search_rule(part, f"代码检索变量：{variable}", translations))
    return list({value["raw"]: value for value in values}.values())


def build_dataset(snapshot: dict) -> dict:
    registry_payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry = registry_payload["collectors"]
    missing_scope = sorted(item["id"] for item in registry if item["id"] not in COLLECTOR_RESEARCH_SCOPE)
    if missing_scope:
        raise ValueError("采集器缺少业务采集说明：" + "、".join(missing_scope))
    projects = {item["id"]: item for item in snapshot.get("projects", [])}
    source_rows = snapshot.get("sources", [])
    records = snapshot.get("records", [])
    record_lookup: dict[tuple[str, str], dict] = {}
    for record in records:
        for adapter in record.get("adapters", []):
            record_lookup.setdefault((adapter, str(record.get("source_id") or "")), record)
    crawlers = []
    translations = keyword_translations()
    all_sources = []
    for item in registry:
        cid = item["id"]
        configured = source_config_map(cid)
        matched_sources = [source for source in source_rows if source.get("monitor") == cid]
        normalized_sources = []
        for source_index, source in enumerate(matched_sources):
            sid = str(source.get("id") or "")
            local_id = sid[len(cid) + 1:] if sid.startswith(cid + ":") else sid
            config = configured.get(local_id, {})
            record = record_lookup.get((cid, local_id), {})
            special = (SPECIAL_SOURCES.get(cid) or [])
            special_name, special_url = special[source_index] if source_index < len(special) else ("", "")
            row = {
                "collector": cid,
                "id": local_id,
                "name": clean_text(config.get("name") or special_name or source.get("name") or local_id),
                "url": config.get("url") or special_url or safe_url(record.get("url")),
                "tier": clean_text(source.get("tier") or config.get("tier") or record.get("tier") or "未分级"),
                "state": clean_text(source.get("state") or "未记录"),
                "count": source.get("count", 0),
                "latest": clean_text(source.get("latest")),
                "error": clean_text(source.get("error")),
            }
            normalized_sources.append(row)
            all_sources.append(row)
        search_queries = config_search_queries(cid, translations) + code_search_queries(cid, item["entrypoint"], translations)
        search_queries = list({rule["raw"]: rule for rule in search_queries}.values())
        research_scope = COLLECTOR_RESEARCH_SCOPE.get(cid, {"dimensions": ["待补充"], "scope": "当前注册表尚未补充该采集器的业务采集说明。"})
        crawlers.append({
            "id": cid,
            "name": clean_text(item.get("name_zh") or cid),
            "topics": item.get("topics", []),
            "output_kind": item.get("output_kind", ""),
            "entrypoint": item.get("entrypoint", ""),
            "research_dimensions": research_scope["dimensions"],
            "collection_scope": research_scope["scope"],
            "records": projects.get(cid, {}).get("records", 0),
            "topic_records": projects.get(cid, {}).get("topic_records", 0),
            "source_count": len(normalized_sources),
            "sources": normalized_sources,
            "search_queries": search_queries,
        })
    cleaning = snapshot.get("cleaning", {})
    observations = snapshot.get("observations", [])
    return {
        "snapshot": {"id": snapshot["id"], "created_at": snapshot["created_at"]},
        "counts": {
            "crawlers": len(crawlers), "sources": len(source_rows), "raw": cleaning.get("raw_records", 0),
            "unique": len(records), "duplicates": cleaning.get("duplicates_merged", 0),
            "quarantined": cleaning.get("records_quarantined", 0), "observations": len(observations),
        },
        "crawlers": crawlers,
        "sources": all_sources,
    }


def render(dataset: dict) -> str:
    data_json = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    framework_rows = "".join(
        f"<tr><td>{html.escape(topic)}</td><td><b>{html.escape(dimension)}</b></td><td>{html.escape(scope)}</td></tr>"
        for topic, dimension, scope in RESEARCH_FRAMEWORK
    )
    collector_count = dataset["counts"]["crawlers"]
    registry_note = f"当前注册表实际启用{collector_count}个采集器，本页按注册表完整列出。"
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GeoAgent｜数据源与处理链路说明</title>
<style>
:root{{--navy:#0b2435;--navy2:#15384c;--paper:#f4f2ec;--sheet:#fffdf8;--ink:#18232d;--muted:#65727b;--line:#cfd5d3;--cyan:#52aab7;--amber:#c58b3c;--red:#b34c48;--green:#438267;--mono:Consolas,"Cascadia Code",monospace;--sans:"Microsoft YaHei","PingFang SC",Arial,sans-serif;--serif:"Noto Serif SC","Songti SC",SimSun,serif}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans)}}button,input,select{{font:inherit}}a{{color:inherit}}.layout{{display:grid;grid-template-columns:230px minmax(0,1fr)}}aside{{position:sticky;top:0;height:100vh;background:var(--navy);color:#d8e6e9;padding:30px 24px;z-index:5}}.brand{{font:26px var(--serif)}}.brand small{{display:block;margin-top:8px;color:#7fa2ae;font:9px var(--sans);letter-spacing:.14em}}nav{{display:grid;margin-top:30px}}nav a{{padding:10px;border-left:2px solid transparent;color:#a8bec5;text-decoration:none;font-size:12px}}nav a:hover{{border-color:var(--cyan);background:#143449;color:white}}.aside-note{{position:absolute;bottom:24px;left:24px;right:24px;border-top:1px solid #345264;padding-top:16px;font-size:10px;line-height:1.7;color:#91abb4}}main{{min-width:0}}header{{padding:70px clamp(30px,7vw,100px) 58px;background:var(--navy);color:white;position:relative;overflow:hidden}}header:after{{content:"";position:absolute;inset:0;background:radial-gradient(circle at 78% 25%,#52aab733,transparent 30%),repeating-linear-gradient(90deg,transparent 0 89px,#ffffff08 90px)}}header>*{{position:relative;z-index:1}}.kicker,.section-no{{font-size:11px;letter-spacing:.12em;color:#86b3bc}}h1{{max-width:1000px;margin:24px 0;font:600 clamp(38px,5.4vw,72px)/1.18 var(--serif)}}.lead{{max-width:900px;color:#bdd0d4;font-size:16px;line-height:1.9}}.stats{{display:grid;grid-template-columns:repeat(6,1fr);margin-top:38px;border-block:1px solid #3b596a}}.stats div{{padding:18px 16px 20px 0}}.stats b{{display:block;font:25px var(--serif)}}.stats span{{font-size:9px;color:#88a8b0}}section{{max-width:1280px;margin:auto;padding:78px clamp(26px,6vw,78px);border-bottom:1px solid var(--line)}}.section-head{{display:grid;grid-template-columns:190px 1fr;gap:36px;margin-bottom:38px}}h2{{font:600 clamp(27px,3vw,42px)/1.28 var(--serif);margin:0 0 14px}}.section-head p{{max-width:850px;color:var(--muted);font-size:13px;line-height:1.9;margin:0}}.flow{{display:grid;grid-template-columns:repeat(5,1fr);border:1px solid var(--line);background:var(--sheet)}}.flow article{{padding:24px;border-right:1px solid var(--line)}}.flow article:last-child{{border:0}}.flow b{{font:24px var(--serif);color:var(--cyan)}}.flow h3{{font-size:14px}}.flow p,.note{{font-size:11px;line-height:1.75;color:var(--muted)}}.overview-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.overview-grid article{{background:var(--sheet);border:1px solid var(--line);padding:22px}}.overview-grid h3{{font-size:15px;margin:0 0 8px}}.overview-grid p{{font-size:11px;line-height:1.75;color:var(--muted);margin:0}}.audience-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.audience-card{{background:var(--navy);color:white;padding:26px}}.audience-card h3{{font:23px var(--serif);margin:0 0 12px}}.audience-card p{{font-size:12px;line-height:1.8;color:#b9ccd1}}.audience-card ul{{margin:14px 0 0;padding-left:18px;color:#d6e1e3;font-size:11px;line-height:1.85}}.capability-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.capability-card{{position:relative;background:var(--sheet);border:1px solid var(--line);padding:22px;min-height:180px}}.capability-card h3{{margin:25px 0 9px;font:19px var(--serif)}}.capability-card p{{font-size:11px;line-height:1.75;color:var(--muted);margin:0}}.status-tag{{position:absolute;top:17px;left:22px;color:var(--green);font-size:9px;font-weight:bold;letter-spacing:.08em}}.workflow{{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}}.workflow article{{background:var(--sheet);padding:20px;min-height:160px}}.workflow b{{font:24px var(--serif);color:var(--cyan)}}.workflow h3{{font-size:13px;margin:12px 0 7px}}.workflow p{{font-size:10px;line-height:1.7;color:var(--muted)}}.deliverable-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.deliverable{{background:var(--sheet);border:1px solid var(--line);padding:22px}}.deliverable h3{{font:20px var(--serif);margin:0 0 9px}}.deliverable p{{font-size:11px;line-height:1.75;color:var(--muted)}}.deliverable ul{{padding-left:18px;margin:12px 0 0;font-size:10px;line-height:1.8}}.boundary-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.boundary-panel{{background:var(--sheet);border:1px solid var(--line);padding:24px}}.boundary-panel h3{{font:19px var(--serif);margin:0 0 12px}}.boundary-panel li{{font-size:11px;line-height:1.8;margin-bottom:5px}}.cta-row{{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px}}.cta{{display:inline-block;text-decoration:none;background:var(--cyan);color:#071b28;padding:11px 16px;font-size:11px;font-weight:bold}}.cta.secondary{{background:transparent;color:#d8e6e9;border:1px solid #527080}}.controls{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}}.controls input{{flex:1;min-width:240px;padding:12px 14px;border:1px solid var(--line);background:white}}.controls select{{padding:12px;border:1px solid var(--line);background:white}}.summary-line{{font-size:11px;color:var(--muted);margin-bottom:14px}}.framework-table{{margin:0 0 24px}}.framework-table table{{min-width:760px}}.framework-table td:nth-child(1){{width:100px;color:#1f7181;font-weight:bold}}.framework-table td:nth-child(2){{width:130px}}.registry-note{{margin:0 0 18px;border-left:4px solid var(--cyan);background:#eef7f7;padding:14px 18px;font-size:11px;line-height:1.7}}.crawler-list{{display:grid;gap:10px}}details.crawler{{background:var(--sheet);border:1px solid var(--line)}}details.crawler>summary{{list-style:none;cursor:pointer;padding:18px 20px;display:grid;grid-template-columns:minmax(200px,1.2fr) .7fr .5fr .5fr;gap:16px;align-items:center}}details.crawler>summary::-webkit-details-marker{{display:none}}.crawler-name b{{display:block;font-size:14px}}.crawler-name code{{font:9px var(--mono);color:var(--muted)}}.pill{{display:inline-block;border:1px solid var(--line);padding:4px 7px;margin:2px;font-size:9px;background:#f0f3ef}}.crawler-body{{padding:0 20px 20px}}.collector-purpose{{display:grid;grid-template-columns:minmax(180px,.55fr) 1.45fr;gap:16px;padding:16px 18px;margin:4px 0 2px;background:#edf3f1;border-left:3px solid var(--cyan)}}.collector-purpose b{{display:block;margin-bottom:7px;font-size:10px;color:#45606a}}.collector-purpose p{{margin:0;font-size:11px;line-height:1.75;color:#465760}}.meta{{display:flex;gap:18px;flex-wrap:wrap;padding:12px 0;font:10px var(--mono);color:var(--muted)}}.table-wrap{{overflow:auto;border:1px solid var(--line)}}table{{width:100%;border-collapse:collapse;min-width:820px;background:white}}th,td{{padding:10px 12px;border-bottom:1px solid #e2e5e3;text-align:left;font-size:10px;vertical-align:top}}th{{background:#e9edeb;color:#4d5c65;position:sticky;top:0}}td code{{font:9px var(--mono)}}.state-ok{{color:var(--green)}}.state-failed,.state-configuration_required{{color:var(--red)}}.source-link{{color:#1f7181;text-decoration:none}}.empty{{padding:25px;text-align:center;color:var(--muted);background:white}}.keyword-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.keyword-card{{background:var(--sheet);border-top:3px solid var(--navy);padding:20px}}.keyword-card h3{{font:20px var(--serif);margin:0 0 8px}}.keyword-card small{{color:var(--muted)}}.stage-summary{{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}}.stage-summary span,.term-stage{{border:1px solid var(--line);padding:3px 6px;font-size:9px;background:white}}.term-stage.stage-发现{{color:#1f7181;border-color:#76b7c1}}.term-stage.stage-准入{{color:#88601d;border-color:#d3a85d}}.term-stage.stage-排除{{color:var(--red);border-color:#d99b98}}.tokens{{max-height:290px;overflow:auto;margin-top:13px}}.term-row{{display:grid;grid-template-columns:52px 1fr;gap:7px;padding:8px;margin-bottom:5px;background:#edf0ec}}.term-copy{{min-width:0}}.term-original{{display:block;font:9px/1.55 var(--mono);overflow-wrap:anywhere}}.term-zh{{display:block;margin-top:3px;font-size:10px;line-height:1.55;color:#465760}}.term-origin{{display:block;margin-top:3px;color:#859097;font-size:8px}}.pipeline{{counter-reset:step;display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}}.pipeline article{{counter-increment:step;background:var(--sheet);padding:24px}}.pipeline article:before{{content:counter(step,decimal-leading-zero);font:25px var(--serif);color:var(--cyan)}}.pipeline h3{{font-size:14px;margin:12px 0 8px}}.pipeline p{{font-size:11px;line-height:1.75;color:var(--muted)}}.result-band{{margin-top:22px;background:var(--navy);color:white;padding:22px;display:flex;gap:28px;flex-wrap:wrap}}.result-band b{{font:24px var(--serif)}}.result-band span{{font-size:10px;color:#9fb8bf;display:block}}.models{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.model{{background:var(--sheet);border:1px solid var(--line);padding:24px}}.model h3{{font:21px var(--serif);margin:0 0 9px}}.fields{{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:16px}}.fields code{{font:9px var(--mono);padding:7px;background:#edf0ec}}.tag-table{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}}.tag-table article{{background:var(--sheet);padding:22px}}.tag-table h3{{font-size:13px;margin:0 0 8px}}.tag-table p{{font-size:10px;line-height:1.7;color:var(--muted);margin:0}}.tag-source{{border-top:3px solid var(--cyan)}}.tag-derived{{border-top:3px solid var(--amber)}}.tag-human{{border-top:3px solid var(--green)}}.outputs{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.outputs article{{background:var(--navy);color:#e7efef;padding:24px;min-height:150px}}.outputs h3{{font:19px var(--serif);margin:0 0 10px}}.outputs p{{font-size:11px;line-height:1.75;color:#abc0c6}}.boundary{{border-left:4px solid var(--amber);padding:20px 24px;background:#fffaf0;font-size:12px;line-height:1.85}}footer{{background:#071b28;color:#91aab2;padding:35px clamp(26px,6vw,78px);font-size:10px}}.print{{position:fixed;right:22px;bottom:22px;border:0;background:var(--cyan);color:#071b28;padding:12px 16px;font-weight:bold;cursor:pointer;z-index:9}}
@media(max-width:980px){{.layout{{grid-template-columns:1fr}}aside{{position:relative;height:auto;overflow:hidden}}.aside-note{{display:none}}nav{{display:flex;overflow:auto;scrollbar-width:none}}nav::-webkit-scrollbar{{display:none}}nav a{{white-space:nowrap;flex:0 0 auto}}.stats{{grid-template-columns:repeat(3,1fr)}}.section-head{{grid-template-columns:1fr}}.flow{{grid-template-columns:repeat(2,1fr)}}.keyword-grid,.models,.overview-grid,.capability-grid,.audience-grid,.deliverable-grid,.boundary-grid{{grid-template-columns:1fr}}}}
@media(max-width:620px){{header{{padding:48px 22px}}.stats,.flow,.pipeline,.tag-table,.outputs,.workflow{{grid-template-columns:1fr}}section{{padding:55px 22px}}details.crawler>summary{{grid-template-columns:1fr 1fr}}.crawler-name{{grid-column:1/-1}}.collector-purpose{{grid-template-columns:1fr}}.fields{{grid-template-columns:1fr}}.print{{position:static;margin:18px}}}}
@media print{{aside,.print,.controls{{display:none}}.layout{{display:block}}details.crawler:not([open]) .crawler-body{{display:block}}section{{break-inside:avoid;padding:36px 20px}}header{{background:white;color:black;padding:35px 20px}}header .lead{{color:#444}}}}
</style></head><body><div class="layout"><aside><div class="brand">GeoAgent<small>数据源与处理链路说明</small></div><nav><a href="#overview">整体概述</a><a href="#value">适用用户</a><a href="#capabilities">已实现功能</a><a href="#workflow">使用流程</a><a href="#deliverables">可以获得什么</a><a href="#boundaries">边界与反馈</a><a href="#pipeline-overview">数据全链路</a><a href="#crawlers">全部爬虫</a><a href="#keywords">检索关键词</a><a href="#cleaning">清洗过程</a><a href="#models">最终记录</a><a href="#tags">标签体系</a><a href="#next">后续优化</a></nav><div class="aside-note">页面由项目注册表、爬虫配置和当前冻结快照生成。<br>快照 {dataset['snapshot']['id'][:10]} · {html.escape(dataset['snapshot']['created_at'][:19])}</div></aside><main>
<header><div class="kicker">地缘动态 · 证据核验 · 情景判断 · 资产传导</div><h1>把美伊与俄乌局势，转化为可追溯的研究判断</h1><p class="lead">GeoAgent 面向研究员和基金经理，将新闻、官方信息、研究资料与市场指标组织成一套持续更新的工作台。它帮助用户发现变化、核查依据、形成情景、理解资产传导，并通过智能问答和研究简报输出可复用的分析结果。</p><div class="cta-row"><a class="cta" href="http://127.0.0.1:8000/" target="_blank" rel="noreferrer">打开 GeoAgent 工作台 ↗</a><a class="cta secondary" href="#capabilities">查看已实现功能</a></div><div class="stats"><div><b>{dataset['counts']['crawlers']}</b><span>注册采集器</span></div><div><b>{dataset['counts']['sources']}</b><span>来源状态记录</span></div><div><b>{dataset['counts']['raw']:,}</b><span>本轮原始记录</span></div><div><b>{dataset['counts']['unique']:,}</b><span>去重后记录</span></div><div><b>{dataset['counts']['observations']}</b><span>量化观测</span></div><div><b>{dataset['counts']['quarantined']}</b><span>主题污染隔离</span></div></div></header>
<section id="overview"><div class="section-head"><div class="section-no">01 / 整体概述</div><div><h2>持续跟踪美伊局势与俄乌冲突，把分散资料组织成可查询、可追溯的研究底座</h2><p>GeoAgent 从新闻、官方数据、市场数据和补充资料中持续采集两大主题的动态，经过相关性筛选、主题分类、去重、中文化与证据标记后，统一进入风险监测、量化指标、情景与资产分析、智能问答和简报生成。</p></div></div><div class="overview-grid"><article><h3>动态资讯采集</h3><p>固定抓取美伊、俄乌相关的军事行动、外交进展、政策变化、能源与运输、援助及内部政治信息，并保留原文链接、发布时间和采集时间。</p></article><article><h3>分类与风险展示</h3><p>按主题、维度、事件类别和地点组织新闻，自动形成升级、持续、缓和或待研判线索，同时允许用户复核和调整。</p></article><article><h3>定量数据监测</h3><p>采集市场价格、利率、通胀、能源库存、航运、财政、援助和其他可量化指标，展示历史走势、变化幅度、单位、口径与来源。</p></article><article><h3>情景与资产关联</h3><p>结合新闻变化和量化指标，整理基准情景、尾部风险、触发条件、反证以及原油、黄金、利率债、信用、权益和外汇的传导逻辑。</p></article><article><h3>智能问答</h3><p>用户可直接询问近期进展、主要变化、判断依据、反向证据和资产影响；模型根据原始问题与当前快照中的检索证据生成中文回答。</p></article><article><h3>简报与报告生成</h3><p>从同一数据快照生成美伊与俄乌简报，包含核心判断、主要事项、后续走势、资产含义和引用；后续将进一步结合业务理解生成专题报告。</p></article></div></section>
<section id="value"><div class="section-head"><div class="section-no">02 / 适用用户</div><div><h2>同一套证据底座，支持不同深度的研究与决策</h2><p>系统不代替用户做最终判断。它减少重复检索和资料整理，把结论、反证、数据缺口和后续触发条件放在同一页面，方便研究讨论和投资决策复核。</p></div></div><div class="audience-grid"><article class="audience-card"><h3>研究员如何使用</h3><p>围绕具体主题建立持续更新的研究台账，从新变化回到原始材料，再形成可引用的观点。</p><ul><li>筛选重要动态，比较不同来源和当事方说法</li><li>查看新闻发布时间、采集时间、来源等级和核验状态</li><li>把定性事件与量化指标放在同一时间线上</li><li>确认或调整升级、持续、缓和和待研判分类</li><li>生成简报并保留版本，复盘结论为何改变</li></ul></article><article class="audience-card"><h3>基金经理如何使用</h3><p>从当前局势直接进入资产含义，快速识别需要关注的风险渠道和重新评估条件。</p><ul><li>先读双主题概况、核心变化和一句话判断</li><li>查看原油、黄金、美债、信用、权益和外汇的条件影响</li><li>理解事件如何通过供需、通胀、增长和风险偏好传导</li><li>同时查看反证、数据缺口和下一观察项</li><li>通过问答追问某项判断的依据和不确定性</li></ul></article></div></section>
<section id="capabilities"><div class="section-head"><div class="section-no">03 / 已实现功能</div><div><h2>当前项目已经可以完成“变化—证据—情景—资产—报告”的完整链路</h2><p>下面只列出代码和当前页面中已经实现的能力。需要模型的功能在模型可用时生成总结；模型不可用时保留本地检索、程序计算和规则结果，并明确显示降级状态。</p></div></div><div class="capability-grid"><article class="capability-card"><span class="status-tag">已实现</span><h3>双主题风险监测</h3><p>分别查看美伊局势和俄乌冲突。二维地图按军事、外交、政策、能源、援助和内政分类，并区分升级、持续、缓和和待研判线索；地点提及与明确位置分开表达。</p></article><article class="capability-card"><span class="status-tag">已实现</span><h3>新闻详情与用户复核</h3><p>右侧详情展示中文阅读层、原始来源、发布时间、采集时间和核验状态。用户可以确认或调整态势分类，填写判断依据并保留修改版本。</p></article><article class="capability-card"><span class="status-tag">已实现</span><h3>主题化量化指标</h3><p>美伊和俄乌使用各自相关的指标集合，支持经济、民调、军事和动态分类搜索；走势图展示坐标、悬停数值、前值、变化幅度、单位、口径、来源与时间。</p></article><article class="capability-card"><span class="status-tag">已实现</span><h3>情景推演</h3><p>围绕持续消耗、升级扩散和缓和收束整理成立条件、支持证据、反证、失效条件和下一观察项。进入页面或数据变化后，可以基于当前筛选范围请求模型重新总结。</p></article><article class="capability-card"><span class="status-tag">已实现</span><h3>大类资产关联</h3><p>覆盖权益、利率债、信用债、商品和外汇，展示条件方向、传导机制、影响期限、反证、市场验证与下一触发条件；市场同步变化不会被直接写成冲突因果。</p></article><article class="capability-card"><span class="status-tag">已实现</span><h3>智能问答</h3><p>根据用户原始问题检索当前快照的新闻和指标，再由模型生成完整中文回答。回答区分事实、程序计算和模型推论，并校验引用 ID 与新增数字。</p></article><article class="capability-card"><span class="status-tag">已实现</span><h3>可配置研究简报</h3><p>支持核心判断、主要事项、基准情景、尾部风险、资产影响和信源结构；可调整模板、生成双主题总结、保存版本、比较差异并导出 Markdown。</p></article><article class="capability-card"><span class="status-tag">已实现</span><h3>采集运行与数据源管理</h3><p>统一管理当前注册表中的全部采集器，查看运行进度、日志、来源状态和导入结果；新数据经过门禁后生成冻结快照。外部 MCP 新闻服务支持配置、连通测试、检索和导入。</p></article><article class="capability-card"><span class="status-tag">已实现</span><h3>模型、PiChat 与 MCP 接入</h3><p>支持 PiChat Agent 或兼容 Chat Completions 的模型接口；项目自身提供只读 MCP 工具和工作台，可向可信 Agent 提供快照、证据、指标、地图和简报能力。</p></article></div></section>
<section id="workflow"><div class="section-head"><div class="section-no">04 / 典型使用流程</div><div><h2>从一个研究问题出发，而不是从阅读全部新闻开始</h2><p>用户可以先查看系统筛选出的变化，再逐层深入证据和资产传导。任何阶段都能回到原始来源，检查系统为何得出当前结果。</p></div></div><div class="workflow"><article><b>01</b><h3>选择主题</h3><p>进入美伊或俄乌工作台，设置时间和研究维度。</p></article><article><b>02</b><h3>发现变化</h3><p>查看风险地图、重点动态和变化明显的量化指标。</p></article><article><b>03</b><h3>核查证据</h3><p>打开新闻详情，检查原文、来源、发布时间和核验状态。</p></article><article><b>04</b><h3>形成判断</h3><p>比较支持证据与反证，确认分类并查看情景成立条件。</p></article><article><b>05</b><h3>理解资产</h3><p>沿供需、通胀、增长、流动性和财政渠道查看资产影响。</p></article><article><b>06</b><h3>问答与输出</h3><p>追问判断依据，生成简报并保留引用和版本。</p></article></div></section>
<section id="deliverables"><div class="section-head"><div class="section-no">05 / 可以获得什么</div><div><h2>页面输出围绕“结论能否被理解、追问和复核”组织</h2><p>同一冻结快照供风险监测、指标、情景、资产、问答和简报共同使用，避免不同页面引用不同批次的数据。</p></div></div><div class="deliverable-grid"><article class="deliverable"><h3>每日研究简报</h3><p>面向晨会、主题跟踪和投资讨论的可阅读结果。</p><ul><li>二至三句话的核心判断</li><li>按当事方或地区整理的主要事项</li><li>基准情景、尾部风险、触发条件和反证</li><li>大类资产影响及传导逻辑</li><li>可打开的证据与信源</li></ul></article><article class="deliverable"><h3>大类资产影响矩阵</h3><p>把地缘事件与资产之间的中间传导环节写清楚。</p><ul><li>新增驱动与判断变化</li><li>条件方向和影响期限</li><li>供需、通胀、增长、风险偏好与财政渠道</li><li>市场验证、反证和失效条件</li><li>下一次需要重新评估的触发点</li></ul></article><article class="deliverable"><h3>有引用的智能回答</h3><p>直接回答局势进展、主要变化、判断和不确定性。</p><ul><li>使用用户原始问题，而不是固定检索模板</li><li>结合新闻证据和结构化指标</li><li>数字来自程序查询和计算</li><li>引用可以返回原始记录和外部链接</li><li>模型失败时仍保留本地证据结果</li></ul></article><article class="deliverable"><h3>可复核的数据台账</h3><p>为后续研究复盘保留当时使用的数据和判断过程。</p><ul><li>不可变快照和原始文件归档</li><li>新闻与量化观测分开保存</li><li>用户复核记录和简报版本</li><li>来源健康、采集日志与数据缺口</li><li>翻译缓存不修改原始证据</li></ul></article></div></section>
<section id="boundaries"><div class="section-head"><div class="section-no">06 / 能力边界与反馈</div><div><h2>系统提供研究辅助，最终判断仍需要用户确认</h2><p>页面把证据、推论和不确定性同时展示。试用阶段最有价值的反馈，是指出哪些信息真正改变判断、哪些指标没有决策意义，以及什么条件会促使用户重新评估资产。</p></div></div><div class="boundary-grid"><article class="boundary-panel"><h3>当前边界</h3><ul><li>关键词命中只代表候选线索，不证明事件真实发生。</li><li>来源等级反映来源类型，不等于每项陈述已经核验。</li><li>机器译文可能存在专名和语义偏差，原始链接始终保留。</li><li>情景是研究假设，不输出缺乏依据的事件概率。</li><li>资产方向是条件判断，不提供价格目标、收益预测或交易指令。</li><li>部分官方接口需要密钥，缺失时明确显示配置或数据缺口。</li></ul></article><article class="boundary-panel"><h3>希望研究员和基金经理重点反馈</h3><ul><li>当前十三类研究维度是否覆盖实际跟踪框架？</li><li>哪些新闻分类、来源等级或态势标签需要调整？</li><li>哪些量化指标能够改变判断，哪些指标应删除或替换？</li><li>情景的触发条件、反证和观察项是否足够具体？</li><li>资产传导链是否符合实际决策过程，缺少哪些市场数据？</li><li>简报是否适合晨会和投资讨论，还需要哪些格式与长度？</li></ul></article></div></section>
<section id="pipeline-overview"><div class="section-head"><div class="section-no">07 / 数据全链路</div><div><h2>采集、筛选、标准化、打标、冻结</h2><p>系统不直接把爬虫输出当成结论。新闻和数字先进入各自采集器，再经过共同的数据契约与质量门禁，最后以不可变快照供页面、简报、情景分析和对话使用。</p></div></div><div class="flow"><article><b>01</b><h3>来源发现</h3><p>固定 RSS、官方 API、站内列表、站内搜索、Google News 查询和外部 MCP。</p></article><article><b>02</b><h3>爬虫内筛选</h3><p>各采集器使用主题锚点、领域词、实体词和排除词，保留候选材料。</p></article><article><b>03</b><h3>统一适配</h3><p>旧 JSONL 与 geo.v6 输出统一成新闻记录和量化观测两类对象。</p></article><article><b>04</b><h3>平台清洗</h3><p>规范链接、去重合并、时间隔离、主题污染清理、字段校验和原始归档。</p></article><article><b>05</b><h3>标签与使用</h3><p>生成主题、维度、事件类别、态势线索、地点精度与翻译状态，再进入各功能页面。</p></article></div></section>
<section id="crawlers"><div class="section-head"><div class="section-no">08 / 全部爬虫与来源</div><div><h2>{collector_count} 个采集器：研究框架、采集内容与来源明细</h2><p>先按美伊八类、俄乌五类研究维度说明业务观察范围，再逐项展开当前注册表中的每个采集器。每项均展示主题、对应维度、重点采集内容、输出类型、启动文件和当前来源状态。</p></div></div><div class="registry-note">{html.escape(registry_note)}</div><div class="table-wrap framework-table"><table><thead><tr><th>主题</th><th>研究维度</th><th>重点观察内容</th></tr></thead><tbody>{framework_rows}</tbody></table></div><div class="controls"><input id="source-search" type="search" placeholder="搜索采集器、来源、域名或来源 ID"><select id="topic-filter"><option value="all">全部主题</option><option value="usiran">美伊局势</option><option value="ukraine">俄乌冲突</option></select></div><div class="summary-line" id="source-summary"></div><div class="crawler-list" id="crawler-list"></div></section>
<section id="keywords"><div class="section-head"><div class="section-no">09 / 当前检索关键词</div><div><h2>展示当前启用爬虫实际提交的搜索词</h2><p>本节只列出爬虫真正发送给 Google News、站内搜索或文档检索接口的查询表达式，数据直接来自当前启用采集器的来源网址参数、queries 配置和明确的代码检索变量。分类桶、事件标签、信号词、说明文字及清洗规则不再当作搜索关键词展示。</p></div></div><div class="boundary"><b>口径说明：</b>Google News 在这里是候选新闻的检索入口，不是所有文章的最终发布方；系统仍为每条记录保存原发布媒体、原文链接、发布时间和采集时间，并按来源等级与交叉证据使用。固定 RSS、官方 API、行情接口和固定网页列表无需提交关键词，因此会明确显示“固定来源，不使用搜索关键词”。Google News 查询中的 <code>when:7d</code> 表示只检索最近 7 天发布的内容，<code>when:14d</code>、<code>when:30d</code> 同理；它不是对事件未来发生时间的判断。搜索命中只代表发现候选内容，不证明事件真实发生。</div><div class="controls" style="margin-top:18px"><input id="term-search" type="search" placeholder="搜索爬虫或实际查询内容，例如 Hormuz、Ukraine、停火"><select id="term-topic"><option value="all">全部主题</option><option value="usiran">美伊局势</option><option value="ukraine">俄乌冲突</option></select></div><div class="keyword-grid" id="keyword-grid"></div></section>
<section id="cleaning"><div class="section-head"><div class="section-no">10 / 清洗过程</div><div><h2>原始材料保留，分析资格可以被移除</h2><p>清洗不会删除证据原文。记录若与主题无关，会移除主题和维度标签并改为背景资料，同时保留排除原因；这样既避免污染分析，也能解释某条材料为什么没有进入主题页面。</p></div></div><div class="pipeline"><article><h3>原始文件归档</h3><p>每个读取文件计算内容哈希并复制到 archive，快照记录文件名、哈希和归档名。</p></article><article><h3>链接规范化</h3><p>仅接受 HTTP/HTTPS；删除片段、跟踪参数和可能包含密钥的查询参数。</p></article><article><h3>稳定 ID 与去重</h3><p>按规范链接或来源 ID、标题、发布时间生成稳定 ID；重复项合并来源适配器、原始引用、主题候选和更完整正文。</p></article><article><h3>时间质量</h3><p>区分事件、发布、采集和首次发现时间。发布时间晚于导入时间两天以上时隔离该时间，不参与时效排序。</p></article><article><h3>主题污染清理</h3><p>体育、公共卫生、自然灾害、文化旅游，以及与俄乌无直接关系的一般贸易会被移出主题；若标题含明确安全行动词则保留。</p></article><article><h3>主题与维度校验</h3><p>美伊八类、俄乌五类维度均需在目录内；geo.v6 显式绑定若使用未知主题或维度，整条输入拒绝。</p></article><article><h3>量化指标校验</h3><p>单位、频率、范围、方法、边界和来源引用必填；日期不可重复，数值只能是有限数或空值，缺失不填零。</p></article><article><h3>地点质量</h3><p>坐标必须标注 explicit、area 或 mention，并携带证据文字；文章地名不能表示实时部署。</p></article><article><h3>中文化与缓存</h3><p>中文原文直接使用；其他语言写入持久翻译缓存，并检查数字保持、残留外文和术语一致性。原文始终不变。</p></article></div><div class="result-band"><div><b>{dataset['counts']['raw']:,}</b><span>原始记录</span></div><div><b>− {dataset['counts']['duplicates']:,}</b><span>重复记录合并</span></div><div><b>− {dataset['counts']['quarantined']}</b><span>主题资格移除</span></div><div><b>= {dataset['counts']['unique']:,}</b><span>冻结快照记录</span></div></div></section>
<section id="models"><div class="section-head"><div class="section-no">11 / 最终记录</div><div><h2>新闻内容和结构化指标分开保存，通过来源 ID 关联</h2><p>最终快照不是一张扁平表。新闻与事件保存文本、证据和标签；量化观测保存时间序列、单位与口径。来源状态、原始文件清单和快照版本提供追溯链。</p></div></div><div class="models"><article class="model"><h3>新闻 / 事件记录 record</h3><p class="note">保存标题、正文或摘要、原始链接及其分析资格。它可以是媒体报道、官方通报、研究材料或 MCP 补充。</p><div class="fields"><code>id · 稳定记录ID</code><code>title / text · 原文</code><code>title_zh / summary_zh · 译文</code><code>url · 原始链接</code><code>source_id / publisher</code><code>published_at / fetched_at</code><code>topics / dimensions</code><code>tier / nature / verification</code><code>classification / scope</code><code>points · 地点与精度</code><code>raw_refs · 原始归档</code><code>upstream_extractions · 上游提取</code></div></article><article class="model"><h3>量化观测 observation</h3><p class="note">保存结构化时间序列。数字不由模型补写，页面的当前值、前值、变化额和变化率由程序计算。</p><div class="fields"><code>id / series / name</code><code>unit / frequency</code><code>topic / dimension / bucket</code><code>scope · 统计范围</code><code>method · 计算与来源口径</code><code>boundary · 解释边界</code><code>history[date,value]</code><code>source_ids · 证据记录</code><code>observed_at / as_of</code><code>quality_issue / upstream_state</code></div></article><article class="model"><h3>来源状态 source</h3><p class="note">说明本轮是否连接成功、获得多少记录、最后更新时间和失败原因。失败、无新增、配置缺失和停更是不同状态。</p><div class="fields"><code>id / monitor / name</code><code>state / http_status</code><code>count / latest</code><code>checked_at / error</code><code>tier</code></div></article><article class="model"><h3>快照与原始清单 snapshot</h3><p class="note">同一快照供地图、指标、情景、资产、简报和对话共同使用，避免页面之间引用不同批次的数据。</p><div class="fields"><code>snapshot id · 内容哈希</code><code>created_at</code><code>manifest[file,hash,archive]</code><code>cleaning · 清洗统计</code><code>import_stats · 新增与修订</code><code>errors / projects</code></div></article></div></section>
<section id="tags"><div class="section-head"><div class="section-no">12 / 标签体系</div><div><h2>是的，新闻与结构化数据均有标记，而且标记分层</h2><p>不能把自动标签和人工确认混为一谈。平台保留上游标签，生成用于展示的派生标签，并允许用户对态势线索做版本化复核。</p></div></div><div class="tag-table"><article class="tag-source"><h3>来源自带标签</h3><p><b>topics / dimensions</b>：采集器显式绑定的主题和维度。<br><b>crawler_buckets / flags / stance</b>：上游命中的词桶、实体、立场和信号。<br><b>tier</b>：T1官方、T2专业媒体、T3聚合或一般来源、未分级。</p></article><article class="tag-derived"><h3>平台派生标签</h3><p><b>classification / scope</b>：显式绑定、关键词候选、背景资料或清洗排除。<br><b>event_category</b>：军事、外交、政策、能源、援助、内政。<br><b>situation_signal</b>：升级、持续、缓和、待研判。</p></article><article class="tag-human"><h3>用户复核标签</h3><p><b>review.signal</b>：用户确认或调整后的态势分类。<br><b>review.reason / version / created_at</b>：理由、版本和时间。<br>复核后仍保留 automatic_signal，便于比较自动判断与用户判断。</p></article><article class="tag-source"><h3>核验与性质</h3><p><b>nature</b>：官方通报、媒体报道、结构化指标、补充资料等。<br><b>verification</b>：待核验、原文已采集、官方数据已导入等。<br>“官方发布”只证明发布行为，不自动证明内容。</p></article><article class="tag-derived"><h3>位置标签</h3><p><b>precision</b>：explicit 明确位置、area 区域参考、mention 文章提及。<br><b>evidence_text / role</b>：原文地点依据与地图解释。<br>没有证据文字的坐标不能进入 geo.v6。</p></article><article class="tag-human"><h3>翻译与质量标签</h3><p><b>translation_quality</b>：中文原文、机器译文待语义核验或待复核。<br><b>issues</b>：数字变化、外文残留、术语问题。<br>译文进入缓存，原始标题和正文保持不可变。</p></article></div></section>
<section id="next"><div class="section-head"><div class="section-no">13 / 后续工作</div><div><h2>从双主题原型走向可配置、可共享、可日常使用的地缘研究平台</h2><p>下一阶段重点解决新事件快速接入、采集策略灵活调整、外部资料补充和日频成果交付，使系统可以围绕新的地缘事件复用现有的数据、分析与报告链路。</p></div></div><div class="capability-grid"><article class="capability-card"><span class="status-tag">后续工作 01</span><h3>地缘主题配置化</h3><p>把主题、研究维度、事件类别、地图范围、情景、资产传导规则和页面入口改为配置驱动。新增“法国内乱”等地缘事件时，通过增加主题配置和数据绑定即可形成独立工作区，尽量减少重复开发。</p></article><article class="capability-card"><span class="status-tag">后续工作 02</span><h3>关键词配置与采集 MCP 化</h3><p>将每个爬虫的发现词、准入词、排除词、时间窗口、来源范围和版本记录统一配置，支持按采集效果灵活调整。把清洗后的新闻、结构化指标、来源状态和检索能力封装为只读 MCP 工具，供外部 Agent 按主题、时间、维度和证据 ID 调用。</p></article><article class="capability-card"><span class="status-tag">后续工作 03</span><h3>接入华泰宏观新闻群</h3><p>增加外部华泰宏观新闻群消息的合规接入、原始留存、去重、中文化、主题分类和来源标记。群内消息作为补充线索单独标识，不自动替代公开来源或官方信息，并保留发布时间、采集时间和可追溯出处。</p></article><article class="capability-card"><span class="status-tag">后续工作 04</span><h3>自动生成日频报告</h3><p>按固定时点冻结当日快照，自动生成美伊、俄乌及后续新增主题的日频报告，覆盖核心判断、当日变化、关键证据、情景变化、大类资产影响、反证、数据缺口和下一观察项；保留版本并支持网页、Markdown 和后续推送渠道。</p></article></div><div class="boundary" style="margin-top:18px"><b>配套完善：</b>继续展示采集器实际检索表达式和准入、分类、排除规则；增加“为何命中、为何归类、为何排除”的解释；将重复报道归并为事件簇；补充指标修订历史与跨源对账；统一翻译纠错、主题调整和态势复核队列；结合业务规则、历史案例和资产传导框架生成专题报告与事件复盘，并清楚区分事实、程序计算、业务判断和模型推论。</div></section>
<footer>GeoAgent 数据源与处理链路说明 · 由当前项目代码、采集器注册表和冻结快照生成 · 数据截至 {html.escape(dataset['snapshot']['created_at'][:19])}</footer><button class="print" onclick="window.print()">打印 / 导出 PDF</button></main></div>
<script>const DATA={data_json};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const topicName=t=>t==='usiran'?'美伊局势':t==='ukraine'?'俄乌冲突':t;
function renderCrawlers(){{const q=document.querySelector('#source-search').value.trim().toLowerCase(),topic=document.querySelector('#topic-filter').value,list=document.querySelector('#crawler-list');let visibleSources=0,visibleCrawlers=0;list.innerHTML='';DATA.crawlers.forEach(c=>{{if(topic!=='all'&&!c.topics.includes(topic))return;const collectorText=[c.id,c.name,c.collection_scope,...c.research_dimensions].join(' ').toLowerCase();const sources=c.sources.filter(s=>!q||[collectorText,s.id,s.name,s.url].join(' ').toLowerCase().includes(q));if(q&&!sources.length&&!collectorText.includes(q))return;visibleCrawlers++;visibleSources+=sources.length;const el=document.createElement('details');el.className='crawler';el.innerHTML=`<summary><span class="crawler-name"><b>${{esc(c.name)}}</b><code>${{esc(c.id)}}</code></span><span>${{c.topics.map(t=>`<i class="pill">${{topicName(t)}}</i>`).join('')}}</span><span><b>${{c.source_count}}</b> 个来源</span><span><b>${{c.records}}</b> 条记录</span></summary><div class="crawler-body"><div class="collector-purpose"><div><b>对应研究框架</b><p>${{c.research_dimensions.map(value=>`<i class="pill">${{esc(value)}}</i>`).join('')}}</p></div><div><b>重点采集内容</b><p>${{esc(c.collection_scope)}}</p></div></div><div class="meta"><span>启动文件：${{esc(c.entrypoint)}}</span><span>输出类型：${{esc(c.output_kind)}}</span><span>主题记录：${{c.topic_records}}</span></div>${{sources.length?`<div class="table-wrap"><table><thead><tr><th>来源</th><th>来源 ID</th><th>等级</th><th>状态</th><th>记录</th><th>最新数据</th></tr></thead><tbody>${{sources.map(s=>`<tr><td>${{s.url?`<a class="source-link" href="${{esc(s.url)}}" target="_blank" rel="noreferrer">${{esc(s.name)}} ↗</a>`:esc(s.name)}}${{s.error?`<br><small>${{esc(s.error)}}</small>`:''}}</td><td><code>${{esc(s.id)}}</code></td><td>${{esc(s.tier)}}</td><td class="state-${{esc(s.state)}}">${{esc(s.state)}}</td><td>${{esc(s.count)}}</td><td>${{esc(s.latest||'—')}}</td></tr>`).join('')}}</tbody></table></div>`:'<div class="empty">当前快照没有单独展开的来源状态；该采集器可能发布聚合指标或单一 geo.v6 数据包。</div>'}}</div>`;list.appendChild(el)}});document.querySelector('#source-summary').textContent=`显示 ${{visibleCrawlers}} 个采集器、${{visibleSources}} 条来源状态；当前快照总计 ${{DATA.counts.sources}} 条。`}}
function renderTerms(){{const q=document.querySelector('#term-search').value.trim().toLowerCase(),topic=document.querySelector('#term-topic').value,grid=document.querySelector('#keyword-grid');grid.innerHTML='';DATA.crawlers.forEach(c=>{{if(topic!=='all'&&!c.topics.includes(topic))return;const all=c.search_queries||[];const rules=all.filter(r=>!q||[c.name,c.id,r.raw,r.zh,r.origin].join(' ').toLowerCase().includes(q));if(q&&!rules.length)return;const el=document.createElement('article');el.className='keyword-card';el.innerHTML=`<h3>${{esc(c.name)}}</h3><small>采集器标识：${{esc(c.id)}} · ${{all.length}} 条当前检索表达式</small><div class="tokens">${{rules.length?rules.map(r=>`<div class="term-row"><span class="term-stage stage-发现">检索</span><span class="term-copy"><code class="term-original">实际查询：${{esc(r.raw)}}</code>${{r.zh&&r.zh!==r.raw?`<span class="term-zh">中文释义：${{esc(r.zh)}}</span>`:''}}${{r.time_scope?`<span class="term-zh"><b>检索时间范围：</b>${{esc(r.time_scope)}}（Google News 时间过滤条件）</span>`:''}}<small class="term-origin">查询入口与配置：${{esc(r.origin)}}</small></span></div>`).join(''):'<div class="empty">固定来源，不使用搜索关键词。该采集器直接读取 RSS、官方 API、行情接口或固定网页列表，再按自身规则筛选内容。</div>'}}</div>`;grid.appendChild(el)}});if(!grid.children.length)grid.innerHTML='<div class="empty">没有匹配的当前检索关键词。</div>'}}
['source-search','topic-filter'].forEach(id=>document.querySelector('#'+id).addEventListener(id.includes('search')?'input':'change',renderCrawlers));['term-search','term-topic'].forEach(id=>document.querySelector('#'+id).addEventListener(id.includes('search')?'input':'change',renderTerms));renderCrawlers();renderTerms();</script></body></html>'''


def main() -> None:
    dataset = build_dataset(latest_snapshot())
    OUTPUT.write_text(render(dataset), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "counts": dataset["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
