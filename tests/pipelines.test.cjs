const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const compile = (file) => ts.transpileModule(fs.readFileSync(path.join(__dirname, "..", "src", file), "utf8"),
  {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}}).outputText;

// storage 为 undefined 时 localStorage 访问抛 ReferenceError 之外的错误；"throw" 时方法抛错。
function loadWith(fetchImpl, storage) {
  const deny = {getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); }};
  const ls = storage === "throw" || !storage ? deny : storage;
  const win = {dispatchEvent() {}};
  const run = (code, extra) => { const mod = {exports: {}};
    new Function("module", "exports", "require", "fetch", "window", "localStorage", code)(mod, mod.exports, extra, fetchImpl, win, ls);
    return mod.exports; };
  const session = run(compile("sessionFetch.ts"));
  const out = run(compile("pipelines.ts"), (n) => { assert.equal(n, "./sessionFetch"); return session; });
  // sessionFetch 读取 globalThis.fetch 与 window，用例期间临时替换。
  const prev = {fetch: globalThis.fetch, window: globalThis.window};
  globalThis.fetch = fetchImpl; globalThis.window = win;
  out.restore = () => { globalThis.fetch = prev.fetch; globalThis.window = prev.window; };
  return out;
}
const m = loadWith(async () => { throw new Error("no fetch"); });
const resp = (status, body) => ({ok: status < 400, status, clone() { return this; },
  json: async () => body, text: async () => (body === undefined ? "" : JSON.stringify(body))});

test("runLabel", () => {
  assert.equal(m.runLabel({trigger: "batch"}), "整批运行");
  assert.equal(m.runLabel({trigger: "trial"}), "测试运行（不进快照）");
});
test("unseenCount 查看后清零", () => {
  const p = {id: "p1", last_run_id: "r2", last_new_items: 7};
  assert.equal(m.unseenCount(p, {}), 7);
  assert.equal(m.unseenCount(p, {p1: "r2"}), 0);
  assert.equal(m.unseenCount({id: "p2", last_run_id: null, last_new_items: 0}, {}), 0);
});
test("conflictMessage", () => {
  const e = new m.PipelineApiError(409, {code: "VERSION_CONFLICT", message: "x", current_version: 4});
  assert.equal(m.conflictMessage(e), "该管线已被修改（当前第 4 版），请刷新后再编辑");
  assert.equal(m.conflictMessage(new m.PipelineApiError(409, "x")), null);
  assert.equal(m.conflictMessage(new m.PipelineApiError(400, {current_version: 4})), null);
});
test("splitTerms 支持中英文分隔", () => {
  assert.deepEqual(m.splitTerms("a, b\n c、d，e\n\na"), ["a", "b", "c", "d", "e"]);
  assert.deepEqual(m.splitTerms("伊朗、核\n制裁,伊朗"), ["伊朗", "核", "制裁"]);
  assert.deepEqual(m.splitTerms(""), []);
});
test("PipelineApiError 归一化 message 与 field", () => {
  let e = new m.PipelineApiError(422, {code: "INVALID", message: "太长", field: "sources[0].id"});
  assert.equal(e.message, "太长"); assert.equal(e.field, "sources[0].id"); assert.equal(e.status, 422);
  e = new m.PipelineApiError(422, [{loc: ["body", "limit"], msg: "bad", type: "x"}]);
  assert.equal(e.message, "body.limit: bad"); assert.equal(e.field, undefined);
  e = new m.PipelineApiError(403, "仅管理员可以管理采集管线");
  assert.equal(e.message, "仅管理员可以管理采集管线");
  e = new m.PipelineApiError(500, undefined);
  assert.equal(e.message, "请求失败");
  assert.equal(e.detail, undefined);
});
test("isAccessDenied", () => {
  assert.equal(m.isAccessDenied(new m.PipelineApiError(403, "x")), true);
  assert.equal(m.isAccessDenied(new m.PipelineApiError(404, "x")), true);
  assert.equal(m.isAccessDenied(new m.PipelineApiError(500, "x")), false);
  assert.equal(m.isAccessDenied(new Error("x")), false);
});
test("emptyDraft 是合法骨架", () => {
  const d = m.emptyDraft(["usiran"]);
  assert.deepEqual(d.row, {name: "", description: "", topics: ["usiran"]});
  assert.deepEqual(d.config, {
    schema_version: "pipeline/1", sources: [],
    filters: {include_any: [], include_strong: [], exclude: [], require_category: true, lookback_days: 14, max_candidates_per_source: 80},
    request: {timeout_seconds: 25, rate_limit_seconds: 1.5, max_pages_per_source: 3},
  });
  const t = ["ukraine"]; const d2 = m.emptyDraft(t); t.push("x");
  assert.deepEqual(d2.row.topics, ["ukraine"]);
});
test("seen 读写容错", () => {
  const store = {}; 
  const a = loadWith(async () => resp(200, {}), {getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; }});
  a.writeSeen({p1: "r1"});
  assert.equal(store["geoagent.pipelines.seen"], '{"p1":"r1"}');
  assert.deepEqual(a.readSeen(), {p1: "r1"});
  store["geoagent.pipelines.seen"] = "{bad"; assert.deepEqual(a.readSeen(), {});
  store["geoagent.pipelines.seen"] = "[1]"; assert.deepEqual(a.readSeen(), {});
  a.restore();
  const b = loadWith(async () => resp(200, {}), "throw");
  assert.deepEqual(b.readSeen(), {}); b.writeSeen({x: "y"});
  b.restore();
});
test("API 请求形态", async () => {
  const calls = [];
  const a = loadWith(async (url, init) => { calls.push([url, init]); return resp(200, {items: [], server_time: "t"}); });
  await a.listPipelines({topic: "usiran", status: "enabled", q: "", changed_since: "2026-11-11T10:00:00+00:00"});
  assert.equal(calls[0][0], "/api/pipelines?topic=usiran&status=enabled&changed_since=2026-11-11T10%3A00%3A00%2B00%3A00");
  await a.getPipeline("a/b"); assert.equal(calls[1][0], "/api/pipelines/a%2Fb");
  await a.updatePipeline("p1", 3, {row: {name: "n"}, config: null, change_note: "c"});
  assert.equal(calls[2][0], "/api/pipelines/p1"); assert.equal(calls[2][1].method, "PATCH");
  assert.equal(calls[2][1].headers["If-Match"], "3");
  assert.equal(calls[2][1].headers["Content-Type"], "application/json");
  await a.setPipelineEnabled("p1", true); assert.equal(calls[3][0], "/api/pipelines/p1/enable"); assert.equal(calls[3][1].method, "POST");
  await a.setPipelineEnabled("p1", false); assert.equal(calls[4][0], "/api/pipelines/p1/disable");
  await a.rollbackPipeline("p1", 2, 5);
  assert.deepEqual(JSON.parse(calls[5][1].body), {to_version: 2, expected_version: 5});
  await a.deletePipeline("p1"); assert.equal(calls[6][1].method, "DELETE");
  await a.trialPipeline("p1"); assert.equal(calls[7][0], "/api/pipelines/p1/trial");
  await a.previewPipeline({row: {}, config: {}, limit: 5}); assert.equal(calls[8][0], "/api/pipelines/preview");
  await a.listVersions("p1"); assert.equal(calls[9][0], "/api/pipelines/p1/versions");
  await a.listRuns("p1"); assert.equal(calls[10][0], "/api/pipelines/p1/runs");
  await a.listRunItems("p1", "r1", 50); assert.equal(calls[11][0], "/api/pipelines/p1/runs/r1/items?offset=50&limit=50");
  await a.createPipeline({row: {}, config: {}, change_note: ""}); assert.equal(calls[12][1].method, "POST");
  a.restore();
});
test("错误响应抛 PipelineApiError", async () => {
  const a = loadWith(async () => resp(409, {detail: {code: "VERSION_CONFLICT", message: "冲突", current_version: 7}}));
  await assert.rejects(a.getPipeline("p"), (e) => e instanceof a.PipelineApiError && e.status === 409
    && e.detail.current_version === 7 && a.conflictMessage(e) === "该管线已被修改（当前第 7 版），请刷新后再编辑");
  a.restore();
  const b = loadWith(async () => ({ok: false, status: 502, clone() { return this; }, json: async () => { throw new Error("x"); }, text: async () => "bad gateway"}));
  await assert.rejects(b.getPipeline("p"), (e) => e.status === 502 && e.message === "bad gateway");
  b.restore();
});
test("latestTrial 取最近一次测试运行", () => {
  const runs = [{run_id: "b", trigger: "batch"}, {run_id: "t2", trigger: "trial"}, {run_id: "t1", trigger: "trial"}];
  assert.equal(m.latestTrial(runs).run_id, "t2");
  assert.equal(m.latestTrial([{run_id: "b", trigger: "batch"}]), null);
  assert.equal(m.latestTrial([]), null);
});
test("newItemsOf 读取 stats.new_items", () => {
  assert.equal(m.newItemsOf({stats: {new_items: 3}}), 3);
  assert.equal(m.newItemsOf({stats: null}), null);
  assert.equal(m.newItemsOf({stats: {new_items: "x"}}), null);
});
test("fieldHit 匹配字段及其子路径", () => {
  assert.equal(m.fieldHit("row.name", "row.name"), true);
  assert.equal(m.fieldHit("sources[0].entry_urls[1]", "sources[0].entry_urls"), true);
  assert.equal(m.fieldHit("sources[0].id", "sources[0]"), true);
  assert.equal(m.fieldHit("sources[1].id", "sources[0]"), false);
  assert.equal(m.fieldHit("sources[10].id", "sources[1]"), false);
  assert.equal(m.fieldHit("row.names", "row.name"), false);
  assert.equal(m.fieldHit(undefined, "row.name"), false);
  assert.equal(m.fieldHit("filters", "filters.include_any"), true, "合计超限时三个词框都标红");
});
test("tabForField 定位到所在详情页签", () => {
  assert.equal(m.tabForField("row.topics"), "basic");
  assert.equal(m.tabForField("sources[2].feed_url"), "sources");
  assert.equal(m.tabForField("sources"), "sources");
  assert.equal(m.tabForField("request.max_pages_per_source"), "sources");
  assert.equal(m.tabForField("filters.exclude"), "keywords");
  assert.equal(m.tabForField("filters"), "keywords");
  assert.equal(m.tabForField(undefined), null);
  assert.equal(m.tabForField("exclude_ids"), null);
});
test("hostsFromUrls 提取小写主机名", () => {
  assert.deepEqual(m.hostsFromUrls(["https://WWW.Reuters.com/world", "http://www.reuters.com/x", "bad", "ftp://a.b/c"]), ["www.reuters.com"]);
  assert.deepEqual(m.hostsFromUrls([]), []);
});
test("draftOf 深拷贝管线为可编辑草稿", () => {
  const p = {name: "n", description: "d", topics: ["usiran"], config: m.emptyDraft(["usiran"]).config};
  const d = m.draftOf(p);
  assert.deepEqual(d.row, {name: "n", description: "d", topics: ["usiran"]});
  d.config.filters.exclude.push("x"); d.row.topics.push("ukraine");
  assert.deepEqual(p.config.filters.exclude, []); assert.deepEqual(p.topics, ["usiran"]);
  const b = m.draftOf({name: "b", description: "", topics: ["ukraine"]});
  assert.deepEqual(b.config, m.emptyDraft([]).config, "内置管线没有 config 时用空骨架");
});
test("类型与后端形态一致", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "pipelines.ts"), "utf8");
  assert.ok(/export type PreviewResult = \{sources: Array<\{source_id: string; state: string; error: string \| null; candidates: Array<\{title: string; url: string; accepted: boolean; reason: string; matched: string\[\]\}>\}>\}/.test(src));
  assert.ok(/export type PipelineRun = \{[^}]*version: number/.test(src));
});
test("列表行带 last_trigger，不再按运行 ID 推断", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "pipelines.ts"), "utf8");
  assert.ok(/last_trigger: "batch" \| "trial" \| null/.test(src));
  assert.equal(m.lastRunTrigger, undefined);
  assert.ok(!src.includes("PreviewCandidate"), "PreviewResult 只保留一份候选类型");
});
test("dimensionLabel 使用应用已有的中文维度名", () => {
  assert.equal(m.dimensionLabel("usiran", "diplomacy"), "美伊：外交斡旋");
  assert.equal(m.dimensionLabel("usiran", "iran_readiness"), "美伊：伊朗战备");
  assert.equal(m.dimensionLabel("ukraine", "frontline"), "俄乌：军事战线");
  assert.equal(m.dimensionLabel("ukraine", "mystery"), "俄乌：mystery");
  assert.equal(m.dimensionLabel("other", "x"), "other：x");
});
test("isWebUrl 只认 http/https", () => {
  assert.equal(m.isWebUrl("https://a.b/c"), true);
  assert.equal(m.isWebUrl("HTTP://a.b"), true);
  assert.equal(m.isWebUrl("javascript:alert(1)"), false);
  assert.equal(m.isWebUrl("/relative"), false);
  assert.equal(m.isWebUrl(""), false);
});
test("trialOutcome 只认点击之后的新测试运行", () => {
  const runs = (...xs) => xs.map(([run_id, trigger, status]) => ({run_id, trigger, status}));
  assert.equal(m.trialOutcome(runs(["t1", "trial", "success"]), "t1"), "waiting", "还是旧的那次");
  assert.equal(m.trialOutcome(runs(["b1", "batch", "running"], ["t1", "trial", "success"]), "t1"), "waiting");
  assert.equal(m.trialOutcome(runs(["t2", "trial", "running"], ["t1", "trial", "success"]), "t1"), "running");
  assert.equal(m.trialOutcome(runs(["t2", "trial", "failed"]), null), "finished", "首次拉取就已结束也要反馈");
  assert.equal(m.trialOutcome([], null), "waiting");
});
test("recentlyUpdated 统计最近 24 小时有新增的管线", () => {
  const now = Date.parse("2026-11-12T12:00:00Z");
  const rows = [
    {last_new_items: 3, last_finished_at: "2026-11-12T01:00:00+00:00"},
    {last_new_items: 0, last_finished_at: "2026-11-12T01:00:00+00:00"},
    {last_new_items: 5, last_finished_at: "2026-11-10T01:00:00+00:00"},
    {last_new_items: 2, last_finished_at: null},
  ];
  assert.equal(m.recentlyUpdated(rows, now), 1);
});
