const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

test("本地问答向页面传递并展示估算费用", () => {
  const backend = fs.readFileSync("backend/research.py", "utf8");
  const app = fs.readFileSync("src/App.tsx", "utf8");
  assert.ok(backend.includes('base["cost_estimate"] = outcome.get("cost_estimate", {})'));
  assert.ok(app.includes("本轮模型费用估算"));
  assert.ok(app.includes("不代表服务商账单"));
});
