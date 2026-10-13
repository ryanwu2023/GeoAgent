import pytest

from backend.semantic import interval_change, metric_semantics, validate_dates, search_evidence
from backend.semantic import map_projection, collector_comparison
from backend.geo_mcp.contracts import MCPError


def test_interval_keeps_zero_and_separates_periodic_comparison():
    metric = {'unit': 'USD', 'previous': 99, 'history': [
        {'date': '2026-10-01', 'value': 0}, {'date': '2026-10-03', 'value': 20},
        {'date': '2026-10-04', 'value': None}]}
    result = interval_change(metric, '2026-10-01', '2026-10-04')
    assert result['delta'] == 20
    assert result['pct'] is None
    assert result['observations'] == 2
    assert result['status'] == 'comparable'
    assert interval_change({**metric, 'quality_issue': 'quarantined'}, '', '')['delta'] is None
    assert interval_change(metric, '2026-10-03', '')['status'] == 'insufficient_observations'


def test_rates_delta_is_basis_points_not_percentage_change():
    result = interval_change({'unit': '%', 'bucket': 'rates', 'delta_unit': '基点',
                             'history': [{'date': '2026-10-01', 'value': 4.1}, {'date': '2026-10-02', 'value': 4.2}]}, '', '')
    assert result['delta'] == 10
    assert result['pct'] is None


@pytest.mark.parametrize('start,end', [('2026-02-30', ''), ('2026-10-02', '2026-10-01'), ('20261001', '')])
def test_invalid_dates_are_explicit(start, end):
    with pytest.raises(MCPError):
        validate_dates(start, end)


def test_metric_explanation_comes_from_shared_registry():
    result = metric_semantics({'series': 'unknown', 'name': '测试序列', 'unit': 'USD'})
    assert '测试序列' in str(result)
    assert result['display_divisor'] == 100000000
    assert result['kind'] == '解释规则，不是因果证据或预测'


def test_evidence_substring_matches_translated_summary():
    rows = [{'id': 'a', 'published_at': '2026-10-02', 'summary_zh': '外交斡旋取得进展'},
            {'id': 'b', 'published_at': '2026-10-03', 'title': 'Other'}]
    assert [r['id'] for r in search_evidence(rows, '外交斡旋')] == ['a']
    assert [r['id'] for r in search_evidence(rows, '')] == ['b', 'a']


def test_map_counts_are_records_not_points_and_reviews_apply_before_filter():
    rows = [{'id': 'a', 'title': 'Iranian strike', 'text': '', 'topics': ['usiran'], 'points': [
        {'label': 'Tehran', 'lon': 51, 'lat': 35, 'precision': 'explicit'},
        {'label': 'Hormuz', 'lon': 56, 'lat': 26, 'precision': 'area'}]},
        {'id': 'b', 'title': 'Other', 'text': '', 'topics': [], 'points': []},
        {'id': 'c', 'supplemental': True}]
    review = {'signal': 'easing', 'reason': '复核', 'version': 1}
    result = map_projection(rows, {'a': review}, ['military', 'domestic'], 'all', '', 1)
    assert result['classified_records_count'] == 2
    assert result['mapped_records_count'] == 1
    assert result['without_location_count'] == 1
    assert result['total_regions'] == 2
    assert result['has_more_regions']
    assert result['signal_counts']['easing'] == 1
    assert map_projection(rows, {'a': review}, ['military'], 'escalation')['classified_records_count'] == 0
    assert map_projection(rows, {}, [], 'all')['classified_records_count'] == 0
    assert map_projection(rows, {}, ['military'], region='霍尔木兹')['mapped_records_count'] == 1


def test_batch_comparison_does_not_fill_missing_counts_or_infer_causes():
    result = collector_comparison({'run_id': 'old', 'collectors': [{'id': 'x', 'status': 'failed'}]},
                                  {'run_id': 'new', 'collectors': [{'id': 'x', 'status': 'success'}, {'id': 'y'}]})
    assert result['batch_fields']['failed_count']['before'] is None
    assert result['collectors'][0]['before_status'] == 'failed'
    assert result['collectors'][1]['presence'] == 'after_only'


def test_protocol_request_id_reuse_does_not_duplicate_audit_receipts():
    from backend.geo_mcp.audit import event_id
    assert event_id('1', 'geo_snapshot_list') != event_id('1', 'geo_snapshot_list')


def test_periodic_rate_comparison_does_not_leak_float_artifacts():
    from backend.research import metric_view
    metric = metric_view({'unit': '%', 'bucket': 'rates', 'history': [
        {'date': '2026-09-28', 'value': 5.24}, {'date': '2026-09-29', 'value': 5.26}]})
    assert metric['delta'] == 2


def test_evidence_text_limit_counts_utf8_bytes():
    from backend.geo_mcp.server import _redacted_record
    record = _redacted_record({'id': 'r', 'text': '中文' * 5000})
    assert len(record['text'].encode('utf8')) <= record['text_limit_bytes']
    assert record['text_truncated']
    assert record['text_bytes_total'] == 30000
