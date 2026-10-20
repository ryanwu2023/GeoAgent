const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = ts.createSourceFile(
  'App.tsx',
  fs.readFileSync(path.join(__dirname, '../src/App.tsx'), 'utf8'),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
function findAll(root, predicate) {
  const matches = [];
  function visit(node) {
    if (predicate(node)) matches.push(node);
    ts.forEachChild(node, visit);
  }
  visit(root);
  return matches;
}
const functions = findAll(source, node => ts.isFunctionDeclaration(node)
  && node.name?.text === 'translateMapTitles');
assert.equal(functions.length, 1, 'one handler owns title translation receipts');
const handlerSource = functions[0].getText(source);
const scopeFields = ['sid', 'topic', 'dimension', 'start', 'end'];
const initialScope = {
  sid: 'snapshot-a', topic: 'usiran', dimension: '', start: '', end: '',
};
const scopeKey = scope => JSON.stringify(scopeFields.map(field => scope[field]));
const cacheKey = (sid, dimension = '') => new URLSearchParams({
  snapshot_id: sid, topic: 'usiran', dimension, start: '', end: '',
}).toString();
const evidence = (title = 'Original A title') => ({
  id: 'shared-record', title, title_translation_state: 'untranslated',
  text_translation_state: 'untranslated', translation_state: 'untranslated',
});

// Execute the real App handler with React-like render-local scope and deferred
// network responses. This exercises receipt ordering, not a live model call.
function harness() {
  const state = {
    map: [evidence()], rows: [evidence()], notice: '', writes: 0,
  };
  const requests = [];
  const context = vm.createContext({
    URLSearchParams,
    mapTranslationScope: {current: scopeKey(initialScope)},
    workspaceCache: {current: new Map()},
    mapCache: {current: new Map()},
    api: (url, body) => new Promise((resolve, reject) =>
      requests.push({url, body, resolve, reject})),
    setMapRecords: update => {
      state.map = typeof update === 'function' ? update(state.map) : update;
      state.writes++;
    },
    setEvidenceRows: update => {
      state.rows = typeof update === 'function' ? update(state.rows) : update;
      state.writes++;
    },
    setNotice: value => { state.notice = value; state.writes++; },
  });
  const compiled = ts.transpileModule(
    `globalThis.createHandler = function(sid, topic, dimension, start, end, status) {
      ${handlerSource}
      return translateMapTitles;
    };`,
    {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}},
  ).outputText;
  vm.runInContext(compiled, context);
  let scope = {...initialScope};
  function render(changes = {}, configured = true) {
    scope = {...scope, ...changes};
    context.mapTranslationScope.current = scopeKey(scope);
    return context.createHandler(
      ...scopeFields.map(field => scope[field]), {model: {configured}},
    );
  }
  return {state, requests, context, render};
}
const success = title => ({ok: true, translated: 1, failed: [],
  titles: {'shared-record': title}, message: ''});

test('translation receipt scope includes all five dashboard filters on every render', () => {
  const assignments = findAll(source, node => ts.isBinaryExpression(node)
    && node.operatorToken.kind === ts.SyntaxKind.EqualsToken
    && node.left.getText(source) === 'mapTranslationScope.current');
  assert.equal(assignments.length, 1);
  const stringify = assignments[0].right;
  assert.equal(stringify.expression.getText(source), 'JSON.stringify');
  assert.deepEqual(stringify.arguments[0].elements.map(node => node.getText(source)),
    scopeFields);
});

test('translation does not call a model without a snapshot, configuration or IDs', async () => {
  for (const [changes, configured, ids] of [
    [{sid: ''}, true, ['shared-record']],
    [{}, false, ['shared-record']],
    [{}, true, []],
  ]) {
    const h = harness();
    const result = await h.render(changes, configured)(ids);
    assert.equal(Object.keys(result).length, 0);
    assert.equal(h.requests.length, 0);
    assert.equal(h.state.writes, 0);
  }
});

test('current translated titles update visible labels but never another snapshot cache', async () => {
  const h = harness();
  for (const key of [cacheKey('snapshot-a'), cacheKey('snapshot-a', 'diplomacy'), cacheKey('snapshot-b')]) {
    h.context.workspaceCache.current.set(key, {time: 1, data: {records: [evidence(key)]}});
    h.context.mapCache.current.set(key, [evidence(key)]);
  }
  const otherWorkspace = h.context.workspaceCache.current.get(cacheKey('snapshot-b'));
  const otherMap = h.context.mapCache.current.get(cacheKey('snapshot-b'));
  const pending = h.render()(['shared-record']);
  assert.equal(h.requests[0].url, '/translations/titles');
  assert.equal(h.requests[0].body.snapshot_id, 'snapshot-a');
  h.requests[0].resolve(success('当前快照的中文标题'));
  const titles = await pending;
  assert.equal(titles['shared-record'], '当前快照的中文标题');
  for (const visible of [h.state.map, h.state.rows]) {
    assert.equal(visible[0].title, '当前快照的中文标题');
    assert.equal(visible[0].title_translation_state, 'ready');
    assert.equal(visible[0].text_translation_state, 'untranslated');
  }
  // Matching-snapshot caches may be updated or invalidated; other snapshots
  // must stay byte-for-byte/reference unchanged even when record IDs coincide.
  for (const key of [cacheKey('snapshot-a'), cacheKey('snapshot-a', 'diplomacy')]) {
    const workspace = h.context.workspaceCache.current.get(key);
    const map = h.context.mapCache.current.get(key);
    if (workspace) assert.equal(workspace.data.records[0].title, '当前快照的中文标题');
    if (map) assert.equal(map[0].title, '当前快照的中文标题');
  }
  assert.equal(h.context.workspaceCache.current.get(cacheKey('snapshot-b')), otherWorkspace);
  assert.equal(h.context.mapCache.current.get(cacheKey('snapshot-b')), otherMap);
  assert.equal(otherMap[0].title, cacheKey('snapshot-b'));
});

test('late success for any old scope cannot change current records, caches or cluster result', async () => {
  for (const changes of [
    {sid: 'snapshot-b'}, {topic: 'ukraine'}, {dimension: 'diplomacy'},
    {start: '2026-10-01'}, {end: '2026-10-20'},
  ]) {
    const h = harness();
    h.context.mapCache.current.set(cacheKey('snapshot-b'), [evidence('Revised B title')]);
    const otherMap = h.context.mapCache.current.get(cacheKey('snapshot-b'));
    const pending = h.render()(['shared-record']);
    h.render(changes);
    h.state.map = [evidence('Revised current title')];
    h.state.rows = [evidence('Revised current title')];
    const writes = h.state.writes;
    h.requests[0].resolve(success('旧范围的迟到中文标题'));
    const titles = await pending;
    assert.equal(Object.keys(titles).length, 0, 'MapView must receive no stale titles');
    assert.equal(h.state.writes, writes);
    assert.equal(h.state.map[0].title, 'Revised current title');
    assert.equal(h.state.rows[0].title, 'Revised current title');
    assert.equal(h.context.mapCache.current.get(cacheKey('snapshot-b')), otherMap);
    assert.equal(h.context.workspaceCache.current.size, 0);
  }
});

test('late old-scope failures cannot replace the current notice', async () => {
  const h = harness();
  const pending = h.render()(['shared-record']);
  h.render({sid: 'snapshot-b'});
  h.state.notice = 'Current scope notice';
  h.requests[0].reject(new Error('Old request failure'));
  const titles = await pending;
  assert.equal(Object.keys(titles).length, 0);
  assert.equal(h.state.notice, 'Current scope notice');
  assert.equal(h.state.writes, 0);
});

test('current failures are visible and leave original titles unchanged', async () => {
  const h = harness();
  const pending = h.render()(['shared-record']);
  h.requests[0].reject(new Error('Current request failure'));
  const titles = await pending;
  assert.equal(Object.keys(titles).length, 0);
  assert.match(h.state.notice, /Current request failure/);
  assert.equal(h.state.map[0].title, 'Original A title');
  assert.equal(h.state.rows[0].title, 'Original A title');
});
