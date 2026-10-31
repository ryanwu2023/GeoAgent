"""美伊与俄乌公开来源的配置校验、主题准入和维度映射。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unicodedata
from typing import Any


VALID_TOPICS = {"usiran", "ukraine"}
VALID_POLICIES = {"public_fulltext", "metadata_only"}


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _phrase_hit(text: str, term: str) -> bool:
    needle = _norm(term)
    if not needle:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 .'+/-]*", needle):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", text))
    return needle in text


def _matches(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if _phrase_hit(text, term)]


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    topics = set(config.get("topics", []))
    if topics != VALID_TOPICS:
        raise ValueError("采集器只允许 usiran 与 ukraine 两个主题")
    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources 必须为非空数组")
    ids: set[str] = set()
    for source in sources:
        source_id = str(source.get("id") or "")
        if not source_id or source_id in ids:
            raise ValueError(f"来源 ID 为空或重复：{source_id}")
        ids.add(source_id)
        if source.get("content_policy") not in VALID_POLICIES:
            raise ValueError(f"来源访问策略无效：{source_id}")
        if not source.get("entry_urls") or not source.get("allowed_hosts"):
            raise ValueError(f"来源入口或允许域名为空：{source_id}")
    return config


def _usiran_dimension(text: str, categories: list[str]) -> str:
    if any(_phrase_hit(text, term) for term in ("胡塞", "houthi", "也门", "yemen", "红海", "red sea", "曼德海峡", "bab el-mandeb", "伊拉克民兵")):
        return "regional"
    if any(_phrase_hit(text, term) for term in ("革命卫队", "irgc", "sepah", "伊朗军方", "iranian military", "سپاه")):
        return "iran_readiness"
    if any(_phrase_hit(text, term) for term in ("centcom", "五角大楼", "pentagon", "美军", "u.s. military", "us military")):
        return "us_readiness"
    if any(_phrase_hit(text, term) for term in ("以色列", "israel", "idf", "以军")) and "military" in categories:
        return "israel"
    if "diplomacy" in categories:
        return "diplomacy"
    if any(_phrase_hit(text, term) for term in ("民调", "poll", "war powers", "战争权力", "反战")):
        return "us_opinion"
    if "economy_livelihood" in categories:
        return "iran_domestic"
    if "sanctions" in categories or "energy_transport" in categories:
        return "financial"
    return "iran_readiness" if "military" in categories or "nuclear_strategic" in categories else "regional"


def _ukraine_dimension(text: str, categories: list[str]) -> str:
    if "diplomacy" in categories:
        return "diplomacy"
    if any(_phrase_hit(text, term) for term in ("eu leaders", "european union", "欧盟", "欧洲议会", "election", "选举")):
        return "eu_domestic"
    if "aid_support" in categories or "defense_spending" in categories:
        return "aid"
    if "economy_livelihood" in categories or "sanctions" in categories:
        return "russia_domestic"
    return "frontline"


def classify_candidate(
    title: str,
    summary: str,
    body: str,
    language: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    del language  # 词表已经同时包含各来源使用的语言。
    title_text = _norm(title)
    combined = _norm(" ".join((title, summary, body)))
    exclusions = _matches(combined, config.get("exclusion_terms", []))
    categories: list[str] = []
    admission_terms: list[str] = []
    for category, terms in config.get("categories", {}).items():
        hits = _matches(combined, terms)
        if hits:
            categories.append(category)
            admission_terms.extend(hits)

    topics: list[str] = []
    dimensions: dict[str, str] = {}
    discovery_terms: list[str] = []
    for topic in config.get("topics", []):
        spec = config["topic_terms"][topic]
        anchor_hits = _matches(combined, spec.get("anchors", []))
        title_anchor_hits = _matches(title_text, spec.get("anchors", []))
        strong_hits = _matches(combined, spec.get("strong_anchors", []))
        if categories and anchor_hits and (title_anchor_hits or strong_hits or len(anchor_hits) >= 2):
            topics.append(topic)
            discovery_terms.extend(anchor_hits)
            dimensions[topic] = (
                _usiran_dimension(combined, categories)
                if topic == "usiran"
                else _ukraine_dimension(combined, categories)
            )

    # 明显体育、娱乐或文化内容只在具有明确战争/伤亡/制裁语义时保留。
    hard_categories = {"military", "sanctions", "humanitarian", "nuclear_strategic"}
    if exclusions and not hard_categories.intersection(categories):
        topics = []
        dimensions = {}

    accepted = bool(topics)
    reason = "" if accepted else (
        "命中排除内容" if exclusions else
        "未同时命中主题锚点与事项准入词"
    )
    return {
        "accepted": accepted,
        "topics": list(dict.fromkeys(topics)),
        "dimensions": dimensions,
        "categories": categories,
        "matched_discovery_terms": list(dict.fromkeys(discovery_terms)),
        "matched_admission_terms": list(dict.fromkeys(admission_terms)),
        "matched_exclusion_terms": exclusions,
        "exclusion_reason": reason,
    }

