"""证券时报美伊、中东与俄乌固定渠道采集器。"""
from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import html
import http.cookiejar
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
BASE = "https://www.stcn.com"
CHANNELS = ("yw", "xw", "kx")
SEARCH_TERMS = ("美伊", "霍尔木兹", "胡塞武装", "红海航运", "曼德海峡", "伊核谈判", "俄乌", "乌克兰")
USER_AGENT = "GeoAgent/1.0 (+local research collector)"
USIRAN_STRONG = re.compile(r"霍尔木兹|胡塞(?:武装)?|安萨尔真主|曼德海峡|美伊(?:冲突|局势|战争|谈判|关系)|美国.{0,8}伊朗|伊朗.{0,8}美国|美以伊|沙特.{0,10}胡塞|胡塞.{0,10}沙特")
IRAN = re.compile(r"伊朗|德黑兰|革命卫队|哈梅内伊|佩泽希齐扬")
CONTEXT = re.compile(r"美国|美军|特朗普|以色列|核(?:问题|协议|谈判|设施)|制裁|次级制裁|关税|豁免|格雷厄姆法案|冲突|战争|袭击|打击|导弹|无人机|波斯湾|阿曼湾|霍尔木兹|红海|曼德海峡|原油|油价|航运|油轮|商船|船对船|海上过驳|护航|水雷|停火")
UKRAINE_ENTITY = re.compile(r"俄乌|乌克兰|基辅|泽连斯基|乌军|顿涅茨克|卢甘斯克|克里米亚")
RUSSIA_ENTITY = re.compile(r"俄罗斯|俄军|俄官员|对俄|莫斯科|普京|克里姆林宫")
UKRAINE_CONTEXT = re.compile(r"冲突|战争|战事|战线|前线|袭击|打击|空袭|导弹|无人机|停火|谈判|制裁|军援|援助|领土|能源设施|黑海|北约")
UKRAINE_SUPPORT = re.compile(r"援乌|对乌援助|向乌(?:提供|交付)|乌克兰.{0,16}(?:军援|援助|武器|贷款|拨款|防空)")


def fetch(url: str, *, xhr: bool = False) -> bytes:
    host = urllib.parse.urlsplit(url).hostname
    if host not in {"www.stcn.com", "stcn.com"}:
        raise ValueError("仅允许证券时报站内地址")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"}
    if xhr:
        headers["X-Requested-With"] = "XMLHttpRequest"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def clean(value: str) -> str:
    value = re.sub(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", "", value, flags=re.I | re.S)
    value = re.sub(r"<img\b[^>]*>", "", value, flags=re.I)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(value)).strip()


def topic_bindings(title: str, summary: str = "") -> list[str]:
    text = title + " " + summary
    topics = []
    if USIRAN_STRONG.search(text) or (IRAN.search(text) and CONTEXT.search(text)):
        topics.append("usiran")
    if (
        re.search(r"俄乌", text)
        or (UKRAINE_ENTITY.search(text) and RUSSIA_ENTITY.search(text) and UKRAINE_CONTEXT.search(text))
        or UKRAINE_SUPPORT.search(text)
    ):
        topics.append("ukraine")
    return topics


def relevant(title: str, summary: str = "") -> bool:
    return bool(topic_bindings(title, summary))


def article_topics(title: str, body: str = "") -> list[str]:
    """正文复核；标题决定主议题，正文用于保留资产传导型报道。"""
    topics = topic_bindings(title, body)
    # 仅在法案名称中偶然出现伊朗的俄乌/对俄报道，不纳入美伊主题。
    if "usiran" in topics and RUSSIA_ENTITY.search(title) and not (USIRAN_STRONG.search(title) or IRAN.search(title)):
        topics.remove("usiran")
    return topics


def dimension_bindings(topic: str, title: str, body: str) -> tuple[str, list[str]]:
    text = title + " " + body
    candidates = []
    if topic == "ukraine":
        if re.search(r"谈判|外交|停火|协议|斡旋|外长|会谈|和平方案", text):
            candidates.append("diplomacy")
        if re.search(r"军援|援乌|援助|武器交付|拨款|贷款|防空系统|弹药", text):
            candidates.append("aid")
        if re.search(r"俄罗斯.{0,20}(?:财政|预算|经济|通胀|卢布|能源出口|制裁)|卢布|俄央行", text):
            candidates.append("russia_domestic")
        if re.search(r"欧盟|欧洲议会|欧洲国家.{0,16}(?:选举|援乌|对乌援助)", text):
            candidates.append("eu_domestic")
        if re.search(r"前线|战线|俄军|乌军|导弹|无人机|空袭|袭击|领土|黑海", text):
            candidates.append("frontline")
        candidates = list(dict.fromkeys(candidates or ["frontline"]))
        return candidates[0], candidates
    if re.search(r"谈判|外交|停火|协议|斡旋|外长|会谈", text):
        candidates.append("diplomacy")
    if re.search(r"霍尔木兹|波斯湾|阿曼湾|红海|曼德海峡|原油|油价|天然气|航运|油轮|商船|船对船|海上过驳|护航|水雷|黄金|通胀", text):
        candidates.append("financial")
    if re.search(r"美军|美国中央司令部|美军基地|美国海军|航母", text):
        candidates.append("us_readiness")
    if re.search(r"伊朗军|革命卫队|伊朗武装|导弹|无人机|伊朗.{0,8}(?:袭击|打击|拦截)", text):
        candidates.append("iran_readiness")
    if re.search(r"以色列|黎巴嫩|也门|胡塞|安萨尔真主|卡塔尔|沙特|伊拉克|巴基斯坦|曼德海峡|红海", text):
        candidates.append("regional")
    candidates = list(dict.fromkeys(candidates or ["regional"]))
    return candidates[0], candidates


def listing_items(fragment: str) -> list[dict]:
    rows = []
    for block in re.findall(r"<li\b.*?</li>", fragment, flags=re.I | re.S):
        match = re.search(
            r'class=["\']tt["\'][^>]*>.*?<a[^>]+href=["\'](/article/detail/(\d+)\.html)["\'][^>]*>(.*?)</a>',
            block,
            flags=re.I | re.S,
        )
        if not match:
            continue
        excerpt = re.search(r'class=["\']text[^"\']*["\'][^>]*>(.*?)</div>', block, flags=re.I | re.S)
        rows.append({
            "id": match.group(2),
            "url": urllib.parse.urljoin(BASE, match.group(1)),
            "title": clean(match.group(3)),
            "summary": clean(excerpt.group(1)) if excerpt else "",
        })
    return rows


def page(channel: str, page_time: int, last_time: int | str = "") -> dict:
    query = urllib.parse.urlencode({"type": channel, "page_time": page_time, "last_time": last_time})
    payload = json.loads(fetch(f"{BASE}/article/list.html?{query}", xhr=True).decode("utf-8"))
    if payload.get("state") != 1:
        raise ValueError(f"列表接口失败：{payload.get('msg', '未知状态')}")
    data = payload.get("data", [])
    if isinstance(data, str):
        items = listing_items(data)
    else:
        items = [{
            "id": str(item.get("id") or item.get("pageTime") or ""),
            "url": urllib.parse.urljoin(BASE, item.get("url") or item.get("web_url") or ""),
            "title": str(item.get("title") or "").strip(),
            "summary": str(item.get("content") or "").strip(),
        } for item in data]
    return {**payload, "items": [item for item in items if item["id"] and item["url"].startswith(BASE)]}


def search_session(keyword: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    params = {"search_type": "all", "keyword": keyword, "uncertainty": 1, "sorter": "time"}
    referer = f"{BASE}/article/search.html?{urllib.parse.urlencode(params)}"
    opener.open(urllib.request.Request(referer, headers={"User-Agent": USER_AGENT}), timeout=30).read()
    return opener, params, referer


def search_page(opener, params: dict, referer: str, page_time: int) -> dict:
    query = urllib.parse.urlencode({**params, "page_time": page_time})
    request = urllib.request.Request(
        f"{BASE}/article/search_data.html?{query}",
        headers={"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest", "Referer": referer},
    )
    payload = json.loads(opener.open(request, timeout=30).read().decode("utf-8"))
    if payload.get("state") != 1:
        raise ValueError(f"搜索接口失败：{payload.get('msg', '未知状态')}")
    fragment = payload.get("data") or ""
    return {"items": listing_items(fragment), "page_time": int(payload.get("page_time") or page_time + 1), "fragment": fragment}


def inferred_search_dates(fragment: str, now: datetime) -> list[datetime]:
    dates = []
    for month, day, hour, minute in re.findall(r"(?<!\d)(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?!\d)", clean(fragment)):
        try:
            candidate = datetime(now.year, int(month), int(day), int(hour), int(minute), tzinfo=timezone(timedelta(hours=8)))
            if candidate > now.astimezone(candidate.tzinfo) + timedelta(days=2):
                candidate = candidate.replace(year=candidate.year - 1)
            dates.append(candidate.astimezone(timezone.utc))
        except ValueError:
            continue
    return dates


def parse_article(source: str) -> tuple[str, str, str]:
    title = re.search(r'<div class=["\']detail-title["\'][^>]*>(.*?)</div>', source, flags=re.I | re.S)
    info = re.search(r'<div class=["\']detail-info["\'][^>]*>(.*?)</div>', source, flags=re.I | re.S)
    body = re.search(r'<div class=["\']detail-content["\'][^>]*>(.*?)<div class=["\']detail-content-editor["\']', source, flags=re.I | re.S)
    if not title or not info or not body:
        raise ValueError("标题、时间或正文结构未识别")
    timestamp = re.search(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}", clean(info.group(1)))
    if not timestamp:
        raise ValueError("发布时间为空")
    paragraphs = [clean(part) for part in re.findall(r"<p\b[^>]*>(.*?)</p>", body.group(1), flags=re.I | re.S)]
    paragraphs = [part for part in paragraphs if part and not re.match(r"^(?:责编|责任编辑|校对|来源：|证券时报平台原创内容|转载与合作)", part)]
    text = "\n".join(paragraphs)
    if not text:
        raise ValueError("正文为空")
    published = datetime.strptime(timestamp.group(0), "%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
    return clean(title.group(1)), text[:12000], published


def discover(
    days: int,
    max_pages: int,
    search_terms: tuple[str, ...] = SEARCH_TERMS,
    search_start_page: int = 1,
    search_delay: float = 2.0,
) -> tuple[dict[str, dict], list[dict], dict[str, int]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    candidates: dict[str, dict] = {}
    errors = []
    page_counts = {}
    successful_channels = 0
    for channel in CHANNELS:
        page_time, last_time, pages = 1, "", 0
        try:
            for _ in range(max_pages):
                result = page(channel, page_time, last_time)
                pages += 1
                for item in result["items"]:
                    if relevant(item["title"], item["summary"]):
                        candidates[item["url"]] = {**item, "discovery": f"证券时报{channel}固定列表"}
                next_page = int(result.get("page_time") or page_time + 1)
                next_last = int(result.get("last_time") or 0)
                if not result["items"] or (next_last and datetime.fromtimestamp(next_last, timezone.utc) < cutoff):
                    break
                if next_page == page_time and str(next_last) == str(last_time):
                    break
                page_time, last_time = next_page, next_last
            successful_channels += 1
        except Exception as exc:
            errors.append({"channel": channel, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
        page_counts[channel] = pages
    if days > 45:
        now = datetime.now(timezone.utc)
        for keyword in search_terms:
            pages = 0
            try:
                opener, params, referer = search_session(keyword)
                page_time = max(1, search_start_page)
                for _ in range(min(max_pages, 120)):
                    result = None
                    for attempt in range(4):
                        try:
                            result = search_page(opener, params, referer, page_time)
                            break
                        except ValueError as exc:
                            if "频繁" not in str(exc) or attempt == 3:
                                raise
                            time.sleep(20 * (attempt + 1))
                    assert result is not None
                    pages += 1
                    for item in result["items"]:
                        if relevant(item["title"], item["summary"]):
                            candidates[item["url"]] = {**item, "discovery": f"证券时报站内搜索：{keyword}"}
                    dates = inferred_search_dates(result["fragment"], now)
                    next_page = result["page_time"]
                    if not result["items"] or (dates and min(dates) < cutoff):
                        break
                    if next_page == page_time:
                        break
                    page_time = next_page
                    if search_delay > 0:
                        time.sleep(search_delay)
            except Exception as exc:
                errors.append({"channel": f"search:{keyword}", "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
            page_counts[f"search:{keyword}"] = pages
    if not successful_channels:
        raise RuntimeError("证券时报三个固定列表均不可用")
    return candidates, errors, page_counts


def main(
    days: int = 30,
    max_pages: int = 40,
    workers: int = 5,
    search_terms: tuple[str, ...] = SEARCH_TERMS,
    search_start_page: int = 1,
    search_delay: float = 2.0,
) -> int:
    stamp = datetime.now(timezone.utc)
    cutoff = stamp - timedelta(days=days)
    out = Path(os.getenv("COLLECTOR_OUTPUT_ROOT") or (ROOT / "output"))
    out.mkdir(parents=True, exist_ok=True)
    run = out / stamp.strftime("%Y%m%dT%H%M%S")
    run.mkdir()
    candidates, errors, page_counts = discover(days, max_pages, search_terms, search_start_page, search_delay)
    prior = out / "geo-v6.json"
    existing = {}
    for record in (json.loads(prior.read_text(encoding="utf-8"))["records"] if prior.exists() else []):
        title, body = str(record.get("title") or ""), str(record.get("summary") or "")
        topics = article_topics(title, body)
        if not topics:
            continue
        bindings = {topic: dimension_bindings(topic, title, body) for topic in topics}
        record["topics"] = topics
        record["dimensions"] = {topic: values[0] for topic, values in bindings.items()}
        record["dimension_candidates"] = {topic: values[1] for topic, values in bindings.items()}
        existing[record["id"]] = record

    def collect(item: tuple[str, dict]):
        url, meta = item
        try:
            source = fetch(url).decode("utf-8")
            title, body, published = parse_article(source)
            if datetime.fromisoformat(published).astimezone(timezone.utc) < cutoff:
                return None, None
            topics = article_topics(title, body)
            if not topics:
                return None, None
            bindings = {topic: dimension_bindings(topic, title, body) for topic in topics}
            uid = hashlib.sha256(url.encode()).hexdigest()[:24]
            (run / f"{uid}.html").write_text(source, encoding="utf-8")
            return {
                "id": uid,
                "title": title,
                "summary": body,
                "title_zh": title,
                "summary_zh": body,
                "link": url,
                "published": published,
                "fetched_at": stamp.isoformat(),
                "source_id": "stcn-usiran",
                "publisher": "证券时报网",
                "tier": "T2",
                "topics": topics,
                "dimensions": {topic: values[0] for topic, values in bindings.items()},
                "dimension_candidates": {topic: values[1] for topic, values in bindings.items()},
                "nature": "财经媒体报道",
                "verification": "证券时报原文已采集；其中转述和市场归因仍需回源核验",
                "discovery": meta["discovery"],
                "origin_id": f"stcn:{meta['id']}",
            }, None
        except Exception as exc:
            return None, {"url": url, "error": f"{type(exc).__name__}: {str(exc)[:180]}"}

    captured = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        for record, error in pool.map(collect, candidates.items()):
            if record:
                captured.append(record)
                existing[record["id"]] = record
            elif error:
                errors.append(error)
    records = sorted(existing.values(), key=lambda item: item.get("published") or "", reverse=True)
    payload = {"schema_version": "geo.v6/1", "name": "证券时报地缘冲突动态", "as_of": stamp.isoformat(), "records": records, "observations": []}
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    (run / "geo-v6.json").write_text(rendered, encoding="utf-8")
    temp = out / "geo-v6.tmp"
    temp.write_text(rendered, encoding="utf-8")
    temp.replace(prior)
    report = {
        "run_at": stamp.isoformat(), "lookback_days": days, "page_counts": page_counts,
        "candidate_count": len(candidates), "captured": len(captured), "archive_records": len(records),
        "errors": errors, "archive": str(run),
    }
    (out / "_collection_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "LATEST.md").write_text("# 证券时报地缘冲突采集\n\n" + json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if captured or records or not candidates else 1


def selftest() -> int:
    fragment = '<li><div class="tt"><a href="/article/detail/4224029.html">霍尔木兹海峡最新消息</a></div><div class="text ellipsis-2"><a>伊朗武装拦截船只</a></div></li>'
    rows = listing_items(fragment)
    assert rows == [{"id": "4224029", "url": "https://www.stcn.com/article/detail/4224029.html", "title": "霍尔木兹海峡最新消息", "summary": "伊朗武装拦截船只"}]
    article = '<div class="detail-title">美伊谈判进展</div><div class="detail-info"><span>来源：证券时报网</span><span>2026-10-18 07:42</span></div><div class="detail-content"><p>美国和伊朗举行谈判。</p></div><div class="detail-content-editor">编辑</div>'
    title, body, published = parse_article(article)
    assert title == "美伊谈判进展" and body == "美国和伊朗举行谈判。" and published.endswith("+08:00")
    assert relevant("霍尔木兹海峡最新消息") and relevant("伊朗与美国恢复谈判") and relevant("胡塞武装袭击红海船只")
    assert article_topics("俄乌冲突前线出现新变化", "俄军与乌军交战") == ["ukraine"]
    assert not relevant("某公司季度业绩增长")
    assert "usiran" not in article_topics("俄官员称特朗普放松对俄制裁有历史意义", "法案名称中同时出现俄罗斯和伊朗")
    primary, candidates = dimension_bindings("usiran", "霍尔木兹海峡美伊谈判", "油轮恢复通行")
    assert primary == "diplomacy" and "financial" in candidates
    primary, candidates = dimension_bindings("ukraine", "俄乌停火谈判", "欧盟讨论继续援乌")
    assert primary == "diplomacy" and "aid" in candidates
    print("证券时报地缘冲突采集自检通过")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--search-term", action="append", default=[])
    parser.add_argument("--search-start-page", type=int, default=1)
    parser.add_argument("--search-delay", type=float, default=2.0)
    args = parser.parse_args()
    sys.exit(
        selftest()
        if args.selftest
        else main(
            max(1, args.days),
            max(1, args.max_pages),
            args.workers,
            tuple(args.search_term) or SEARCH_TERMS,
            max(1, args.search_start_page),
            max(0.0, args.search_delay),
        )
    )
