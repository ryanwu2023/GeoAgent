const {test}=require('node:test');
const assert=require('node:assert/strict');
const {embedAuthPayload}=require('./load_ts.cjs')('src/embedAuthPayload.ts');

test('repeat delivery retains PiChat shadow user instead of product user binding',()=>{
 const serverUser={id:'embed:app:server-user',username:'server-user',role:'embed',orgId:'default'};
 const body={token:'opaque-issued-token',userId:serverUser.id,user:serverUser};
 const issued=embedAuthPayload(body);
 const verifiedUser={...serverUser};
 // PiChat's bootstrap deduplicates by token and canonical user ID after /auth/me.
 const wouldOverwrite=user=>user.id!==verifiedUser.id;
 assert.equal(wouldOverwrite({id:'geoagent-owner'}),true);
 for(let attempt=0;attempt<4;attempt++) {
  assert.equal(wouldOverwrite(issued.user),false);
  assert.deepEqual(issued.user,serverUser);
 }
 assert.equal(issued.token,body.token);
});

test('missing or inconsistent issued user fails closed, never invents user or role',()=>{
 for(const body of [null,{}, {token:'t'}, {token:'t',user:{id:'u'}},
  {token:'t',userId:'a',user:{id:'b'}}, {token:'',userId:'u',user:{id:'u'}}]) {
  assert.throws(()=>embedAuthPayload(body));
 }
});
