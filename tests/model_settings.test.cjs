const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");
const React = require("react");
const {renderToStaticMarkup} = require("react-dom/server");

function load(file, dependencies = {}) {
  const context = vm.createContext({exports: {}, URL, require: name => dependencies[name] || require(name)});
  vm.runInContext(ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022},
  }).outputText, context);
  return context.exports;
}
const helpers = load("src/model_settings.ts");
const {providerDraft, publicModelDraft, normalizeModelBaseUrl, canKeepModelKey, modelDraftError, initialModelSettings, modelSettingsReducer: reduce} = helpers;
const {ModelSettingsForm} = load("src/ModelSettings.tsx", {"./model_settings": helpers, "./sessionFetch": load("src/sessionFetch.ts")});
const config = {
  provider: "deepseek", base_url: "https://api.deepseek.com", model: "deepseek-flash",
  timeout_seconds: 90, api_key_configured: true, configured: true, source: "saved", protocol: "chat-completions",
};
function loaded(cfg = config) {
  return reduce(reduce(initialModelSettings, {type: "loading", requestId: 1}),
    {type: "loaded", requestId: 1, config: cfg, runtime: null});
}
function render(state) {
  return renderToStaticMarkup(React.createElement(ModelSettingsForm, {
    state, onEdit() {}, onProvider() {}, onReload() {}, onTest() {}, onSave() {},
  }));
}
const passed = {ok: true, message: "样本通过", source: "draft", translation: {ok: true, original: "A statement.", text: "一份声明。", issues: []}};

test("DeepSeek preset is explicit and public hydration cannot expose returned secrets", () => {
  const preset = providerDraft("deepseek");
  assert.equal(preset.base_url, "https://api.deepseek.com");
  assert.equal(preset.model, "deepseek-flash");
  assert.equal(preset.timeout_seconds, 90);
  assert.equal(preset.api_key, "");
  assert.equal(publicModelDraft({...config, api_key: "test-key"}).api_key, "");
});

test("blank key is kept only for same configured provider and endpoint", () => {
  const draft = publicModelDraft(config);
  assert.equal(canKeepModelKey(config, draft), true);
  assert.equal(canKeepModelKey(config, {...draft, base_url: "https://api.deepseek.com/"}), true);
  assert.equal(canKeepModelKey(config, {...draft, base_url: "https://API.DEEPSEEK.COM:443/chat/completions"}), true);
  assert.equal(canKeepModelKey(config, {...draft, base_url: "https://other.example"}), false);
  assert.equal(canKeepModelKey(config, {...draft, provider: "openai-compatible"}), false);
  assert.equal(canKeepModelKey({...config, api_key_configured: false}, draft), false);
  assert.match(modelDraftError(config, {...draft, base_url: "https://other.example"}), /输入新的 API Key/);
});

test("canonical endpoints match backend rules without inheriting for unsafe or unknown URLs", () => {
  assert.equal(normalizeModelBaseUrl("https://API.DEEPSEEK.COM:443/chat/completions/", "deepseek"), "https://api.deepseek.com");
  assert.equal(normalizeModelBaseUrl("https://API.EXAMPLE:443/v1/chat/completions", "openai-compatible"), "https://api.example/v1");
  for (const suffix of ["", "/api/v1", "/api/v1/chat/"]) {
    const cfg = {...config, provider: "pichat", base_url: "https://pichat.example"};
    assert.equal(canKeepModelKey(cfg, {...publicModelDraft(cfg), base_url: `https://PICHAT.EXAMPLE:443${suffix}`}), true);
  }
  for (const url of [
    "https://api.deepseek.com?key=sentinel", "https://api.deepseek.com#",
    "https://@api.deepseek.com", "https://api.deepseek.com/v1/../",
    "https://api.deepseek.com/%2e", "https://api.deepseek.com\\@other.example",
    "http://api.deepseek.com", "https://api.deepseek.com:0",
    "https://api.deepseek.com/chat", "https://api.deepseek.com/admin",
  ]) {
    assert.equal(canKeepModelKey(config, {...publicModelDraft(config), base_url: url}), false, url);
    assert.throws(() => normalizeModelBaseUrl(url, "deepseek"));
  }
  assert.equal(normalizeModelBaseUrl("http://LOCALHOST:80/v1/", "openai-compatible"), "http://localhost/v1");
  assert.equal(normalizeModelBaseUrl("http://127.0.0.1:8011", "openai-compatible"), "http://127.0.0.1:8011");
  assert.equal(normalizeModelBaseUrl("http://[0:0:0:0:0:0:0:1]:80", "pichat"), "http://[0:0:0:0:0:0:0:1]");
  assert.throws(() => normalizeModelBaseUrl("http://127.1", "pichat"));
});

test("changing endpoint or provider clears draft secret and old test result", () => {
  let state = {...loaded(), result: passed, draft: {...publicModelDraft(config), api_key: "typed-key"}};
  state = reduce(state, {type: "edit", patch: {base_url: "https://other.example"}});
  assert.equal(state.draft.api_key, "");
  assert.equal(state.result, null);
  state = reduce({...state, draft: {...state.draft, api_key: "new-key"}}, {type: "provider", provider: "pichat"});
  assert.equal(state.draft.api_key, "");
  assert.equal(state.draft.model, "");
  assert.equal(state.draft.base_url, "");
});

test("editing during test invalidates late asynchronous success", () => {
  let state = reduce(loaded(), {type: "started", operation: "testing", requestId: 2});
  state = reduce(state, {type: "edit", patch: {model: "different-model"}});
  state = reduce(state, {type: "tested", requestId: 2, result: passed});
  assert.equal(state.result, null);
  assert.equal(state.phase, "idle");
  assert.equal(state.draft.model, "different-model");
});

test("older test cannot replace newer test and test never saves configuration", () => {
  const state = reduce(loaded(), {type: "started", operation: "testing", requestId: 4});
  assert.equal(reduce(state, {type: "tested", requestId: 3, result: passed}), state);
  const result = reduce(state, {type: "tested", requestId: 4, result: passed});
  assert.equal(result.config, state.config);
  assert.equal(result.result, passed);
  assert.equal(result.notice, "");
});

test("save clears secret and validation, explicitly reports unverified", () => {
  let state = {...loaded(), result: passed, draft: {...publicModelDraft(config), api_key: "new-key"}};
  state = reduce(state, {type: "started", operation: "saving", requestId: 5});
  state = reduce(state, {type: "saved", requestId: 5, config});
  assert.equal(state.draft.api_key, "");
  assert.equal(state.result, null);
  assert.match(state.notice, /未验证/);
  assert.match(state.notice, /不会自动启动批量/);
});

test("disabled needs no endpoint model or key; PiChat has no model selector", () => {
  assert.equal(modelDraftError(config, providerDraft("disabled")), "");
  const pichat = {...providerDraft("pichat"), base_url: "https://chat.example", api_key: "app-key"};
  assert.equal(modelDraftError(null, pichat), "");
  assert.match(modelDraftError(null, {...pichat, base_url: "https://chat.example/chat"}), /根地址/);
  assert.equal(modelDraftError(null, {...pichat, base_url: "https://chat.example/api/v1/chat"}), "");
  const html = render({...loaded(), draft: pichat});
  assert.ok(!html.includes("模型名称（手动指定）"));
  assert.ok(html.includes("Agent 由 PiChat 应用权限路由"));
});

test("custom provider requires model, safe URL, timeout and key", () => {
  const draft = {...providerDraft("openai-compatible"), base_url: "https://model.example/v1", api_key: "key"};
  assert.match(modelDraftError(null, draft), /模型名称/);
  for (const timeout of [0, 1, 4, 301, Infinity, NaN]) {
    assert.match(modelDraftError(null, {...draft, model: "a", timeout_seconds: timeout}), /5 到 300/);
  }
  for (const timeout of [5, 90, 300]) {
    assert.equal(modelDraftError(null, {...draft, model: "a", timeout_seconds: timeout}), "");
  }
  assert.match(modelDraftError(null, {...draft, model: "a", base_url: "https://model.example?key=hidden"}), /不含/);
  assert.equal(modelDraftError(null, {...draft, model: "a"}), "");
});

test("actual form renders configured as unverified and separate test/save controls", () => {
  const html = render(loaded());
  assert.ok(html.includes("已配置（不代表已验证）"));
  assert.ok(html.includes("测试草稿（小样本翻译）"));
  assert.ok(html.includes("保存后台模型配置"));
  assert.ok(html.includes('type="password"'));
  assert.ok(html.includes('autoComplete="new-password"'));
  assert.ok(html.includes("非思考调用"));
  assert.ok(html.includes("https://api-docs.deepseek.com/"));
  assert.ok(!html.includes("本地模型：未接入"));
  assert.ok(html.includes('min="5" max="300"'));
  assert.ok(!html.includes("测试通过"));
});

test("configuration error keeps repair form usable without inheriting credentials", () => {
  const broken = {
    provider: "disabled", base_url: "", model: "", timeout_seconds: 90,
    api_key_configured: false, configured: false, source: "error", protocol: "未启用",
    config_error: "模型配置无法读取，未回退到其他凭据。",
  };
  let state = loaded(broken);
  const html = render(state);
  assert.ok(html.includes('role="alert"'));
  assert.ok(html.includes(broken.config_error));
  assert.ok(html.includes("配置读取失败（未生效）"));
  assert.ok(html.includes("模型提供方"));
  assert.ok(html.includes("保存并禁用后台模型"));
  assert.equal(modelDraftError(broken, state.draft), "");
  state = reduce(state, {type: "provider", provider: "deepseek"});
  assert.equal(canKeepModelKey(broken, state.draft), false);
  assert.match(modelDraftError(broken, state.draft), /输入新的 API Key/);
  state = reduce(state, {type: "edit", patch: {api_key: "test-repair"}});
  assert.equal(modelDraftError(broken, state.draft), "");
  state = reduce(state, {type: "started", operation: "saving", requestId: 6});
  state = reduce(state, {type: "saved", requestId: 6, config});
  assert.ok(!render(state).includes(broken.config_error));
});

test("actual result renders original, translated text and failure issues without claiming success", () => {
  const success = render({...loaded(), result: passed});
  assert.ok(success.includes("草稿小样本翻译通过（仅测试，不修改配置）"));
  assert.ok(success.includes("A statement."));
  assert.ok(success.includes("一份声明。"));
  const failure = render({...loaded(), result: {...passed, translation: {...passed.translation, ok: false, issues: ["输出含未翻译英文"]}}});
  assert.ok(failure.includes("草稿测试未通过"));
  assert.ok(failure.includes("输出含未翻译英文"));
  assert.ok(!failure.includes("草稿小样本翻译通过"));
});

test("model section uses new component without localStorage or legacy saved-model test", () => {
  const host = fs.readFileSync("src/IntegrationSettings.tsx", "utf8");
  const component = fs.readFileSync("src/ModelSettings.tsx", "utf8");
  assert.ok(host.includes("<ModelSettings onSaved={onSaved}/>"));
  assert.ok(!host.includes("/api/model/check"));
  assert.ok(component.includes('"/api/integrations/model/test", "POST"'));
  assert.ok(component.includes('"/api/integrations/model", "PUT"'));
  assert.ok(!component.includes("localStorage"));
  assert.ok(!component.includes("sessionStorage"));
});
