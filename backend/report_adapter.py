"""Secondary research is searchable context, never an independent event confirmation."""
import re
from .store import digest

def load_reports(root,read,manifests,records,sources):
    for file in sorted(root.glob('deep-research-report*.md')):
        original=read(file)
        archive=manifests[-1]['archive']
        heading=next((line.lstrip('# ').strip() for line in original.splitlines() if line.startswith('# ')),file.stem)
        dated=re.search(r'截至\s*(\d{4})年(\d{1,2})月(\d{1,2})日',original)
        as_of='-'.join([dated[1],dated[2].zfill(2),dated[3].zfill(2)]) if dated else None
        sections=re.split(r'(?m)^(?=## )',original)
        count=0
        for index,section in enumerate(sections):
            if not section.strip():continue
            title=section.splitlines()[0].lstrip('# ').strip()
            # Remove non-portable citation tokens from display; immutable original remains downloadable.
            text=re.sub(r'cite.*?','〔报告原引用待回源核验〕',section)
            rid='report-'+digest([file.name,index])
            record={'id':rid,'source_id':file.stem,'source_name':'补充深度研究报告','publisher':'本地研究资料','tier':'未分级',
             'title':heading+' · '+title,'text':text,'url':'/api/archive/'+archive,'published_at':None,'fetched_at':None,'first_seen':None,'event_at':None,
             'report_as_of':as_of,'party':'未标注','nature':'补充研究报告（观点与背景）','verification':'报告观点未独立核验；引用标记需回源',
             'classification':'补充资料，不计为独立新闻或事件','topics':['usiran','ukraine'],'dimensions':{},'scope':'双主题研究背景',
             'points':[],'origin_id':'report:'+file.name,'adapters':[file.stem],'raw_refs':[archive],'supplemental':True}
            records[rid]=record;count+=1
        sources.append({'id':file.stem,'monitor':file.stem,'name':'补充深度研究报告','state':'imported','count':count,'latest':None,'report_as_of':as_of})

    huatai=root/'华泰中东简报.md'
    if huatai.exists():
        original=read(huatai)
        archive=manifests[-1]['archive']
        starts=[m.start() for m in re.finditer(r'(?m)^(?=【海外每日速递|华泰政策[^\n]*(?:简报|小结))',original)]
        sections=[original[a:b].strip() for a,b in zip(starts,starts[1:]+[len(original)]) if original[a:b].strip()]
        count=0;latest=None
        for index,section in enumerate(sections):
            heading=section.splitlines()[0].strip(' #')
            dated=re.search(r'(20\d{2})[.年/-](\d{1,2})(?:[.月/-](\d{1,2}))?',heading)
            short=re.search(r'[（(](\d{1,2})/(\d{1,2})[）)]',heading)
            if dated:
                range_end=re.match(r'-(\d{1,2})',heading[dated.end():])
                day=range_end[1] if range_end else dated[3] or '01'
                as_of='-'.join((dated[1],dated[2].zfill(2),day.zfill(2)))
            elif short:
                as_of='2026-'+short[1].zfill(2)+'-'+short[2].zfill(2)
            else:as_of=None
            latest=max(latest or '',as_of or '') or None
            rid='huatai-'+digest([huatai.name,index,section])
            record={'id':rid,'source_id':'huatai-middle-east-brief','source_name':'华泰政策 · 中东简报','publisher':'华泰证券研究资料','tier':'T2',
             'title':heading,'text':section,'url':'/api/archive/'+archive,'published_at':as_of,'fetched_at':None,'first_seen':None,'event_at':as_of,
             'report_as_of':as_of,'party':'研究机构','nature':'高可信研究简报（综合与转述）','verification':'研究资料可信度高；其中转述、预测和市场数字仍需回源核验',
             'classification':'高可信补充研究资料，不计为独立事件确认','topics':['usiran'],'dimensions':{'usiran':'financial'},'scope':'美伊与中东研究背景',
             'points':[],'origin_id':'huatai:'+digest([heading,as_of]),'adapters':['huatai-middle-east-brief'],'raw_refs':[archive],'supplemental':True}
            records[rid]=record;count+=1
        sources.append({'id':'huatai-middle-east-brief','monitor':'huatai-middle-east-brief','name':'华泰政策 · 中东简报（高可信研究资料）','state':'imported','count':count,'latest':latest,'tier':'T2','report_as_of':latest})
