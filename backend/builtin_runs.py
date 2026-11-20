"""可编辑内置采集器的测试运行与试跑（设计：docs/superpowers/specs/2026-11-19-内置采集器来源与关键词编辑设计.md 第 6 节）。

两者都在隔离目录里跑原采集代码，产物不进快照、不碰正式输出和增量状态：
- 测试运行：用已保存的当前版本配置跑完整采集器，目录在 .pipeline-trials/<id>/，结果写运行记录与“本次新增”
  （对照正式产物里已有的条目判断是否新增）。
- 试跑：用页面上未保存的草稿，只跑改动过的来源（没有改动时取前几个启用来源），同步返回每个来源抓到的条目。

旧式采集器（注册表 legacy_output：伊朗升级、海军、战备）把输出和状态写在自己的源码目录里，
这里先把源码目录复制到隔离目录再运行，避免测试结果混进正式的累积档案。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from . import builtin_config
from .adapters import normalize
from .paths import collector_data_root, collector_source_root
from .pipeline_config import default_resolve

PREVIEW_SOURCES = 5
PREVIEW_TIMEOUT_SEC = 150
MAX_PREVIEWS = 2
PREVIEW_BUSY = "试跑并发已满，请稍后再试"
RECORD_FILES = ("ALL-records.jsonl", "LATEST-records.jsonl")
_PREVIEW_SLOTS = threading.BoundedSemaphore(MAX_PREVIEWS)


class BuiltinRunError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message, self.field = message, field


def registry_entry(collector_id: str) -> dict:
    from scripts.collect import load_registry  # 延迟导入：scripts 依赖 backend
    for item in load_registry(collector_source_root() / "collectors.json"):
        if item.get("id") == collector_id:
            return item
    raise BuiltinRunError(f"注册表里没有采集器 {collector_id}")


def current_config(collector_id: str, version_config: dict | None) -> dict:
    """当前版本（已保存）合并后的运行配置。"""
    return builtin_config.apply_overlay(builtin_config.base_config(collector_id),
                                        builtin_config.overlay_of(version_config))


def source_type(source: dict) -> str:
    """来源类型映射到运行记录的 web/rss/search。"""
    kind = str(source.get("type") or source.get("kind") or "").lower()
    if kind in {"gnews", "query", "search"}:
        return "search"
    if "rss" in kind or "sitemap" in kind:
        return "rss"
    if kind:
        return "web"
    url = str(source.get("url") or "").lower()
    return "rss" if any(k in url for k in ("rss", "feed", ".xml", "atom")) else "web"


def run_collector(collector_id: str, config: dict, work: Path, *, timeout: int,
                  run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict:
    """在 work 下隔离运行一次采集器；每次从空输出、空状态开始。返回 {returncode, output, stdout, stderr}。"""
    item = registry_entry(collector_id)
    shutil.rmtree(work, ignore_errors=True)
    output, state, previous = work / "output", work / "state", work / "previous"
    for folder in (output, state, previous):
        folder.mkdir(parents=True, exist_ok=True)
    config_path = state / builtin_config.OVERRIDE_FILE
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    collector_dir = collector_source_root() / item.get("source_dir", collector_id)
    if item.get("legacy_output"):
        sandbox = work / "sandbox"
        shutil.copytree(collector_dir, sandbox, ignore=shutil.ignore_patterns("output", "state", "__pycache__"))
        collector_dir = sandbox
    from scripts.collect import _collector_environment  # 延迟导入：scripts 依赖 backend
    env = {k: v for k, v in os.environ.items() if not k.startswith("COLLECTOR_")}
    env.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "COLLECTOR_RUN_ID": f"trial-{uuid.uuid4().hex[:12]}",
                "COLLECTOR_OUTPUT_ROOT": str(output), "COLLECTOR_STATE_ROOT": str(state),
                "COLLECTOR_PREVIOUS_OUTPUT_ROOT": str(previous), builtin_config.OVERRIDE_ENV: str(config_path)})
    env = _collector_environment(item, env)
    completed = run([sys.executable, str(collector_dir / item["entrypoint"])], cwd=collector_dir, env=env,
                    timeout=timeout, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if item.get("legacy_output") and not any(output.iterdir()) and (collector_dir / "output").is_dir():
        output = collector_dir / "output"
    return {"returncode": completed.returncode, "output": output,
            "stdout": (completed.stdout or "")[-2000:], "stderr": (completed.stderr or "")[-2000:]}


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _latest(output: Path, name: str) -> Path | None:
    found = sorted(output.rglob(name), key=lambda p: (len(p.parts), str(p)))
    return found[0] if found else None


def read_results(collector_id: str, output: Path) -> dict:
    """读隔离产物 → {"items": [条目], "statuses": {来源ID: {state, error}}}。

    条目统一为 {id, source_id, title, link, published, summary, accepted, reason, matched, language}；
    id 与导入时的证据 ID 一致（adapters.normalize）。
    """
    raws: list[tuple[dict, bool, str]] = []
    path = next((p for p in (_latest(output, n) for n in RECORD_FILES) if p), None)
    if path:
        for raw in _jsonl(path):
            excluded = raw.get("kind") == "excluded"
            raws.append((raw, not excluded, "已被当前口径排除" if excluded else ""))
    geo = output / "geo-v6.json"
    if not raws and geo.is_file():
        payload = json.loads(geo.read_text(encoding="utf-8"))
        raws += [(r, True, "") for r in payload.get("records") or [] if isinstance(r, dict)]
    for signals in sorted(output.rglob("signals.jsonl")):  # 会晤日历：信号入选，拒收留原因
        raws += [(r, True, "") for r in _jsonl(signals)]
        rejects = signals.with_name("rejects.jsonl")
        if rejects.is_file():
            raws += [(r, False, str(r.get("detail") or r.get("reason") or "未通过过滤")) for r in _jsonl(rejects)]
    items, seen = [], set()
    for raw, accepted, reason in raws:
        link = raw.get("link") or raw.get("url") or raw.get("source_url") or ""
        record = {"link": link, "title": raw.get("title") or raw.get("headline") or "无标题", "id": raw.get("id"),
                  "published": raw.get("published_utc") or raw.get("published") or raw.get("published_at")}
        evidence = normalize({**raw, **record}, collector_id)["id"]
        if evidence in seen:
            continue
        seen.add(evidence)
        matched = [str(x) for key in ("matched_terms", "buckets", "signals", "flags") for x in (raw.get(key) or [])
                   if isinstance(x, (str, int))]
        items.append({"id": evidence, "source_id": str(raw.get("source_id") or raw.get("source") or ""),
                      "title": str(record["title"]), "link": str(link), "published": record["published"],
                      "summary": str(raw.get("summary") or raw.get("body") or "")[:1000],
                      "language": str(raw.get("language") or raw.get("lang") or ""),
                      "accepted": accepted, "reason": reason, "matched_discovery_terms": matched[:12]})
    return {"items": items, "statuses": _statuses(output)}


def _statuses(output: Path) -> dict[str, dict]:
    status = _latest(output, "_source_status.json")
    if status:
        data = json.loads(status.read_text(encoding="utf-8"))
        return {str(s.get("id")): {"state": s.get("state") or "ok", "error": s.get("error"),
                                   "fetched": s.get("count") if isinstance(s.get("count"), int) else None}
                for s in data.get("sources") or [] if isinstance(s, dict)}
    fetch = _latest(output, "_fetch_stats.json")
    if fetch:
        data = json.loads(fetch.read_text(encoding="utf-8"))
        return {str(k): {"state": ("ok" if v.get("n") else "no_data") if v.get("ok") else "failed", "error": v.get("err"),
                         "fetched": v.get("n")}
                for k, v in (data.get("src") or {}).items() if isinstance(v, dict)}
    return {}


def known_record_ids(collector_id: str) -> set[str]:
    """正式产物里已有条目的证据 ID；测试运行据此判断“本次新增”。读不到时视为空。"""
    try:
        return {item["id"] for item in read_results(collector_id, collector_data_root() / collector_id / "output")["items"]}
    except Exception:  # noqa: BLE001 - 正式产物缺失或损坏时全部视为新增
        return set()


# ---------------------------------------------------------------- 试跑

def preview_sources(collector_id: str, draft: dict, saved: dict, source_ids: list[str] | None,
                    limit: int = PREVIEW_SOURCES) -> list[str]:
    """要试跑的来源：页面指定的；否则草稿里相对已保存版本改动或新增的启用来源；都没有时取前几个启用来源。"""
    rows = [s for s in draft.get("sources") or [] if isinstance(s, dict) and s.get("enabled", True)]
    ids = [str(s.get("id")) for s in rows]
    if source_ids:
        chosen = [i for i in source_ids if i in ids]
    else:
        before = {str(s.get("id")): builtin_config.source_view(s) for s in saved.get("sources", [])}
        keys = ("name", "url", "tier", "enabled")
        chosen = [str(s.get("id")) for s in rows
                  if str(s.get("id")) not in before or any(str(s.get(k, "")) != str(before[str(s.get("id"))].get(k, ""))
                                                           for k in keys)]
        chosen = chosen or ids
    return chosen[:limit]


def preview(collector_id: str, draft: dict, version_config: dict | None, *, source_ids: list[str] | None = None,
            limit: int = 5, timeout: int = PREVIEW_TIMEOUT_SEC,
            resolve: Callable[[str], list[str]] = default_resolve, runner: Callable[..., dict] = run_collector) -> dict:
    """用未保存的草稿试跑部分来源；不写任何表和正式目录。"""
    if not builtin_config.editable(collector_id):
        raise BuiltinRunError(builtin_config.READONLY_REASON)
    overlay = builtin_config.build_overlay(collector_id, draft, resolve=resolve)
    config = builtin_config.apply_overlay(builtin_config.base_config(collector_id), overlay)
    saved = current_config(collector_id, version_config)
    chosen = preview_sources(collector_id, draft, saved, source_ids)
    if not chosen:
        raise BuiltinRunError("没有启用的来源可以试跑", "sources")
    keep = set(chosen)
    config["sources"] = [{**s, "enabled": True} for s in config.get("sources", []) if str(s.get("id")) in keep]
    if not _PREVIEW_SLOTS.acquire(blocking=False):
        raise BuiltinRunError(PREVIEW_BUSY)
    try:
        with tempfile.TemporaryDirectory(prefix=f"builtin-preview-{collector_id}-") as tmp:
            try:
                result = runner(collector_id, config, Path(tmp), timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise BuiltinRunError(f"试跑超时（{timeout} 秒），请少选几个来源再试") from exc
            if result["returncode"] != 0:
                detail = (result["stderr"] or result["stdout"]).strip()[-300:]
                raise BuiltinRunError("试跑失败" + (f"：{detail}" if detail else ""))
            found = read_results(collector_id, result["output"])
    finally:
        _PREVIEW_SLOTS.release()
    limit = max(1, min(int(limit), 20))
    sources = []
    for sid in chosen:
        status = found["statuses"].get(sid, {})
        candidates = [{"title": i["title"], "url": i["link"], "accepted": i["accepted"], "reason": i["reason"],
                       "matched": i["matched_discovery_terms"]} for i in found["items"] if i["source_id"] == sid]
        candidates.sort(key=lambda c: not c["accepted"])
        state = status.get("state") or ("ok" if candidates else "no_data")
        sources.append({"source_id": sid, "state": "failed" if status.get("error") and not candidates else state,
                        "error": status.get("error"), "fetched": status.get("fetched"), "candidates": candidates[:limit]})
    return {"sources": sources, "builtin": True}
