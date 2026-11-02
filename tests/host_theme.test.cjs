const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {GEO_HOST_PALETTE,hostThemeMessage,wantsHostTheme} = require('./load_ts.cjs')('src/hostTheme.ts');

const HOST_COLOR = /^(?:#[0-9a-f]{3,8}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+)\s*)?\))$/i;

test('theme message carries only the PiChat palette vocabulary with plain colors', () => {
  const message = hostThemeMessage();
  assert.equal(message.type, 'finance:theme:set');
  assert.equal(message.theme, 'dark');
  assert.deepEqual(Object.keys(message.palette).sort(),
    ['accent', 'canvas', 'line', 'onAccent', 'panel', 'raised', 'text', 'textMuted']);
  for (const color of Object.values(message.palette)) assert.match(color, HOST_COLOR);
  message.palette.canvas = '#000000';
  assert.equal(GEO_HOST_PALETTE.canvas, '#0b1524');
});

test('sends on the panel handshake and on this agent panel ready signal', () => {
  for (const data of [{type: 'finance:chat:ready', agentId: 'geo-research-assistant'}, {type: 'finance:auth:request'}, {type: 'finance:auth:request', agentId: 'geo-research-assistant'}])
    assert.equal(wantsHostTheme(data, 'geo-research-assistant'), true);
  for (const data of [null, 'finance:chat:ready', {type: 'finance:chat:ready'}, {type: 'finance:chat:ready', agentId: 'other'}, {type: 'finance:auth:request', agentId: 'other'}, {type: 'finance:agent:running', agentId: 'geo-research-assistant'}])
    assert.equal(wantsHostTheme(data, 'geo-research-assistant'), false);
});

test('embed URL no longer pins hideNav or theme; the entry policy owns them', () => {
  const app = fs.readFileSync(path.resolve(__dirname, '..', 'src/App.tsx'), 'utf8');
  assert.doesNotMatch(app, /searchParams\.set\("hideNav"/);
  assert.doesNotMatch(app, /searchParams\.set\("theme"/);
  assert.match(app, /useHostTheme\(pichatFrameRef/);
});
