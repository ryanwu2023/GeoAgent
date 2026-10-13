const {test} = require('node:test');
const assert = require('node:assert/strict');
const {HostViewBridge,validView,trustedViewSource,panelEntryScope} = process.env.GEO_HOST_BRIDGE_JS ? require(process.env.GEO_HOST_BRIDGE_JS) : require('./load_ts.cjs')('src/hostViewContext.ts');
test('explicit entry preserves legacy scopes; active range switches never navigate',()=>{
  const first=panelEntryScope('',false,'old-snapshot');
  assert.equal(first,'.geo-research-assistant/old-snapshot');
  assert.equal(panelEntryScope(first,true,'new-snapshot'),first);
  const b=bridge();b.ready({...ready,workspaceScope:first});
  const pending=b.send(view);
  const next=b.send({...view,snapshot_id:'new-snapshot',start:'2026-09-28',end:'2026-09-30'});
  assert.equal(next.workspaceScope,pending.workspaceScope);
  assert.equal(panelEntryScope(first,false,'new-snapshot'),'.geo-research-assistant/new-snapshot');
  assert.equal(panelEntryScope(first,false,'old-snapshot'),first);
  assert.equal(panelEntryScope('',false,''),'');
  assert.equal(panelEntryScope('',false,'../other'),'');
});
test('origin and source must both match current iframe',()=>{
  const frame={};
  assert.equal(trustedViewSource('http://127.0.0.1:5585',frame,'http://127.0.0.1:5585',frame),true);
  assert.equal(trustedViewSource('http://127.0.0.1:9172',frame,'http://127.0.0.1:5585',frame),false);
  assert.equal(trustedViewSource('http://127.0.0.1:5585',{},'http://127.0.0.1:5585',frame),false);
  assert.equal(trustedViewSource('',null,'',null),false);
});
const view={version:'geoagent-view/v1',snapshot_id:'0b18ddf4468552d911eba06e',topic:'usiran',dimension:'',start:'',end:''};
const ready={type:'finance:view-context:ready',capability:'host-view-context.v1',panelId:'panel1',agentId:'geo-research-assistant',sessionId:null,supportedViews:['geoagent-view/v1']};
function bridge(){let n=0;return new HostViewBridge(ready.agentId,()=>`request-${++n}`);}
function ack(p){return {...p,type:'finance:view-context:result',ok:true,stage:'received',modelConsumed:false,appliesTo:'next_user_turn'};}
test('ready and revision, empty fields preserved; no hidden message or identity',()=>{
  const b=bridge();assert.equal(b.send(view),null);assert.ok(b.ready(ready));
  const p=b.send({...view,token:'must-not-send'});assert.deepEqual(p.view,view);assert.equal(p.revision,1);
  assert.equal(p.type,'finance:view-context:set');assert.equal(p.token,undefined);
  assert.equal(b.result(ack(p)),'received');
  const p2=b.send({...view,topic:'ukraine'});assert.equal(p2.revision,2);
  assert.equal(b.result(ack(p)),null);assert.equal(b.result(ack(p2)),'received');
});
test('new ready rebinds same view and rejects old binding, wrong agent and version',()=>{
  const b=bridge();assert.equal(b.ready({...ready,agentId:'other'}),false);
  assert.equal(b.ready({...ready,capability:'unknown'}),false);
  b.ready(ready);const old=b.send(view);
  b.ready({...ready,panelId:'panel2',sessionId:'session2',workspaceScope:'scope2'});
  const current=b.send(view);assert.equal(current.sessionId,'session2');assert.equal(current.workspaceScope,'scope2');
  assert.equal(b.result(ack(old)),null);assert.equal(b.result({...ack(current),revision:1}),null);
  assert.equal(b.result({...ack(current),view:{...view,topic:'all'}}),null);
  assert.equal(b.result({...ack(current),modelConsumed:true}),null);
  assert.equal(b.result(ack(current)),'received');
});
test('invalid dates, extra acknowledgement fields, rejection/reset cannot remain successful',()=>{
  const b=bridge();b.ready(ready);
  assert.equal(validView({...view,start:'2026-02-30'}),false);
  assert.equal(b.send({...view,start:'2026-10-02',end:'2026-10-01'}),null);
  const p=b.send(view);assert.equal(b.result({...ack(p),view:{...view,extra:1}}),null);
  assert.equal(b.result({...p,type:'finance:view-context:result',ok:false}), 'failed');
  assert.equal(b.result(ack(p)),null);
  b.reset();assert.equal(b.send(view),null);
});
