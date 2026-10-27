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
const effects = findAll(source, node =>
  ts.isCallExpression(node) && ts.isIdentifier(node.expression)
  && node.expression.text === 'useEffect'
  && findAll(node.arguments[0], child =>
    ts.isStringLiteral(child) && child.text === '/map-records?').length > 0);
assert.equal(effects.length, 1, 'one map loading effect must own the visible records');
const effect = effects[0];

// Execute the real App effect and its returned cleanup against deferred API
// responses. These are request-ordering tests, not a substitute for browser QA.
function harness() {
  const state = {records: [], meetings: [], loading: false, error: '', writes: 0};
  const requests = [];
  const context = vm.createContext({
    sid: 'snapshot-a', topic: 'usiran', dimension: '', start: '', end: '', tab: 'map',
    URLSearchParams,
    mapCache: {current: new Map()},
    meetingMapCache: {current: new Map()},
    api: url => new Promise((resolve, reject) => requests.push({url, resolve, reject})),
    apiMeetings: () => Promise.resolve({records: []}),
    setMapRecords: value => { state.records = value; state.writes++; },
    setMapMeetings: value => { state.meetings = value; state.writes++; },
    setMapLoading: value => { state.loading = value; state.writes++; },
    setError: value => { state.error = value; state.writes++; },
  });
  const compiled = ts.transpileModule(
    `globalThis.runMapEffect = ${effect.arguments[0].getText(source)};`,
    {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}},
  ).outputText;
  vm.runInContext(compiled, context);
  let cleanup;
  function render(changes = {}) {
    cleanup?.();
    Object.assign(context, changes);
    cleanup = context.runMapEffect();
  }
  function unmount() {
    cleanup?.();
    cleanup = undefined;
  }
  return {state, requests, context, render, unmount};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const cacheKey = request => request.url.slice('/map-records?'.length);
function settle(request, rejected, label) {
  if (rejected) request.reject(new Error(label));
  else request.resolve([{id: label}]);
}

test('map effect observes every view scope field and the active business tab', () => {
  assert.deepEqual(effect.arguments[1].elements.map(item => item.getText(source)),
    ['sid', 'topic', 'dimension', 'start', 'end', 'tab']);
});

test('current map results populate the matching cache and finish loading', async () => {
  const h = harness();
  h.render();
  assert.equal(h.state.loading, true);
  const records = [{id: 'current'}];
  h.requests[0].resolve(records);
  await flush();
  assert.equal(h.state.records, records);
  assert.equal(h.context.mapCache.current.get(cacheKey(h.requests[0])), records);
  assert.equal(h.state.loading, false);
});

test('scope changes reject old success and finally before or after the current response', async () => {
  for (const changes of [
    {sid: 'snapshot-b'}, {topic: 'ukraine'}, {dimension: 'diplomacy'},
    {start: '2026-10-01'}, {end: '2026-10-20'},
  ]) {
    for (const oldFinishesFirst of [false, true]) {
      const h = harness();
      h.render();
      h.render(changes);
      assert.equal(h.requests.length, 2);
      if (oldFinishesFirst) {
        settle(h.requests[0], false, 'old');
        await flush();
        assert.equal(h.state.records.length, 0);
        assert.equal(h.state.loading, true);
      }
      settle(h.requests[1], false, 'current');
      await flush();
      if (!oldFinishesFirst) {
        settle(h.requests[0], false, 'old');
        await flush();
      }
      assert.equal(h.state.records[0].id, 'current');
      assert.equal(h.state.loading, false);
      assert.equal(h.context.mapCache.current.has(cacheKey(h.requests[0])), false);
    }
  }
});

test('an old failure cannot overwrite the error or finish a newer pending request', async () => {
  const h = harness();
  h.render();
  h.render({sid: 'snapshot-b'});
  settle(h.requests[0], true, 'old failure');
  await flush();
  assert.equal(h.state.error, '');
  assert.equal(h.state.loading, true);
  settle(h.requests[1], true, 'current failure');
  await flush();
  assert.equal(h.state.error, 'current failure');
  assert.equal(h.state.loading, false);
});

test('leaving the map or clearing its snapshot invalidates every old callback', async () => {
  for (const changes of [{tab: 'metrics'}, {sid: ''}]) {
    for (const rejected of [false, true]) {
      const h = harness();
      h.render();
      h.render(changes);
      assert.equal(h.requests.length, 1);
      assert.equal(h.state.loading, false);
      const writes = h.state.writes;
      settle(h.requests[0], rejected, 'obsolete');
      await flush();
      assert.equal(h.state.writes, writes);
      assert.equal(h.context.mapCache.current.size, 0);
    }
  }
});

test('unmounted map effects perform no state writes on late success or failure', async () => {
  for (const rejected of [false, true]) {
    const h = harness();
    h.render();
    h.unmount();
    const writes = h.state.writes;
    settle(h.requests[0], rejected, 'after unmount');
    await flush();
    assert.equal(h.state.writes, writes);
    assert.equal(h.context.mapCache.current.size, 0);
  }
});

test('a cache hit clears loading and cannot be replaced by the prior pending request', async () => {
  for (const rejected of [false, true]) {
    const h = harness();
    h.render();
    const nextKey = new URLSearchParams({
      snapshot_id: 'snapshot-b', topic: 'usiran', dimension: '', start: '', end: '',
    }).toString();
    const cached = [{id: 'cached-b'}];
    h.context.mapCache.current.set(nextKey, cached);
    h.context.meetingMapCache.current.set(nextKey, []);
    h.render({sid: 'snapshot-b'});
    assert.equal(h.requests.length, 1);
    assert.equal(h.state.records, cached);
    assert.equal(h.state.loading, false);
    const writes = h.state.writes;
    settle(h.requests[0], rejected, 'old');
    await flush();
    assert.equal(h.state.writes, writes);
    assert.equal(h.state.records, cached);
    assert.equal(h.state.error, '');
  }
});
