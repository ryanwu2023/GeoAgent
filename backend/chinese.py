"""Chinese presentation cache, separate from immutable original evidence."""
import re
import subprocess
import sys
import os
import json
from .store import db, digest, now, ROOT, data_dir, snapshots, get_snapshot
from .events import event_metadata
from .translation_quality import language, normalize_terms, quality

def table(c):
    c.execute('CREATE TABLE IF NOT EXISTS translations (hash TEXT PRIMARY KEY, original TEXT NOT NULL, chinese TEXT NOT NULL, method TEXT NOT NULL, created_at TEXT NOT NULL)')

def save_translation(original, chinese, method='本地机器翻译（术语校准）'):
    global _translation_memory
    _translation_memory = None
    with db() as c:
        table(c)
        c.execute('INSERT OR REPLACE INTO translations VALUES (?,?,?,?,?)',(digest(original),original,chinese,method,now()))

_translation_memory = None

def translation_cache():
    global _translation_memory
    from time import monotonic
    key=str(data_dir().resolve())
    if _translation_memory and _translation_memory[0]==key and monotonic()-_translation_memory[1]<5:
        return _translation_memory[2]
    result=_read_translation_cache()
    _translation_memory=(key,monotonic(),result)
    return result

def _read_translation_cache():
    with db() as c:
        table(c)
        cache={r['hash']:r['chinese'] for r in c.execute("SELECT hash,chinese,original,method FROM translations WHERE method != '本地机器翻译'") if is_chinese(r['chinese']) and (r['method'].startswith('模型翻译') or language(r['original']) not in ('cyrillic','hebrew','arabic_unknown'))}
    overrides=json.loads((ROOT/'backend'/'zh_overrides.json').read_text(encoding='utf-8'))
    cache.update({digest(k):v for k,v in overrides.items()})
    return cache

def is_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]',text)) and len(re.findall(r'[a-zA-Z]',text)) < max(30,len(text)*.3)

def chinese_text(text, cache, fallback='中文译文准备中，可查看原始来源'):
    if not text:return ''
    result=cache.get(digest(text),text if is_chinese(text) else fallback)
    if re.search(r'\bAI\b',text) and not re.search(r'Amnesty',text,re.I):
        result=result.replace('大赦国际','人工智能')
    for en,zh in {'Zaporizhzhia':'扎波罗热','Zelenskyy':'泽连斯基','KYIV':'基辅','Lockheed Martin':'洛克希德·马丁','GM Defense':'通用汽车防务公司','Novaspace':'诺瓦航天咨询公司','Bitcoin':'比特币','IRGC':'伊朗伊斯兰革命卫队','AI':'人工智能'}.items():
        result=re.sub(r'\b'+re.escape(en)+r'\b',zh,result)
    if re.search(r'Patriot',text,re.I):result=result.replace('爱国者电池','爱国者防空导弹连').replace('爱国者拦截机','爱国者拦截弹').replace('帕特里奥','爱国者')
    return normalize_terms(result)

SOURCE_NAMES={'华泰政策':'华泰政策 · 中东简报（高可信研究资料）','USNI News':'美国海军学会新闻','Defense News':'防务新闻','Al Jazeera English':'半岛电视台英文频道','Yahoo':'雅虎新闻','Reuters':'路透社','Associated Press':'美联社','BBC':'英国广播公司','France 24':'法国二十四小时新闻台','Google News':'谷歌新闻','naval-monitor':'海军动态采集','defense-spend-monitor':'军费与补给采集','readiness-monitor':'战备动态采集'}

def source_name(text,cache):
    localized={'Mehr':'迈赫尔通讯社','Pars Today':'今日波斯','Sepah News':'伊朗革命卫队新闻社','Tasnim':'塔斯尼姆通讯社','Fars':'法尔斯通讯社','IRNA':'伊朗伊斯兰共和国通讯社','ISNA':'伊朗学生通讯社','Mashregh':'马什雷格新闻','Kayhan':'宇宙报','Defa Press':'伊朗防务通讯社','Tehran Times':'德黑兰时报','الم':'阿拉伯语新闻来源'}
    for source,zh in localized.items():
        if source.lower() in text.lower():return zh
    for source,zh in SOURCE_NAMES.items():
        if source.lower() in text.lower():return zh
    if digest(text) in cache:return cache[digest(text)]
    text=re.sub(r'\([^)]*[A-Za-z][^)]*\)|（[^）]*[A-Za-z][^）]*）','',text)
    text=re.sub(r'\b(?:IRNA|ISNA|IRGC|MTS|RSS|CRS|CENTCOM|AP)\b','',text)
    return chinese_text(text.strip(' ·—（）()'),cache,'国际公开来源')

def present_record(record,cache=None):
    cache=translation_cache() if cache is None else cache
    metadata=event_metadata(record)
    metadata['points']=[{**p,'label':chinese_text(p['label'],cache,'相关地区')} for p in metadata['points']]
    title=normalize_terms(record.get('title_zh') or chinese_text(record['title'],cache))
    text=normalize_terms(record.get('summary_zh') or chinese_text(record['text'],cache))
    review=quality(record['title']+' '+record['text'],title+' '+text)
    return {**record,**metadata,'translation_quality':review,'original_title':record['title'],'original_text':record['text'],
            'title':title,'text':text,
            'source_name':source_name(record['source_name'],cache),'translation_note':review['status']+'。'+('；'.join(review['issues'])+'。' if review['issues'] else '')+'机器译文不等于语义已验证，请对照原文。'}

def present_answer(answer):
    cache=translation_cache()
    return {**answer,'facts':[{**f,'text':f['text'].replace(' USD',' 美元')} for f in answer['facts']],'citations':[present_record(r,cache) for r in answer['citations']]}

_worker=None

def translation_running():
    if _worker is not None and _worker.poll() is None:return True
    lock_path=data_dir()/'translation.lock'
    if os.name=='nt' and lock_path.exists():
        import msvcrt
        with lock_path.open('r+b') as lock:
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:return True
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
    return False

def start_translation():
    global _worker
    if translation_running():return
    if not (data_dir()/'models'/'opus-mt-en-zh'/'pytorch_model.bin').exists():return
    if translation_status()['pending']==0:return
    folder=data_dir();folder.mkdir(parents=True,exist_ok=True)
    with (folder/'translation.log').open('a',encoding='utf-8') as log:
        _worker=subprocess.Popen([sys.executable,'-u',str(ROOT/'scripts'/'translate_snapshot.py')],cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)

def translation_status():
    available=snapshots()
    if not available:return {'pending':0,'ready':0,'total':0,'running':False}
    records=get_snapshot(available[0]['id'])['records']
    cache=translation_cache()
    ready=sum(all(not r[f] or is_chinese(r[f]) or digest(r[f]) in cache for f in ('title','text')) for r in records)
    return {'pending':len(records)-ready,'ready':ready,'total':len(records),'running':translation_running()}

def localize_brief_markdown(markdown):
    cache=translation_cache()
    # Only change source-link labels in presentation; keep the stored archive immutable.
    return re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)',lambda m:'['+chinese_text(m[1],cache)+']('+m[2]+')',markdown).replace(' USD',' 美元')
