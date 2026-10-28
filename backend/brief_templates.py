"""Versioned, constrained research-brief templates."""
from copy import deepcopy
import json

from .store import canonical, db, digest, now

SECTION_DEFAULTS = [
    {"id": "title", "title": "标题与日期", "enabled": True, "instruction": "标明主题、当前最重要事态与时效。"},
    {"id": "bluf", "title": "核心判断（Bottom Line Up Front）", "enabled": True, "instruction": "用二至三句话说明基准走势、首要风险及最重要的资产含义。"},
    {"id": "developments", "title": "当前主要事项（Key Developments）", "enabled": True, "instruction": "按当事方或地区陈述军事、外交、基础设施和核心诉求事实。"},
    {"id": "outlook", "title": "事态发展研判（Outlook）", "enabled": True, "instruction": "区分基准情景和尾部风险，列出触发条件、反证及观察指标。"},
    {"id": "assets", "title": "大类资产影响（Asset Implications）", "enabled": True, "instruction": "展示原油、黄金、美债、美元和权益市场的条件方向与传导逻辑。"},
    {"id": "sources", "title": "证据与信源", "enabled": True, "instruction": "列出来源、发布时间、采集时间、核验状态和链接。"},
]

DEFAULT_TEMPLATE = {
    "id": "default-investment-brief",
    "name": "投资研究每日简报",
    "version": 2,
    "is_default": True,
    "is_system": True,
    "topic_scope": "all",
    "lookback_hours": 24,
    "max_lookback_days": 7,
    "evidence_limit": 8,
    "sections": SECTION_DEFAULTS,
}

ALLOWED_SECTIONS = {item["id"] for item in SECTION_DEFAULTS}


def _validate(config):
    value = deepcopy(config)
    name = str(value.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("模板名称长度应为1—80个字符")
    if value.get("topic_scope") not in {"all", "usiran", "ukraine"}:
        raise ValueError("模板主题范围无效")
    for key, low, high in (("lookback_hours", 1, 168), ("max_lookback_days", 1, 30), ("evidence_limit", 2, 30)):
        try:
            value[key] = int(value.get(key))
        except (TypeError, ValueError):
            raise ValueError("模板数值配置无效") from None
        if not low <= value[key] <= high:
            raise ValueError(f"{key} 超出允许范围")
    sections = value.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("模板至少需要一个章节")
    seen = set()
    cleaned = []
    for section in sections:
        sid = str(section.get("id", ""))
        if sid not in ALLOWED_SECTIONS or sid in seen:
            raise ValueError("模板章节无效或重复")
        title = str(section.get("title", "")).strip()
        instruction = str(section.get("instruction", "")).strip()
        if not title or len(title) > 100 or len(instruction) > 500:
            raise ValueError("章节标题或写作要求无效")
        cleaned.append({"id": sid, "title": title, "enabled": bool(section.get("enabled", True)), "instruction": instruction})
        seen.add(sid)
    if "sources" not in seen or not next(item for item in cleaned if item["id"] == "sources")["enabled"]:
        raise ValueError("证据与信源章节必须启用")
    value["name"] = name
    value["sections"] = cleaned
    return value


def _ensure_default():
    with db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71005)")
        row = conn.execute("SELECT version FROM brief_templates WHERE id=%s", (DEFAULT_TEMPLATE["id"],)).fetchone()
        if not row:
            timestamp = now()
            conn.execute(
                "INSERT INTO brief_templates VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (DEFAULT_TEMPLATE["id"], DEFAULT_TEMPLATE["name"], DEFAULT_TEMPLATE["version"], 1, 1, canonical(DEFAULT_TEMPLATE), timestamp, timestamp),
            )
        elif int(row["version"]) < DEFAULT_TEMPLATE["version"]:
            conn.execute(
                "UPDATE brief_templates SET name=%s,version=%s,config=%s,updated_at=%s WHERE id=%s",
                (DEFAULT_TEMPLATE["name"], DEFAULT_TEMPLATE["version"], canonical(DEFAULT_TEMPLATE), now(), DEFAULT_TEMPLATE["id"]),
            )


def list_templates():
    _ensure_default()
    with db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71005)")
        rows = conn.execute("SELECT * FROM brief_templates ORDER BY is_default DESC,is_system DESC,updated_at DESC").fetchall()
    return [{**json.loads(row["config"]), "id": row["id"], "name": row["name"], "version": row["version"], "is_default": bool(row["is_default"]), "is_system": bool(row["is_system"]), "updated_at": row["updated_at"]} for row in rows]


def get_template(template_id=""):
    templates = list_templates()
    if template_id:
        return next((item for item in templates if item["id"] == template_id), None)
    return next((item for item in templates if item["is_default"]), templates[0])


def save_template(config, template_id=""):
    value = _validate(config)
    _ensure_default()
    timestamp = now()
    with db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71005)")
        existing = conn.execute("SELECT * FROM brief_templates WHERE id=%s", (template_id,)).fetchone() if template_id else None
        if template_id and not existing:
            raise KeyError("模板不存在")
        if existing and existing["is_system"]:
            raise PermissionError("系统默认模板不可覆盖，请先复制为自定义模板")
        tid = template_id or "brief-template-" + digest([value["name"], timestamp])[:12]
        version = int(existing["version"]) + 1 if existing else 1
        is_default = bool(value.get("is_default"))
        value.update(id=tid, version=version, is_default=is_default, is_system=False)
        if is_default:
            conn.execute("UPDATE brief_templates SET is_default=0")
        if existing:
            conn.execute("UPDATE brief_templates SET name=%s,version=%s,is_default=%s,config=%s,updated_at=%s WHERE id=%s", (value["name"], version, int(is_default), canonical(value), timestamp, tid))
        else:
            conn.execute("INSERT INTO brief_templates VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (tid, value["name"], version, int(is_default), 0, canonical(value), timestamp, timestamp))
    return get_template(tid)


def set_default(template_id):
    if not get_template(template_id):
        raise KeyError("模板不存在")
    with db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71005)")
        conn.execute("UPDATE brief_templates SET is_default=CASE WHEN id=%s THEN 1 ELSE 0 END", (template_id,))
    return get_template(template_id)


def delete_template(template_id):
    template = get_template(template_id)
    if not template:
        raise KeyError("模板不存在")
    if template["is_system"]:
        raise PermissionError("系统默认模板不可删除")
    was_default = template["is_default"]
    with db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71005)")
        conn.execute("DELETE FROM brief_templates WHERE id=%s", (template_id,))
        if was_default:
            conn.execute("UPDATE brief_templates SET is_default=1 WHERE id=%s", (DEFAULT_TEMPLATE["id"],))
    return {"deleted": template_id}
