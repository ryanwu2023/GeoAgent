import {useState} from 'react';
import type {Metric} from './types';
import {num} from './api';
import './metric-chart.css';

export default function MetricChart({metric}:{metric:Metric}){
  const [range,setRange]=useState(60),[selected,setSelected]=useState<string|null>(null);
  const rows=range?metric.history.slice(-range):metric.history;
  const values=rows.flatMap(p=>p.value==null?[]:[p.value]);
  if(!values.length)return <div className="spark-empty">暂无可用历史</div>;
  const low=Math.min(...values),high=Math.max(...values),padding=(high-low||Math.max(Math.abs(high)*.1,1))*.12;
  const rawStep=(high-low+2*padding)/4;
  const magnitude=10**Math.floor(Math.log10(rawStep));
  const step=[1,2,5,10].find(n=>n*magnitude>=rawStep)!*magnitude;
  const min=Math.floor((low-padding)/step)*step,max=Math.ceil((high+padding)/step)*step;
  const first=Date.parse(rows[0].date),last=Date.parse(rows[rows.length-1].date);
  const x=(date:string)=>last===first?245:76+(Date.parse(date)-first)/(last-first)*338;
  const y=(value:number)=>135-(value-min)/(max-min)*110;
  const valueText=(value:number|null)=>num(value,metric.unit)+(metric.unit==='USD'?'':` ${metric.unit}`);
  const active=rows.find(p=>p.date===selected)||rows[rows.length-1];
  const ticks=Array.from({length:Math.round((max-min)/step)+1},(_,i)=>min+i*step);
  const dates=Array.from(new Set([rows[0].date,rows[Math.floor((rows.length-1)/2)].date,rows[rows.length-1].date]));
  const selectAt=(clientX:number,element:SVGSVGElement)=>{
    const rect=element.getBoundingClientRect();const position=(clientX-rect.left)/rect.width*440;
    const point=rows.reduce((a,b)=>Math.abs(x(a.date)-position)<Math.abs(x(b.date)-position)?a:b);
    setSelected(point.date);
  };
  return <div className="metric-chart">
    <div className="chart-controls"><span>原始观测 · {metric.unit==='USD'?'亿美元':metric.unit||'汇率'}</span><div>{[[60,'近60期'],[180,'近180期'],[0,'全部']].map(([count,label])=><button key={count} className={range===count?'selected':''} onClick={()=>{setRange(Number(count));setSelected(null)}}>{label}</button>)}</div></div>
    <svg viewBox="0 0 440 166" role="slider" tabIndex={0} aria-label={`${metric.name}走势图，左右方向键选择日期`} aria-valuemin={0} aria-valuemax={rows.length-1} aria-valuenow={rows.indexOf(active)} aria-valuetext={`${active.date}，${valueText(active.value)}`} onPointerMove={e=>selectAt(e.clientX,e.currentTarget)} onClick={e=>selectAt(e.clientX,e.currentTarget)} onKeyDown={e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();setSelected(rows[Math.max(0,Math.min(rows.length-1,rows.indexOf(active)+(e.key==='ArrowLeft'?-1:1)))].date)}}}>
      {ticks.map((v,i)=><g key={i}><line x1="76" x2="414" y1={y(v)} y2={y(v)} stroke="#2b4358"/><text x="69" y={y(v)+4} textAnchor="end">{num(v,metric.unit).replace(' 亿美元','')}</text></g>)}
      {rows.map((p,i)=>i&&p.value!=null&&rows[i-1].value!=null?<line key={p.date} x1={x(rows[i-1].date)} y1={y(rows[i-1].value!)} x2={x(p.date)} y2={y(p.value)} stroke="#79bace" strokeWidth="1.5"/>:null)}
      {rows.length<=60&&rows.map(p=>p.value!=null?<circle key={p.date} cx={x(p.date)} cy={y(p.value)} r="2" fill="#79bace"/>:null)}
      {dates.map((d,i)=><text key={d} x={x(d)} y="158" textAnchor={i===0?'start':i===dates.length-1?'end':'middle'}>{d}</text>)}
      <line x1={x(active.date)} x2={x(active.date)} y1="22" y2="137" stroke="#d8ecfa" strokeDasharray="3 3"/>
      {active.value!=null&&<circle cx={x(active.date)} cy={y(active.value)} r="4" fill="#d8ecfa"/>}
    </svg>
    <div className="chart-value" role="status">{active.date} · <strong>{active.value==null?'数据缺失':valueText(active.value)}</strong></div>
    <small>显示 {rows.length} / {metric.history.length} 期 · 悬停或点击查看数值{!range&&rows.length>180?' · 全历史较密集，可切换近60期':''}</small>
  </div>;
}
