import asyncio
import time
from contextlib import contextmanager

from backend.geo_mcp import server


def test_latest_lineage_uses_metadata_without_decoding_archive(monkeypatch):
    monkeypatch.setattr(server.store, 'snapshots', lambda: [{'id': 'latest', 'created_at': '2026-10-14'}])
    def forbidden():
        raise AssertionError('Full archive summaries must not be used for lineage')
    monkeypatch.setattr(server.store, 'snapshot_summaries', forbidden)
    assert server._latest_snapshot_id() == 'latest'
    monkeypatch.setattr(server.store, 'snapshots', lambda: [])
    assert server._latest_snapshot_id() is None


def test_workspace_classifies_each_record_once_and_retains_review(monkeypatch):
    class FakeConnection:
        def execute(self, *args): return self
        def fetchall(self):
            return [{'record_id': 'a', 'signal': 'easing', 'version': 1, 'created_at': '2026-10-01'}]
    @contextmanager
    def fake_db(): yield FakeConnection()
    monkeypatch.setattr(server.store, 'db', fake_db)
    monkeypatch.setattr(server.research, 'coverage', lambda *args, **kwargs: [])
    monkeypatch.setattr(server.research, 'filtered_records', lambda *args: [{'id':'a'}, {'id':'b'}])
    seen = []
    def classify(record):
        seen.append(record['id'])
        return {'event_category': 'military', 'situation_signal': 'escalation'}
    monkeypatch.setattr(server, 'event_metadata', classify)
    result = server._workspace_counts({'id':'test'}, 'all', '', '', '')
    assert seen == ['a', 'b']
    assert result['event_category_counts'] == {'military': 2}
    assert result['signal_counts']['easing'] == 1
    assert result['signal_counts']['escalation'] == 1


def test_analysis_overview_does_not_embed_unbounded_history_or_drop_semantics():
    original = {'id': 'brent', 'series': 'brent', 'name': '原油', 'unit': '美元/桶',
                'current': {'date': '2026-10-09', 'value': 103},
                'previous': {'date': '2026-10-08', 'value': 104}, 'delta': -1,
                'history': [{'date': '2026-10-01', 'value': 100}] * 10000,
                'source_ids': [str(i) for i in range(70)], 'quality_issue': None}
    projected = server._analysis_metric_projection(original)
    assert 'history' not in projected
    assert projected['history_points_count'] == 10000
    assert projected['history_included'] is False
    assert len(projected['source_ids']) == 50 and projected['source_ids_truncated']
    assert projected['source_ids_count'] == 70
    assert projected['current'] == original['current'] and projected['delta'] == -1
    assert projected['semantics']['kind'] == '解释规则，不是因果证据或预测'
    assert len(original['history']) == 10000  # Never mutate cached snapshot state.


def test_sync_projection_does_not_block_event_loop(monkeypatch):
    def slow_projection(**kwargs):
        time.sleep(.15)
        return 'done'
    monkeypatch.setattr(server, '_execute_tool', slow_projection)

    async def scenario():
        slow = asyncio.create_task(server._run_tool(ctx=None, tool='test', handler=None))
        await asyncio.sleep(.02)
        assert not slow.done()  # Event loop advanced before the sync work finished.
        assert await slow == 'done'
    asyncio.run(scenario())
