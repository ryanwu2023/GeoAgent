"""Persistent, deduplicated storage for the internal macro message source."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .store import data_dir


CHINA_TZ = timezone(timedelta(hours=8))
SCHEMA_VERSION = "1"
DEFAULT_FETCH_TIMES = ("08:00", "14:00", "20:00")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def message_db_path() -> Path:
    configured = os.getenv("HT_MACRO_DB_PATH", "").strip()
    return Path(configured) if configured else data_dir() / "ht-macro" / "messages.sqlite3"


def raw_archive_root() -> Path:
    configured = os.getenv("HT_MACRO_RAW_ROOT", "").strip()
    return Path(configured) if configured else data_dir() / "ht-macro" / "raw"


def status_path() -> Path:
    configured = os.getenv("HT_MACRO_STATUS_PATH", "").strip()
    return Path(configured) if configured else data_dir() / "ht-macro" / "status.json"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or message_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize(path: Path | None = None) -> Path:
    target = path or message_db_path()
    with _connect(target) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                public_id TEXT PRIMARY KEY,
                dedupe_key TEXT NOT NULL UNIQUE,
                upstream_unique_id TEXT,
                external_unique_id TEXT,
                upstream_message_id TEXT,
                message_type TEXT,
                title TEXT NOT NULL DEFAULT '',
                content_abstract TEXT NOT NULL DEFAULT '',
                ai_title TEXT NOT NULL DEFAULT '',
                ai_short TEXT NOT NULL DEFAULT '',
                ai_content TEXT NOT NULL DEFAULT '',
                message_at TEXT,
                message_time_ms INTEGER,
                upstream_updated_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                text_type TEXT,
                text_class TEXT,
                industry_json TEXT NOT NULL DEFAULT '[]',
                primary_tag TEXT,
                tag_code TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                group_name TEXT,
                sender_name TEXT,
                receive_time TEXT,
                origin_type TEXT,
                is_important INTEGER,
                topics_json TEXT NOT NULL DEFAULT '[]',
                relevance_status TEXT NOT NULL DEFAULT 'unclassified',
                extra_info_error TEXT,
                content_hash TEXT NOT NULL,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_message_at_idx ON messages(message_at DESC);
            CREATE INDEX IF NOT EXISTS messages_group_idx ON messages(group_name);
            CREATE INDEX IF NOT EXISTS messages_class_idx ON messages(text_class);
            CREATE INDEX IF NOT EXISTS messages_tag_idx ON messages(primary_tag);
            CREATE TABLE IF NOT EXISTS message_tags (
                public_id TEXT NOT NULL REFERENCES messages(public_id) ON DELETE CASCADE,
                tag_name TEXT NOT NULL,
                tag_type TEXT,
                classification TEXT,
                PRIMARY KEY(public_id, tag_name)
            );
            CREATE TABLE IF NOT EXISTS collection_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                pages INTEGER NOT NULL DEFAULT 0,
                received INTEGER NOT NULL DEFAULT 0,
                inserted INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,
                duplicates INTEGER NOT NULL DEFAULT 0,
                upstream_total INTEGER,
                error_type TEXT,
                error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS collection_runs_started_idx ON collection_runs(started_at DESC);
            """
        )
        connection.execute(
            "INSERT INTO schema_meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SCHEMA_VERSION,),
        )
    return target


@contextmanager
def database(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    initialize(path)
    connection = _connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _canonical(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _message_time(value: Any) -> tuple[int | None, str | None]:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None, None
    try:
        stamp = datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return milliseconds, None
    return milliseconds, stamp.astimezone(CHINA_TZ).isoformat()


def _extra_info(value: Any) -> tuple[dict[str, Any], str | None]:
    if isinstance(value, dict):
        return value, None
    if not value:
        return {}, None
    try:
        parsed = json.loads(str(value))
        return (parsed, None) if isinstance(parsed, dict) else ({}, "extraInfo 不是对象")
    except (TypeError, ValueError):
        return {}, "extraInfo 不是有效 JSON"


TOPIC_TERMS = {
    "usiran": (
        "美伊", "伊朗", "霍尔木兹", "胡塞", "也门", "以色列", "海湾", "沙特", "中东",
        "iran", "houthi", "hormuz", "israel", "yemen",
    ),
    "ukraine": (
        "俄乌", "乌克兰", "俄罗斯", "普京", "泽连斯基", "顿巴斯", "克里米亚", "黑海",
        "ukraine", "russia", "zelensky", "putin",
    ),
    "asset_macro": (
        "布伦特", "原油", "黄金", "美债", "美元", "通胀", "利率", "股指", "天然气", "航运",
        "brent", "wti", "treasury", "inflation",
    ),
}

_USIRAN_DIRECT = re.compile(r"美伊|伊朗|德黑兰|霍尔木兹|胡塞|\biran\w*\b|\btehran\b|\bhormuz\b|\bhouthi\w*\b", re.I)
_UKRAINE_DIRECT = re.compile(r"俄乌|乌克兰|基辅|泽连斯基|顿巴斯|顿涅茨克|赫尔松|\bukrain\w*\b|\bkyiv\b|\bzelensk\w*\b|\bdonbas\w*\b", re.I)
_RUSSIA = re.compile(r"俄罗斯|莫斯科|普京|\brussia\w*\b|\bmoscow\b|\bputin\w*\b", re.I)
_WAR_ACTION = re.compile(r"停火|和谈|前线|战线|袭击|打击|导弹|无人机|防空|援助|对乌|入侵|ceasefire|frontline|strike|missile|drone|aid", re.I)
_REGIONAL_MARKET = re.compile(r"中东|海湾|红海|以色列|沙特|原油|石油|黄金|航运|油价|middle east|gulf|oil|gold|shipping", re.I)


def _message_search_text(message: dict[str, Any]) -> str:
    return " ".join(
        _text(message.get(key))
        for key in (
            "title", "content_abstract", "ai_title", "ai_short", "ai_content",
            "primary_tag", "group_name", "text_class",
        )
    ).lower()


def message_topic_lane(query: str, message: dict[str, Any]) -> str:
    """Classify internal content using its real text, never injected topic words."""
    if str(message.get("relevance_status") or "").lower() == "irrelevant":
        return "rejected"
    lowered = str(query or "").lower()
    text = _message_search_text(message)
    asks_usiran = bool(re.search(r"美伊|伊朗|iran|霍尔木兹|胡塞", lowered, re.I))
    asks_ukraine = bool(re.search(r"俄乌|乌克兰|ukrain|俄罗斯|russia", lowered, re.I))
    if asks_usiran:
        if _USIRAN_DIRECT.search(text):
            return "core"
        return "cross_topic" if _REGIONAL_MARKET.search(text) else "rejected"
    if asks_ukraine:
        if _UKRAINE_DIRECT.search(text):
            return "core"
        if _RUSSIA.search(text) and _WAR_ACTION.search(text) and not _USIRAN_DIRECT.search(text):
            return "core"
        topics = {str(item) for item in message.get("topics", []) if str(item)}
        if _RUSSIA.search(text) or "ukraine" in topics or _REGIONAL_MARKET.search(text):
            return "cross_topic"
        return "rejected"
    return "core"


def internal_query_terms(query: str) -> list[str]:
    """Split a natural-language request into bounded entity, event and asset probes."""
    value = str(query or "")
    terms: list[str] = []
    if "美伊" in value:
        terms.extend(("伊朗", "美国"))
    if "俄乌" in value:
        terms.extend(("乌克兰", "俄罗斯"))
    vocabulary = (
        "伊朗", "美国", "霍尔木兹", "胡塞", "以色列", "沙特", "中东",
        "乌克兰", "俄罗斯", "泽连斯基", "普京", "北约", "欧盟",
        "谈判", "会谈", "停火", "斡旋", "协议", "制裁", "援助",
        "袭击", "打击", "战线", "导弹", "无人机", "防空", "撤离",
        "原油", "布伦特", "黄金", "美债", "美元", "天然气", "航运",
    )
    terms.extend(term for term in vocabulary if term in value)
    return list(dict.fromkeys(terms))[:16]


def _topics(record: dict[str, Any]) -> list[str]:
    haystack = " ".join(
        _text(record.get(key))
        for key in ("title", "contentAbstract", "aiTitle", "aiShort", "aiContent", "tag", "textClass")
    ).lower()
    return [topic for topic, terms in TOPIC_TERMS.items() if any(term.lower() in haystack for term in terms)]


def normalize_message(record: dict[str, Any], *, seen_at: str | None = None) -> dict[str, Any]:
    seen = seen_at or utc_now()
    extra, extra_error = _extra_info(record.get("extraInfo"))
    tags = [item for item in _json_list(record.get("tags")) if isinstance(item, dict)]
    milliseconds, message_at = _message_time(record.get("time"))
    title = _text(record.get("title"))
    abstract = _text(record.get("contentAbstract"))
    unique_id = _text(record.get("unqMsgId"))
    external_id = _text(record.get("externalUnqMsgId"))
    message_type = _text(record.get("msgType"))
    message_id = _text(record.get("msgId"))
    if unique_id:
        dedupe_key = "unq:" + unique_id
    elif external_id:
        dedupe_key = "external:" + external_id
    elif message_type and message_id:
        dedupe_key = f"message:{message_type}:{message_id}"
    else:
        fingerprint_basis = "|".join(
            (_canonical(title), _canonical(abstract), _text(milliseconds), _text(extra.get("groupname")))
        )
        dedupe_key = "fingerprint:" + hashlib.sha256(fingerprint_basis.encode("utf-8")).hexdigest()
    public_id = "htm-" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:24]
    content_hash = hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    topics = _topics(record)
    return {
        "public_id": public_id,
        "dedupe_key": dedupe_key,
        "upstream_unique_id": unique_id or None,
        "external_unique_id": external_id or None,
        "upstream_message_id": message_id or None,
        "message_type": message_type or None,
        "title": title,
        "content_abstract": abstract,
        "ai_title": _text(record.get("aiTitle")),
        "ai_short": _text(record.get("aiShort")),
        "ai_content": _text(record.get("aiContent")),
        "message_at": message_at,
        "message_time_ms": milliseconds,
        "upstream_updated_at": _text(record.get("updateTime")) or None,
        "first_seen_at": seen,
        "last_seen_at": seen,
        "text_type": _text(record.get("textType")) or None,
        "text_class": _text(record.get("textClass")) or None,
        "industry_json": json.dumps(_json_list(record.get("industry")), ensure_ascii=False),
        "primary_tag": _text(record.get("tag")) or None,
        "tag_code": _text(record.get("tagCode")) or None,
        "tags_json": json.dumps(tags, ensure_ascii=False),
        "group_name": _text(extra.get("groupname")) or None,
        "sender_name": _text(extra.get("sendername")) or None,
        "receive_time": _text(extra.get("receiveTime")) or None,
        "origin_type": _text(extra.get("msgOriginType")) or None,
        "is_important": record.get("isImportant") if record.get("isImportant") in (0, 1) else None,
        "topics_json": json.dumps(topics, ensure_ascii=False),
        "relevance_status": "relevant" if topics else "unclassified",
        "extra_info_error": extra_error,
        "content_hash": content_hash,
        "raw_json": json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        "tag_rows": [
            {
                "tag_name": _text(item.get("tagName")),
                "tag_type": _text(item.get("tagType")) or None,
                "classification": _text(item.get("classification")) or None,
            }
            for item in tags
            if _text(item.get("tagName"))
        ],
    }


MESSAGE_COLUMNS = (
    "public_id", "dedupe_key", "upstream_unique_id", "external_unique_id", "upstream_message_id",
    "message_type", "title", "content_abstract", "ai_title", "ai_short", "ai_content", "message_at",
    "message_time_ms", "upstream_updated_at", "first_seen_at", "last_seen_at", "text_type", "text_class",
    "industry_json", "primary_tag", "tag_code", "tags_json", "group_name", "sender_name", "receive_time",
    "origin_type", "is_important", "topics_json", "relevance_status", "extra_info_error", "content_hash", "raw_json",
)


def upsert_messages(records: list[dict[str, Any]], *, path: Path | None = None, seen_at: str | None = None) -> dict[str, int]:
    result = {"received": len(records), "inserted": 0, "updated": 0, "duplicates": 0}
    with database(path) as connection:
        for raw in records:
            item = normalize_message(raw, seen_at=seen_at)
            existing = connection.execute(
                "SELECT public_id,content_hash FROM messages WHERE dedupe_key=?", (item["dedupe_key"],)
            ).fetchone()
            tag_rows = item.pop("tag_rows")
            if existing is None:
                placeholders = ",".join("?" for _ in MESSAGE_COLUMNS)
                connection.execute(
                    f"INSERT INTO messages({','.join(MESSAGE_COLUMNS)}) VALUES({placeholders})",
                    tuple(item[column] for column in MESSAGE_COLUMNS),
                )
                result["inserted"] += 1
            else:
                item["public_id"] = existing["public_id"]
                item["first_seen_at"] = connection.execute(
                    "SELECT first_seen_at FROM messages WHERE public_id=?", (existing["public_id"],)
                ).fetchone()[0]
                if existing["content_hash"] == item["content_hash"]:
                    connection.execute(
                        "UPDATE messages SET last_seen_at=? WHERE public_id=?",
                        (item["last_seen_at"], item["public_id"]),
                    )
                    result["duplicates"] += 1
                else:
                    assignments = ",".join(f"{column}=?" for column in MESSAGE_COLUMNS if column != "public_id")
                    connection.execute(
                        f"UPDATE messages SET {assignments} WHERE public_id=?",
                        tuple(item[column] for column in MESSAGE_COLUMNS if column != "public_id") + (item["public_id"],),
                    )
                    result["updated"] += 1
            connection.execute("DELETE FROM message_tags WHERE public_id=?", (item["public_id"],))
            connection.executemany(
                "INSERT OR IGNORE INTO message_tags(public_id,tag_name,tag_type,classification) VALUES(?,?,?,?)",
                [(item["public_id"], tag["tag_name"], tag["tag_type"], tag["classification"]) for tag in tag_rows],
            )
    return result


def record_run_start(run_id: str, *, path: Path | None = None, started_at: str | None = None) -> None:
    with database(path) as connection:
        connection.execute(
            "INSERT INTO collection_runs(run_id,started_at,status) VALUES(?,?,?)",
            (run_id, started_at or utc_now(), "running"),
        )


def record_run_finish(run_id: str, result: dict[str, Any], *, path: Path | None = None) -> None:
    with database(path) as connection:
        connection.execute(
            """UPDATE collection_runs SET finished_at=?,status=?,pages=?,received=?,inserted=?,updated=?,
               duplicates=?,upstream_total=?,error_type=?,error_message=? WHERE run_id=?""",
            (
                utc_now(), result.get("status", "succeeded"), int(result.get("pages", 0)),
                int(result.get("received", 0)), int(result.get("inserted", 0)), int(result.get("updated", 0)),
                int(result.get("duplicates", 0)), result.get("upstream_total"), result.get("error_type"),
                _text(result.get("error_message"))[:500] or None, run_id,
            ),
        )


def _validate_date(value: str, field: str) -> None:
    if not value:
        return
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} 必须为 YYYY-MM-DD") from exc


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _clip(value: str | None, limit: int = 500) -> tuple[str, bool]:
    text = value or ""
    return text[:limit], len(text) > limit


def _public_message(row: sqlite3.Row, *, detail: bool) -> dict[str, Any]:
    abstract, abstract_truncated = _clip(row["content_abstract"])
    ai_short, ai_short_truncated = _clip(row["ai_short"])
    result = {
        "id": row["public_id"],
        "title": row["title"],
        "content_abstract": row["content_abstract"] if detail else abstract,
        "ai_title": row["ai_title"],
        "ai_short": row["ai_short"] if detail else ai_short,
        "message_at": row["message_at"],
        "upstream_updated_at": row["upstream_updated_at"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "text_type": row["text_type"],
        "text_class": row["text_class"],
        "primary_tag": row["primary_tag"],
        "tags": json.loads(row["tags_json"] or "[]"),
        "group_name": row["group_name"],
        "sender_name": row["sender_name"],
        "receive_time": row["receive_time"],
        "origin_type": row["origin_type"],
        "is_important": row["is_important"],
        "topics": json.loads(row["topics_json"] or "[]"),
        "relevance_status": row["relevance_status"],
        "source": "华泰宏观消息源",
        "verification": "内部消息归档，内容须结合原始信源核验",
        "detail_included": detail,
        "detail_tool": None if detail else "geo_internal_message_get",
    }
    if detail:
        result["ai_content"] = row["ai_content"]
    else:
        result["preview_truncated"] = abstract_truncated or ai_short_truncated or bool(row["ai_content"])
    return result


def search_messages(
    *, query: str = "", start: str = "", end: str = "", group_name: str = "", tag: str = "",
    text_class: str = "", offset: int = 0, limit: int = 20, path: Path | None = None,
) -> dict[str, Any]:
    _validate_date(start, "start")
    _validate_date(end, "end")
    if start and end and start > end:
        raise ValueError("end 不能早于 start")
    if offset < 0:
        raise ValueError("offset 不能为负数")
    if not 1 <= limit <= 50:
        raise ValueError("limit 必须在 1 到 50 之间")
    clauses: list[str] = []
    params: list[Any] = []
    if query.strip():
        pattern = _like(query.strip())
        columns = ("title", "content_abstract", "ai_title", "ai_short", "ai_content", "primary_tag", "group_name")
        clauses.append("(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in columns) + ")")
        params.extend([pattern] * len(columns))
    if start:
        clauses.append("message_at>=?")
        params.append(start + "T00:00:00+08:00")
    if end:
        clauses.append("message_at<?")
        params.append((date.fromisoformat(end) + timedelta(days=1)).isoformat() + "T00:00:00+08:00")
    for column, value in (("group_name", group_name), ("primary_tag", tag), ("text_class", text_class)):
        if value.strip():
            clauses.append(f"{column} LIKE ? ESCAPE '\\'")
            params.append(_like(value.strip()))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database(path) as connection:
        total = connection.execute("SELECT COUNT(*) FROM messages" + where, params).fetchone()[0]
        rows = connection.execute(
            "SELECT * FROM messages" + where + " ORDER BY COALESCE(message_at,last_seen_at) DESC,public_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {
        "messages": [_public_message(row, detail=False) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
        "query_scope": {
            "query": query, "start": start, "end": end, "group_name": group_name,
            "tag": tag, "text_class": text_class,
        },
    }


def get_message(public_id: str, *, path: Path | None = None) -> dict[str, Any] | None:
    with database(path) as connection:
        row = connection.execute("SELECT * FROM messages WHERE public_id=?", (public_id,)).fetchone()
    return _public_message(row, detail=True) if row else None


def message_stats(*, path: Path | None = None) -> dict[str, Any]:
    with database(path) as connection:
        total = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        relevant = connection.execute("SELECT COUNT(*) FROM messages WHERE relevance_status='relevant'").fetchone()[0]
        latest = connection.execute("SELECT MAX(message_at) FROM messages").fetchone()[0]
        run = connection.execute(
            "SELECT * FROM collection_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        classes = [dict(row) for row in connection.execute(
            "SELECT COALESCE(text_class,'未分类') AS name,COUNT(*) AS count FROM messages GROUP BY text_class ORDER BY count DESC LIMIT 20"
        )]
        tags = [dict(row) for row in connection.execute(
            "SELECT tag_name AS name,COUNT(*) AS count FROM message_tags GROUP BY tag_name ORDER BY count DESC LIMIT 20"
        )]
    return {
        "configured": bool(os.getenv("HT_MACRO_BASE_URL", "").strip()),
        "enabled": os.getenv("HT_MACRO_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        "records": total,
        "relevant_records": relevant,
        "latest_message_at": latest,
        "latest_run": dict(run) if run else None,
        "text_classes": classes,
        "tags": tags,
        "basis": "本地去重消息数据库的实时统计；消息及上游 AI 摘要不等于已核验事实。",
    }


def parse_fetch_times(value: str | None) -> tuple[datetime_time, ...]:
    raw = [item.strip() for item in (value or ",".join(DEFAULT_FETCH_TIMES)).split(",") if item.strip()]
    if not raw:
        raise ValueError("HT_MACRO_FETCH_TIMES 至少包含一个时点")
    parsed: list[datetime_time] = []
    for item in raw:
        try:
            parsed.append(datetime.strptime(item, "%H:%M").time())
        except ValueError as exc:
            raise ValueError(f"非法采集时点: {item}") from exc
    return tuple(sorted(set(parsed)))


def next_scheduled_fetch(now: datetime, fetch_times: tuple[datetime_time, ...]) -> datetime:
    local = now.astimezone(CHINA_TZ)
    for point in fetch_times:
        candidate = datetime.combine(local.date(), point, CHINA_TZ)
        if candidate > local:
            return candidate
    return datetime.combine(local.date() + timedelta(days=1), fetch_times[0], CHINA_TZ)
