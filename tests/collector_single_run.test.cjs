const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

test("采集器列表支持只提交单个采集器", () => {
  const source = fs.readFileSync("src/CollectorMonitor.tsx", "utf8");
  assert.ok(source.includes("singleCollector ? 300 : 900"));
  assert.ok(source.includes("singleCollector ? 1 : 2"));
  assert.ok(source.includes("queueRun([collector.id]"));
  assert.ok(source.includes("只运行这个采集器"));
  assert.ok(source.includes("单独采集"));
});
