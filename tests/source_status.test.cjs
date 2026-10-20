const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const ts=require("typescript");
const vm=require("node:vm");
const source=fs.readFileSync("src/source_status.ts","utf8");
const context=vm.createContext({exports:{}});
vm.runInContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,context);
const {reconcileNewsServer,canImportNews,titleTranslationLabel}=context.exports;
const server=id=>({id,name:id,url:"https://example.test/mcp",configured:true});

test("news importer only defaults a single service and retains explicit selection",()=>{
  assert.equal(reconcileNewsServer("",[]),"");
  assert.equal(reconcileNewsServer("",[server("a")]),"a");
  assert.equal(reconcileNewsServer("",[server("a"),server("b")]),"");
  assert.equal(reconcileNewsServer("b",[server("a"),server("b")]),"b");
});
test("deleting selected service cannot silently import from remaining service",()=>{
  assert.equal(reconcileNewsServer("a",[server("b")]),"");
  assert.equal(canImportNews("a",[server("b")]),false);
});
test("news import requires configured selected service and known unsupported tools block it",()=>{
  assert.equal(canImportNews("",[server("a")]),false);
  assert.equal(canImportNews("a",[{...server("a"),configured:false}]),false);
  assert.equal(canImportNews("a",[{...server("a"),news_import:{supported:false}}]),false);
  assert.equal(canImportNews("a",[{...server("a"),news_import:{supported:true}}]),true);
  // Unknown capability must be checked by the backend on the actual fetch.
  assert.equal(canImportNews("a",[server("a")]),true);
});
test("translation labels distinguish unavailable, running and not translated",()=>{
  const record={title_translation_state:"untranslated"};
  assert.equal(titleTranslationLabel(record,{available:false,running:false}),"原文 · 翻译未配置");
  assert.equal(titleTranslationLabel(record,{available:true,running:true}),"原文 · 当前快照翻译中");
  assert.equal(titleTranslationLabel(record,{available:true,running:false}),"原文 · 暂无中文译文");
  assert.equal(titleTranslationLabel(record),"原文 · 暂无中文译文");
  assert.equal(titleTranslationLabel({title_translation_state:"ready"},{available:false}),"");
});
test("news page uses canonical registry and explicit service, not duplicate credentials",()=>{
  const app=fs.readFileSync("src/App.tsx","utf8");
  assert.ok(app.includes('"/integrations/external-mcp"'));
  assert.ok(app.includes("server_id: mcpServerId"));
  assert.ok(!app.includes('"/mcp/config"'));
  assert.ok(!app.includes("mcpAuthorization"));
  const map=fs.readFileSync("src/MapView.tsx","utf8");
  assert.ok(map.includes("title_translation_state==='untranslated'"));
  assert.ok(!map.includes("title.includes('中文译文准备中')"));
});
