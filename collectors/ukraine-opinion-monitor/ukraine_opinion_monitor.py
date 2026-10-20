"""Source-backed Russia/Ukraine polling; never infer percentages from news snippets.

Levada's monthly releases repeat the same negotiations/hostilities question.
Read the two latest releases from its official RSS and require complete methods
and both national totals. Changed layouts fail closed instead of matching a
demographic subgroup, reusing cached values, or fabricating a daily series.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixed_channel_common import (  # noqa: E402
    archive_raw, atomic_json, base_payload, fetch, observation, output_root,
    source_record, utc_now, validate_payload,
)

FEED = "https://www.levada.ru/tag/ukraina/feed/"
MONTHS = dict(zip(
    ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"),
    range(1, 13),
))
QUESTION = "Как Вы считаете, сейчас следует продолжать военные действия или начать мирные переговоры?"
QUESTION_ZH = "您认为现在应该继续军事行动，还是开始和平谈判？"
CHOICES = {
    "ru_levada_peace_talks": ("俄罗斯受访者：倾向开始和平谈判", "开始和平谈判（合并肯定／倾向选项）"),
    "ru_levada_continue_hostilities": ("俄罗斯受访者：倾向继续军事行动", "继续军事行动（合并肯定／倾向选项）"),
}


def plain(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", text))).strip()


def releases(raw, today):
    items = []
    for item in ET.fromstring(raw).findall("./channel/item"):
        title, url = plain(item.findtext("title", "")), item.findtext("link", "").strip()
        parts = urlsplit(url)
        if not title.startswith("Конфликт с Украиной в "):
            continue
        if parts.scheme != "https" or parts.netloc != "www.levada.ru" or not re.match(r"^/\d{4}/\d{2}/\d{2}/konflikt-s-ukrain", parts.path) or parts.query or parts.fragment:
            raise ValueError("民调 RSS 报告链接不符合已审核的一手来源规则")
        published = parsedate_to_datetime(item.findtext("pubDate", "")).date()
        if published > today:
            raise ValueError("民调报告发布日期在未来")
        items.append({"url": url, "title": title, "published": published.isoformat()})
    items = sorted({x["url"]: x for x in items}.values(), key=lambda x: x["published"], reverse=True)
    if not items:
        raise ValueError("一手 RSS 未发现俄乌月度民调报告")
    return items[:2]


def parse_release(raw, *, published, today):
    text = raw.decode("utf-8")
    paragraphs = [plain(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", text, re.I | re.S)]
    methods = [p for p in paragraphs if "Общероссийский опрос Левада-Центра проведен" in p]
    if len(methods) != 1:
        raise ValueError("民调方法段缺失或歧义")
    method = methods[0]
    period = re.search(r"проведен с (\d{1,2}) по (\d{1,2}) (\w+) (\d{4}) года", method)
    sample = re.search(r"объемом (\d[\d ]*) человек", method)
    if not period or not sample or "18 лет и старше" not in method or "методом личного интервью" not in method:
        raise ValueError("缺少完整调查期、成年样本量或访谈方法；不猜测")
    start_day, end_day, month, year = period.groups()
    start = date(int(year), MONTHS[month], int(start_day))
    end = date(int(year), MONTHS[month], int(end_day))
    size = int(sample[1].replace(" ", ""))
    if not start <= end <= date.fromisoformat(published) <= today or not 500 <= size <= 10000:
        raise ValueError("民调调查期、发布日期或样本量不合理")
    totals = []
    for paragraph in paragraphs:
        # Only the national summary that contains BOTH choices, never the
        # following demographic breakdowns or the conditional Putin experiment.
        if "мирных переговоров" not in paragraph and "мирные переговоры" not in paragraph:
            continue
        if "продолжения военных действий" not in paragraph or re.search(r"выше среди|ниже среди|В\. Путин|решение президента", paragraph):
            continue
        peace = re.search(r"мирн(?:ых|ые) переговор(?:ов|ы)[^%]{0,100}?(?:[–—-]\s*(?:до\s*)?|до\s+)(\d{1,3})%", paragraph)
        war = re.search(r"продолжения военных действий[^%]{0,80}?(?:[–—-]\s*(?:до\s*)?|до\s+)(\d{1,3})%", paragraph)
        if peace and war:
            totals.append((int(peace[1]), int(war[1])))
    if len(totals) != 1 or not all(0 <= v <= 100 for v in totals[0]) or sum(totals[0]) > 100:
        raise ValueError("未唯一识别同题全国总体的两项比例，拒绝用分组或新闻数字替代")
    return {"start": start.isoformat(), "end": end.isoformat(), "sample_size": size,
            "values": dict(zip(CHOICES, totals[0]))}


def collect(script_file, *, fetcher=fetch, today=None):
    today = today or date.today()
    root, stamp = output_root(script_file), utc_now()
    payload = base_payload("俄乌相关民调 · Levada 俄罗斯成年人", stamp)
    try:
        rss = fetcher(FEED)
        archive_raw(root, "levada-index", rss, "xml")
        reports = []
        for release in releases(rss, today):
            raw = fetcher(release["url"])
            ref = archive_raw(root, "levada-report", raw, "html")
            result = parse_release(raw, published=release["published"], today=today)
            reports.append({**release, **result, "raw": ref})
        reports.sort(key=lambda x: x["end"])
        if len({r["end"] for r in reports}) != len(reports):
            raise ValueError("同一调查期重复发布，需人工核对修订，不自动合并")
        ids = []
        for report in reports:
            rid = "levada-" + report["end"]
            ids.append(rid)
            description = (f"调查机构 Levada Center；俄罗斯城乡18岁及以上成年人。调查期 {report['start']} 至 {report['end']}；"
                           f"样本量 {report['sample_size']}；在受访者家中面对面访谈、人口结构加权。"
                           f"题目（中文译）：{QUESTION_ZH} 原文：{QUESTION} "
                           f"开始和平谈判 {report['values']['ru_levada_peace_talks']}%；继续军事行动 {report['values']['ru_levada_continue_hostilities']}%。"
                           "均为合并选项占全样本比例，其他／难回答未归入任一项。不是乌克兰民意，也不是停火概率。")
            record = source_record(rid, "俄乌相关民调 · 俄罗斯受访者 · " + report["end"], description,
                                   report["url"], stamp, dimension="russia_domestic", publisher="Levada Center（列瓦达中心）",
                                   verification="一手报告明确发布比例；调查方法及受访环境存在局限")
            record["published"] = report["published"]
            record["raw_files"] = [report["raw"]]
            payload["records"].append(record)
        latest = reports[-1]
        method = (f"Levada 月度独立调查波次，非每日民调；日期取调查结束日。采集时最新调查期 {latest['start']} 至 {latest['end']}，"
                  f"样本 {latest['sample_size']} 名俄罗斯18岁及以上成年人；入户面对面访谈、人口结构加权。题目：{QUESTION_ZH}")
        boundary = ("仅代表俄罗斯受访成年人，不代表乌克兰或全部冲突方；战时访谈可能有非回应及社会期许偏差。"
                    "支持谈判不等于接受领土让步，也不排斥支持军队；不是停火概率或冲突因果证据。仅保留已核验发布的调查波次，不插值补日频。")
        for series, (name, choice) in CHOICES.items():
            item = observation(series, name, "%", "月度调查波次", method + " 当前选项：" + choice,
                               boundary, [{"date": r["end"], "value": r["values"][series]} for r in reports],
                               list(reversed(ids)), "russia_domestic", bucket="polling")
            item["scope"] = "俄罗斯城乡18岁及以上受访者 · 俄乌相关民调"
            payload["observations"].append(item)
        payload["collection_status"] = "ok"
        statuses = [{"id": "levada-ukraine-poll", "publisher": "Levada Center（俄罗斯成年人）", "state": "ok", "count": len(reports), "latest": latest["end"], "http_status": 200}]
    except Exception as exc:
        # A failed latest release must not be presented as a recovered source.
        payload["records"], payload["observations"] = [], []
        payload["collection_status"] = "failed"
        statuses = [{"id": "levada-ukraine-poll", "publisher": "Levada Center（俄罗斯成年人）", "state": "failed", "error": str(exc)[:400], "count": 0}]
    validate_payload(payload)
    atomic_json(root / "geo-v6.json", payload)
    atomic_json(root / "_source_status.json", {"run_at": stamp, "collection_status": payload["collection_status"], "sources": statuses})
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", "--self-test", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        assert len(CHOICES) == 2 and QUESTION.endswith("?")
        print("[selftest] ukraine-opinion-monitor: ok (offline)")
        return 0
    result = collect(__file__)
    print({"collection_status": result["collection_status"], "observations": len(result["observations"])})
    return 0 if result["collection_status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
