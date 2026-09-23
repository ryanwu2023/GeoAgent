import {useEffect,useMemo,useRef,useState} from 'react';
import maplibregl from 'maplibre-gl';
import {MapboxOverlay} from '@deck.gl/mapbox';
import {ScatterplotLayer,TextLayer,LineLayer} from '@deck.gl/layers';
import {Focus,Globe2,Layers,X} from 'lucide-react';
import type {Evidence,Point} from './types';
import 'maplibre-gl/dist/maplibre-gl.css';
type Marker=Point & {record:Evidence};
function placeLabel(items:Marker[]){
  const labels=[...new Set(items.map(p=>p.label==='岩国'?'日本岩国':p.label))];
  return labels.length===1?labels[0]:labels.slice(0,3).join('、')+(labels.length>3?'等地区':'');
}
function placeNote(p:Marker){
  return (p.label==='岩国'||p.label==='日本岩国')&&/news\.usni\.org\/2026\/09\/(08|14)\/usni-news-fleet-and-marine-tracker/.test(p.record.url)
    ?'报道提及岩国航空站为舰载机部队驻地；不是部队当前行动位置。'
    :p.precision==='explicit'?'来源记载的具体地点':p.precision==='area'?'来源记载的大致区域':'新闻中提到的地点，点击查看具体内容';
}
type CountryLabel={name:string;position:[number,number];code:string};
const colors:Record<string,[number,number,number,number]>={escalation:[255,123,138,235],sustain:[240,199,104,235],easing:[98,207,170,235],unknown:[97,170,229,230]};
const categories:Record<string,string>={military:'军事行动',diplomacy:'外交谈判',policy:'制裁与政策',energy:'能源与运输',support:'军费与援助',domestic:'政治与社会'};
const signals:Record<string,string>={all:'全部动态',escalation:'升级相关',sustain:'持续相关',easing:'缓和相关',unknown:'待研判'};
const majorCountries=new Set(['USA','CAN','BRA','RUS','CHN','IND','AUS','ZAF','EGY','IRN','SAU','TUR','UKR','FRA','DEU','GBR','JPN','MEX','ARG','KAZ']);
const places=[{label:'霍尔木兹海峡',position:[56.25,26.57]},{label:'曼德海峡',position:[43.33,12.58]},{label:'红海',position:[38,20]},{label:'阿拉伯海',position:[64,15]}];
const ukrainePlaces=[{label:'黑海',position:[34,43]},{label:'亚速海',position:[36.7,46]}];
export default function MapView({records,topic,onSelect,resetKey=0}:{records:Evidence[];topic:string;onSelect:(r:Evidence)=>void;resetKey?:number}){
  const host=useRef<HTMLDivElement>(null),map=useRef<maplibregl.Map|null>(null),overlay=useRef<MapboxOverlay|null>(null);
  const [legendOpen,setLegendOpen]=useState(()=>window.innerWidth>900);
  const [error,setError]=useState(''),[layers,setLayers]=useState<Record<string,boolean>>(Object.fromEntries(Object.keys(categories).map(k=>[k,true]))),[signal,setSignal]=useState('all');
  const [layoutVersion,setLayoutVersion]=useState(0);
  const [zoom,setZoom]=useState(1.5),[ready,setReady]=useState(false),[countryLabels,setCountryLabels]=useState<CountryLabel[]>([]),[cluster,setCluster]=useState<Marker[]>([]);
  const onClick=useRef(onSelect);onClick.current=onSelect;
  const filtered=useMemo(()=>records.filter(r=>layers[r.event_category]&&(signal==='all'||r.situation_signal===signal)),[records,layers,signal]);
  const pts=useMemo(()=>filtered.flatMap(record=>record.points.map(p=>({...p,record}))),[filtered]);
  function globalView(){map.current?.flyTo({center:[30,27],zoom:1.5,duration:600});setCluster([])}
  function focusView(){map.current?.flyTo({center:topic==='ukraine'?[34,48]:[48,27],zoom:topic==='ukraine'?3.5:3,duration:600});setCluster([])}
  useEffect(()=>{
    if(!host.current)return;
    let cancelled=false;
    const grid={type:'FeatureCollection' as const,features:[...Array.from({length:11},(_,i)=>({type:'Feature' as const,properties:{},geometry:{type:'LineString' as const,coordinates:[[-150+i*30,-80],[-150+i*30,80]]}})),...[-60,-30,0,30,60].map(y=>({type:'Feature' as const,properties:{},geometry:{type:'LineString' as const,coordinates:[[-180,y],[180,y]]}}))]};
    try{
      const m=new maplibregl.Map({container:host.current,locale:{'NavigationControl.ZoomIn':'放大','NavigationControl.ZoomOut':'缩小','AttributionControl.ToggleAttribution':'地图来源','Map.Title':'综合态势地图'},center:[30,27],zoom:1.5,minZoom:1,maxZoom:7,renderWorldCopies:false,attributionControl:false,style:{version:8,sources:{countries:{type:'geojson',data:'/countries.geojson'},grid:{type:'geojson',data:grid}},layers:[{id:'ocean',type:'background',paint:{'background-color':'#091523'}},{id:'grid',type:'line',source:'grid',paint:{'line-color':'#1a3650','line-width':0.65,'line-opacity':0.6}},{id:'land',type:'fill',source:'countries',paint:{'fill-color':'#142d44'}},{id:'focus',type:'fill',source:'countries',filter:['in','ADM0_A3','IRN','USA'],paint:{'fill-color':'#284b68','fill-opacity':0.7}},{id:'borders',type:'line',source:'countries',paint:{'line-color':'#355976','line-width':0.8,'line-opacity':0.8}}]}});
      map.current=m;m.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-left');
      m.addControl(new maplibregl.AttributionControl({customAttribution:'自然地球 · 公开地理底图'}),'bottom-right');
      const o=new MapboxOverlay({interleaved:false,layers:[]});overlay.current=o;m.addControl(o as unknown as maplibregl.IControl);
      m.on('load',()=>{if(!cancelled)setReady(true)});m.on('moveend',()=>{setZoom(m.getZoom());setLayoutVersion(v=>v+1)});
      m.on('error',()=>{if(!cancelled)setError('地图加载失败，可切换证据台账继续研究。')});
      const resize=new ResizeObserver(()=>{m.resize();setLayoutVersion(v=>v+1)});resize.observe(host.current);
      fetch('/countries.geojson').then(r=>r.json()).then(data=>{if(!cancelled)setCountryLabels(data.features.map((f:{properties:Record<string,string|number>})=>({name:f.properties.NAME_ZH||f.properties.NAME,position:[f.properties.LABEL_X,f.properties.LABEL_Y],code:f.properties.ADM0_A3}))) }).catch(()=>{});
      return()=>{cancelled=true;resize.disconnect();m.remove();map.current=null;overlay.current=null;setReady(false)};
    }catch{setError('当前设备不支持地图，请使用证据台账。')}
  },[]);
  useEffect(()=>{if(ready){map.current?.setFilter('focus',['in','ADM0_A3',...(topic==='ukraine'?['RUS','UKR']:topic==='all'?['IRN','USA','RUS','UKR']:['IRN','USA'])]);setCluster([])}},[topic,ready]);
  useEffect(()=>{if(resetKey){globalView();setSignal('all')}},[resetKey]);
  useEffect(()=>setCluster([]),[layers,signal]);
  useEffect(()=>{
    if(!overlay.current||!ready)return;
    const size=zoom<2.7?5:zoom<4?1.6:0;
    const groups=new Map<string,Marker[]>();
    for(const p of pts){const key=size?`${Math.round(p.lon/size)}/${Math.round(p.lat/size)}`:`${p.lon}/${p.lat}`;const group=groups.get(key);if(group)group.push(p);else groups.set(key,[p])}
    const grouped=[...groups.values()].flatMap(group=>Object.keys(colors).map(key=>group.filter(p=>p.record.situation_signal===key)).filter(items=>items.length).map((items,index)=>({...items[0],lon:group[0].lon,lat:group[0].lat,label:placeLabel(items),items,count:new Set(items.map(p=>p.record.id)).size,categories:[...new Set(items.map(p=>p.record.event_category_label))].join(' / '),signals:[...new Set(items.map(p=>p.record.signal_label))].join(' / '),ring:index,signalKey:items[0].record.situation_signal})));
    // Keep source coordinates intact; displacement is only a collision-free display layout.
    const placed:{x:number;y:number;radius:number}[]=[];
    const data=grouped.map(d=>{
      const anchor=map.current!.project([d.lon,d.lat]);
      const radius=8;
      let x=anchor.x,y=anchor.y;
      for(let step=0;step<2000;step++){
        const distance=step===0?0:4*Math.sqrt(step),angle=step*2.399963;
        x=anchor.x+Math.cos(angle)*distance;y=anchor.y+Math.sin(angle)*distance;
        if(placed.every(p=>Math.hypot(x-p.x,y-p.y)>=radius+p.radius+4))break;
      }
      placed.push({x,y,radius});
      const position=map.current!.unproject([x,y]);
      return {...d,radius,position:[position.lng,position.lat] as [number,number],offset:Math.hypot(x-anchor.x,y-anchor.y)>1};
    });
    const labels=countryLabels.filter(c=>zoom>3||majorCountries.has(c.code));
    overlay.current.setProps({layers:[
      new TextLayer({id:'country-labels',data:labels,getPosition:d=>d.position,getText:d=>d.name,getSize:zoom>2.7?12:10,getColor:[128,158,181,210],fontFamily:'Microsoft YaHei',fontWeight:400,characterSet:'auto',getTextAnchor:'middle',pickable:false}),
      new LineLayer({id:'location-connectors',data:data.filter(d=>d.offset),getSourcePosition:d=>[d.lon,d.lat],getTargetPosition:d=>d.position,getColor:[119,158,185,95],getWidth:1,pickable:false}),
      new ScatterplotLayer({id:'evidence',data,pickable:true,getPosition:d=>d.position,getFillColor:d=>colors[d.signalKey]||colors.unknown,getLineColor:[220,238,247,180],stroked:true,getLineWidth:1,lineWidthUnits:'pixels',getRadius:d=>d.radius,radiusUnits:'pixels',onClick:({object})=>{if(object){setCluster(object.items);onClick.current(object.record)}}}),
      new TextLayer({id:'bubble-counts',data,getPosition:d=>d.position,getText:d=>String(d.count),getSize:11,getColor:[10,25,40,255],fontFamily:'Microsoft YaHei',fontWeight:700,characterSet:'auto',pickable:false}),
      new TextLayer({id:'sea-labels',data:topic==='ukraine'?ukrainePlaces:places,getPosition:d=>d.position as [number,number],getText:d=>d.label,getSize:11,getColor:[114,182,208,220],getPixelOffset:[0,20],fontFamily:'Microsoft YaHei',characterSet:'auto',outlineWidth:2,outlineColor:[9,21,35],fontSettings:{sdf:true},visible:zoom>2.3}),
    ],getTooltip:({object})=>object?.record?`${object.label} · ${signals[object.signalKey]} · ${object.count} 篇关联报道\n${object.categories}\n${new Set(object.items.map((p:Marker)=>p.label)).size>1?'附近多个地点的报道汇总，请展开查看各地点':placeNote(object.items[0])}`:null});
  },[pts,zoom,ready,countryLabels,topic,layoutVersion]);
  const count=new Set(pts.map(p=>p.record.id)).size;
  return <div className="map-shell"><div ref={host} className="map-canvas"/>
    <div className="map-view-controls"><button onClick={globalView}><Globe2 size={14}/>全球</button><button onClick={focusView}><Focus size={14}/>聚焦主题</button></div>
    <div className="map-signal-tabs">{Object.entries(signals).map(([key,label])=><button key={key} className={signal===key?'selected':''} onClick={()=>setSignal(key)}>{label}{key!=='all'&&<i style={{background:`rgb(${colors[key].slice(0,3).join(',')})`}}/>}</button>)}</div>
    <div className="map-badge"><b>{topic==='ukraine'?'俄乌冲突':topic==='all'?'双主题':'美伊局势'} · 综合动态</b><p>军事、外交、政策、能源与社会动态</p><small>地图展示 {count} 条动态 · 当前分类 {filtered.length} 条 · 点位大小固定</small></div>
    <div className="map-legend"><button className="legend-heading" aria-expanded={legendOpen} aria-label="事件图层" onClick={()=>setLegendOpen(v=>!v)}><Layers size={13}/>事件类型 · {legendOpen?'收起':'展开'}</button>{legendOpen&&<>{Object.entries(categories).map(([key,label])=><label key={key}><input type="checkbox" checked={layers[key]} onChange={e=>setLayers({...layers,[key]:e.target.checked})}/>{label}<span>{records.filter(r=>r.event_category===key).length}</span></label>)}<small>点位大小固定；数字为报道数，同地点错开展示</small></>}</div>
    {cluster.length>1&&<div className="map-cluster"><div><b>{placeLabel(cluster)} · 关联报道</b><button aria-label="关闭地点列表" onClick={()=>setCluster([])}><X size={14}/></button></div>{Array.from(new Map(cluster.map(p=>[p.record.id,p])).values()).map(p=><button key={p.record.id} onClick={()=>onSelect(p.record)}>{p.record.title}<small style={{display:'block',marginTop:6,color:'#9eb4c7'}}>{placeLabel(cluster.filter(x=>x.record.id===p.record.id))} · {placeNote(cluster.find(x=>x.record.id===p.record.id&&x.label==='岩国')||p)}</small></button>)}</div>}
    {error&&<div className="map-error">{error}</div>}
    {!count&&<div className="map-no-points">当前分类暂无可定位动态，可在证据台账查看全部报道</div>}
  </div>
}
