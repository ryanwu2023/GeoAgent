const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");
const React = require("react");
const {renderToStaticMarkup} = require("react-dom/server");

// Render the actual insight-summary JSX with an intentionally empty records
// payload. The lightweight API keeps aggregates even when rows are omitted.
const source = ts.createSourceFile("App.tsx", fs.readFileSync("src/App.tsx", "utf8"),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let summary;
function visit(node) {
  if (ts.isJsxElement(node) && node.openingElement.attributes.properties.some(
    attr => ts.isJsxAttribute(attr) && attr.name.text === "className"
      && attr.initializer?.text === "insight-summary",
  )) summary = node.getText(source);
  ts.forEachChild(node, visit);
}
visit(source);
assert.ok(summary, "the production insight summary exists");
const context = vm.createContext({React});
vm.runInContext(ts.transpileModule(
  `globalThis.renderSummary = (work) => {
    const dimensionCount = 13, workingSid = "snapshot", dateText = () => "";
    return (${summary});
  };`,
  {compilerOptions: {jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022}},
).outputText, context);

test("insight renders scope aggregates without requiring workspace or map rows", () => {
  const html = renderToStaticMarkup(context.renderSummary({
    records: [], records_count: 2301,
    signal_counts: {escalation: 92, sustain: 54, easing: 135, unknown: 2020},
    snapshot: {created_at: "2026-09-30"},
  }));
  for (const [label, count] of [["升级相关", 92], ["持续相关", 54], ["缓和相关", 135]]) {
    assert.ok(html.includes(`<span>${label}</span><b>${count}</b>`));
  }
  assert.ok(html.includes("<b>2301</b><small>范围内记录</small>"));
});

test("empty scope renders actual zero counts", () => {
  const html = renderToStaticMarkup(context.renderSummary({
    records: [], records_count: 0,
    signal_counts: {escalation: 0, sustain: 0, easing: 0, unknown: 0},
    snapshot: {created_at: "2026-09-30"},
  }));
  assert.ok(html.includes("<span>升级相关</span><b>0</b>"));
  assert.ok(html.includes("<b>0</b><small>范围内记录</small>"));
});
