"""配置化公开来源采集器：美伊与俄乌新闻、声明和政策资料。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from filtering import classify_candidate, load_config  # noqa: E402
from web import (  # noqa: E402
    Candidate,
    FetchResult,
    PolicyHttpClient,
    canonicalize_url,
    discover_candidates,
    is_restricted_document,
    parse_public_article,
)


ACCESS_RANK = {
    "unavailable": 0,
    "title_only": 1,
    "public_metadata": 2,
    "restricted_metadata_only": 2,
    "public_summary": 3,
    "public_fulltext": 4,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: str, length: int = 24) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _canonical_title(value: object) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff\u0400-\u04ff\u0600-\u06ff]+", " ", str(value or "").casefold()).strip()


def _day(value: object) -> str:
    match = re.search(r"20\d{2}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else ""


def _origin_id(record: dict[str, Any]) -> str:
    existing = str(record.get("origin_id") or "")
    if existing:
        return existing
    basis = _canonical_title(record.get("title")) + "|" + _day(record.get("published"))
    if not basis.strip("|"):
        basis = canonicalize_url(str(record.get("link") or ""))
    return "geo-source:" + _digest(basis)


def _source_mention(record: dict[str, Any]) -> dict[str, str]:
    return {
        "source_id": str(record.get("source_id") or ""),
        "publisher": str(record.get("publisher") or ""),
        "link": canonicalize_url(str(record.get("link") or "")),
    }


def _revalidate(record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    result = classify_candidate(
        str(record.get("title") or ""),
        str(record.get("summary") or ""),
        str(record.get("body") or ""),
        str(record.get("language") or ""),
        config,
    )
    if not result["accepted"]:
        return None
    clean = dict(record)
    clean["link"] = canonicalize_url(str(record.get("link") or ""))
    clean["topics"] = result["topics"]
    clean["dimensions"] = result["dimensions"]
    clean["categories"] = result["categories"]
    clean["matched_discovery_terms"] = result["matched_discovery_terms"]
    clean["matched_admission_terms"] = result["matched_admission_terms"]
    clean["origin_id"] = _origin_id(clean)
    clean.setdefault("aliases", [clean["link"]] if clean["link"] else [])
    clean.setdefault("source_mentions", [_source_mention(clean)])
    return clean


def merge_records(
    previous: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in [*previous, *incoming]:
        record = _revalidate(raw, config)
        if record is None:
            continue
        key = record["origin_id"]
        current = merged.get(key)
        if current is None:
            record["aliases"] = sorted({url for url in record.get("aliases", []) if url} | ({record["link"]} if record.get("link") else set()))
            record["source_mentions"] = list({_source_mention(record)["source_id"] + "|" + _source_mention(record)["link"]: _source_mention(record)}.values())
            merged[key] = record
            continue
        aliases = set(current.get("aliases", [])) | set(record.get("aliases", []))
        if record.get("link"):
            aliases.add(record["link"])
        mentions = {
            item.get("source_id", "") + "|" + item.get("link", ""): item
            for item in [*current.get("source_mentions", []), *record.get("source_mentions", []), _source_mention(record)]
        }
        preferred = record if ACCESS_RANK.get(record.get("content_access", ""), 0) > ACCESS_RANK.get(current.get("content_access", ""), 0) else current
        combined = dict(preferred)
        combined["aliases"] = sorted(url for url in aliases if url)
        combined["source_mentions"] = list(mentions.values())
        combined["first_seen"] = min(filter(None, [str(current.get("first_seen") or ""), str(record.get("first_seen") or "")]), default="")
        combined["last_seen"] = max(str(current.get("last_seen") or ""), str(record.get("last_seen") or ""))
        merged[key] = combined
    return sorted(merged.values(), key=lambda item: (str(item.get("published") or ""), item.get("id", "")), reverse=True)


def _event_category(categories: list[str]) -> str:
    if "military" in categories or "nuclear_strategic" in categories:
        return "military"
    if "diplomacy" in categories:
        return "diplomacy"
    if "sanctions" in categories:
        return "policy"
    if "energy_transport" in categories:
        return "energy"
    if "defense_spending" in categories or "aid_support" in categories:
        return "support"
    return "domestic"


def _verification(source: dict[str, Any], content_access: str) -> str:
    if content_access in {"title_only", "public_metadata", "restricted_metadata_only"}:
        return "仅取得公开标题或摘要，属于待回源核验线索"
    if source.get("tier") == "T1":
        return "官方原文已采集；仅证明该机构作出声明，声明内容仍需交叉核验"
    return "媒体公开内容已采集，报道内容待交叉核验"


def _source_error_state(result: FetchResult) -> str:
    """将访问策略/网关限制与采集器自身技术故障分开。"""
    if result.error_type == "robots_disallowed":
        return "restricted"
    if result.status in {401, 403} or 300 <= result.status < 400:
        return "restricted"
    return "failed"


def _raw_write(output_dir: Path, run_tag: str, source_id: str, name: str, body: str) -> str:
    relative = Path("raw") / run_tag / source_id / name
    target = output_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return relative.as_posix()


def _record_from_candidate(
    candidate: Candidate,
    source: dict[str, Any],
    classification: dict[str, Any],
    stamp: str,
    *,
    summary: str = "",
    body: str = "",
    published: str = "",
    content_access: str,
    raw_files: list[str] | None = None,
) -> dict[str, Any]:
    link = canonicalize_url(candidate.url)
    record = {
        "id": _digest(source["id"] + "|" + link),
        "title": candidate.title,
        "summary": body or summary or candidate.summary,
        "link": link,
        "source_id": source["id"],
        "source_name": source["name_zh"],
        "publisher": source["publisher"],
        "tier": source["tier"],
        "language": source.get("language", ""),
        "published": published or candidate.published or None,
        "event_at": None,
        "fetched_at": stamp,
        "first_seen": stamp,
        "last_seen": stamp,
        "topics": classification["topics"],
        "dimensions": classification["dimensions"],
        "categories": classification["categories"],
        "event_category": _event_category(classification["categories"]),
        "nature": "官方声明" if source.get("tier") == "T1" else "媒体报道",
        "verification": _verification(source, content_access),
        "discovery_method": candidate.discovery_method,
        "matched_discovery_terms": classification["matched_discovery_terms"],
        "matched_admission_terms": classification["matched_admission_terms"],
        "content_access": content_access,
        "raw_files": raw_files or [],
    }
    record["origin_id"] = _origin_id(record)
    record["aliases"] = [link]
    record["source_mentions"] = [_source_mention(record)]
    return record


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_collection(
    config_path: Path,
    output_dir: Path,
    client: PolicyHttpClient | None = None,
) -> dict[str, Any]:
    config = load_config(Path(config_path))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now()
    run_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    defaults = config.get("request", {})
    http = client or PolicyHttpClient(user_agent=config["user_agent"])
    incoming: list[dict[str, Any]] = []
    statuses: dict[str, dict[str, Any]] = {}
    counts = {"candidates": 0, "accepted": 0, "excluded": 0, "errors": 0, "restricted": 0}

    for configured in config["sources"]:
        source = {**defaults, **configured}
        status = {
            "id": source["id"], "publisher": source["publisher"], "state": "no_data",
            "candidates": 0, "accepted": 0, "excluded": 0, "http_statuses": [], "error": None,
        }
        source_records: list[dict[str, Any]] = []
        for entry_index, entry_url in enumerate(source["entry_urls"]):
            listing = http.fetch(entry_url, source)
            status["http_statuses"].append(listing.status)
            if listing.error_type:
                status["error"] = listing.error_type
                status["state"] = _source_error_state(listing)
                counts["restricted" if status["state"] == "restricted" else "errors"] += 1
                continue
            listing_ref = _raw_write(output_dir, run_tag, source["id"], f"listing-{entry_index}.html", listing.body)
            if is_restricted_document(listing.body):
                status["state"] = "restricted"
                status["error"] = "access_challenge"
                counts["restricted"] += 1
                continue
            candidates = discover_candidates(listing.body, listing.final_url or entry_url, source)
            candidates = candidates[: int(source.get("max_candidates_per_source", 80))]
            status["candidates"] += len(candidates)
            counts["candidates"] += len(candidates)
            for candidate in candidates:
                initial = classify_candidate(candidate.title, candidate.summary, "", source.get("language", ""), config)
                if not initial["accepted"]:
                    status["excluded"] += 1
                    counts["excluded"] += 1
                    continue
                if source["content_policy"] == "metadata_only":
                    source_records.append(_record_from_candidate(
                        candidate, source, initial, stamp,
                        summary=candidate.summary, published=candidate.published,
                        content_access="public_metadata", raw_files=[listing_ref],
                    ))
                    continue
                fetched = http.fetch(candidate.url, source)
                status["http_statuses"].append(fetched.status)
                if fetched.error_type:
                    source_records.append(_record_from_candidate(
                        candidate, source, initial, stamp,
                        content_access="title_only", raw_files=[listing_ref],
                    ))
                    continue
                article = parse_public_article(fetched.body, fetched.final_url or candidate.url, source)
                title = article.title or candidate.title
                final = classify_candidate(title, article.summary or candidate.summary, article.body, source.get("language", ""), config)
                if not final["accepted"]:
                    status["excluded"] += 1
                    counts["excluded"] += 1
                    continue
                candidate = Candidate(candidate.url, title, article.summary or candidate.summary, article.published or candidate.published, candidate.discovery_method)
                raw_files = [listing_ref]
                if article.content_access in {"public_fulltext", "public_summary"}:
                    raw_files.append(_raw_write(output_dir, run_tag, source["id"], _digest(candidate.url) + ".html", fetched.body))
                source_records.append(_record_from_candidate(
                    candidate, source, final, stamp,
                    summary=article.summary, body=article.body, published=article.published,
                    content_access=article.content_access, raw_files=raw_files,
                ))
        status["accepted"] = len(source_records)
        counts["accepted"] += len(source_records)
        if source_records:
            status["state"] = "ok"
            status["error"] = None
        elif status["state"] not in {"failed", "restricted"}:
            status["state"] = "no_data"
        incoming.extend(source_records)
        statuses[source["id"]] = status

    previous_path = output_dir / "geo-v6.json"
    previous: list[dict[str, Any]] = []
    if previous_path.is_file():
        try:
            previous_payload = json.loads(previous_path.read_text(encoding="utf-8"))
            previous = previous_payload.get("records", []) if isinstance(previous_payload, dict) else []
        except (OSError, json.JSONDecodeError):
            previous = []
    records = merge_records(previous, incoming, config)
    unique_incoming = len({_origin_id(item) for item in incoming})
    failures = sum(1 for item in statuses.values() if item["state"] == "failed")
    usable = sum(1 for item in statuses.values() if item["state"] in {"ok", "no_data", "restricted"})
    collection_status = "failed" if not usable else "partial" if failures else "ok"
    payload = {
        "schema_version": "geo.v6/1",
        "name": config["name"],
        "as_of": stamp,
        "collection_status": collection_status,
        "records": records,
        "observations": [],
    }
    report = {
        "run_at": stamp,
        "collection_status": collection_status,
        "records": len(records),
        **counts,
        "duplicates_merged": max(0, len(incoming) - unique_incoming),
        "sources": statuses,
        "archive": f"raw/{run_tag}",
    }
    status_payload = {
        "run_at": stamp,
        "collection_status": collection_status,
        "sources": [
            {
                "id": item["id"], "publisher": item["publisher"], "state": item["state"],
                "error": item["error"], "latest": stamp if item["state"] == "ok" else None,
                "candidates": item["candidates"], "accepted": item["accepted"],
            }
            for item in statuses.values()
        ],
    }
    _atomic_json(output_dir / "geo-v6.json", payload)
    _atomic_json(output_dir / "_collection_report.json", report)
    _atomic_json(output_dir / "_source_status.json", status_payload)
    return report


def selftest() -> int:
    config = load_config(HERE / "config.json")
    fixture = json.loads((HERE / "fixtures" / "source_samples.json").read_text(encoding="utf-8"))
    for example in fixture["classification_examples"]:
        result = classify_candidate(example["title"], example["summary"], "", "en", config)
        assert result["accepted"]
        assert result["dimensions"][example["topic"]] == example["dimension"]
    assert len(config["sources"]) == 20
    print("多源地缘新闻采集器自检通过：20 个来源，美伊与俄乌主题门控正常")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--output", type=Path, default=Path(os.getenv("COLLECTOR_OUTPUT_ROOT", HERE / "output")))
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    report = run_collection(args.config, args.output)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if report["collection_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

