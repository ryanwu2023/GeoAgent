const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('tool trace component exposes technical and full details without voice controls', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/QaTrace.tsx'), 'utf8');
  assert.match(source, /技术详情/);
  assert.match(source, /完整详情/);
  assert.match(source, /getToolCallDetail/);
  assert.match(source, /AbortController/);
  assert.doesNotMatch(source, /语音回复/);
});

test('tool drawer CSS constrains long JSON locally and becomes full width on narrow screens', () => {
  const css = fs.readFileSync(path.join(__dirname, '../src/style.css'), 'utf8');
  assert.match(css, /\.tool-call-drawer[^{]*\{[^}]*position:\s*fixed/s);
  assert.match(css, /\.tool-call-json[^{]*\{[^}]*overflow:\s*auto/s);
  assert.match(css, /@media\(max-width:720px\)[^{]*\{[^}]*\.tool-call-drawer[^{]*\{[^}]*width:\s*100%/s);
});

test('tool metadata resets the global definition-list columns', () => {
  const css = fs.readFileSync(path.join(__dirname, '../src/style.css'), 'utf8');
  assert.match(css, /\.tool-call-technical dl\.tool-call-metadata,\.tool-call-detail-grid\{display:block;width:100%;min-width:0/);
  assert.match(css, /grid-template-columns:96px minmax\(0,1fr\)/);
  assert.match(css, /word-break:normal/);
});

test('first attempt and empty arguments are presented as normal execution', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/QaTrace.tsx'), 'utf8');
  assert.match(source, /\(item\.attempt\?\?1\)>1/);
  assert.match(source, /无需输入参数/);
  assert.match(source, /范围来自当前工作快照和看板筛选/);
});

test('multi-agent trace exposes specialist status, failure degradation and nested tools', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/QaTrace.tsx'), 'utf8');
  assert.match(source, /主任务/);
  assert.match(source, /子 Agent/);
  assert.match(source, /失败降级/);
  assert.match(source, /groupAgentTrace/);
  assert.match(source, /agent-tool-call-list/);
});

test('trace exposes evidence match semantics, cache reuse and facet coverage without hidden reasoning', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/QaTrace.tsx'), 'utf8');
  assert.match(source, /命中/);
  assert.match(source, /本轮新增/);
  assert.match(source, /复用缓存/);
  assert.match(source, /研究覆盖/);
  assert.match(source, /researchCoverage/);
  assert.doesNotMatch(source, /思维链详情/);
});
