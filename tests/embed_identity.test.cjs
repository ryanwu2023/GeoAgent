const {test}=require('node:test');
const assert=require('node:assert/strict');
const {EmbedIdentity}=process.env.GEO_IDENTITY_JS ? require(process.env.GEO_IDENTITY_JS) : require('./load_ts.cjs')('src/embedIdentity.ts');
const token=(exp,tag='a')=>`header.${Buffer.from(JSON.stringify({exp,tag})).toString('base64url')}.signature`;
function harness(){
  let now=100000,calls=0;const deliveries=[],states=[],timers=new Map(),queue=[];let id=0;
  const machine=new EmbedIdentity({now:()=>now,
    fetchToken:signal=>{calls++;return new Promise((resolve,reject)=>queue.push({resolve,reject,signal}));},
    deliver:t=>deliveries.push(t),status:(s,e)=>states.push([s,e]),
    schedule:(fn,ms)=>{timers.set(++id,{fn,ms});return id;},cancel:i=>timers.delete(i)});
  return {machine,deliveries,states,timers,queue,get calls(){return calls;},time:n=>now=n};
}
test('first load coalesces concurrent request and invalidation; valid cache reused',async()=>{
 const h=harness(),a=h.machine.request(),b=h.machine.request(true);
 assert.equal(h.calls,1);h.queue[0].resolve(token(1000));await Promise.all([a,b]);
 assert.equal(h.deliveries.length,1);await h.machine.request();assert.equal(h.calls,1);
});
test('expired cached token and explicit auth-required both mint fresh identity',async()=>{
 const h=harness();let p=h.machine.request();h.queue[0].resolve(token(200));await p;
 h.time(201000);p=h.machine.request();assert.equal(h.calls,2);h.queue[1].resolve(token(500,'b'));await p;
 p=h.machine.request(true);assert.equal(h.calls,3);h.queue[2].resolve(token(600,'c'));await p;
 assert.equal(h.deliveries.at(-1),token(600,'c'));
});
test('old scheduled deliveries cannot replace newer identity',async()=>{
 const h=harness();let p=h.machine.request();h.queue[0].resolve(token(200));await p;
 const old=[...h.timers.values()];p=h.machine.request(true);h.queue[1].resolve(token(500,'b'));await p;
 const count=h.deliveries.length;old.forEach(t=>t.fn());assert.equal(h.deliveries.length,count);
});
test('close aborts pending and ignores late old response; new binding independent',async()=>{
 const h=harness(),p=h.machine.request();h.machine.dispose();assert.ok(h.queue[0].signal.aborted);
 h.queue[0].resolve(token(500));await p;assert.equal(h.deliveries.length,0);assert.equal(h.timers.size,0);
 const next=harness(),q=next.machine.request();next.queue[0].resolve(token(600));await q;
 assert.equal(next.deliveries.length,1);
});
test('rejection and request timeout leave recoverable error; manual retry succeeds',async()=>{
 const h=harness();let p=h.machine.request();h.queue[0].reject(new Error('denied'));await p;
 assert.equal(h.states.at(-1)[0],'error');p=h.machine.request(true);
 const timeout=[...h.timers.values()].find(t=>t.ms===15000);timeout.fn();await p;
 assert.equal(h.states.at(-1)[0],'error');h.queue[1].resolve(token(500,'late'));
 p=h.machine.request(true);h.queue[2].resolve(token(600));await p;assert.equal(h.deliveries.at(-1),token(600));
});
test('no ready receipt times out, ready cancels timers; malformed/expired response not delivered',async()=>{
 const h=harness();let p=h.machine.request();h.queue[0].resolve(token(500));await p;
 [...h.timers.values()].find(t=>t.ms===10000).fn();assert.equal(h.states.at(-1)[0],'error');
 h.machine.ready();assert.equal(h.timers.size,0);assert.equal(h.states.at(-1)[0],'ready');
 const bad=harness();p=bad.machine.request();bad.queue[0].resolve('opaque');await p;
 assert.equal(bad.deliveries.length,0);assert.equal(bad.states.at(-1)[0],'error');
});
test('close clears delivered-token timers and late ready cannot revive disposed binding',async()=>{
 const h=harness(),p=h.machine.request();h.queue[0].resolve(token(500));await p;
 const timers=[...h.timers.values()];h.machine.dispose();const count=h.deliveries.length,states=h.states.length;
 timers.forEach(t=>t.fn());h.machine.ready();await h.machine.request();
 assert.equal(h.deliveries.length,count);assert.equal(h.states.length,states);assert.equal(h.timers.size,0);
});
test('near expiry renews and rejected expiry never delivers; old ready ignored during fetch',async()=>{
 const h=harness();let p=h.machine.request();h.queue[0].resolve(token(200));await p;
 h.time(171000);p=h.machine.request();assert.equal(h.calls,2);
 h.machine.ready();assert.equal(h.states.at(-1)[0],'fetching');
 h.queue[1].resolve(token(100));await p;assert.equal(h.states.at(-1)[0],'error');assert.equal(h.deliveries.length,1);
});
