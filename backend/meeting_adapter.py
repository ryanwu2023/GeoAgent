"""Import the meeting-calendar collector without flattening its lifecycle semantics."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable

from .store import digest


STATUS_LABELS = {
    "confirmed": "已确认", "scheduled": "日期已定", "arranged": "已安排",
    "proposed": "已预告", "reported": "媒体报道", "unconfirmed": "暂无确认日期",
    "denied": "已否认", "postponed": "已推迟", "canceled": "已取消",
    "lapsed": "过期未证实", "held": "已举行",
}
INACTIVE = {"denied", "postponed", "canceled", "lapsed", "held"}
TRACK_LABELS = {
    "us-ir": "美伊", "ru-ua": "俄乌", "il-ir": "以伊 / 胡塞",
    "cn-eu": "中国—欧洲", "asia": "亚洲与印太", "mideast": "中东（其他）",
    "multi": "多边峰会与其他",
}
LOCATION_POINTS = {
    "莫斯科": (37.6173, 55.7558), "北京": (116.4074, 39.9042), "深圳": (114.0579, 22.5431),
    "多哈": (51.5310, 25.2854), "日内瓦": (6.1432, 46.2044), "纽约": (-74.0060, 40.7128),
    "华盛顿": (-77.0369, 38.9072), "德黑兰": (51.3890, 35.6892), "安卡拉": (32.8597, 39.9334),
    "伊斯坦布尔": (28.9784, 41.0082), "阿布扎比": (54.3773, 24.4539), "利雅得": (46.6753, 24.7136),
    "新德里": (77.2090, 28.6139), "东京": (139.6917, 35.6895), "首尔": (126.9780, 37.5665),
    "新加坡": (103.8198, 1.3521), "雅加达": (106.8456, -6.2088), "合肥": (117.2272, 31.8206),
    "马德里": (-3.7038, 40.4168), "华沙": (21.0122, 52.2297),
}


def _load_people(project: Path) -> dict[str, dict]:
    """Load display metadata even when runtime publishing contains output only."""
    from .paths import collector_source_root

    candidates = [
        project / "config.json",
        collector_source_root() / "meeting-calendar-monitor" / "config.json",
    ]
    for config_file in candidates:
        if not config_file.is_file():
            continue
        try:
            config = json.loads(config_file.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        people = {
            str(person["id"]): person
            for person in config.get("persons", [])
            if isinstance(person, dict) and person.get("id")
        }
        if people:
            return people
    return {}


def _person_label(person: dict) -> str:
    name = str(person.get("name") or "人物信息待补充")
    role = str(person.get("role") or "职务待补充")
    return f"{name}（{role}）"


def _topics(item: dict) -> list[str]:
    track = item.get("track")
    if track == "us-ir" or track == "il-ir":
        return ["usiran"]
    if track == "ru-ua":
        return ["ukraine"]
    actors = set(item.get("actors") or [])
    topics = []
    if actors & {"iran", "israel", "houthis", "usa"}:
        topics.append("usiran")
    if actors & {"russia", "ukraine"}:
        topics.append("ukraine")
    return topics


def _point(location: str) -> list[dict]:
    coordinates = LOCATION_POINTS.get(location.strip()) if location else None
    if not coordinates:
        return []
    lon, lat = coordinates
    return [{
        "lon": lon, "lat": lat, "label": location, "precision": "explicit",
        "role": "计划会晤地点，不代表实时位置", "evidence_text": location,
        "location_method": "会晤日历明确城市与受控地点字典匹配",
    }]


def _tier(value) -> str:
    if value == "B":
        return "B"
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "未分级"
    return f"T{number}" if 1 <= number <= 4 else "未分级"


def _best_source(item: dict) -> dict:
    sources = [source for source in item.get("sources", []) if isinstance(source, dict)]
    if not sources:
        return {}
    def rank(source: dict) -> int:
        tier = _tier(source.get("tier"))
        if tier == "B":
            return 5
        return int(tier[1:]) if tier.startswith("T") and tier[1:].isdigit() else 99
    return min(sources, key=lambda source: (rank(source), -(len(source.get("quote") or ""))))


def load_meetings(
    root: Path,
    read: Callable,
    manifests: list[dict],
    records: dict[str, dict],
    sources: list[dict],
) -> list[dict]:
    project = root / "meeting-calendar-monitor"
    file = project / "output" / "LATEST.jsonl"
    if not file.is_file():
        return []
    content = read(file)
    archive = manifests[-1]["archive"]
    people = _load_people(project)

    meetings = []
    source_counts: dict[str, int] = {}
    for line_no, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        event_id = str(item.get("id") or f"row-{line_no}")
        topics = _topics(item)
        persons = []
        for person_id in item.get("persons") or []:
            person = people.get(person_id, {})
            persons.append({
                "id": person_id, "name": person.get("zh") or "人物信息待补充",
                "role": person.get("role") or "职务待补充", "country": person.get("country") or "国家待补充",
            })
        best = _best_source(item)
        status = str(item.get("status") or "unconfirmed")
        non_meeting = bool(item.get("family") == "election" or "非会晤" in str(item.get("title") or ""))
        evidence_id = "meeting-" + digest([event_id, item.get("first_seen") or ""])
        points = _point(str(item.get("location") or ""))
        dimensions = {topic: ("regional" if topic == "usiran" and item.get("track") == "il-ir" else "diplomacy") for topic in topics}
        summary = "；".join(part for part in [
            "、".join(_person_label(person) for person in persons),
            str(item.get("location") or "地点未定"), STATUS_LABELS.get(status, status),
        ] if part)
        record = {
            "id": evidence_id, "source_id": str(best.get("source") or "meeting-calendar-monitor"),
            "source_name": str(best.get("source") or "会晤日历"), "tier": _tier(best.get("tier")),
            "publisher": str(best.get("outlet") or best.get("source") or ""),
            "title": str(item.get("title") or "会晤安排"), "text": summary,
            "url": str(best.get("url") or ""), "published_at": best.get("published") or None,
            "fetched_at": item.get("last_seen") or None, "first_seen": item.get("first_seen") or None,
            "event_at": item.get("date_start") or None, "party": "、".join(item.get("actors") or []) or "未标注",
            "nature": "政治日程" if non_meeting else "计划会晤",
            "verification": STATUS_LABELS.get(status, status), "classification": "会晤日历结构化记录",
            "topics": topics, "dimensions": dimensions,
            "dimension_candidates": {topic: [dimension] for topic, dimension in dimensions.items()},
            # Meeting locations use their own map layer. Keeping them out of
            # generic evidence points prevents a planned venue from being
            # mistaken for a current military or news location.
            "scope": "会晤日历", "points": [], "origin_id": event_id,
            "adapters": ["meeting-calendar-monitor"], "raw_refs": [archive],
            "event_category": "diplomacy", "situation_signal": "unknown",
            "signal_basis": "计划会晤及其确认状态，不直接代表冲突升级或缓和。",
            "upstream_extractions": [{"monitor": "meeting-calendar-monitor", "values": {
                "event_id": event_id, "status": status, "precision": item.get("precision"),
                "location": item.get("location"), "persons": item.get("persons") or [],
                "track": item.get("track"), "conflict": bool(item.get("conflict")),
                "non_meeting": non_meeting,
            }}],
        }
        records[evidence_id] = record
        meeting = {
            "id": event_id, "evidence_id": evidence_id, "title": record["title"],
            "family": item.get("family") or "meeting", "track": item.get("track") or "multi",
            "track_label": TRACK_LABELS.get(item.get("track"), item.get("track") or "其他"),
            "topics": topics, "date_start": item.get("date_start") or "", "date_end": item.get("date_end") or "",
            "precision": item.get("precision") or "tbd", "date_raw": item.get("date_raw") or "",
            "location": item.get("location") or "", "location_raw": item.get("location_raw") or "",
            "points": points, "persons": persons, "actors": item.get("actors") or [],
            "status": status, "status_label": STATUS_LABELS.get(status, status),
            "inactive": status in INACTIVE, "conflict": bool(item.get("conflict")),
            "baseline": bool(item.get("baseline")), "baseline_confirmed": bool(item.get("baseline_confirmed")),
            "baseline_note": item.get("baseline_note") or "", "non_meeting": non_meeting,
            "first_seen": item.get("first_seen") or None, "last_seen": item.get("last_seen") or None,
            "claims": item.get("claims") or [], "sources": item.get("sources") or [], "updates": item.get("updates") or [],
        }
        meetings.append(meeting)
        for source in meeting["sources"]:
            key = str(source.get("name") or "公开来源")
            source_counts[key] = source_counts.get(key, 0) + 1

    for name, count in sorted(source_counts.items()):
        sources.append({
            "id": "meeting-calendar-monitor:" + digest(name), "monitor": "meeting-calendar-monitor",
            "name": name, "state": "imported", "count": count,
            "latest": max((m.get("last_seen") or "" for m in meetings), default="") or None,
        })
    meetings.sort(key=lambda item: (item.get("date_start") or "9999-12-31", item["id"]))
    return meetings
