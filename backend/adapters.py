"""Read-only crawler adapters; all unreviewed extracted relationships remain candidates."""
import json
import re
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .catalog import TOPICS, topic_dimensions
from .store import digest, data_dir
from .events import CATEGORIES,SIGNALS

MONITOR_DIMENSIONS = {"opinion-monitor":"us_opinion", "israel-monitor":"israel", "mideast-monitor":"regional", "iran-domestic-monitor":"iran_domestic", "diplomacy-monitor":"diplomacy", "iran-escalation-monitor":"iran_readiness"}
UKRAINE_DIMENSIONS = {'ua-front-monitor':'frontline','ukraine-aid-monitor':'aid','russia-domestic-monitor':'russia_domestic','eu-domestic-monitor':'eu_domestic','ru-diplomacy-monitor':'diplomacy'}
MONITOR_BINDINGS = {**{k:('usiran',v) for k,v in MONITOR_DIMENSIONS.items()}, **{k:('ukraine',v) for k,v in UKRAINE_DIMENSIONS.items()}}
MONITORS = ("naval-monitor", "defense-spend-monitor", "readiness-monitor", *MONITOR_BINDINGS)
ANCHORS = {"usiran": r"\biran(?:ian)?\b|\birgc\b|\bhormuz\b|\bhouthi\w*\b|\bred sea\b|伊朗|霍尔木兹|胡塞|红海", "ukraine": r"\bukrain\w*\b|\bdonbas\w*\b|\bdonetsk\b|\bkyiv\b|\bzelensk\w*\b|乌克兰|俄乌|顿涅茨克"}
DIM_RULES = {
    "usiran": [("diplomacy", r"talks|negotiat|ceasefire|mediati|谈判|停火|斡旋"), ("us_opinion", r"poll|war powers|民调|战争权力"), ("iran_domestic", r"rial|unemployment|里亚尔|失业"), ("israel", r"israel|以色列"), ("financial", r"oil price|通行量|油价"), ("regional", r"houthi|saudi|iraq|胡塞|沙特|伊拉克")],
    "ukraine": [("diplomacy", r"talks|negotiat|ceasefire|谈判|停火"), ("aid", r"aid|assistance|deliver|loan|援助|交付|贷款"), ("russia_domestic", r"russian budget|ruble|卢布|财政赤字"), ("eu_domestic", r"election|选举|民调")],
}

# Topic crawlers intentionally cast a wide net. These rules remove obvious
# false positives at the shared ingestion boundary, while retaining the raw
# record and an auditable reason. A security/policy phrase wins over a noise
# phrase so mixed headlines such as "Iran talks ... World Cup" are preserved.
HARD_GEOPOLITICAL = re.compile(
    r"\b(?:attack\w*|strike\w*|missile\w*|drone\w*|military|troop\w*|army|navy|"
    r"war(?:s|fare|time|mongering)?|conflict\w*|combat\w*|ceasefire\w*|sanction\w*|"
    r"nuclear|weapon\w*|defen[cs]e|security|negotiat\w*|diplomat\w*|blockade\w*|"
    r"intercept\w*|deploy\w*|mobiliz\w*|occupation|frontline|airstrike\w*)\b|"
    r"袭击|打击|导弹|无人机|军队|军事|战争|冲突|停火|制裁|核设施|核武|武器|"
    r"国防|安全|谈判|外交|封锁|拦截|部署|动员|占领|前线",
    re.I,
)
NOISE_RULES = (
    (
        "体育资讯",
        re.compile(
            r"football|soccer|world cup|asian cup|asian games|championship|tournament|"
            r"wrestl|basketball|volleyball|tennis|cricket|olympic|athlete|medal|"
            r"premier league|\bafc\b|\bfifa\b|\bu-?23\b|"
            r"足球|世界杯|亚洲杯|亚运会|锦标赛|摔跤|篮球|排球|网球|板球|奥运|运动员|奖牌|联赛",
            re.I,
        ),
    ),
    ("公共卫生资讯", re.compile(r"mpox|monkeypox|smallpox|measles|dengue|\bflu\b|influenza|猴痘|麻疹|登革热|流感疫情", re.I)),
    ("自然灾害资讯", re.compile(r"earthquake|seismic|volcano|tsunami|地震|火山喷发|海啸", re.I)),
    ("文化旅游资讯", re.compile(r"touris[mt]|architecture|archaeolog|museum|film festival|fashion week|旅游|建筑风貌|考古|博物馆|电影节|时装周", re.I)),
)
UKRAINE_GENERAL_TRADE = re.compile(
    r"(?=[\s\S]{0,300}(?:eu|european union|欧洲联盟|欧盟))"
    r"(?=[\s\S]{0,300}(?:china|chinese|中国))"
    r"(?=[\s\S]{0,300}(?:trade|tariff|贸易|关税))",
    re.I | re.S,
)
UKRAINE_LINK = re.compile(
    r"ukrain|乌克兰|俄乌|crimea|克里米亚|donbas|顿巴斯|"
    r"russia.{0,60}(?:war|sanction|energy|oil|gas|military|defen[cs]e|budget|ruble)|"
    r"俄罗斯.{0,60}(?:战争|冲突|制裁|能源|石油|天然气|军事|国防|财政|卢布)",
    re.I | re.S,
)
USIRAN_REGION = re.compile(
    r"iran|iranian|irgc|tehran|khamenei|pezeshkian|araghchi|israel|israeli|idf|netanyahu|gaza|"
    r"west bank|palestin|leban|syria|iraq|jordan|saudi|uae|emirat|qatar|bahrain|kuwait|"
    r"oman|yemen|houthi|hezbollah|hamas|gulf|middle east|centcom|hormuz|red sea|"
    r"turk(?:ey|iye)|egypt|mecca|jerusalem|knesset|likud|eisenkot|bennett|lapid|tel aviv|haredi|"
    r"伊朗|德黑兰|哈梅内伊|佩泽希齐扬|阿拉格齐|以色列|加沙|约旦河西岸|巴勒斯坦|黎巴嫩|叙利亚|"
    r"伊拉克|约旦|沙特|阿联酋|卡塔尔|巴林|科威特|阿曼|也门|胡塞|真主党|哈马斯|海湾|"
    r"中东|中央司令部|霍尔木兹|红海|土耳其|埃及|麦加|耶路撒冷|议会|特拉维夫",
    re.I,
)
USIRAN_WIDE_NET_ADAPTERS = set(MONITOR_DIMENSIONS) | {"xinhua-monitor", "stcn-usiran-monitor"}


def _parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clear_future_published_at(record, *, now_utc=None, tolerance=timedelta(days=2)):
    """Quarantine publisher dates that would incorrectly advance recency."""
    value = record.get("published_at")
    published = _parse_timestamp(value)
    reference = now_utc or datetime.now(timezone.utc)
    if published is None or published <= reference + tolerance:
        return False
    record["published_at_original"] = value
    record["published_at"] = None
    record.setdefault("timestamp_issues", []).append({
        "field": "published_at",
        "value": value,
        "reason": "发布时间晚于导入时间两天以上，已从时效排序中隔离",
    })
    return True


def clean_record_topics(record):
    """Remove clear topic false positives without deleting source evidence."""
    if not record.get("topics"):
        return []
    title = " ".join(str(record.get(k, "") or "") for k in ("title", "title_zh"))
    text = " ".join(str(record.get(k, "") or "") for k in ("title", "title_zh", "text", "summary_zh"))
    region_text = re.sub(r"Middle East\s*(?:&|and)\s*North Africa", "", text, flags=re.I)
    hard = bool(HARD_GEOPOLITICAL.search(text))
    removals = []
    for topic in list(record["topics"]):
        reason = None
        for label, pattern in NOISE_RULES:
            if not pattern.search(text):
                continue
            # News summaries often mention a country's security backdrop even
            # when the article itself is about a match or an award. For sport,
            # only a geopolitical phrase in the headline can override cleanup.
            relevant_override = bool(HARD_GEOPOLITICAL.search(title)) if label == "体育资讯" else hard
            if not relevant_override:
                reason = label
                break
        if topic == "ukraine" and not reason and UKRAINE_GENERAL_TRADE.search(text) and not UKRAINE_LINK.search(text):
            reason = "与俄乌无直接关联的一般贸易资讯"
        if topic == "usiran" and not reason and set(record.get("adapters", [])) & USIRAN_WIDE_NET_ADAPTERS and not USIRAN_REGION.search(region_text):
            reason = "与美伊或中东无直接关联的资讯"
        if not reason:
            continue
        record["topics"].remove(topic)
        record.get("dimensions", {}).pop(topic, None)
        record.get("dimension_candidates", {}).pop(topic, None)
        item = {"topic": topic, "reason": reason, "rule": "topic-relevance-v1"}
        record.setdefault("topic_exclusions", []).append(item)
        removals.append(item)
    if removals and not record["topics"]:
        record["classification"] = "相关性清洗排除"
        record["scope"] = "背景资料"
    return removals

def safe_url(url):
    try:
        u = urlsplit(str(url or ""))
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            return ""
        query = [(k, v) for k, v in parse_qsl(u.query) if not k.lower().startswith("utm_") and k.lower() not in {"api_key", "apikey", "key", "token"}]
        return urlunsplit((u.scheme, u.netloc, u.path.rstrip("/"), urlencode(query), ""))
    except ValueError:
        return ""

def classify(raw):
    title = re.sub(r"\s*\|\s*Ukraine news.*$", "", str(raw.get("title", "")), flags=re.I)
    summary = re.sub(r"\s*\|\s*Ukraine news[^.]*", "", str(raw.get("summary", "")), flags=re.I)
    text = (title + " " + summary).lower()
    topics = [t for t, pattern in ANCHORS.items() if re.search(pattern, text)]
    dims = {}
    for topic in topics:
        default = "frontline" if topic == "ukraine" else ("iran_readiness" if raw.get("party") == "iran" else "us_readiness")
        dims[topic] = next((d for d, pattern in DIM_RULES[topic] if re.search(pattern, text)), default)
    return topics, dims

def normalize(raw, monitor):
    link = safe_url(raw.get("link", raw.get("url", "")))
    title = str(raw.get("title", "无标题"))
    rid = digest(link or [monitor, raw.get("id"), title, raw.get("published")])
    topics, dimensions = classify(raw)
    dimension_candidates = {t:[d] for t,d in dimensions.items()}
    if raw.get('kind')=='excluded':
        topics,dimensions,dimension_candidates=[],{},{}
    elif monitor in MONITOR_BINDINGS:
        topic,dimension=MONITOR_BINDINGS[monitor]
        if raw.get('buckets') or topic in topics:
            if topic not in topics:topics.append(topic)
            dimension_candidates.setdefault(topic,[])
            if dimension not in dimension_candidates[topic]:dimension_candidates[topic].append(dimension)
            dimensions[topic]=dimension
    points = []
    # Dictionary locations are ONLY article mentions; localized extraction never proves deployment.
    for p in raw.get("places", []):
        if isinstance(p, dict) and isinstance(p.get("lat"), (int, float)) and isinstance(p.get("lon"), (int, float)):
            if -90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180:
                points.append({"lat": p["lat"], "lon": p["lon"], "label": p.get("name", "文中地点"), "precision": "mention", "role": "文章提及，非部署确认"})
    return {
        "id": rid, "source_id": str(raw.get("source_id", monitor)),
        "source_name": str(raw.get("source_name", raw.get("publisher", monitor))),
        "tier": raw.get("tier", "未分级"), "publisher": raw.get("publisher", ""),
        "title": title, "text": str(raw.get("summary", "")), "url": link,
        "published_at": raw.get("published_utc") or raw.get("published") or None,
        "fetched_at": raw.get("fetched_at") or None,
        "first_seen": raw.get("first_seen") or None, "event_at": None,
        "party": raw.get("party", "未标注"), "nature": ("原始表态/通报" if raw.get("tier") == "T1" else "报道/转述") if monitor == "readiness-monitor" else "公开记录",
        "verification": "待核验", "classification": "关键词候选，需研究员核验", "topics": topics,
        "dimensions": dimensions, "dimension_candidates":dimension_candidates, "crawler_buckets":raw.get("buckets",[]), "scope": "主题候选" if topics else "背景资料",
        "points": points, "origin_id": digest(safe_url(raw.get("original_url"))) if safe_url(raw.get("original_url")) else rid,
        "adapters": [monitor], "raw_refs": [],
        "upstream_kind":raw.get('kind'),
        "upstream_extractions": [{"monitor":monitor,"values":{k:raw[k] for k in ('polls','metrics','amounts','quantities','entities','flags','stance','ships','signals','claim_kinds','relevance','buckets','date_unknown') if k in raw}}],
    }

def load_bundle(root: Path):
    records = {}
    observations = []
    sources = []
    manifests = []
    errors = []
    cleaning = {"raw_records": 0, "duplicates_merged": 0, "invalid_records": 0}

    def read(path, decode=True):
        content = path.read_bytes()
        key = digest(content.hex())
        folder = data_dir() / "archive"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (key + path.suffix)
        if not target.exists():
            target.write_bytes(content)
        try:
            label = str(path.relative_to(root))
        except ValueError:
            label = "knowledge/" + path.name
        manifests.append({"file": label, "hash": key, "archive": target.name})
        return content.decode("utf-8-sig") if decode else content

    def merge(record, archive):
        if clear_future_published_at(record):
            cleaning["future_published_cleared"] = cleaning.get("future_published_cleared", 0) + 1
        record["raw_refs"] = [archive]
        if record["id"] in records:
            cleaning["duplicates_merged"] += 1
            prev = records[record["id"]]
            prev["adapters"] = sorted(set(prev["adapters"] + record["adapters"]))
            prev["raw_refs"] = sorted(set(prev["raw_refs"] + record["raw_refs"]))
            prev["topics"] = sorted(set(prev["topics"] + record["topics"]))
            for topic, values in record.get('dimension_candidates',{}).items():
                prev.setdefault('dimension_candidates',{}).setdefault(topic,[])
                prev['dimension_candidates'][topic]=sorted(set(prev['dimension_candidates'][topic]+values))
            prev["dimensions"].update(record["dimensions"])
            extractions = prev.setdefault('upstream_extractions', [])
            existing = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in extractions}
            extractions.extend(item for item in record.get('upstream_extractions', []) if json.dumps(item, ensure_ascii=False, sort_keys=True) not in existing)
            if len(record["text"]) > len(prev["text"]):
                prev["text"] = record["text"]
            if prev["tier"] == "未分级":
                prev["tier"] = record["tier"]
        else:
            records[record["id"]] = record

    discovered={p.parent.parent.name for p in root.glob('*/output/ALL-records.jsonl')}
    discovered|={p.parent.parent.name for p in root.glob('*/output/LATEST-records.jsonl') if p.parent.parent.name!='finance-front-monitor'}
    for monitor in sorted(set(MONITORS)|discovered):
        file = root / monitor / "output" / "ALL-records.jsonl"
        if not file.exists():file=root / monitor / 'output' / 'LATEST-records.jsonl'
        if not file.exists():
            sources.append({"id": monitor, "monitor": monitor, "name": monitor, "state": "not_connected", "count": 0, "latest": None})
            continue
        content = read(file)
        archive = manifests[-1]["archive"]
        monitor_records = []
        for line_no, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                continue
            cleaning["raw_records"] += 1
            try:
                raw = json.loads(line)
                record = normalize(raw, monitor)
                merge(record, archive)
                monitor_records.append(record)
            except (ValueError, TypeError, AttributeError) as exc:
                cleaning["invalid_records"] += 1
                errors.append({"file": str(file.relative_to(root)), "line": line_no, "error": type(exc).__name__})
        statuses = {}
        status_file = file.parent / "_source_status.json"
        checked_at = None
        if status_file.exists():
            status = json.loads(read(status_file))
            checked_at = status.get("run_at")
            statuses = {x["id"]: x for x in status.get("sources", [])}
        fetch_file=file.parent/'_fetch_stats.json'
        if not statuses and fetch_file.exists():
            fetch=json.loads(read(fetch_file)); checked_at=fetch.get('run')
            statuses={k:{'state':('ok-empty' if v.get('n')==0 else 'ok') if v.get('ok') else 'failed','error':v.get('err'),'http_status':v.get('status')} for k,v in fetch.get('src',{}).items()}
        ids = sorted(set(r["source_id"] for r in monitor_records) | set(statuses))
        for source_id in ids:
            subset = [r for r in monitor_records if r["source_id"] == source_id]
            state = statuses.get(source_id, {})
            sources.append({"id": monitor + ":" + source_id, "monitor": monitor, "name": state.get("publisher") or (subset[0]["source_name"] if subset else source_id), "state": state.get("state", "imported"), "checked_at": checked_at,
                            "count": len(subset), "latest": max((str(r["published_at"]) for r in subset if r["published_at"]), default=None), "tier": state.get("tier", subset[0]["tier"] if subset else "未分级"), 'error':state.get('error'), 'http_status':state.get('http_status')})

    metric_files = sorted((root / "defense-spend-monitor" / "output").glob("*/metrics-*.json"))
    # Every upstream metric revision gets its own snapshot; within a snapshot latest file wins.
    if metric_files:
        file = metric_files[-1]
        metrics = json.loads(read(file))
        archive = manifests[-1]["archive"]
        for key, line in metrics.get("treasury", {}).get("lines", {}).items():
            rid = "metric-" + digest([key, manifests[-1]["hash"]])
            record = {"id": rid, "source_id": "treasury-mts", "source_name": "美国财政部 MTS · 爬虫指标文件", "tier": "T1", "publisher": "美国财政部", "title": line["label"] + " · 月度净支出", "text": "爬虫结构化指标；范围为美国整体，不能直接归因于美伊或俄乌。记录保留于快照原始文件。", "url": "https://fiscaldata.treasury.gov/datasets/monthly-treasury-statement/", "published_at": None, "fetched_at": None, "first_seen": None, "event_at": None, "party": "us", "nature": "结构化指标", "verification": "已导入，未独立回源复核", "classification": "美国整体背景", "topics": [], "dimensions": {}, "scope": "美国整体背景", "points": [], "origin_id": "treasury-mts:" + key, "adapters": ["defense-spend-monitor"], "raw_refs": [archive]}
            records[rid] = record
            observations.append({"id": "treasury-" + digest(key), "name": line["label"], "type": "quantitative", "unit": "USD", "frequency": "月度", "scope": "美国整体背景", "topic": None, "dimension": "us_readiness", "method": "Treasury MTS net outlay；实际净支出，非预算、非合同义务", "boundary": "不可单独推断冲突强度或作战意图", "source_ids": [rid], "history": [{"date": d, "value": v} for d, v in line.get("history", [])], "observed_at": line.get("latest_month")})

    from .finance_adapter import load_finance
    load_finance(root,read,manifests,records,observations,sources)

    from .taco_adapter import load_taco
    load_taco(root, records, observations, sources, read, manifests)

    # Extension contract: any child project can publish output/geo-v6.json.
    for file in sorted(root.glob("*/output/geo-v6.json")):
        extension = json.loads(read(file))
        if extension.get("schema_version") != "geo.v6/1":
            raise ValueError(f"{file.name}: schema_version 必须是 geo.v6/1")
        archive = manifests[-1]["archive"]
        local_ids = {}
        for raw in extension.get("records", []):
            record = normalize(raw, file.parents[1].name)
            explicit_topics = raw.get("topics", [])
            for topic in explicit_topics:
                if topic not in {t["id"] for t in TOPICS} or raw.get("dimensions", {}).get(topic) not in topic_dimensions(topic):
                    raise ValueError("扩展记录包含无效主题或维度")
            record.update(topics=explicit_topics, dimensions=raw.get("dimensions", {}), classification="适配器显式绑定", scope="主题记录", dimension_candidates={t:[d] for t,d in raw.get("dimensions",{}).items()})
            record["verification"] = raw.get("verification", "待核验")
            record["event_at"] = raw.get("event_at")
            record["nature"] = raw.get("nature", "公开记录")
            for field in ('title_zh','summary_zh'):
                if raw.get(field):record[field]=str(raw[field])
            if raw.get('event_category'):
                if raw['event_category'] not in CATEGORIES:raise ValueError('无效事件类型')
                record['event_category']=raw['event_category']
            if raw.get('situation_signal'):
                if raw['situation_signal'] not in SIGNALS or not raw.get('signal_basis'):raise ValueError('态势线索需要有效类型与依据')
                record['situation_signal']=raw['situation_signal']
                record['signal_basis']=str(raw['signal_basis'])
            # Accepted coordinates must explicitly describe uncertainty and cite the location text.
            record["points"] = []
            for point in raw.get("points", []):
                if point.get("precision") not in {"explicit", "area", "mention"} or not point.get("evidence_text"):
                    raise ValueError("位置需要 precision 与 evidence_text")
                if not (-90 <= point["lat"] <= 90 and -180 <= point["lon"] <= 180):
                    raise ValueError("坐标越界")
                record["points"].append({**point, "role": point.get("role", "来源记载地点，非实时舰位")})
            if raw["id"] in local_ids:
                raise ValueError("同一适配器内记录 ID 不可重复")
            local_ids[raw["id"]] = record["id"]
            merge(record, archive)
        for obs in extension.get("observations", []):
            for field in ("id", "name", "unit", "frequency", "scope", "method", "history", "source_ids"):
                if field not in obs:
                    raise ValueError(f"指标缺少 {field}")
            topic = obs.get("topic")
            if topic and (topic not in {t["id"] for t in TOPICS} or obs.get("dimension") not in topic_dimensions(topic)):
                raise ValueError("指标主题或维度无效")
            if not obs["source_ids"] or any(s not in local_ids for s in obs["source_ids"]):
                raise ValueError("指标引用必须关联本适配器 records")
            if any(not isinstance(p.get("date"), str) or (p.get("value") is not None and not isinstance(p["value"], (int, float))) for p in obs["history"]):
                raise ValueError("history 必须包含日期及数值或 null")
            dates = set()
            for point in obs["history"]:
                date.fromisoformat(point["date"])
                if point["date"] in dates:
                    raise ValueError("同一指标日期不可重复，请先统一口径")
                dates.add(point["date"])
                value = point.get("value")
                if isinstance(value, bool) or (value is not None and not math.isfinite(value)):
                    raise ValueError("指标数值必须为有限数或 null")
            observations.append({**obs, "id": file.parents[1].name + ":" + obs["id"], "source_ids": [local_ids[s] for s in obs["source_ids"]]})
        project_id = file.parents[1].name
        source_status_file = file.parent / "_source_status.json"
        source_statuses = []
        checked_at = None
        if source_status_file.is_file():
            status_payload = json.loads(read(source_status_file))
            checked_at = status_payload.get("run_at")
            source_statuses = [item for item in status_payload.get("sources", []) if isinstance(item, dict)]
        if source_statuses:
            for item in source_statuses:
                source_id = str(item.get("id") or item.get("publisher") or "source")
                sources.append({
                    "id": project_id + ":" + source_id,
                    "monitor": project_id,
                    "name": item.get("publisher") or source_id,
                    "state": item.get("state", "unknown"),
                    "count": item.get("count", len(extension.get("records", []))),
                    "latest": item.get("latest") or extension.get("as_of"),
                    "checked_at": checked_at,
                    "error": item.get("error"),
                    "http_status": item.get("http_status"),
                })
        else:
            sources.append({"id": project_id, "monitor": project_id, "name": extension.get("name", project_id), "state": "imported", "count": len(extension.get("records", [])), "latest": extension.get("as_of")})

    from .report_adapter import load_reports
    load_reports(root,read,manifests,records,sources)
    from .paths import collector_read_root, knowledge_root
    if root.resolve() == collector_read_root().resolve() and knowledge_root().is_dir():
        load_reports(knowledge_root(), read, manifests, records, sources)

    topic_exclusions = []
    for record in records.values():
        for exclusion in clean_record_topics(record):
            topic_exclusions.append({"record_id": record["id"], **exclusion})
    if topic_exclusions:
        cleaning["topic_assignments_removed"] = len(topic_exclusions)
        cleaning["records_quarantined"] = len({item["record_id"] for item in topic_exclusions})
    projects=[]
    for project in sorted(root.glob('*')):
        output=project/'output'
        if not project.is_dir() or not output.is_dir():continue
        documents=[]
        for file in sorted(output.iterdir()):
            if file.is_file() and (file.name.startswith('LATEST') and file.suffix=='.md' or file.name.startswith('_') and file.suffix=='.json' or file.name=='ALL-observations.jsonl'):
                read(file,False)
                documents.append({'name':file.name,'archive':manifests[-1]['archive']})
        subset=[r for r in records.values() if project.name in r['adapters']]
        statuses=[s for s in sources if s['monitor']==project.name]
        projects.append({'id':project.name,'records':len(subset),'topic_records':sum(bool(r['topics']) for r in subset),'missing_published':sum(not r['published_at'] for r in subset),'missing_fetched':sum(not r['fetched_at'] for r in subset),'failed_sources':sum('fail' in str(s['state']) or s['state'] in {'configuration_required','stale'} for s in statuses),'sources':len(statuses),'state':'connected' if statuses or subset else 'unsupported','documents':documents})
    cleaning["unique_records"] = len(records)
    return {"schema_version": "geo.v6/1", "records": sorted(records.values(), key=lambda r: r["id"]), "observations": observations, "sources": sources, "manifest": manifests, "errors": errors,'projects':projects,'cleaning':cleaning,"topic_exclusions":topic_exclusions}
