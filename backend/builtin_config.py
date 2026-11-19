"""内置采集器的来源与词表编辑（设计：docs/superpowers/specs/2026-11-19-内置采集器来源与关键词编辑设计.md）。

页面改动以“覆盖层”存进管线版本表，不改仓库里的 config.json：
- sources：{"set": {来源ID: {字段: 值}}, "add": [完整来源], "remove": [来源ID]}
- terms：{JSON 路径: [词]}，整体替换 config.json 里对应的字符串数组

运行时把覆盖层合并到当前 config.json，写到 collector-output/<id>/state/config.override.json，
经注册表 env 的 GEO_COLLECTOR_CONFIG 交给采集器；代码新增的来源和未改过的词表照常随代码更新。
"""
from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .paths import collector_data_root, project_root
from .pipeline_config import PipelineConfigError, _HostChecker, default_resolve

log = logging.getLogger(__name__)

# 有来源清单的新闻类采集器；数据序列类（finance-front、taco、defense-spend 等）和没有配置文件的保持只读。
EDITABLE = frozenset({
    "asset-transmission-monitor", "diplomacy-monitor", "eu-domestic-monitor", "geopolitical-source-monitor",
    "iran-domestic-monitor", "iran-escalation-monitor", "israel-monitor", "meeting-calendar-monitor",
    "mideast-monitor", "naval-monitor", "opinion-monitor", "readiness-monitor", "ru-diplomacy-monitor",
    "russia-domestic-monitor", "ua-front-monitor", "ukraine-aid-monitor",
})
READONLY_REASON = "该采集器采的是数据序列或没有可编辑的来源清单，由代码维护"
OVERRIDE_ENV = "GEO_COLLECTOR_CONFIG"
OVERRIDE_FILE = "config.override.json"

# 不作为“词表”列出的配置段：来源清单、基线、报告、请求参数等
SKIP_KEYS = {"sources", "report", "glossary", "request", "history", "schedule", "window", "output", "outlet_map",
             "zones", "probe_evidence", "baseline", "kind_rules", "queries", "anchors", "tags", "lines",
             "extra_lines", "tracks", "staleness", "formula", "series", "derived", "topics"}
SECTION_LABELS = {
    "buckets": "分桶", "entities": "实体", "stance": "立场词", "flags": "信号标记", "signals": "信号",
    "claims": "表态类型", "relevance": "相关度", "topic_terms": "主题锚词", "categories": "事项准入词",
    "exclusion_terms": "排除词", "relevance_keywords": "相关关键词", "meeting_terms": "会晤词",
    "families": "会议系列", "exclude_title_patterns": "排除标题", "persons": "人物", "locations": "地点",
    "actors": "国家与组织", "status_lexicon": "状态词", "tracker_title_match": "舰队追踪标题",
    "place_exclude_phrases": "排除地名短语", "exclude_keywords": "排除词", "extra_keywords": "补充关键词",
    "topic_title_terms": "标题主题词", "munitions_terms": "弹药词", "munitions_context_exclude": "弹药排除语境",
    "quantity_units": "数量单位", "signal_lexicon": "信号词", "ship_hull_prefixes": "舰体编号前缀",
    "meeting_terms_extra": "会晤补充词",
}
FIELD_LABELS = {"require_all": "须同时命中", "require_any": "任一命中", "any": "任一命中", "keywords": "关键词",
                "phrases": "短语", "aliases": "别名", "terms": "词", "exclude": "排除", "strong": "强相关",
                "medium": "中相关", "anchor": "锚词", "domain": "领域词", "anchors": "锚词", "strong_anchors": "强锚词",
                "escalate": "升级", "deescalate": "降级", "usiran": "美伊", "ukraine": "俄乌",
                "military": "军事", "diplomacy": "外交", "sanctions": "制裁", "defense_spending": "军费",
                "economy_livelihood": "经济民生", "energy_transport": "能源运输", "humanitarian": "人道",
                "nuclear_strategic": "核与战略", "aid_support": "援助"}
NAME_KEYS = ("publisher", "name")
SOURCE_FIELDS = ("name", "url", "tier", "enabled", "note")
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
MAX_TERM_LEN = 200
MAX_TERMS_PER_LIST = 2000
MAX_SOURCES = 400


class BuiltinConfigError(PipelineConfigError):
    pass


def editable(collector_id: str) -> bool:
    return collector_id in EDITABLE


def base_config(collector_id: str) -> dict:
    path = project_root() / "collectors" / collector_id / "config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def overlay_of(version_config: dict | None) -> dict:
    overlay = (version_config or {}).get("overlay") or {}
    return overlay if isinstance(overlay, dict) else {}


# ---------------------------------------------------------------- 路径与词表发现

def _escape(key: str) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def _split(path: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")] if path else []


def _resolve(config: Any, path: str) -> Any:
    node = config
    for part in _split(path):
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                return None
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node


def _is_term_list(value: Any) -> bool:
    return (isinstance(value, list) and bool(value) and all(isinstance(v, str) for v in value)
            and not any(v.startswith(("http://", "https://")) for v in value))


def _node_label(node: Any, key: str) -> str:
    if isinstance(node, dict):
        for name in ("label", "zh", "name"):
            if isinstance(node.get(name), str) and node[name].strip():
                return node[name].strip()
    return FIELD_LABELS.get(key, key)


def term_groups(config: dict) -> list[dict]:
    """config.json 里所有可编辑的字符串数组，按出现顺序列出，附中文标签。"""
    groups: list[dict] = []

    def walk(node: Any, path: list[str], labels: list[str]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("_") or key in SKIP_KEYS or key.endswith("baseline"):
                    continue
                label = SECTION_LABELS.get(key, key) if not path else _node_label(value, key)
                visit(value, [*path, _escape(key)], [*labels, label])
        elif isinstance(node, list) and node and not all(isinstance(v, str) for v in node):
            for index, value in enumerate(node):
                visit(value, [*path, str(index)], [*labels, _node_label(value, f"第 {index + 1} 组")])

    def visit(value: Any, path: list[str], labels: list[str]) -> None:
        if _is_term_list(value):
            groups.append({"path": "/".join(path), "label": " · ".join(labels), "terms": list(value)})
        elif isinstance(value, (dict, list)):
            walk(value, path, labels)

    walk(config, [], [])
    return groups


# ---------------------------------------------------------------- 来源

def _name_key(source: dict) -> str:
    return next((k for k in NAME_KEYS if k in source), "publisher")


def _enabled(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() not in {"false", "0", "no", ""}


def source_view(source: dict) -> dict:
    return {"id": str(source.get("id", "")), "name": str(source.get(_name_key(source), "") or ""),
            "url": str(source.get("url", "") or ""), "tier": str(source.get("tier", "") or ""),
            "enabled": _enabled(source.get("enabled", True)), "note": str(source.get("note", "") or "")}


def tiers_of(config: dict) -> list[str]:
    """该采集器可选的来源等级；来源本身没有 tier 字段（如海军监测用 priority）时返回空，页面不显示等级。"""
    found = {str(s.get("tier")) for s in config.get("sources", []) if s.get("tier")}
    return sorted(found | {"T1", "T2", "T3"}) if found else []


# ---------------------------------------------------------------- 合并

def apply_overlay(config: dict, overlay: dict) -> dict:
    """把覆盖层合并到 config 副本；代码已删除的来源、已不存在的词表路径直接忽略。"""
    merged = copy.deepcopy(config)
    sources_patch = overlay.get("sources") or {}
    removed = set(sources_patch.get("remove") or [])
    changes = sources_patch.get("set") or {}
    sources = []
    for source in merged.get("sources", []):
        sid = str(source.get("id"))
        if sid in removed:
            continue
        for field, value in (changes.get(sid) or {}).items():
            source[_name_key(source) if field == "name" else field] = value
        sources.append(source)
    known = {str(s.get("id")) for s in sources}
    sources += [copy.deepcopy(s) for s in sources_patch.get("add") or [] if str(s.get("id")) not in known]
    if "sources" in merged:
        merged["sources"] = sources
    for path, terms in (overlay.get("terms") or {}).items():
        parts = _split(path)
        parent = _resolve(merged, "/".join(_escape(p) for p in parts[:-1]))
        last = parts[-1] if parts else ""
        if isinstance(parent, dict) and _is_term_list(parent.get(last)):
            parent[last] = list(terms)
        elif isinstance(parent, list) and last.isdigit() and int(last) < len(parent) and _is_term_list(parent[int(last)]):
            parent[int(last)] = list(terms)
    return merged


def view(collector_id: str, version_config: dict | None) -> dict:
    """详情页用：可编辑性、合并后的来源与词表。"""
    if not editable(collector_id):
        return {"editable": False, "reason": READONLY_REASON}
    try:
        base = base_config(collector_id)
    except (OSError, ValueError):
        return {"editable": False, "reason": "读取 config.json 失败"}
    overlay = overlay_of(version_config)
    merged = apply_overlay(base, overlay)
    changed_paths = set((overlay.get("terms") or {}).keys())
    base_ids = {str(s.get("id")) for s in base.get("sources", [])}
    sources = []
    for source in merged.get("sources", []):
        item = source_view(source)
        item["added"] = item["id"] not in base_ids
        item["changed"] = item["id"] in (overlay.get("sources", {}).get("set") or {})
        sources.append(item)
    groups = [{**g, "changed": g["path"] in changed_paths} for g in term_groups(merged)]
    return {"editable": True, "sources": sources, "term_groups": groups, "tiers": tiers_of(base),
            "removed": sorted(set(overlay.get("sources", {}).get("remove") or []) & base_ids)}


# ---------------------------------------------------------------- 校验并计算覆盖层

def _term(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise BuiltinConfigError(field, "词必须是文本")
    text = value.strip()
    if not text:
        raise BuiltinConfigError(field, "词不能为空")
    if len(text) > MAX_TERM_LEN:
        raise BuiltinConfigError(field, f"单个词不能超过 {MAX_TERM_LEN} 个字符")
    try:
        re.compile(text)
    except re.error as exc:
        raise BuiltinConfigError(field, f"“{text}”不是有效的正则写法（{exc.msg}）；部分采集器把词当正则匹配") from exc
    return text


def _url(value: Any, field: str, checker: _HostChecker) -> str:
    url = str(value or "").strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018  触发端口格式校验
    except ValueError as exc:
        raise BuiltinConfigError(field, "网址格式无效") from exc
    if parts.scheme not in ("http", "https") or not host:
        raise BuiltinConfigError(field, "网址必须是 http 或 https 开头的完整地址")
    if parts.username is not None or parts.password is not None:
        raise BuiltinConfigError(field, "网址不能包含用户名或密码")
    problem = checker.problem(host)
    if problem:
        raise BuiltinConfigError(field, problem)
    return url


def build_overlay(collector_id: str, desired: dict, *,
                  resolve: Callable[[str], list[str]] = default_resolve) -> dict:
    """页面提交的期望状态（完整来源列表 + 改过的词表）→ 相对 config.json 的覆盖层。

    desired = {"sources": [{id,name,url,tier,enabled,note,template?}], "terms": {路径: [词]}}
    """
    if not editable(collector_id):
        raise BuiltinConfigError("config", READONLY_REASON)
    if not isinstance(desired, dict):
        raise BuiltinConfigError("config", "必须是对象")
    base = base_config(collector_id)
    base_sources = {str(s.get("id")): s for s in base.get("sources", [])}
    tiers = set(tiers_of(base))
    checker = _HostChecker(resolve)
    overlay: dict = {}

    rows = desired.get("sources")
    if rows is not None:
        if not isinstance(rows, list) or len(rows) > MAX_SOURCES:
            raise BuiltinConfigError("sources", f"来源必须是数组，最多 {MAX_SOURCES} 个")
        seen: set[str] = set()
        changes: dict[str, dict] = {}
        added: list[dict] = []
        for index, row in enumerate(rows):
            field = f"sources[{index}]"
            if not isinstance(row, dict):
                raise BuiltinConfigError(field, "来源必须是对象")
            sid = str(row.get("id") or "").strip()
            if not SOURCE_ID.fullmatch(sid):
                raise BuiltinConfigError(f"{field}.id", "来源 ID 只能用字母、数字、- 和 _，最长 80 个字符")
            if sid in seen:
                raise BuiltinConfigError(f"{field}.id", f"来源 ID 重复：{sid}")
            seen.add(sid)
            if tiers and str(row.get("tier", "")) not in tiers:
                raise BuiltinConfigError(f"{field}.tier", f"来源等级只能是 {'、'.join(sorted(tiers))}")
            name = str(row.get("name") or "").strip()
            if not name or len(name) > 120:
                raise BuiltinConfigError(f"{field}.name", "名称必填，最长 120 个字符")
            note = str(row.get("note") or "")[:500]
            original = base_sources.get(sid)
            if original is None:
                template = base_sources.get(str(row.get("template") or "")) or next(iter(base_sources.values()), {})
                source = {k: copy.deepcopy(v) for k, v in template.items() if k not in {"probe", "note", "tier_note"}}
                source.update({"id": sid, _name_key(template): name, "url": _url(row.get("url"), f"{field}.url", checker),
                               "enabled": bool(row.get("enabled", True)), "note": note})
                if tiers:
                    source["tier"] = row["tier"]
                added.append(source)
                continue
            current = source_view(original)
            diff = {}
            for key in SOURCE_FIELDS:
                value = {"name": name, "note": note, "enabled": bool(row.get("enabled", True))}.get(key, row.get(key))
                if (key == "note" and "note" not in original and not value) or (key == "tier" and not tiers):
                    continue
                if value != current[key]:
                    diff[key] = _url(value, f"{field}.url", checker) if key == "url" else value
            if diff:
                changes[sid] = diff
        removed = sorted(set(base_sources) - seen)
        if changes or added or removed:
            overlay["sources"] = {k: v for k, v in (("set", changes), ("add", added), ("remove", removed)) if v}

    terms = desired.get("terms")
    if terms is not None:
        if not isinstance(terms, dict):
            raise BuiltinConfigError("terms", "词表必须是对象")
        known = {g["path"]: g["terms"] for g in term_groups(base)}
        replaced = {}
        for path, values in terms.items():
            field = f"terms.{path}"
            if path not in known:
                raise BuiltinConfigError(field, "词表不存在，可能已被代码移除，请刷新后再改")
            if not isinstance(values, list) or not values:
                raise BuiltinConfigError(field, "每个词表至少保留 1 个词")
            if len(values) > MAX_TERMS_PER_LIST:
                raise BuiltinConfigError(field, f"每个词表最多 {MAX_TERMS_PER_LIST} 个词")
            clean = list(dict.fromkeys(_term(v, field) for v in values))
            if clean != known[path]:
                replaced[path] = clean
        if replaced:
            overlay["terms"] = replaced
    return overlay


def validate_overlay(collector_id: str, overlay: dict) -> dict:
    """回滚到旧版本时重新校验：只保留当前 config.json 里仍存在的词表路径。"""
    if not editable(collector_id):
        raise BuiltinConfigError("config", READONLY_REASON)
    known = {g["path"] for g in term_groups(base_config(collector_id))}
    clean = copy.deepcopy(overlay)
    if clean.get("terms"):
        clean["terms"] = {p: t for p, t in clean["terms"].items() if p in known}
        if not clean["terms"]:
            clean.pop("terms")
    return clean


def source_count(collector_id: str, version_config: dict | None) -> int | None:
    if not editable(collector_id):
        return None
    try:
        return len(apply_overlay(base_config(collector_id), overlay_of(version_config)).get("sources", []))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 运行时

def _current_overlays() -> dict[str, dict]:
    from .store import db
    with db() as conn:
        rows = conn.execute(
            "SELECT p.id, v.config FROM collection_pipelines p JOIN collection_pipeline_versions v "
            "ON v.pipeline_id=p.id AND v.version=p.current_version AND NOT v.is_deleted "
            "WHERE p.kind='builtin' AND NOT p.is_deleted").fetchall()
    return {r["id"]: overlay_of(r["config"]) for r in rows if r["id"] in EDITABLE and overlay_of(r["config"])}


def with_overrides(registry: list[dict], root: Path | None = None,
                   overlays: dict[str, dict] | None = None) -> list[dict]:
    """给有覆盖层的内置采集器写合并后的配置，并经 env 传路径；读库失败时原样返回（按代码配置运行）。"""
    try:
        overlays = _current_overlays() if overlays is None else overlays
    except Exception as exc:  # noqa: BLE001 - 数据库不可用时整批照常按代码配置运行
        log.warning("读取内置采集器覆盖配置失败，本次按代码配置运行：%s", exc)
        return registry
    root = root or collector_data_root()
    result = []
    for item in registry:
        overlay = overlays.get(item.get("id"))
        if overlay and editable(item.get("id", "")):
            try:
                merged = apply_overlay(base_config(item["id"]), overlay)
                target = root / item["id"] / "state" / OVERRIDE_FILE
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(".tmp")
                temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(target)
                item = {**item, "env": {**(item.get("env") or {}), OVERRIDE_ENV: str(target)}}
            except Exception as exc:  # noqa: BLE001 - 单个覆盖坏了不影响其它采集器
                log.warning("内置采集器 %s 覆盖配置无法生成，本次按代码配置运行：%s", item.get("id"), exc)
        result.append(item)
    return result
