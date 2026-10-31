const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const vm = require('node:vm');
const {ReadableStream} = require('node:stream/web');
const {TextDecoder, TextEncoder} = require('node:util');

function loadApi(fetch) {
  const source = fs.readFileSync(path.join(__dirname, '../src/api.ts'), 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
  }).outputText;
  const sessionModule = {exports: {}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/sessionFetch.ts'), 'utf8'), {
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
  }).outputText, {exports: sessionModule.exports, fetch});
  const moduleValue = {exports: {}};
  vm.runInNewContext(output, {
    module: moduleValue, exports: moduleValue.exports, fetch, TextDecoder, Uint8Array,
    require: name => {assert.equal(name, "./sessionFetch");return sessionModule.exports;},
  }, {filename: 'api.js'});
  return moduleValue.exports;
}

function streamingResponse(chunks, ok = true) {
  const encoder = new TextEncoder();
  return {
    ok,
    body: new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    text: async () => chunks.join(''),
  };
}

test('NDJSON stream preserves run, progress and a final answer across split chunks', async () => {
  const chunks = [
    '{"event":"run","data":{"run_id":"qa-1","phase":"planning","status":"running"}}\n{"event":"pro',
    'gress","data":{"round":1,"title":"规划"}}\n{"event":"answer","data":{"id":"answer-1"}}',
  ];
  const {apiStream} = loadApi(async () => streamingResponse(chunks));
  const progress = [], runs = [];
  const answer = await apiStream('/ask/stream', {question: 'q'}, item => progress.push(item), item => runs.push(item));
  assert.equal(runs[0].run_id, 'qa-1');
  assert.equal(progress[0].title, '规划');
  assert.equal(answer.id, 'answer-1');
});

test('NDJSON error event rejects with its actionable server message', async () => {
  const {apiStream} = loadApi(async () => streamingResponse([
    '{"event":"error","data":{"message":"模型服务暂时不可用"}}\n',
  ]));
  await assert.rejects(
    apiStream('/ask/stream', {}, () => {}),
    /模型服务暂时不可用/,
  );
});
