"""Deterministic, inert HTML export for a persisted research answer."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from markdown_it import MarkdownIt


_MARKDOWN = (
    MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    .enable("table")
    .enable("strikethrough")
)
_TOPIC_NAMES = {"all": "综合范围", "usiran": "美伊局势", "ukraine": "俄乌冲突"}
_BEIJING = timezone(timedelta(hours=8))


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _display_time(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "时间未披露"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        utc = parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M 世界时")
        beijing = parsed.astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M 北京时间")
        return f"{utc} / {beijing}"
    except (TypeError, ValueError):
        return raw[:40]


def _display_day(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "未披露"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(_BEIJING).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return raw[:10]


def _safe_url(value: object) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    return raw if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else ""


def _lead_from_markdown(value: object) -> str:
    """Extract the first prose paragraph without trusting Markdown as HTML."""
    raw = str(value or "").replace("\r\n", "\n")
    for block in re.split(r"\n\s*\n", raw):
        candidate = block.strip()
        if not candidate or candidate.startswith(("#", "- ", "* ", ">", "|", "```")):
            continue
        candidate = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", candidate)
        candidate = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", candidate)
        candidate = re.sub(r"[*_`~]", "", candidate)
        candidate = re.sub(r"^\s*\d+[.)、]\s*", "", candidate)
        cleaned = _clean_text(candidate)
        if cleaned:
            return cleaned[:520]
    return "本报告忠实呈现本次已校验回答，并将事实、媒体转述、模型判断和数据缺口分区展示。"


def report_title(query: object) -> str:
    title = _clean_text(query).strip("。！？?!；;：: ")
    if not title:
        title = "地缘局势分析"
    return f"{title[:60]} · 分析报告"


def report_filename(query: object) -> str:
    stem = report_title(query).replace(" · 分析报告", "")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")[:48] or "GeoAgent分析报告"
    return f"{stem} · 分析报告.html"


def content_disposition(query: object) -> str:
    filename = report_filename(query)
    return f"attachment; filename=geoagent-report.html; filename*=UTF-8''{quote(filename)}"


def render_answer_report(answer: dict, generated_at: str | None = None, business_lead=None) -> str:
    summary = answer.get("summary")
    if not isinstance(summary, dict) or not _clean_text(summary.get("text")):
        raise ValueError("该回答没有可导出的总结内容")

    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    title = report_title(answer.get("query"))
    summary_html = _MARKDOWN.render(str(summary.get("text") or ""))
    cited_ids = list(dict.fromkeys(str(item) for item in summary.get("evidence_ids", []) if item))
    citation_by_id = {
        str(item.get("id")): item
        for item in answer.get("citations", [])
        if isinstance(item, dict) and item.get("id")
    }
    citations = [citation_by_id[item] for item in cited_ids if item in citation_by_id]

    evidence_rows = []
    for item in citations:
        source = _clean_text(item.get("source_name") or item.get("publisher") or "来源未标注")
        headline = _clean_text(item.get("title") or "未提供标题")
        excerpt = _clean_text(item.get("text") or "该记录未提供正文片段，请打开原始来源核验。")[:420]
        tier = _clean_text(item.get("tier") or "未分级")
        verification = _clean_text(item.get("verification") or "待核验")
        source_url = _safe_url(item.get("url"))
        link = (
            f'<a href="{html.escape(source_url, quote=True)}" target="_blank" rel="noreferrer noopener">打开原始来源</a>'
            if source_url else "未提供外部链接"
        )
        evidence_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(source)}</strong><br><span>{html.escape(headline)}</span></td>"
            f"<td>{html.escape(_display_time(item.get('published_at')))}<br><small>采集：{html.escape(_display_time(item.get('fetched_at') or item.get('first_seen')))}</small></td>"
            f"<td>{html.escape(excerpt)}</td>"
            f"<td>{html.escape(tier)}<br><small>{html.escape(verification)}</small></td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    evidence_html = "".join(evidence_rows) or '<tr><td colspan="5">当前回答没有可展开的逐条引用。</td></tr>'

    gap_values = [_clean_text(item) for item in answer.get("gaps", []) if _clean_text(item)]
    lead_warning = _clean_text(getattr(business_lead, "warning", ""))
    if lead_warning and lead_warning not in gap_values:
        gap_values.append(lead_warning)
    gaps = [f"<li>{html.escape(item)}</li>" for item in gap_values]
    gaps_html = "".join(gaps) or "<li>当前回答未单列其他数据缺口。</li>"
    filters = answer.get("filters") if isinstance(answer.get("filters"), dict) else {}
    date_range = "未限定日期"
    if filters.get("start") or filters.get("end"):
        date_range = f"{filters.get('start') or '最早记录'} 至 {filters.get('end') or '数据截至日'}"
    topic = _TOPIC_NAMES.get(str(answer.get("topic") or "all"), str(answer.get("topic") or "综合范围"))
    dimension = _clean_text(filters.get("dimension")) or "全部维度"
    lead = _clean_text(getattr(business_lead, "text", "")) or _lead_from_markdown(summary.get("text"))

    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#0b1118;--panel:#121b25;--panel2:#16202c;--line:#233240;--ink:#e6edf3;--ink2:#9fb0c0;--ink3:#6b7d8f;--gold:#c9a227;--red:#d4573f;--green:#3f9e6a;--blue:#4a90c2;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}}
*{{box-sizing:border-box;margin:0;padding:0}}html{{scroll-behavior:smooth}}body{{background:var(--bg);color:var(--ink);font:15px/1.75 var(--sans);-webkit-font-smoothing:antialiased}}a{{color:#87bddf;text-decoration-color:#42647a;text-underline-offset:3px;overflow-wrap:anywhere}}a:hover{{color:#b9ddf2}}a:focus-visible{{outline:2px solid var(--gold);outline-offset:3px}}.wrap{{max-width:1120px;margin:0 auto;padding:0 32px 80px}}
header{{padding:56px 0 36px;border-bottom:1px solid var(--line);margin-bottom:34px}}.kicker{{font-size:12px;letter-spacing:.18em;color:var(--gold);margin-bottom:16px;font-weight:650}}h1{{max-width:920px;font-size:38px;line-height:1.28;font-weight:720;letter-spacing:-.01em;margin-bottom:14px}}.sub{{max-width:820px;font-size:16px;color:var(--ink2);margin-bottom:26px}}.meta{{display:flex;flex-wrap:wrap;gap:10px 28px;padding-top:18px;border-top:1px solid var(--line);font-size:12.5px;color:var(--ink3)}}.meta b{{color:var(--ink2);font-weight:650}}
.lead{{font-size:16px;color:#d5e0ea;background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--gold);padding:22px 26px;margin:26px 0;border-radius:0 6px 6px 0}}.lead strong{{color:#fff}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:26px 0 46px}}.card{{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:18px 18px 16px}}.card .lbl{{font-size:11.5px;color:var(--ink3);letter-spacing:.04em;margin-bottom:8px}}.card .val{{font-size:23px;font-weight:700;letter-spacing:-.02em;line-height:1.2;overflow-wrap:anywhere}}.card .note{{font-size:11.5px;color:var(--ink3);margin-top:7px;line-height:1.5}}
.section-title{{font-size:23px;font-weight:700;margin:52px 0 18px;letter-spacing:-.005em;padding-left:14px;border-left:3px solid var(--gold)}}.section-title .num{{color:var(--gold);font-size:15px;margin-right:10px;letter-spacing:.05em}}.analysis-body{{min-width:0}}.analysis-body>h2{{font-size:20px;margin:38px 0 10px;padding-top:24px;border-top:1px solid var(--line);color:#f1f5f8}}.analysis-body>h2:first-child{{margin-top:0;padding-top:0;border-top:0}}.analysis-body h3{{font-size:16.5px;font-weight:650;margin:30px 0 10px;color:#dbe6ef}}.analysis-body p{{margin:12px 0;color:#c8d5e0;max-width:82ch}}.analysis-body ul,.analysis-body ol{{margin:12px 0 12px 22px}}.analysis-body li{{margin:7px 0;color:#c8d5e0}}strong{{color:#fff;font-weight:650}}em{{color:var(--gold);font-style:normal;font-weight:600}}blockquote{{margin:20px 0;padding:16px 20px;background:#101a23;border-left:3px solid var(--blue);color:#b8c9d6}}code{{background:var(--panel2);border:1px solid var(--line);padding:2px 6px;border-radius:4px;color:#c9d7e2;font-size:.9em}}
.table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:8px;margin:22px 0}}table{{width:100%;border-collapse:collapse;font-size:13px}}th{{text-align:left;padding:11px 12px;border-bottom:2px solid var(--line);background:#0f1821;color:var(--ink3);font-size:11.5px;font-weight:650;letter-spacing:.04em;white-space:nowrap}}td{{padding:12px;border-bottom:1px solid var(--line);color:#c8d5e0;vertical-align:top;overflow-wrap:anywhere}}tbody tr:last-child td{{border-bottom:0}}tbody tr:hover td{{background:var(--panel)}}small,td span{{color:var(--ink3)}}.analysis-body>table{{display:block;overflow-x:auto;border:1px solid var(--line);border-radius:8px;margin:22px 0}}.analysis-body>table th,.analysis-body>table td{{min-width:120px}}.evidence td:nth-child(1){{min-width:190px}}.evidence td:nth-child(2){{min-width:210px}}.evidence td:nth-child(3){{min-width:320px}}.evidence td:nth-child(4){{min-width:120px}}.evidence td:nth-child(5){{min-width:110px}}
.section-lead{{color:var(--ink3);font-size:12.5px;margin:-6px 0 16px}}.warn{{background:#1c1512;border:1px solid #4a2d1e;border-left:3px solid var(--red);padding:18px 22px;border-radius:0 6px 6px 0;margin:20px 0}}.warn .t{{font-size:13px;font-weight:700;color:#e8a08c;margin-bottom:8px}}.warn ul{{margin:8px 0 8px 20px}}.warn li{{font-size:13.5px;color:#d3bfb7;margin:7px 0}}.method{{margin-top:16px;padding-top:14px;border-top:1px solid #4a2d1e;color:#bbaaa3;font-size:12.5px}}footer{{display:flex;justify-content:space-between;gap:24px;margin-top:60px;padding-top:26px;border-top:1px solid var(--line);font-size:12px;color:var(--ink3);line-height:1.8}}
@media(max-width:820px){{.wrap{{padding:0 18px 60px}}header{{padding-top:38px}}h1{{font-size:28px}}.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.section-title{{margin-top:40px}}footer{{display:block}}footer span{{display:block;margin-top:8px}}}}@media(max-width:480px){{.cards{{grid-template-columns:1fr}}.lead{{padding:18px}}h1{{font-size:25px}}}}@media print{{:root{{--bg:#fff;--panel:#f6f7f8;--panel2:#eef1f3;--line:#ccd3d8;--ink:#111820;--ink2:#334450;--ink3:#66747e;--gold:#8a6a00;--red:#9f3525;--blue:#356c91}}body{{background:#fff;color:var(--ink)}}.wrap{{max-width:none;padding:0 12mm}}header{{padding-top:12mm}}.card,.lead,.warn,th{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}.analysis-body p,.analysis-body li,td{{color:#202b33}}.evidence tbody tr{{break-inside:avoid}}.section-title{{break-after:avoid}}a{{color:inherit}}}}
</style></head><body><div class="wrap">
<header><div class="kicker">GeoAgent 地缘数据 · 专题研判</div><h1>{html.escape(title)}</h1><p class="sub">基于本次本地模型回答、结构化指标与逐条新闻证据形成。正文区分事实、媒体转述和研究判断，并保留可回查的原始来源。</p><div class="meta"><span><b>主题</b> {html.escape(topic)}</span><span><b>维度</b> {html.escape(dimension)}</span><span><b>区间</b> {html.escape(date_range)}</span><span><b>数据截至</b> {html.escape(_display_time(answer.get('as_of')))}</span><span><b>本次取数</b> {html.escape(_display_time(answer.get('created_at')))}</span><span><b>报告生成</b> {html.escape(_display_time(generated_at))}</span></div></header>
<div class="lead"><strong>核心判断：</strong> {html.escape(lead)}</div>
<div class="cards"><div class="card"><div class="lbl">引用材料</div><div class="val">{len(citations)} 条</div><div class="note">仅统计本回答实际引用且可回查的记录</div></div><div class="card"><div class="lbl">明确缺口</div><div class="val">{len(gaps)} 项</div><div class="note">缺口不等于事件不存在或结论被证伪</div></div><div class="card"><div class="lbl">研究范围</div><div class="val">{html.escape(topic)}</div><div class="note">{html.escape(dimension)} · {html.escape(date_range)}</div></div><div class="card"><div class="lbl">数据日期</div><div class="val">{html.escape(_display_day(answer.get('as_of')))}</div><div class="note">以回答持久化时记录的数据截至时间为准</div></div></div>
<main><section class="analysis"><h2 class="section-title"><span class="num">01</span>综合研判</h2><div class="analysis-body">{summary_html}</div></section>
<section class="evidence"><h2 class="section-title"><span class="num">02</span>证据与信源</h2><p class="section-lead">仅列出本回答实际引用的材料。来源等级表示来源类型，不代表陈述已获独立确认。</p><div class="table-wrap"><table><thead><tr><th>来源与标题</th><th>发布时间与采集时间</th><th>证据内容要点</th><th>层级与核验</th><th>原始链接</th></tr></thead><tbody>{evidence_html}</tbody></table></div></section>
<section class="boundary"><h2 class="section-title"><span class="num">03</span>口径、限制与缺口</h2><div class="warn"><div class="t">必须与结论一起阅读的限制</div><ul>{gaps_html}</ul><div class="method">媒体转述、当事方声明和关键词命中不自动等于事实成立。报道数量不代表事件概率，价格同步变化不单独证明因果；资产关联属于有条件的研究判断，不构成交易指令。</div></div></section></main>
<footer><span>GeoAgent · 地缘政治数据与分析工作台</span><span>导出时调用模型生成业务核心判断；原问答正文与引用未重新生成，内部回答编号、快照编号和证据内部 ID 未写入报告。</span></footer>
</div></body></html>'''
