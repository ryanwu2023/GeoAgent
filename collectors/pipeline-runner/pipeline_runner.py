"""可配置采集管线的共享执行器。

读取 GEO_PIPELINE_CONFIG（已由后端翻译好的 geopolitical-source-monitor 配置）与
COLLECTOR_OUTPUT_ROOT，复用多源采集内核运行一次采集；--preview N 只列出每源前 N 个候选
及过滤结果，不写输出目录。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

HERE = Path(__file__).resolve().parent
MONITOR_DIR = HERE.parent / "geopolitical-source-monitor"
REPO_ROOT = HERE.parents[1]
for _path in (REPO_ROOT, MONITOR_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from feeds import FeedParseError, discover_feed_candidates  # noqa: E402
from filtering import classify_candidate, load_config  # noqa: E402
from geopolitical_source_monitor import _source_error_state, run_collection  # noqa: E402
from web import PolicyHttpClient, TransportResponse, discover_candidates, is_restricted_document  # noqa: E402


def _preview_source(configured: dict[str, Any], defaults: dict[str, Any], config: dict[str, Any],
                    http: PolicyHttpClient, limit: int) -> dict[str, Any]:
    source = {**defaults, **configured}
    result: dict[str, Any] = {"source_id": source["id"], "state": "no_data", "error": None, "candidates": []}
    link_filter = None
    if source.get("link_include_pattern"):
        try:
            link_filter = re.compile(str(source["link_include_pattern"]))
        except re.error:
            return {**result, "state": "failed", "error": "CONFIG_ERROR"}
    for entry_url in source["entry_urls"]:
        if len(result["candidates"]) >= limit:
            break
        listing = http.fetch(entry_url, source)
        if listing.error_type:
            result["state"], result["error"] = _source_error_state(listing), listing.error_type
            continue
        if is_restricted_document(listing.body):
            result["state"], result["error"] = "restricted", "access_challenge"
            continue
        base_url = listing.final_url or entry_url
        if source.get("discovery_mode") == "rss":
            try:
                candidates = discover_feed_candidates(listing.body, base_url, source)
            except FeedParseError:
                result["state"], result["error"] = "failed", "PARSE_ERROR"
                continue
        else:
            candidates = discover_candidates(listing.body, base_url, source)
        if link_filter:
            candidates = [item for item in candidates if link_filter.search(item.url)]
        for candidate in candidates[: limit - len(result["candidates"])]:
            verdict = classify_candidate(candidate.title, candidate.summary, "", source.get("language", ""), config)
            result["candidates"].append({
                "title": candidate.title,
                "url": candidate.url,
                "accepted": verdict["accepted"],
                "reason": verdict["exclusion_reason"],
                "matched": list(dict.fromkeys([*verdict["matched_discovery_terms"], *verdict["matched_admission_terms"]])),
            })
    if result["candidates"]:
        result["state"], result["error"] = "ok", None
    return result


def preview(config_path: Path, limit: int, client: PolicyHttpClient | None = None) -> dict[str, Any]:
    """每源取前 limit 个候选并给出准入判断；不写输出目录与 HTTP 缓存。"""
    config = load_config(Path(config_path))
    defaults = config.get("request", {})
    http = client or PolicyHttpClient(user_agent=config["user_agent"])
    workers = max(1, min(4, int(defaults.get("source_workers", 3))))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pipeline-preview") as pool:
        sources = list(pool.map(lambda s: _preview_source(s, defaults, config, http, limit), config["sources"]))
    return {"sources": sources}


def selftest() -> int:
    """用内置样例（离线假传输）跑通试跑与正式采集。"""
    base = json.loads((MONITOR_DIR / "config.json").read_text(encoding="utf-8"))
    config = {**base, "name": "管线自检", "topics": ["usiran"],
              "topic_terms": {"usiran": base["topic_terms"]["usiran"]},
              "sources": [{"id": "selftest-rss", "name_zh": "自检源", "publisher": "自检源", "tier": "T2",
                           "language": "en", "allowed_hosts": ["selftest.invalid"], "content_policy": "metadata_only",
                           "discovery_mode": "rss", "entry_urls": ["https://selftest.invalid/feed.xml"]}]}
    stamp = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    feed = (
        "<rss><channel>"
        f"<item><title>Iran and the United States resume mediated nuclear talks</title>"
        f"<link>https://selftest.invalid/a/1</link><description>Oman hosts negotiations on sanctions</description>"
        f"<pubDate>{stamp}</pubDate></item>"
        f"<item><title>Football final tonight</title><link>https://selftest.invalid/a/2</link>"
        f"<pubDate>{stamp}</pubDate></item>"
        "</channel></rss>"
    )

    def transport(request, timeout):
        return TransportResponse(200, {"Content-Type": "application/rss+xml"}, feed.encode("utf-8"), request.full_url)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_path = root / "pipeline-config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        client = PolicyHttpClient(user_agent=config["user_agent"], transport=transport,
                                  sleep=lambda _: None, robots_fetcher=lambda _: "", cache_dir=root / "cache")
        shown = preview(config_path, 5, client=client)["sources"][0]
        assert shown["state"] == "ok" and [c["accepted"] for c in shown["candidates"]] == [True, False], shown
        previous = os.environ.pop("COLLECTOR_PREVIOUS_OUTPUT_ROOT", None)
        try:
            report = run_collection(config_path, root / "output", client=client)
        finally:
            if previous is not None:
                os.environ["COLLECTOR_PREVIOUS_OUTPUT_ROOT"] = previous
        payload = json.loads((root / "output" / "geo-v6.json").read_text(encoding="utf-8"))
        assert report["collection_status"] == "ok" and len(payload["records"]) == 1, report
        assert payload["records"][0]["topics"] == ["usiran"]
    print("采集管线执行器自检通过：试跑与采集均按配置过滤")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--preview", type=int, metavar="N")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    configured = os.getenv("GEO_PIPELINE_CONFIG")
    if not configured or not Path(configured).is_file():
        print("configuration_required: 缺少 GEO_PIPELINE_CONFIG 管线配置文件", flush=True)
        return 2
    if args.preview is not None:
        if args.preview < 1:
            print("configuration_required: --preview 必须大于 0", flush=True)
            return 2
        print(json.dumps(preview(Path(configured), args.preview), ensure_ascii=False), flush=True)
        return 0
    output = os.getenv("COLLECTOR_OUTPUT_ROOT")
    if not output:
        print("configuration_required: 缺少 COLLECTOR_OUTPUT_ROOT", flush=True)
        return 2
    report = run_collection(Path(configured), Path(output))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if report["collection_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
