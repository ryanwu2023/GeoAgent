"""Shared panel/MCP business semantics, without model-generated conclusions."""
import json
from datetime import date
from functools import lru_cache

from .paths import project_root
from . import store
from .chinese import translation_cache, source_name, chinese_text
from .events import event_metadata


@lru_cache(maxsize=1)
def metric_guides():
    return json.loads((project_root() / 'config' / 'metric-guides.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def project_labels():
    return json.loads((project_root() / 'config' / 'project-labels.json').read_text(encoding='utf-8'))


def metric_semantics(metric):
    guides = metric_guides()
    guide = guides.get(metric.get('series'))
    if guide is None:
        key = '__fiscal__' if metric.get('unit') == 'USD' and '美国整体' in metric.get('scope', '') else '__unknown__'
        guide = guides[key]
    return {
        **{k: v.replace('{name}', metric.get('name', '')) for k, v in guide.items()},
        'method': metric.get('method'), 'boundary': metric.get('boundary'),
        'comparison_step': metric.get('comparison_step', 1),
        'comparison_basis': 'previous是前comparison_step个观测期，不必然是上一自然日；区间变化另用interval_change。',
        'quality_issue': metric.get('quality_issue'),
        'display_unit': '亿美元' if metric.get('unit') == 'USD' else metric.get('unit'),
        'display_divisor': 100000000 if metric.get('unit') == 'USD' else 1,
        'kind': '解释规则，不是因果证据或预测',
    }


def validate_dates(start, end):
    from .geo_mcp.contracts import MCPError
    try:
        for value in (start, end):
            if value and (len(value) != 10 or date.fromisoformat(value).isoformat() != value):
                raise ValueError()
    except ValueError as exc:
        raise MCPError('GEO_INVALID_DATE', '日期必须为有效YYYY-MM-DD') from exc
    if start and end and start > end:
        raise MCPError('GEO_INVALID_DATE', '起始日期不能晚于结束日期')


def interval_change(metric, start, end):
    """First/last usable observations, distinct from periodic comparison."""
    points = [p for p in metric.get('history', []) if p.get('value') is not None
              and (not start or p['date'] >= start) and (not end or p['date'] <= end)]
    points.sort(key=lambda p: p['date'])
    result = {'requested_start': start, 'requested_end': end, 'observations': len(points),
              'status': 'quality_excluded' if metric.get('quality_issue') else 'insufficient_observations',
              'first': points[0] if points else None, 'last': points[-1] if points else None,
              'delta': None, 'delta_unit': metric.get('delta_unit', metric.get('unit')), 'pct': None,
              'basis': '区间内首末有效观测；不是逐日连续覆盖，也不是单期比较。'}
    if len(points) >= 2 and not metric.get('quality_issue'):
        difference = points[-1]['value'] - points[0]['value']
        result.update(status='comparable', delta=round(difference * (100 if metric.get('unit') == '%' and metric.get('bucket') == 'rates' else 1), 10))
        if metric.get('unit') not in ('%', '基点', '个百分点', 'σ') and points[0]['value']:
            result['pct'] = difference / abs(points[0]['value']) * 100
    return result


def review_index(snapshot_id):
    with store.db() as conn:
        rows = conn.execute('SELECT * FROM reviews WHERE snapshot_id=? ORDER BY version', (snapshot_id,)).fetchall()
    return {row['record_id']: dict(row) for row in rows}


def apply_review(record, review):
    if review:
        from .events import SIGNALS
        return {**record, 'automatic_signal': record['situation_signal'], 'situation_signal': review['signal'],
                'signal_label': SIGNALS[review['signal']], 'signal_basis': '研究员研判：' + review['reason'], 'review': review}
    return record


def evidence_semantics(record, review=None, cache=None):
    fields = event_metadata(record)
    cache = translation_cache() if cache is None else cache
    fields['points'] = [{**p, 'label': chinese_text(p['label'], cache, '相关地区')} for p in fields['points']]
    return apply_review(fields, review)


def search_evidence(records, query):
    """Exact panel substring matching, including translated fields and sources."""
    needle = query.strip().casefold()
    if needle:
        cache = translation_cache()
        records = [r for r in records if any(needle in str(v).casefold() for v in (
            r.get('title', ''), r.get('text', ''), r.get('title_zh', ''), r.get('summary_zh', ''),
            r.get('source_name', ''), r.get('publisher', ''), source_name(r.get('source_name', ''), cache)))]
    return sorted(records, key=lambda r: (r.get('published_at') or '', r['id']), reverse=True)


def map_projection(records, reviews, categories, signal='all', region='', limit=20):
    """MapView's layer/signal projection; points are mentions, not deployments."""
    from collections import Counter
    selected = []
    groups = {}
    cache = translation_cache()
    for raw in records:
        record = {**raw, **evidence_semantics(raw, reviews.get(raw['id']), cache)}
        if record['event_category'] not in categories or (signal != 'all' and record['situation_signal'] != signal):
            continue
        points = [p for p in record['points'] if not region or region.casefold() in p['label'].casefold()]
        if region and not points:
            continue
        selected.append((record, points))
        for p in points:
            key = (p['label'], p['lon'], p['lat'])
            group = groups.setdefault(key, {'ids': set(), 'precisions': set(), 'signals': Counter()})
            if record['id'] not in group['ids']:
                group['signals'][record['situation_signal']] += 1
            group['ids'].add(record['id'])
            group['precisions'].add(p.get('precision', 'unknown'))
    regions = [{'label': k[0], 'lon': k[1], 'lat': k[2], 'records_count': len(v['ids']),
                'signal_counts': dict(v['signals']), 'location_precisions': sorted(v['precisions']),
                'evidence_ids': sorted(v['ids'])[:limit], 'has_more_evidence': len(v['ids']) > limit}
               for k, v in groups.items()]
    regions.sort(key=lambda r: (-r['records_count'], r['label'], r['lon'], r['lat']))
    return {'classified_records_count': len(selected),
            'mapped_records_count': sum(bool(points) for _, points in selected),
            'without_location_count': sum(not points for _, points in selected),
            'event_category_counts': dict(Counter(r['event_category'] for r, _ in selected)),
            'signal_counts': dict(Counter(r['situation_signal'] for r, _ in selected)),
            'regions': regions[:limit], 'total_regions': len(regions), 'has_more_regions': len(regions) > limit,
            'limit': limit, 'basis': 'classified为图层和信号筛选后的报道，mapped为其中有地点的去重报道；一条报道可关联多地，地区计数不可相加。坐标为原始/地区参考点，不是当前部署或事件已核实位置；不是当前视口/缩放后的气泡数。地区筛选仅匹配地点标签，不搜索正文。'}


def collector_comparison(before, after):
    fields = ('run_status', 'import_status', 'collector_count', 'failed_count', 'snapshot_id')
    old = {c['id']: c for c in before.get('collectors', [])}
    new = {c['id']: c for c in after.get('collectors', [])}
    return {'before_run_id': before.get('run_id'), 'after_run_id': after.get('run_id'),
            'batch_fields': {k: {'before': before.get(k), 'after': after.get(k)} for k in fields},
            'collectors': [{'id': cid, 'presence': 'both' if cid in old and cid in new else 'before_only' if cid in old else 'after_only',
                            'before_status': old.get(cid, {}).get('status'), 'after_status': new.get(cid, {}).get('status'),
                            'before_source_health': old.get(cid, {}).get('source_health'), 'after_source_health': new.get(cid, {}).get('source_health')}
                           for cid in sorted(old.keys() | new.keys())],
            'basis': '两个已读批次的持久化状态对照，不推断差异原因、进程活跃或来源完整性；字段缺失为未知，不补成零。'}
