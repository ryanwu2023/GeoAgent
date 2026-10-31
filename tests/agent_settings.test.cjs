const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const ts = require("typescript");
const vm = require("node:vm");

function loadModule(path) {
  const source = fs.readFileSync(path, "utf8");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(`(function(require,module,exports){${output}\n})(require,module,module.exports)`, { require, module });
  return module.exports;
}

const { settingsToDraft, validateAgentSettings, draftToPayload } = loadModule("src/agent_settings.ts");
const base = settingsToDraft({
  per_agent_timeout_seconds: 70, total_timeout_seconds: 150, max_parallel: 2,
  input_price_per_million: null, output_price_per_million: null, max_cost_cny: null,
});

test("费用上限留空时不限制", () => {
  assert.equal(validateAgentSettings(base), "");
  assert.equal(draftToPayload(base).max_cost_cny, null);
});

test("填写费用上限时要求输入输出单价", () => {
  assert.match(validateAgentSettings({ ...base, max_cost_cny: "2" }), /同时填写/);
  assert.equal(validateAgentSettings({ ...base, max_cost_cny: "2", input_price_per_million: "0", output_price_per_million: "8" }), "");
});

test("校验两级超时与并发数", () => {
  assert.match(validateAgentSettings({ ...base, per_agent_timeout_seconds: "200", total_timeout_seconds: "150" }), /不能小于/);
  assert.match(validateAgentSettings({ ...base, max_parallel: "4" }), /1 到 3/);
});

test("配置页公开 Agent 标签和后端接口", () => {
  const host = fs.readFileSync("src/IntegrationSettings.tsx", "utf8");
  const form = fs.readFileSync("src/AgentSettings.tsx", "utf8");
  assert.ok(host.includes('id: "agent", title: "智能问答 Agent"'));
  assert.ok(host.includes("<AgentSettings onSaved={onSaved}/>"));
  assert.ok(form.includes('"/api/qa-agent/settings"'));
  assert.ok(form.includes("留空表示无限制"));
});
