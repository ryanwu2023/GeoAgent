"""Evidence-bounded opening statements, not unconditional allocation signals."""
import re
from datetime import datetime, timedelta, timezone
from .chinese import chinese_text
from .events import event_metadata

def topic_takeaway(snapshot, topic, cache):
    as_of=datetime.fromisoformat(snapshot['created_at'].replace('Z','+00:00'))
    if as_of.tzinfo is None:as_of=as_of.replace(tzinfo=timezone.utc)
    candidates=[]
    terms=r'伊朗|美伊|胡塞|沙特|霍尔木兹|iran|houthi|hormuz' if topic=='usiran' else r'俄乌|乌克兰|俄军|乌军|基辅|ukrain|russian (?:strike|attack|forces)'
    for r in snapshot['records']:
        if topic not in r['topics'] or r.get('supplemental'):continue
        try:
            date=datetime.fromisoformat((r.get('published_at') or '').replace('Z','+00:00'))
            if date.tzinfo is None:continue
        except ValueError:continue
        if not as_of-timedelta(days=7)<=date<=as_of:continue
        title=chinese_text(r['title'],cache)
        if '中文译文准备中' in title or not re.search(terms,r['title']+' '+title,re.I):continue
        action=bool(re.search(r'strike|attack|missile|offensive|front.line|袭击|空袭|打击|战线|进攻|炮击|无人机|交战',r['title']+' '+title,re.I))
        talks=bool(re.search(r'negotiat|ceasefire|peace talks|truce|谈判|停火|和谈|斡旋|达成协议',r['title']+' '+title,re.I))
        if not action and not talks:continue
        # Prefer native-language reports to unreviewed machine translations in the opening.
        native=bool(re.search('[\u4e00-\u9fff]',r['title']))
        commentary=bool(re.search('深读|观察|问答|威胁有多大|analysis|opinion',r['title'],re.I))
        candidates.append((date,r,title,'diplomacy' if talks else 'military',native,commentary))
    candidates.sort(key=lambda v:(v[4],not v[5],v[0]),reverse=True)
    chosen=[];seen=set()
    for category in ('military','diplomacy'):
        item=next((x for x in candidates if x[3]==category and x[2] not in seen),None)
        if item:chosen.append(item);seen.add(item[2])
    condition=('美方直接介入范围、伊朗及地区武装后续行动与谈判执行' if topic=='usiran' else '战线和远程打击变化、援助实际交付与停火执行')
    asset=('若能源供应或航运持续受损，应重点评估原油与黄金的防御作用、能源进口方权益及信用风险，若缓和落实则重估风险溢价，利率债与外汇需同时核对通胀、实际利率和美元' if topic=='usiran' else '若能源、粮食运输再受冲击，应重点评估相关商品与欧洲权益及信用风险，若停火落实则重估风险溢价，黄金、利率债与外汇仍需核对实际利率和政策预期')
    if len(chosen)==2:
        current='从近期公开报道看，'+('美伊冲突仍有地区军事外溢风险，同时保留外交接触窗口' if topic=='usiran' else '俄乌军事行动与停火外交仍并行，谈判线索尚不能等同于战事收束')
    elif chosen:
        current=('近期公开报道仍有军事行动线索，当前材料不足以确认全面收束' if chosen[0][3]=='military' else '近期公开报道出现谈判或停火线索，但缺少行动层面的交叉验证，暂不能判定全面缓和')
    else:
        current='最近一周缺少可引用且有明确发布时间的冲突进展，暂不能可靠概括当前阶段'
    return {'text':f'{current}，后续走向取决于{condition}；资产配置上，{asset}。','evidence_ids':[x[1]['id'] for x in chosen],'kind':'基于报道的条件研判'}
