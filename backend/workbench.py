"""Auditable selection and topic-specific conditional research."""
from datetime import date

PRIORITIES={
 'usiran':['brent','hormuz_total','breakeven10_fred','dgs10','hy_oas','gold','sp500','usdcny'],
 'ukraine':['natgas','brent','eurusd','hy_oas','dgs10','gold','sp500'],
 'all':['brent','hormuz_total','dgs10','hy_oas','gold','sp500','eurusd'],
}
PATHS={
 'usiran':{
  'commodity':('能源设施与海峡运输','若供应损失持续且替代供给不足，原油可能获得支撑；黄金另需核对实际利率。','设施恢复、替代出口或库存释放抵消损失','实际停产量、出口量、战争险与运输恢复'),
  'equity':('能源成本与国防补库','若能源成本持续提高，进口及耗能行业利润可能承压；军工需见实际订单与交付。','成本转嫁或估值下降抵消订单利好','订单交付、能源行业相对收益与盈利修正'),
  'rates':('能源通胀与避险需求','若能源冲击推升通胀补偿，名义债可能承压；避险买盘可能形成相反作用。','实际利率或通胀补偿下降，供给冲击短暂','名义与实际利率分解、通胀预期和政策路径'),
  'credit':('运输与融资成本','若受影响企业成本及再融资压力上升，相关信用利差可能扩大。','企业套保、现金流与政策支持缓冲成本','发行人能源暴露、到期债务及分行业利差'),
  'fx':('能源进口账单与相对利差','若进口成本持续上升，能源进口方货币可能承压；美元还受利差与避险需求影响。','政策干预、出口收入或资金流抵消贸易冲击','货币对、双边利差及能源贸易收支'),
 },
 'ukraine':{
  'commodity':('炼厂、电力、粮食与黑海运输','若炼厂或黑海出口持续受损，相关能源或粮食品种价格可能受到供给收缩支撑；需核对欧洲市场。','维修恢复、替代产地及储备抵消缺口','炼厂实际减产、欧洲天然气储气与粮食出口'),
  'equity':('欧洲成本与援助产业链','若援助实际交付带动补库，相关产业订单可能改善；欧洲工业仍受能源与需求共同影响。','承诺未交付、财政约束或需求走弱','援助交付、欧洲行业盈利及订单兑现'),
  'rates':('欧洲能源、财政与增长','若能源或援助支出改变通胀和发行压力，利率债可能重估；增长下行可能带来反向作用。','能源恢复、财政安排或政策宽松缓冲压力','欧洲债券曲线、发行计划与政策预期；美债仅作背景'),
  'credit':('制裁暴露与企业现金流','若制裁或基础设施损失影响收入和支付，相关发行人信用风险可能上升。','直接暴露低、保险赔付或融资支持','发行人地区收入、制裁适用范围及融资可得性'),
  'fx':('欧洲贸易条件与制裁结算','若能源进口负担与制裁结算变化持续，欧元及相关货币可能受影响；需结合相对政策。','替代能源、资本管制或双边利差形成反向作用','欧元及卢布、贸易结算与跨境资金流'),
 }
}

def freshness(m,as_of):
    current=m.get('current')
    try:age=(date.fromisoformat(as_of[:10])-date.fromisoformat(current['date'][:10])).days if current else None
    except (ValueError,TypeError):age=None
    frequency=m.get('frequency','')
    threshold=m.get('max_lag')
    if not isinstance(threshold,(int,float)):threshold=45 if '月' in frequency else 14 if '周' in frequency else 7
    state='隔离' if m.get('quality_issue') else '无有效值' if not current or current.get('value') is None else '日期待核对' if age is None or age<0 else '数据滞后' if age>threshold else '可用'
    return {**m,'freshness':state,'age_days':age,'freshness_limit':threshold}

def important_metrics(rows,topic,limit=7):
    priority=PRIORITIES.get(topic,PRIORITIES['all'])
    valid=[m for m in rows if m.get('current') and m['current']['value'] is not None and not m.get('quality_issue')]
    valid.sort(key=lambda m:(m.get('freshness') not in (None,'可用'),priority.index(m.get('series')) if m.get('series') in priority else 100,m.get('name','')))
    selected=[];groups={}
    for m in valid:
        group=m.get('bucket') or m.get('dimension') or 'other'
        if groups.get(group,0)>=2:continue
        selected.append({**m,'selection_reason':'按主题重点与资产渠道选取，同类最多两项；非异常评分或预测概率'})
        groups[group]=groups.get(group,0)+1
        if len(selected)>=limit:break
    return selected

def topic_paths(assets,topic,records,as_of):
    from .research import candidates
    from .catalog import ASSETS
    recent=[]
    for r in records:
        if r.get('supplemental') or r.get('nature') in ('结构化指标','派生指标','市场观测'):continue
        try:
            age=(date.fromisoformat(as_of[:10])-date.fromisoformat((r.get('published_at') or '')[:10])).days
            if 0<=age<=14:recent.append(r)
        except ValueError:continue
    for asset in assets:
        paths=[]
        for t in (['usiran','ukraine'] if topic=='all' else [topic]):
            channel,condition,counter,watch=PATHS[t][asset['id']]
            terms=next(a['terms'] for a in ASSETS if a['id']==asset['id'])
            ids=candidates([r for r in recent if t in r['topics']],terms,3)
            paths.append({'topic':t,'channel':channel,'condition':condition,'counter':counter,'watch':watch,'evidence_ids':ids,'status':'有近期线索，待核验' if ids else '当前范围无近期匹配线索'})
        asset['topic_paths']=paths
        asset['evidence_ids']=list(dict.fromkeys(asset['evidence_ids']+[rid for p in paths for rid in p['evidence_ids']]))
    return assets
