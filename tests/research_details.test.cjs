const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const {renderToStaticMarkup} = require('react-dom/server');

const source = ts.createSourceFile(
  'App.tsx',
  fs.readFileSync(path.join(__dirname, '../src/App.tsx'), 'utf8'),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
function findAll(root, predicate) {
  const result = [];
  function visit(node) {
    if (predicate(node)) result.push(node);
    ts.forEachChild(node, visit);
  }
  visit(root);
  return result;
}
function one(nodes) {
  assert.equal(nodes.length, 1);
  return nodes[0];
}
function calls(root, name) {
  return findAll(root, node => ts.isCallExpression(node)
    && ts.isIdentifier(node.expression) && node.expression.text === name);
}
function jsxAttribute(node, name) {
  const opening = ts.isJsxElement(node) ? node.openingElement
    : ts.isJsxSelfClosingElement(node) ? node : null;
  return opening?.attributes.properties.find(item =>
    ts.isJsxAttribute(item) && item.name.getText(source) === name)?.initializer;
}
function expressionAttribute(node, name) {
  const value = jsxAttribute(node, name);
  assert.ok(value && ts.isJsxExpression(value) && value.expression);
  return value.expression;
}
function evaluate(code, context) {
  vm.runInContext(ts.transpileModule(code, {
    fileName: 'details-test.tsx',
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX},
  }).outputText, context);
}

// Run the actual App request functions and UI callbacks against deferred API
// responses. This verifies async ordering without claiming a browser lifecycle test.
function harness() {
  const state = {selected: null, asset: null, metric: null, error: '', notice: '', chatOpen: true, translating: false};
  const requests = [];
  const context = vm.createContext({
    sid: 'snapshot-a',
    selected: {id: 'record-a'},
    sourceMetric: null,
    detailRequestVersion: {current: 0},
    selectionVersion: {current: 17},
    api: url => new Promise((resolve, reject) => requests.push({url, resolve, reject})),
    setSelected: value => { state.selected = value; },
    setAsset: value => { state.asset = value; },
    setSourceMetric: value => { state.metric = value; },
    setError: value => { state.error = value; },
    setNotice: value => { state.notice = value; },
    setChatOpen: value => { state.chatOpen = value; },
    setTranslating: value => { state.translating = value; },
    openResearchAssistant: () => { state.chatOpen = true; },
  });
  const functions = ['closeDetails', 'openAsset', 'openEvidence'].map(name => one(findAll(source,
    node => ts.isFunctionDeclaration(node) && node.name?.text === name)).getText(source));
  const dock = one(findAll(source, node => ts.isJsxSelfClosingElement(node)
    && node.tagName.getText(source) === 'ResearchDockHeading'));
  const closeButton = label => one(findAll(source, node => {
    const attribute = jsxAttribute(node, 'aria-label');
    return attribute && ts.isStringLiteral(attribute) && attribute.text === label;
  }));
  const scopeEffect = one(calls(source, 'useEffect').filter(call =>
    calls(call.arguments[0], 'closeDetails').length > 0
    && call.arguments[1] && ts.isArrayLiteralExpression(call.arguments[1])
    && call.arguments[1].elements.some(item => ts.isIdentifier(item) && item.text === 'workingSid')));
  assert.deepEqual(scopeEffect.arguments[1].elements.map(item => item.getText(source)),
    ['sid', 'workingSid', 'topic', 'dimension', 'start', 'end']);
  const translation = one(findAll(source, node => ts.isJsxAttribute(node)
    && node.name.getText(source) === 'onClick' && calls(node, 'setTranslating').length > 0));
  evaluate(`${functions.join('\n')}
    globalThis.openEvidence = openEvidence;
    globalThis.openAsset = openAsset;
    globalThis.closeEvidence = ${expressionAttribute(closeButton('关闭证据详情'), 'onClick').getText(source)};
    globalThis.closeAsset = ${expressionAttribute(closeButton('关闭资产详情'), 'onClick').getText(source)};
    globalThis.changeTab = ${expressionAttribute(dock, 'onTabChange').getText(source)};
    globalThis.rangeChanged = ${scopeEffect.arguments[0].getText(source)};
    globalThis.translate = ${translation.initializer.expression.getText(source)};
  `, context);
  return {state, requests, context};
}

test('the most recently requested evidence wins when responses arrive out of order', async () => {
  const h = harness(), metric = {id: 'metric-b'};
  const old = h.context.openEvidence('record-a');
  const current = h.context.openEvidence('record-b', metric);
  h.requests[1].resolve({id: 'record-b'});
  await current;
  h.requests[0].resolve({id: 'record-a'});
  await old;
  assert.equal(h.state.selected.id, 'record-b');
  assert.equal(h.state.metric, metric);
  assert.equal(h.context.selectionVersion.current, 17);
});

test('switching either right tab prevents a late evidence response from reopening details', async () => {
  for (const next of [false, true]) {
    const h = harness(), pending = h.context.openEvidence('record-a');
    h.context.changeTab(next);
    h.requests[0].resolve({id: 'record-a'});
    await pending;
    assert.equal(h.state.selected, null);
    assert.equal(h.state.asset, null);
    assert.equal(h.state.chatOpen, next);
    assert.equal(h.context.selectionVersion.current, 17);
  }
});

test('both detail close buttons invalidate pending evidence, including failed requests', async () => {
  for (const close of ['closeEvidence', 'closeAsset']) {
    for (const rejected of [false, true]) {
      const h = harness(), pending = h.context.openEvidence('record-a');
      h.context[close]();
      if (rejected) h.requests[0].reject(new Error('stale failure'));
      else h.requests[0].resolve({id: 'record-a'});
      await pending;
      assert.equal(h.state.selected, null);
      assert.equal(h.state.asset, null);
      assert.equal(h.state.error, '');
    }
  }
});

test('late evidence success or failure cannot clear a newly opened asset or report an old error', async () => {
  for (const rejected of [false, true]) {
    const h = harness(), pending = h.context.openEvidence('record-a');
    const asset = {id: 'oil'};
    h.context.openAsset(asset);
    if (rejected) h.requests[0].reject(new Error('stale failure'));
    else h.requests[0].resolve({id: 'record-a'});
    await pending;
    assert.equal(h.state.asset, asset);
    assert.equal(h.state.selected, null);
    assert.equal(h.state.error, '');
  }
});

test('changing the working range or unmounting invalidates old detail requests', async () => {
  const h = harness(), pending = h.context.openEvidence('record-a');
  const cleanup = h.context.rangeChanged();
  h.requests[0].resolve({id: 'record-a'});
  await pending;
  assert.equal(h.state.selected, null);
  const afterChange = h.context.openEvidence('record-b');
  cleanup();
  h.requests[1].reject(new Error('response after unmount'));
  await afterChange;
  assert.equal(h.state.error, '');
  assert.equal(h.context.selectionVersion.current, 17);
});

test('a current detail error is still displayed and missing snapshots issue no request', async () => {
  const h = harness(), pending = h.context.openEvidence('record-a');
  h.requests[0].reject(new Error('current failure'));
  await pending;
  assert.equal(h.state.error, 'current failure');
  h.context.sid = '';
  await h.context.openEvidence('record-b');
  assert.equal(h.requests.length, 1);
});

test('late translation completion cannot reopen dismissed evidence or show stale notices', async () => {
  for (const rejected of [false, true]) {
    const h = harness(), pending = h.context.translate();
    h.context.closeEvidence();
    if (rejected) h.requests[0].reject(new Error('late translation'));
    else h.requests[0].resolve({ok: true, message: 'finished'});
    await pending;
    assert.equal(h.requests.length, 1);
    assert.equal(h.state.selected, null);
    assert.equal(h.state.error, '');
    assert.equal(h.state.notice, '');
    assert.equal(h.state.translating, false);
  }
});

function emptyDock(status, workingSid, sid) {
  const empty = one(findAll(source, node => {
    const className = jsxAttribute(node, 'className');
    const role = jsxAttribute(node, 'role');
    return className && ts.isStringLiteral(className) && className.text === 'empty'
      && role && ts.isStringLiteral(role) && role.text === 'status';
  }));
  const selections = [];
  const context = vm.createContext({exports: {}, require, status, workingSid, sid,
    selectSnapshot: id => { selections.push(id); }});
  evaluate(`globalThis.element = (${empty.getText(source)});`, context);
  return {element: context.element, html: renderToStaticMarkup(context.element), selections};
}
function buttons(element) {
  if (!element || typeof element !== 'object') return [];
  if (Array.isArray(element)) return element.flatMap(buttons);
  return [...(element.type === 'button' ? [element] : []), ...buttons(element.props?.children)];
}

test('a stored but unconfirmed snapshot offers explicit confirmation instead of endless loading', () => {
  const h = emptyDock({snapshots: [{id: 'snapshot-a'}]}, '', 'snapshot-a');
  assert.match(h.html, /请先确认工作快照/);
  assert.doesNotMatch(h.html, /正在加载工作快照/);
  assert.deepEqual(h.selections, []);
  one(buttons(h.element)).props.onClick();
  assert.deepEqual(h.selections, ['snapshot-a']);
});

test('an empty snapshot pool requests import while a pending initial load remains a loading state', () => {
  const empty = emptyDock({snapshots: []}, '', '');
  assert.match(empty.html, /请先导入并选择工作快照/);
  assert.equal(buttons(empty.element).length, 0);
  const loading = emptyDock(null, '', '');
  assert.match(loading.html, /正在加载工作快照/);
  assert.equal(buttons(loading.element).length, 0);
  const initializing = emptyDock({snapshots: [{id: 'snapshot-a'}]}, 'snapshot-a', 'snapshot-a');
  assert.match(initializing.html, /正在加载工作快照/);
  assert.equal(buttons(initializing.element).length, 0);
});
