"""Transparent market observations and conditional research, not causal attribution."""
GAPS = {
 'equity':'补充欧洲及中国股指、行业盈利修正、军工订单实际交付；当前样本主要覆盖美股。',
 'rates':'补充央行政策路径、国债发行与期限溢价；区分通胀压力与避险需求。',
 'credit':'补充发行人区域暴露、到期债务及违约数据；利差只能描述融资风险定价。',
 'commodity':'补充受损产能、实际出口、运费与保险费、欧洲天然气和小麦；通行量下降不必然等于供给损失。',
 'fx':'补充欧元区与美国利差、卢布及跨境资金流；美元指数不能代表所有货币对。',
}
REQUIREMENTS = {
 'sustain':['连续可比的行动频率与地域记录','补给、援助的实际交付时间与数量','谈判执行情况及行动是否同步变化'],
 'escalate':['相对前期新增的打击地点、强度或参与方','设施损失、停产量或通行受阻的持续时间','独立来源核验，并排除同源转载'],
 'ease':['协议文本、执行起点和第三方核查','可比较的行动减少、撤出或运输恢复记录','违反协议事件与观察窗口'],
}

def enrich(assets, metrics):
    by={m.get('series'):m for m in metrics if m.get('current') and m.get('previous') and m.get('delta') is not None and not m.get('quality_issue') and m.get('freshness') in (None,'可用')}
    groups={'equity':['sp500','nasdaq','xle','ita'],'rates':['dgs2','dgs10'],'credit':['hy_oas','ig_oas'],'commodity':['brent','wti','gold','natgas'],'fx':['dxy','usdcny','eurusd']}
    for a in assets:
        selected=[by[s] for s in groups[a['id']] if s in by]
        a['missing_data']=GAPS[a['id']]
        a['assessment_kind']='市场观察 + 条件传导；未作冲突因果归因'
        a['view']='当前筛选范围缺少可比市场观测。'+a['mechanism']
        if not selected: continue
        descriptions=[]
        for m in selected:
            d=m['delta']; verb='上升' if d>0 else '下降' if d<0 else '持平'
            descriptions.append(f"{m['name']}{verb}（{m['previous']['date']}→{m['current']['date']}）")
        aid=a['id']
        if aid in ('rates','credit'):
            signs={1 if m['delta']>0 else -1 if m['delta']<0 else 0 for m in selected}
            label=('债价承压' if aid=='rates' else '信用风险溢价扩大') if signs=={1} else ('债价获支撑' if aid=='rates' else '信用风险溢价收窄') if signs=={-1} else '市场变化分化' if len(signs)>1 else '市场变化有限'
        else: label='分品种观察'
        a['direction']=label
        a['view']='；'.join(descriptions)+'。'+{
         'equity':'需要同时看大盘与能源、军工行业的相对表现，不能把冲突升级等同于整体权益上涨。',
         'rates':'收益率上升对应存量同期限债券价格承压。冲突可能带来避险买盘，也可能通过能源推升通胀，两条路径需要分别验证。',
         'credit':'利差扩大通常意味着额外信用风险补偿上升；持平不能证明风险解除。信用债总回报还受无风险利率和久期影响。',
         'commodity':'能源看供应损失与替代供给，黄金看实际利率、美元和避险需求，不能给全部商品一个共同方向。',
         'fx':'欧元兑美元上升代表欧元相对走强，美元兑人民币上升代表人民币相对走弱；还需核对相对利差。',
        }[aid]
        a['change']='首次规则研判；以上比较的是市场观测前值，不是上次研究观点。'
        a['evidence_ids']=list(dict.fromkeys(a['evidence_ids']+[rid for m in selected for rid in m['source_ids']]))
    return assets

def overview(assets):
    usable=[a for a in assets if a['direction']!='证据不足']
    opening='研究观点：当前应按能源供给、利率与信用、行业暴露三条线跟踪，而不是将两场冲突机械映射成所有资产同涨同跌。市场表现是检验线索，不能单独确认冲突进入升级或缓和阶段。'
    if not usable: opening='研究观点：当前筛选范围缺少可比市场数据，先建立事件与市场时间轴，再判断冲突传导；暂不能形成当前市场方向判断。'
    if usable:
        opening='当前市场观察：'+'；'.join(a['name']+'—'+a['direction'] for a in usable)+'。这些观察不代表冲突因果；主题条件、反证与后续触发点见分主题分析。'
    return opening, [{'text':a['view'],'evidence_ids':a['evidence_ids']} for a in usable]
