const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const read = (file) => fs.readFileSync(file, "utf8");
const pageSources = () => [read("src/PipelineWorkbench.tsx"),
  ...fs.readdirSync("src/pipeline").filter((f) => f.endsWith(".tsx")).map((f) => read(`src/pipeline/${f}`))].join("\n");

test("配置页挂在独立页签", () => {
  const app = read("src/App.tsx");
  assert.ok(app.includes('["pipeline-config", "采集管线配置"]'));
  assert.ok(app.includes("<PipelineWorkbench"));
});

test("页面包含关键交互", () => {
  const s = read("src/PipelineWorkbench.tsx");
  for (const t of ["新建管线", "试跑", "测试运行", "确认启用", "本次新增", "版本历史", "不要求命中事项词", "conflictMessage", "changed_since"]) assert.ok(s.includes(t), t);
});

test("页签仅在管线接口可访问时显示", () => {
  const app = read("src/App.tsx");
  assert.ok(app.includes("isAccessDenied"));
  assert.ok(app.includes("listPipelines"));
  assert.ok(/pipelinesAvailable[\s\S]{0,200}pipeline-config/.test(app) || /pipeline-config[\s\S]{0,300}pipelinesAvailable/.test(app));
  // 与采集管线监控一样不显示快照筛选、空快照引导与快照内容区
  assert.ok(app.includes('["pipeline", "pipeline-config", "meetings"].includes(tab)'));
  assert.ok(app.includes('!sid && !snapshotFreeTabs.has(tab)'));
  assert.ok(app.includes('work && page !== "briefs" && !snapshotFreeTabs.has(tab)'));
});

test("启用、停用与内置采集器文案", () => {
  const s = pageSources();
  for (const t of [
    "内置采集器随整批采集运行，来源由代码维护",
    "尚未测试运行",
    "启用后将从下一次整批采集起与现有采集器一起运行，结果进入快照",
    "不再进入后续快照",
    "该管线正在测试运行",
    "测试结果，不进快照",
    "待并入快照",
    "已有新版本",
    "重新加载",
    "最近 24 小时有新增",
  ]) assert.ok(s.includes(t), t);
});

test("轮询、主题多选与字段标红", () => {
  const s = read("src/PipelineWorkbench.tsx");
  assert.ok(s.includes("15000"));
  assert.ok(/document\.visibilityState [!=]== "visible"/.test(s));
  assert.ok(s.includes("server_time"));
  assert.ok(s.includes("writeSeen"));
  assert.ok(s.includes("trialPipeline"));
  const all = pageSources();
  assert.ok(all.includes("美伊") && all.includes("俄乌"));
  assert.ok(all.includes('type="checkbox"'));
  assert.ok(all.includes("fieldHit"));
  assert.ok(all.includes("runLabel"));
  assert.ok(all.includes("dateText"));
});

test("样式限定在页面根类下", () => {
  const css = read("src/pipelines.css");
  const selectors = css.replace(/\/\*[\s\S]*?\*\//g, "").replace(/@media[^{]*\{/g, "").replace(/\{[^{}]*\}/g, "\n").replace(/\}/g, "\n").split(/\n/)
    .flatMap((line) => line.split(",")).map((x) => x.trim()).filter((x) => x && !x.startsWith("@"));
  assert.ok(selectors.length > 10);
  for (const sel of selectors) assert.ok(sel.startsWith(".pipeline-workbench"), sel);
});

test("主文件保持精简", () => {
  assert.ok(read("src/PipelineWorkbench.tsx").split("\n").length <= 650);
});
