import type {Metric} from './types';

type Guide={meaning:string;reading:string;impact:string;limit:string};
const displayName=(m:Metric)=>m.series==='RCPPTAPP_approve'?'特朗普支持率（Silver Bulletin 聚合）':m.name;
import registry from '../config/metric-guides.json';
const guides:Record<string,Guide>=registry;

export function metricGuide(m:Metric):Guide{
  if(m.series&&guides[m.series])return guides[m.series];
  const fallback=guides[m.unit==='USD'&&m.scope.includes('美国整体')?'__fiscal__':'__unknown__'];
  return {...fallback,meaning:fallback.meaning.replace('{name}',m.name)};
}

export default function MetricGuide({metric:m}:{metric:Metric}){
 const guide=metricGuide(m);
 return <section className="metric-guide"><h2>{displayName(m)} · 指标解读</h2><small>研究解释 · 条件作用，不是已确认的资产预测</small>{[['指标含义',guide.meaning],['如何看变化',guide.reading],['可能影响',guide.impact],['解释边界',guide.limit]].map(([title,text])=><div key={title}><h3>{title}</h3><p>{text}</p></div>)}<h3>统计与计算口径</h3><p>{m.scope} · {m.frequency||'频率未提供'} · {m.unit==='USD'?'美元（页面折算为亿美元）':m.unit}</p><p>{m.method.replace('Treasury MTS net outlay','美国财政部月度净支出')}</p><p>当前观测：{m.current?.date||'缺失'}；比较基准：{m.previous?.date||'历史不足'}，前 {m.comparison_step||1} 个观测期。</p>{m.boundary&&<p>{m.boundary}</p>}{m.quality_issue&&<p className="alert">{m.quality_issue}</p>}<h3>以下为来源记录与原始文件</h3></section>;
}
