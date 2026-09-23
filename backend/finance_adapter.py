"""Import observed market series, preserving upstream provenance and cadence."""
import json
import math
import re
from .store import digest

ASSET_BUCKETS={'equity':['equity'],'rates':['rates'],'credit':['credit'],'fx':['fx'],'commodity':['energy','stocks','shipping','fx']}

def chinese_label(text):
    for word,label in {'Brent':'布伦特','WTI':'西得州中质','Henry Hub':'亨利枢纽','Frontline':'前线海运','TIPS':'通胀保值债券','ETF':'交易型基金','EFFR':'有效联邦基金利率','SPR':'战略石油储备','DXY':'美元指数','VIX':'波动率','bp':'基点','pp':'个百分点'}.items():
        text=text.replace(word,label)
    text=re.sub(r'(\d+)Y',r'\1年期',text)
    text=re.sub(r'(\d+)M',r'\1个月期',text)
    return text

def load_finance(root,read,manifests,records,observations,sources):
    folder=root/'finance-front-monitor'/'output'
    file=folder/'LATEST-records.jsonl'
    if not file.exists():return
    rows=[json.loads(line) for line in read(file).splitlines() if line.strip()]
    archive=manifests[-1]['archive']
    for row in rows:
        row['label']=chinese_label(row['label'])
        series=row['series']; rid='finance-'+digest([series,archive])
        refs=[archive]; history=[]
        history_file=folder/'history'/f'{series}.jsonl'
        if history_file.exists():
            history=[json.loads(line) for line in read(history_file).splitlines() if line.strip()]
            refs.append(manifests[-1]['archive'])
        else:history=[{'date':d,'value':v} for d,v in row.get('window',[])]
        history=[{'date':p['date'],'value':p['value']} for p in history if p['date']<=str(row.get('date') or '') and (p['value'] is None or isinstance(p['value'],(int,float)) and math.isfinite(p['value']))]
        history=sorted({p['date']:p for p in history}.values(),key=lambda p:p['date'])
        quality_issue=''
        if series=='brent_vol20':
            quality_issue='上游使用价格差计算，和“收益率波动率”定义不一致，已隔离，待修正后接入。'
            history=[]
        hit=row.get('chain_hit') or {}; provider=hit.get('provider','derived')
        # Preserve raw responses used by this run alongside the parsed histories.
        run_day=str(row.get('run_tag',''))[:10]
        for attempt in row.get('tried',[]):
            raw=folder/run_day/'raw'/str(attempt.get('raw') or '')
            if attempt.get('ok') and raw.is_file() and raw.resolve().is_relative_to(folder.resolve()):
                read(raw,False);refs.append(manifests[-1]['archive'])
        method=row.get('definition') or '来源公开观测；市场行情为已收盘值，非实时价格'
        sources_name={'yahoo':'雅虎财经','treasury':'美国财政部','nyfed':'纽约联邦储备银行','eia_wpsr':'美国能源信息署','arcgis':'国际货币基金组织港口监测','derived':'爬虫派生指标'}
        unit={'bp':'基点','USD':'美元','$':'美元','$/bbl':'美元/桶','$/oz':'美元/盎司','$/MMBtu':'美元/百万英热单位','pp':'个百分点','%':'%'}.get(row['unit'],row['unit'])
        records[rid]={'id':rid,'source_id':'finance:'+series,'source_name':sources_name.get(provider,'金融数据来源'),'publisher':sources_name.get(provider,'金融数据来源'),'tier':'T1' if provider in ('treasury','nyfed','eia_wpsr','arcgis') else '未分级','title':row['label'],'text':method+'。'+row.get('note','')+' '+row.get('caveat',''),'url':hit.get('url',''),'published_at':row.get('date'),'fetched_at':row.get('run_tag'),'first_seen':None,'event_at':row.get('date'),'party':'未标注','nature':'派生指标' if row.get('derived') else '结构化指标','verification':'已导入，未独立回源复核','classification':'金融爬虫结构化观测','topics':['usiran','ukraine'],'dimensions':{'usiran':'financial'},'scope':'共享市场背景，不代表单一冲突归因','points':[],'origin_id':'finance:'+series,'adapters':['finance-front-monitor'],'raw_refs':refs}
        observations.append({'id':'finance:'+series,'series':series,'name':row['label'],'unit':unit,'frequency':{'daily':'日频','weekly':'周频','monthly':'月频'}.get(row['cadence'],row['cadence']),'scope':'共享市场背景','topic':None,'shared_market':True,'dimension':'financial','method':method,'boundary':'同期变动不等于冲突因果；行情与官方统计存在发布滞后','source_ids':[rid],'history':history,'comparison_step':row.get('step',1),'derived':bool(row.get('derived')),'inputs':row.get('inputs',[]),'bucket':row['bucket'],'as_of':row.get('run_tag'),'new_point':row.get('new_point'),'lag_days':row.get('lag_days'),'max_lag':row.get('max_lag'),'upstream_state':row.get('state'),'revisions':row.get('revised',[])})
        observations[-1]['quality_issue']=quality_issue
        if quality_issue:records[rid]['text']=quality_issue+' 原始数据已归档。原定义：'+method
        sources.append({'id':'finance:'+series,'monitor':'finance-front-monitor','name':row['label'],'state':'imported' if history else 'no_data','count':len(history),'latest':row.get('date'),'checked_at':row.get('run_tag')})
