const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const loadTs = require('./load_ts.cjs');

const { projectCollectorDiagnosis } = loadTs('src/collectorDiagnostics.ts');

test('collector diagnosis prefers structured API fields and preserves history timestamps', () => {
  const result = projectCollectorDiagnosis({
    finished_at: '2026-10-26T01:00:00Z',
    last_run_at: '2026-10-26T01:01:00Z',
    last_success_at: '2026-10-25T01:00:00Z',
    error_type: 'UPSTREAM_UNAVAILABLE',
    error_reason: '上游站点返回 503。',
    error_detail: 'HTTP Error 503',
    error_message: '旧版泛化错误',
    suggested_action: '稍后单独补采。',
  });

  assert.equal(result.lastRunAt, '2026-10-26T01:01:00Z');
  assert.equal(result.lastSuccessAt, '2026-10-25T01:00:00Z');
  assert.equal(result.errorCategory, 'UPSTREAM_UNAVAILABLE');
  assert.equal(result.errorReason, '上游站点返回 503。');
  assert.equal(result.errorDetail, 'HTTP Error 503');
  assert.equal(result.suggestedAction, '稍后单独补采。');
});

test('collector diagnosis remains compatible with legacy runtime payloads', () => {
  const result = projectCollectorDiagnosis({
    started_at: '2026-10-25T01:00:00Z',
    last_success: '2026-10-24T01:00:00Z',
    error_type: 'TIMEOUT',
    error_message: '请求超时',
  });

  assert.equal(result.lastRunAt, '2026-10-25T01:00:00Z');
  assert.equal(result.lastSuccessAt, '2026-10-24T01:00:00Z');
  assert.equal(result.errorReason, '请求超时');
  assert.match(result.suggestedAction, /重试/);
});

test('collector monitor renders diagnosis, last run, last success and action labels', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/CollectorMonitor.tsx'), 'utf8');
  for (const label of ['失败诊断', '最近运行', '最近成功', '建议处理', '脱敏日志摘要']) {
    assert.ok(source.includes(label), `missing ${label}`);
  }
});
