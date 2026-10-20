const {test}=require('node:test');
const assert=require('node:assert/strict');
const {EmbedIdentity,EmbedIdentityFailure,EMBED_IDENTITY_TIMEOUT_MS,EMBED_READY_TIMEOUT_MS,EMBED_SLOW_NOTICE_MS}=process.env.GEO_IDENTITY_JS ? require(process.env.GEO_IDENTITY_JS) : require('./load_ts.cjs')('src/embedIdentity.ts');
const token=(exp,tag='a')=>`header.${Buffer.from(JSON.stringify({exp,tag})).toString('base64url')}.signature`;
function harness(onDeliver){
  let now=100000,calls=0;const deliveries=[],states=[],timers=new Map(),queue=[];let id=0;
  const machine=new EmbedIdentity({now:()=>now,
    fetchToken:signal=>{calls++;return new Promise((resolve,reject)=>queue.push({resolve,reject,signal}));},
    deliver:t=>{deliveries.push(t);onDeliver?.(machine);},status:(s,e)=>states.push([s,e]),
    schedule:(fn,ms)=>{timers.set(++id,{fn,ms});return id;},cancel:i=>timers.delete(i)});
  return {machine,deliveries,states,timers,queue,get calls(){return calls;},time:n=>now=n};
}
test('first load coalesces ordinary requests; valid cache reused',async()=>{
 const h=harness(),a=h.machine.request(),b=h.machine.request();
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
 const h=harness();let p=h.machine.request();h.queue[0].reject(new EmbedIdentityFailure('denied'));await p;
 assert.equal(h.states.at(-1)[0],'error');h.machine.recover();p=h.machine.request();
 const timeout=[...h.timers.values()].find(t=>t.ms===EMBED_IDENTITY_TIMEOUT_MS);timeout.fn();await p;
 assert.equal(h.states.at(-1)[0],'retrying');h.queue[1].resolve(token(500,'late'));
 [...h.timers.values()].find(t=>t.ms===2000).fn();
 p=h.machine.request();h.queue[2].resolve(token(600));await p;assert.equal(h.deliveries.at(-1),token(600));
});
test('no ready receipt times out, ready cancels timers; malformed/expired response not delivered',async()=>{
 const h=harness();let p=h.machine.request();h.queue[0].resolve(token(500));await p;
 [...h.timers.values()].find(t=>t.ms===EMBED_READY_TIMEOUT_MS).fn();assert.equal(h.states.at(-1)[0],'error');
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
test('iframe load reuses pending/issued identity rather than rotating credentials',async()=>{
 const h=harness(),p=h.machine.request(),load=h.machine.frameLoaded();
 assert.equal(h.calls,1);h.queue[0].resolve(token(500));await Promise.all([p,load]);
 await h.machine.frameLoaded();assert.equal(h.calls,1);
 assert.ok(h.deliveries.every(t=>t===token(500)));
 h.machine.ready();const states=h.states.length,deliveries=h.deliveries.length;
 await h.machine.frameLoaded();await h.machine.frameLoaded();
 assert.equal(h.calls,1);assert.equal(h.states.length,states);
 assert.equal(h.deliveries.length,deliveries);assert.equal(h.timers.size,0);
 // An explicit auth rejection still renews and is not suppressed by ready.
 const refresh=h.machine.request(true);assert.equal(h.calls,2);
 h.queue[1].resolve(token(600,'renewed'));await refresh;
});
test('ready during delivery survives promise settlement and has no timeout left behind',async()=>{
 const h=harness(machine=>machine.ready()),p=h.machine.request();
 h.queue[0].resolve(token(500));await p;
 assert.equal(h.states.at(-1)[0],'ready');assert.equal(h.timers.size,0);
 await h.machine.frameLoaded();assert.equal(h.calls,1);
});
test('ready without an issued token cannot authenticate an unbound frame',()=>{
 const h=harness();h.machine.ready();assert.equal(h.states.length,0);
});
test('auth-required retires ordinary fetch and coalesces duplicate recovery; old result cannot win',async()=>{
 const h=harness(),old=h.machine.request(),fresh=h.machine.request(true);
 assert.equal(h.calls,2);assert.ok(h.queue[0].signal.aborted);
 const duplicate=h.machine.request(true);assert.equal(h.calls,2);
 h.queue[1].resolve(token(600,'fresh'));await Promise.all([old,fresh,duplicate]);
 h.queue[0].resolve(token(700,'stale'));await Promise.resolve();
 assert.deepEqual(h.deliveries,[token(600,'fresh')]);
});
test('slow ready is a neutral notice; later ready removes it without rotating identity',async()=>{
 const h=harness(),p=h.machine.request();h.queue[0].resolve(token(600));await p;
 [...h.timers.values()].find(t=>t.ms===EMBED_SLOW_NOTICE_MS).fn();
 assert.equal(h.states.at(-1)[0],'waiting');
 h.machine.ready();assert.equal(h.states.at(-1)[0],'ready');
 assert.equal(h.calls,1);assert.equal(h.timers.size,0);
});
test('load/auth events do not postpone ready deadline indefinitely',async()=>{
 const h=harness(),p=h.machine.request();h.queue[0].resolve(token(600));await p;
 const deadline=[...h.timers.values()].find(t=>t.ms===EMBED_READY_TIMEOUT_MS);
 for(let i=0;i<5;i++){await h.machine.request();await h.machine.frameLoaded();}
 assert.equal([...h.timers.values()].find(t=>t.ms===EMBED_READY_TIMEOUT_MS),deadline);
 deadline.fn();assert.equal(h.states.at(-1)[0],'error');
});
test('transient identity failures retry twice with backoff then stop',async()=>{
 const h=harness();let p=h.machine.request();
 h.queue[0].reject(new TypeError('network'));await p;
 assert.equal(h.states.at(-1)[0],'retrying');
 await h.machine.request();await h.machine.request(true);assert.equal(h.calls,1);
 [...h.timers.values()].find(t=>t.ms===2000).fn();p=h.machine.request();
 h.queue[1].reject(new EmbedIdentityFailure('gateway',true));await p;
 [...h.timers.values()].find(t=>t.ms===5000).fn();p=h.machine.request();
 h.queue[2].reject(new TypeError('network'));await p;
 assert.equal(h.calls,3);assert.equal(h.states.at(-1)[0],'error');assert.equal(h.timers.size,0);
 await h.machine.request();await h.machine.request(true);assert.equal(h.calls,3);
});
test('a bounded retry automatically succeeds and cancels stale retry callbacks',async()=>{
 const h=harness();let p=h.machine.request();h.queue[0].reject(new Error('temporary'));await p;
 const retry=[...h.timers.values()].find(t=>t.ms===2000);
 retry.fn();p=h.machine.request();h.queue[1].resolve(token(600));await p;
 h.machine.ready();retry.fn();assert.equal(h.calls,2);assert.equal(h.states.at(-1)[0],'ready');
});
test('permission/config/invalid credential failures never auto-retry',async()=>{
 const h=harness(),p=h.machine.request();h.queue[0].reject(new EmbedIdentityFailure('permission'));await p;
 assert.equal(h.calls,1);assert.equal(h.states.at(-1)[0],'error');assert.equal(h.timers.size,0);
});
test('network resume recovers terminal failure without touching a healthy frame',async()=>{
 const h=harness();let p=h.machine.request();h.queue[0].resolve(token(600));await p;
 [...h.timers.values()].find(t=>t.ms===EMBED_READY_TIMEOUT_MS).fn();
 h.machine.recover();assert.equal(h.calls,1);assert.equal(h.states.at(-1)[0],'sent');
 h.machine.ready();const n=h.states.length,d=h.deliveries.length;
 h.machine.recover();assert.equal(h.states.length,n);assert.equal(h.deliveries.length,d);
});
test('dispose during backoff prevents automatic recovery',async()=>{
 const h=harness(),p=h.machine.request();h.queue[0].reject(new Error('temporary'));await p;
 const retry=[...h.timers.values()].find(t=>t.ms===2000);h.machine.dispose();retry.fn();h.machine.recover();
 assert.equal(h.calls,1);assert.equal(h.timers.size,0);
});
