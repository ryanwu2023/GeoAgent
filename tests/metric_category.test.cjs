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
