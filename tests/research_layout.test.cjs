const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const loadTs = require('./load_ts.cjs');
const {
  DEFAULT_RESEARCH_WIDTH,
  MIN_RESEARCH_WIDTH,
  MAX_RESEARCH_WIDTH,
  clampResearchWidth,
  researchWidthAtPointer,
  researchWidthForKey,
} = loadTs('src/researchLayout.ts');
const {panelEntryScope} = loadTs('src/hostViewContext.ts');

test('research dock starts at 42 percent and keeps both panes within resize bounds', () => {
  assert.equal(DEFAULT_RESEARCH_WIDTH, 42);
  assert.equal(MIN_RESEARCH_WIDTH, 36);
  assert.equal(MAX_RESEARCH_WIDTH, 54);
  for (const value of [MIN_RESEARCH_WIDTH, 41.5, DEFAULT_RESEARCH_WIDTH, MAX_RESEARCH_WIDTH])
    assert.equal(clampResearchWidth(value), value);
  for (const value of [-100, 0, MIN_RESEARCH_WIDTH - 1])
    assert.equal(clampResearchWidth(value), MIN_RESEARCH_WIDTH);
  for (const value of [MAX_RESEARCH_WIDTH + 1, 100, Number.MAX_VALUE])
    assert.equal(clampResearchWidth(value), MAX_RESEARCH_WIDTH);
});

test('non-finite research widths recover to the default', () => {
  for (const value of [NaN, Infinity, -Infinity])
    assert.equal(clampResearchWidth(value), DEFAULT_RESEARCH_WIDTH);
});

test('pointer resize measures the right pane against viewport-relative container bounds', () => {
  assert.equal(researchWidthAtPointer(600, 20, 1000), 42);
  assert.equal(researchWidthAtPointer(1100, 520, 1000), 42);
  assert.equal(researchWidthAtPointer(604, 20, 1000), 42);
  assert.equal(researchWidthAtPointer(606, 20, 1000), 41);
  assert.equal(researchWidthAtPointer(560, 20, 1000), 46);
  assert.equal(researchWidthAtPointer(640, 20, 1000), 38);
});

test('pointer resize clamps at either edge and when dragging outside the container', () => {
  for (const clientX of [-1000, 20, 200])
    assert.equal(researchWidthAtPointer(clientX, 20, 1000), MAX_RESEARCH_WIDTH);
  for (const clientX of [1000, 1020, 2000])
    assert.equal(researchWidthAtPointer(clientX, 20, 1000), MIN_RESEARCH_WIDTH);
});

test('invalid pointer coordinates or unavailable container dimensions keep the default', () => {
  for (const value of [NaN, Infinity, -Infinity]) {
    assert.equal(researchWidthAtPointer(value, 20, 1000), DEFAULT_RESEARCH_WIDTH);
    assert.equal(researchWidthAtPointer(600, value, 1000), DEFAULT_RESEARCH_WIDTH);
    assert.equal(researchWidthAtPointer(600, 20, value), DEFAULT_RESEARCH_WIDTH);
  }
  for (const width of [0, -1, -1000])
    assert.equal(researchWidthAtPointer(600, 20, width), DEFAULT_RESEARCH_WIDTH);
});

test('keyboard separator movement widens leftward, narrows rightward and respects limits', () => {
  assert.equal(researchWidthForKey(42, 'ArrowLeft'), 44);
  assert.equal(researchWidthForKey(42, 'ArrowRight'), 40);
  assert.equal(researchWidthForKey(42, 'Home'), MIN_RESEARCH_WIDTH);
  assert.equal(researchWidthForKey(42, 'End'), MAX_RESEARCH_WIDTH);
  let width = DEFAULT_RESEARCH_WIDTH;
  for (let step = 0; step < 30; step++) width = researchWidthForKey(width, 'ArrowLeft');
  assert.equal(width, MAX_RESEARCH_WIDTH);
  for (let step = 0; step < 30; step++) width = researchWidthForKey(width, 'ArrowRight');
  assert.equal(width, MIN_RESEARCH_WIDTH);
});

test('unrelated keys preserve a valid width and invalid keyboard input recovers safely', () => {
  for (const key of ['Enter', 'Escape', 'Tab', 'ArrowUp', 'ArrowDown', ''])
    assert.equal(researchWidthForKey(43, key), 43);
  assert.equal(researchWidthForKey(100, 'Enter'), MAX_RESEARCH_WIDTH);
  assert.equal(researchWidthForKey(0, 'Enter'), MIN_RESEARCH_WIDTH);
  for (const width of [NaN, Infinity, -Infinity]) {
    assert.equal(researchWidthForKey(width, 'ArrowLeft'), DEFAULT_RESEARCH_WIDTH);
    assert.equal(researchWidthForKey(width, 'ArrowRight'), DEFAULT_RESEARCH_WIDTH);
    assert.equal(researchWidthForKey(width, 'Enter'), DEFAULT_RESEARCH_WIDTH);
  }
});

test('captured dock scope survives visible, hidden snapshot changes and return to chat', () => {
  let scope = panelEntryScope('', false, '');
  assert.equal(scope, '');
  scope = panelEntryScope(scope, Boolean(scope), 'snapshot-a');
  const captured = scope;
  assert.equal(captured, '.geo-research-assistant/snapshot-a');

  // Scope ownership follows initialization, not the visible tab. These calls
  // model the contract; browser checks must verify actual React/frame lifetime.
  for (const step of [
    {visible: true, snapshot: 'snapshot-a'},
    {visible: false, snapshot: 'snapshot-b'},
    {visible: false, snapshot: ''},
    {visible: true, snapshot: 'snapshot-b'},
  ]) {
    scope = panelEntryScope(scope, Boolean(scope), step.snapshot);
    assert.equal(scope, captured, `visible=${step.visible}, snapshot=${step.snapshot}`);
  }
});

// Source-level integration guards catch accidental navigation or duplicate
// frame placement. They do not prove mounted DOM identity or browser behavior.
const app = ts.createSourceFile(
  'App.tsx',
  fs.readFileSync(path.join(__dirname, '../src/App.tsx'), 'utf8'),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);

function descendants(root, predicate) {
  const matches = [];
  function visit(node) {
    if (predicate(node)) matches.push(node);
    ts.forEachChild(node, visit);
  }
  visit(root);
  return matches;
}
function opening(node) {
  if (ts.isJsxElement(node)) return node.openingElement;
  if (ts.isJsxSelfClosingElement(node)) return node;
  return null;
}
function attribute(node, name) {
  return opening(node)?.attributes.properties.find(
    item => ts.isJsxAttribute(item) && item.name.getText(app) === name,
  )?.initializer;
}
function literalAttribute(node, name) {
  const value = attribute(node, name);
  return value && ts.isStringLiteral(value) ? value.text : undefined;
}
function ancestors(node) {
  const parents = [];
  for (let parent = node.parent; parent; parent = parent.parent) parents.push(parent);
  return parents;
}
function hasIdentifier(node, name) {
  return descendants(node, item => ts.isIdentifier(item) && item.text === name).length > 0;
}
function calls(root, name) {
  return descendants(root, node => ts.isCallExpression(node)
    && ts.isIdentifier(node.expression) && node.expression.text === name);
}

test('App places one embedded iframe inside the persistent right chat pane', () => {
  const frames = descendants(app, node => opening(node)?.tagName.getText(app) === 'iframe');
  assert.equal(frames.length, 1);
  const parents = ancestors(frames[0]);
  assert.ok(parents.some(node => literalAttribute(node, 'id') === 'research-chat-pane'));
  assert.ok(parents.some(node => literalAttribute(node, 'id') === 'research-dock'));
  assert.ok(!parents.some(node => opening(node)?.tagName.getText(app) === 'main'));

  const key = attribute(frames[0], 'key');
  assert.ok(key && ts.isJsxExpression(key) && key.expression);
  assert.ok(ts.isIdentifier(key.expression) && key.expression.text === 'connectionVersion');
  for (const parent of parents) {
    const condition = ts.isConditionalExpression(parent) ? parent.condition
      : ts.isBinaryExpression(parent)
        && parent.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken ? parent.left : null;
    if (condition) {
      assert.equal(hasIdentifier(condition, 'chatOpen'), false,
        'Changing right tabs must not conditionally remove the iframe');
      assert.equal(hasIdentifier(condition, 'chatExpanded'), false,
        'Expanding the dock must not conditionally remove the iframe');
    }
  }
});

test('left business menu retains its eight views and has no chat route', () => {
  const menus = descendants(app, node => literalAttribute(node, 'className') === 'monitor-tabs');
  assert.equal(menus.length, 1);
  const menuMaps = descendants(menus[0], node => ts.isCallExpression(node)
    && ts.isPropertyAccessExpression(node.expression)
    && node.expression.name.text === 'map'
    && ts.isArrayLiteralExpression(node.expression.expression));
  assert.equal(menuMaps.length, 1);
  const ids = menuMaps[0].expression.expression.elements.map(item => {
    assert.ok(ts.isArrayLiteralExpression(item));
    assert.ok(ts.isStringLiteral(item.elements[0]));
    return item.elements[0].text;
  });
  assert.deepEqual(ids, ['map', 'evidence', 'scenarios', 'sources', 'pipeline', 'metrics', 'assets', 'briefs']);
  assert.equal(calls(menus[0], 'setChatOpen').length, 0);
});

test('business actions never navigate the left workspace into the retired chat route', () => {
  const tabChanges = calls(app, 'setTab');
  assert.ok(tabChanges.length > 0);
  assert.equal(tabChanges.some(call => call.arguments.some(argument =>
    descendants(argument, node => ts.isStringLiteral(node) && node.text === 'chat').length > 0,
  )), false);
});

test('opening the dock uses captured scope state and does not request a new connection', () => {
  const entries = descendants(app, node => ts.isVariableDeclaration(node)
    && ts.isIdentifier(node.name) && node.name.text === 'openResearchAssistant');
  assert.equal(entries.length, 1);
  const scopeCalls = calls(entries[0], 'panelEntryScope');
  assert.equal(scopeCalls.length, 1);
  assert.equal(hasIdentifier(scopeCalls[0].arguments[1], 'chatOpen'), false);
  assert.ok(hasIdentifier(scopeCalls[0].arguments[1], 'embedWorkspaceScope'));
  assert.equal(calls(entries[0], 'setConnectionVersion').length, 0);
});

test('unconfigured embedded chat uses local QA without disabling a hidden configured session', () => {
  const identity = calls(app, 'useEmbedIdentity');
  const bridge = calls(app, 'useHostViewContext');
  assert.equal(identity.length, 1);
  assert.equal(bridge.length, 1);
  for (const enabled of [identity[0].arguments[5], bridge[0].arguments[4]]) {
    assert.ok(hasIdentifier(enabled, 'pichatEmbedConfigured'));
    assert.ok(hasIdentifier(enabled, 'embedWorkspaceScope'));
    assert.equal(hasIdentifier(enabled, 'chatOpen'), false);
  }
  assert.ok(hasIdentifier(bridge[0].arguments[6], 'chatOpen'));
});

test('local QA keeps a separate bottom composer row in the research dock', () => {
  const css = fs.readFileSync(path.join(__dirname, '../src/research-dock.css'), 'utf8');
  assert.match(css, /\.research-chat-pane\s*>\s*\.chat-panel-local\s*\{[^}]*grid-template-rows:\s*auto minmax\(0,\s*1fr\) auto/s);
  assert.match(css, /\.chat-panel-local\s*>\s*\.chat-body\s*\{[^}]*min-height:\s*0;[^}]*overflow:\s*auto/s);
});
