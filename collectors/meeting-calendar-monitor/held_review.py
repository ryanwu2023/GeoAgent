"""「已举行」的模型终审：关键字规则只做初筛，模型读原句判断双方是否已经当面见过。

规则只能看到 "meeting with"，看不懂句子在讲谁、讲什么时候 —— 「dreams of a meeting
with Putin」「Trump's envoys met Putin」都会被当成已举行。命中已举行关键字的少量标题
交给模型复核；模型不可用（未配置、未开启、超时、报错、超出本轮额度）时返回 None，
调用方照旧用规则结果，采集不会因此失败。

每条报道只问一次，结论按链接和标题落在 state/held_reviews.json，之后的复核直接读缓存。
"""
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

PROMPT_VERSION = "v1"
CACHE_NAME = "held_reviews.json"
MAX_CALLS = int(os.getenv("GEO_MEETING_REVIEW_MAX_CALLS", "40"))
TIMEOUT = float(os.getenv("GEO_MEETING_REVIEW_TIMEOUT", "20"))
# 单条失败后重问的次数；重问后仍连续失败到 MAX_FAILURES 条就本轮停用，免得每条都等满超时
RETRIES = 1
MAX_FAILURES = 2
_CONTROL_TOKEN = re.compile(r"<\|[A-Za-z0-9_▁\-]{1,40}\|>")

SYSTEM = (
    "你是外交新闻核查员。只根据给出的英文新闻标题和摘要判断，不使用外部知识。"
    "问题：候选人物中，是否至少有两位已经当面会晤（本人到场，同一地点见面，会谈已经发生）。"
    "以下都不算已当面会晤：计划、预告、可能、希望、提议、即将举行的会；只通了电话或视频；"
    "只是特使、代表团、外长等代表见了对方；在引号里被引用的话；"
    "只提到「此前曾见过」而报道的不是这次会面。"
    "只输出一个 JSON 对象，不要任何其他文字，格式："
    '{"held": true 或 false, "participants": [当面见面的人物 id], '
    '"date": "会晤实际日期 YYYY-MM-DD，原文没写就留空", '
    '"location": "会晤城市英文名，原文没写就留空", '
    '"evidence": "支持结论的原文片段，不超过 120 个字符"}'
)

_state: Dict[str, object] = {"calls": 0, "failures": 0, "disabled": False, "errors": [],
                             "stats": {"asked": 0, "cached": 0, "fallback": 0, "rejected": 0}}
_cache: Dict[str, dict] = {}
_cache_path: Optional[str] = None
_dirty = False
# 测试或离线运行时替换成假的调用函数：(messages) -> 模型回复文本
_caller: Optional[Callable[[List[dict]], str]] = None


def enabled() -> bool:
    """只在线上托管运行（或显式开启）时调用模型；本地自检和单元测试默认不联网。"""
    flag = os.getenv("GEO_MEETING_MODEL_REVIEW", "").strip().lower()
    if flag in {"0", "off", "false", "no"}:
        return False
    return _caller is not None or flag in {"1", "on", "true", "yes"} or os.getenv("GEO_COLLECTOR_MANAGED") == "true"


def load(state_dir: str) -> None:
    global _cache, _cache_path, _dirty
    _cache_path = os.path.join(state_dir, CACHE_NAME)
    try:
        with open(_cache_path, encoding="utf-8") as f:
            data = json.load(f)
        _cache = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        _cache = {}
    _dirty = False


def save() -> None:
    if not (_dirty and _cache_path):
        return
    tmp = _cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _cache_path)


def stats() -> Dict[str, int]:
    return dict(_state["stats"])  # type: ignore[arg-type]


def errors() -> List[str]:
    """本轮模型调用失败的原因（最多 5 条），写进运行日志，方便查为什么回退到规则。"""
    return list(_state["errors"])  # type: ignore[arg-type]


def reset_run(caller: Optional[Callable[[List[dict]], str]] = None) -> None:
    global _caller
    _caller = caller
    _state.update(calls=0, failures=0, disabled=False, errors=[],
                  stats={"asked": 0, "cached": 0, "fallback": 0, "rejected": 0})


def cache_key(url: str, headline: str) -> str:
    raw = f"{PROMPT_VERSION}|{url.strip()}|{' '.join(headline.split()).lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _backend_call(messages: List[dict]) -> str:
    root = Path(__file__).resolve().parents[2]
    if (root / "backend" / "llm.py").is_file() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from backend import llm  # noqa: PLC0415  只在线上运行时才需要
    text, _model = asyncio.run(llm.complete(messages, model_role="fast", temperature=0,
                                            max_output_tokens=400, timeout_seconds=TIMEOUT))
    return text


def _parse(text: str, candidates: Dict[str, str], published: date) -> Optional[dict]:
    # 自建网关偶尔把模板控制符漏进正文：{"held":<|endoftext|>true}
    text = _CONTROL_TOKEN.sub("", text or "")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("held"), bool):
        return None
    people = [p for p in raw.get("participants") or [] if isinstance(p, str) and p in candidates]
    day = ""
    value = raw.get("date")
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            parsed = date.fromisoformat(value.strip())
        except ValueError:
            parsed = None
        # 已举行的会只能在发稿日前；离发稿日太远多半是模型把背景事件的日期当成了这次会面
        if parsed and published - timedelta(days=45) <= parsed <= published:
            day = parsed.isoformat()
    location = raw.get("location") if isinstance(raw.get("location"), str) else ""
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), str) else ""
    held = raw["held"] and len(set(people)) >= 2
    return {"held": held, "participants": sorted(set(people)), "date": day,
            "location": location.strip()[:60], "evidence": evidence.strip()[:160]}


def review(headline: str, desc: str, url: str, published: date,
           candidates: Dict[str, str]) -> Optional[dict]:
    """返回模型结论；拿不到结论时返回 None，调用方回退到规则。

    candidates：人物 id → 显示名（英文名/中文名），模型只能从中挑参会人。
    """
    global _dirty
    st = _state["stats"]  # type: ignore[assignment]
    key = cache_key(url, headline)
    hit = _cache.get(key)
    if isinstance(hit, dict) and "held" in hit:
        st["cached"] += 1
        if not hit["held"]:
            st["rejected"] += 1
        return hit
    if not enabled() or _state["disabled"] or len(candidates) < 2:
        st["fallback"] += 1
        return None
    if int(_state["calls"]) >= MAX_CALLS:
        st["fallback"] += 1
        return None
    names = "\n".join(f"- {pid}: {name}" for pid, name in sorted(candidates.items()))
    user = (f"发稿日期：{published.isoformat()}\n标题：{headline}\n"
            f"摘要：{(desc or '').strip()[:400] or '（无）'}\n候选人物（id: 名字）：\n{names}")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    _state["calls"] = int(_state["calls"]) + 1
    verdict, problem = None, ""
    # 自建网关偶尔回空内容，同一条重问一次通常就好了
    for _attempt in range(RETRIES + 1):
        try:
            text = (_caller or _backend_call)(messages)
            verdict = _parse(text, candidates, published)
            if verdict is None:
                problem = "无法解析：" + " ".join((text or "").split())[:80]
        except Exception as exc:  # 模型或网络出任何问题都只是回退到规则
            verdict, problem = None, f"{type(exc).__name__}: {str(exc)[:80]}"
        if verdict is not None:
            break
    if verdict is None:
        if len(_state["errors"]) < 5:  # type: ignore[arg-type]
            _state["errors"].append(problem)  # type: ignore[union-attr]
        _state["failures"] = int(_state["failures"]) + 1
        if int(_state["failures"]) >= MAX_FAILURES:
            _state["disabled"] = True
        st["fallback"] += 1
        return None
    _state["failures"] = 0
    st["asked"] += 1
    if not verdict["held"]:
        st["rejected"] += 1
    verdict["by"] = f"model:{PROMPT_VERSION}"
    _cache[key] = verdict
    _dirty = True
    return verdict


def candidate_names(ids: Sequence[str], persons: Sequence[dict]) -> Dict[str, str]:
    table = {p["id"]: p for p in persons}
    out = {}
    for pid in ids:
        p = table.get(pid)
        if not p:
            continue
        english = next((a for a in p.get("aliases", []) if a.isascii()), "")
        out[pid] = " / ".join(x for x in (english, p.get("zh", "")) if x) or pid
    return out
