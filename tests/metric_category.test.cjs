const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const context = vm.createContext({exports:{}});
vm.runInContext(ts.transpileModule(fs.readFileSync('src/metric_category.ts','utf8'), {
  compilerOptions:{module:ts.ModuleKind.CommonJS}
}).outputText, context);
const {metricCategory} = context.exports;
test('polling category uses explicit semantic bucket, not a Trump-only label', () => {
  assert.equal(metricCategory({name:'俄罗斯受访者：倾向开始和平谈判',bucket:'polling'}),'民调');
  assert.equal(metricCategory({name:'俄罗斯受访者：倾向继续军事行动',bucket:'polling'}),'民调');
  assert.equal(metricCategory({name:'Existing alias',series:'taco_trump_approval',shared_market:true}),'民调');
  assert.equal(metricCategory({name:'军费中的百分比',unit:'%'}),'军事');
  assert.equal(metricCategory({name:'利率',unit:'%',bucket:'rates'}),'经济');
});
test('European sovereign yield metrics are economic indicators', () => {
  for (const name of ['德国10年期国债收益率','法国10年期国债收益率','欧元区10年期收益率','法德10年期国债利差']) {
    assert.equal(metricCategory({name,unit:name.includes('利差')?'bp':'%',bucket:'rates'}),'经济');
  }
});
