"""Chinese presentation cache, separate from immutable original evidence."""
import re
import difflib
import subprocess
import sys
import os
import json
from .store import db, digest, now, ROOT, data_dir, snapshots, get_snapshot, get_working_snapshot_id
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

def _translation_marker():
    """Cheap cross-process cache version for the persisted translation table."""
    with db() as c:
        table(c)
        row = c.execute("SELECT COUNT(*) AS count,COALESCE(MAX(created_at),'') AS latest FROM translations").fetchone()
    overrides = ROOT / 'backend' / 'zh_overrides.json'
    return (int(row['count']), str(row['latest']), overrides.stat().st_mtime_ns)

def translation_cache():
    global _translation_memory
    key=str(data_dir().resolve())
    marker = _translation_marker()
    if _translation_memory and _translation_memory[0]==key and _translation_memory[1]==marker:
        return _translation_memory[2]
    result=_read_translation_cache()
    _translation_memory=(key,marker,result)
    return result

def _read_translation_cache():
    with db() as c:
        table(c)
        cache={r['hash']:r['chinese'] for r in c.execute("SELECT hash,chinese,original,method FROM translations WHERE method != '本地机器翻译'") if is_chinese(r['chinese']) and (r['method'].startswith(('模型翻译','模型批量翻译')) or language(r['original']) not in ('cyrillic','hebrew','arabic_unknown'))}
    overrides=json.loads((ROOT/'backend'/'zh_overrides.json').read_text(encoding='utf-8'))
    cache.update({digest(k):v for k,v in overrides.items()})
    return cache

def is_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]',text)) and len(re.findall(r'[a-zA-Z]',text)) < max(30,len(text)*.3)

def chinese_text(text, cache, fallback=None):
    if not text:return ''
    # A missing translation is not a queued job. Preserve readable source text
    # unless the caller explicitly requests a non-content fallback (e.g. a label).
    if digest(text) not in cache and not is_chinese(text) and fallback is None:
        return text
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

def record_translation(record, cache):
    """Display projection only: keep immutable source evidence untouched."""
    values = {}
    states = {}
    nonempty_states = []
    for field, translated_field in (('title', 'title_zh'), ('text', 'summary_zh')):
        original = record.get(field) or ''
        translated = record.get(translated_field) or cache.get(digest(original), '')
        if translated and not is_chinese(translated):
            translated = ''
        ready = not original or is_chinese(original) or bool(translated and is_chinese(translated))
        if ready:
            # Retain the existing original-aware terminology corrections for
            # actual Chinese translations (AI, Patriot, etc.). Untranslated
            # source text must bypass those substitutions and remain verbatim.
            values[field] = (
                chinese_text(original, {digest(original): translated} if translated else {})
                if original else normalize_terms(translated)
            )
        else:
            values[field] = original
        states[f'{field}_translation_state'] = 'ready' if ready else 'untranslated'
        if original:
            nonempty_states.append(ready)
    overall = 'ready' if all(nonempty_states) else 'partial' if any(nonempty_states) else 'untranslated'
    return {**values, **states, 'translation_state': overall}

def present_record(record,cache=None):
    cache=translation_cache() if cache is None else cache
    metadata=event_metadata(record)
    metadata['points']=[{**p,'label':chinese_text(p['label'],cache,'相关地区')} for p in metadata['points']]
    translation=record_translation(record,cache)
    # Original text shown as a fallback is not a machine translation. Check
    # only fields that actually have a Chinese translation, so a partial
    # record's untranslated body cannot create a false translation warning.
    translated_fields=[
        field for field in ('title','text')
        if record.get(field) and not is_chinese(record[field])
        and translation[f'{field}_translation_state']=='ready'
    ]
    original_language=language(record['title']+' '+record['text'])
    if translated_fields:
        issues=list(dict.fromkeys(
            issue for field in translated_fields
            for issue in quality(record[field],translation[field])['issues']
        ))
        review={'status':'待复核' if issues else '机器译文待语义核验',
                'issues':issues,'language':original_language}
        note=('暂无完整中文译文，缺译部分显示原文；' if translation['translation_state'] != 'ready' else '')+review['status']+'。'+('；'.join(issues)+'。' if issues else '')+'机器译文不等于语义已验证，请对照原文。'
    elif translation['translation_state'] != 'ready':
        partial=translation['translation_state']=='partial'
        review={'status':'部分内容未翻译' if partial else '未翻译',
                'issues':[],'language':original_language}
        note=('部分内容尚未翻译，缺译部分显示原文；' if partial else '尚未翻译，当前显示原文；')+'未执行译文质量检查。'
    else:
        review={'status':'中文原文','issues':[],'language':original_language}
        note='中文原文，无需翻译。'
    return {**record,**metadata,'translation_quality':review,'original_title':record['title'],'original_text':record['text'],
            **translation,
            'source_name':source_name(record['source_name'],cache),'translation_note':note}

def present_record_summary(record, cache=None, text_limit=600):
    """Return the fields needed by lists and the map.

    Full originals, extraction payloads and translation diagnostics remain
    available from /api/evidence/{id}.  Keeping them out of /api/workspace
    prevents every menu change from transferring the complete evidence corpus.
    """
    cache = translation_cache() if cache is None else cache
    metadata = event_metadata(record)
    metadata['points'] = [
        {**p, 'label': chinese_text(p['label'], cache, '相关地区')}
        for p in metadata['points']
    ]
    translation = record_translation(record, cache)
    body = translation['text']
    if len(body) > text_limit:
        body = body[:text_limit].rstrip() + '…'
    keys = (
        'id', 'url', 'source_id', 'tier', 'publisher', 'published_at',
        'fetched_at', 'first_seen', 'event_at', 'party', 'nature',
        'verification', 'classification', 'topics', 'dimensions', 'scope',
        'supplemental', 'report_as_of',
    )
    return {
        **{key: record.get(key) for key in keys if key in record},
        **metadata,
        **translation,
        'text': body,
        'source_name': source_name(record['source_name'], cache),
    }

def present_map_record(record, cache=None):
    """Return only the fields used to draw and inspect map clusters."""
    cache = translation_cache() if cache is None else cache
    metadata = event_metadata(record)
    metadata['points'] = [
        {**point, 'label': chinese_text(point['label'], cache, '相关地区')}
        for point in metadata['points']
    ]
    return {
        'id': record['id'],
        'url': record.get('url', ''),
        **{key: value for key, value in record_translation(record, cache).items() if key != 'text'},
        **metadata,
    }

def present_answer(answer):
    cache=translation_cache()
    return {**answer,'facts':[{**f,'text':f['text'].replace(' USD',' 美元')} for f in answer['facts']],'citations':[present_record(r,cache) for r in answer['citations']]}

_worker=None
_worker_snapshot_id=None

def translation_capabilities():
    from .llm import status as model_status
    configured = bool(model_status()['configured'])
    local_model = (data_dir()/'models'/'opus-mt-en-zh'/'pytorch_model.bin').is_file()
    return {
        'model_configured': configured,
        'local_model_available': local_model,
        'available': configured or local_model,
    }

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
    global _worker, _worker_snapshot_id
    # The workers currently consume the newest snapshot, not the user's older
    # selected context. Keep startup/import scheduling explicit about that.
    available = snapshots()
    if not available:return
    target = available[0]['id']
    status = translation_status(target)
    if status['pending']==0 or not status['available']:return
    folder=data_dir();folder.mkdir(parents=True,exist_ok=True)
    # Coalesce imports that arrive while a worker is active.  The pipeline
    # removes this marker before each pass and repeats when a later import
    # creates it again, so newly collected records are not stranded.
    (folder/'translation.request').write_text(now(),encoding='utf-8')
    if translation_running():return
    with (folder/'translation.log').open('a',encoding='utf-8') as log:
        # Run the complete incremental pipeline after every successful import:
        # the bundled model handles ordinary English/Persian first, then the
        # configured model fills Cyrillic, Arabic and mixed-language gaps.
        # Both stages write through the same persistent translation cache.
        _worker=subprocess.Popen([sys.executable,'-u',str(ROOT/'scripts'/'translate_pipeline.py')],cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        _worker_snapshot_id=target

_translation_status_memory = None

def translation_status(snapshot_id=None):
    global _translation_status_memory
    available=snapshots()
    latest_id = available[0]['id'] if available else None
    selected_id = snapshot_id or get_working_snapshot_id() or latest_id
    capability = translation_capabilities()
    worker_running = translation_running()
    # A later import can make the worker advance to another snapshot. Without
    # per-pass receipts its precise active target is then unknown, not guessed.
    worker_id = _worker_snapshot_id if worker_running and _worker_snapshot_id == latest_id else None
    running = bool(worker_running and worker_id and worker_id == selected_id)
    counts = {'pending': 0, 'ready': 0, 'total': 0}
    if selected_id:
        status_key = (str(data_dir().resolve()), selected_id, _translation_marker())
        if _translation_status_memory and _translation_status_memory[0] == status_key:
            counts = _translation_status_memory[1]
        else:
            records=get_snapshot(selected_id)['records']
            cache=translation_cache()
            ready=sum(record_translation(r,cache)['translation_state']=='ready' for r in records)
            counts = {'pending':len(records)-ready,'ready':ready,'total':len(records)}
            _translation_status_memory = (status_key, counts)
    blocked_reason = None
    if not selected_id:
        blocked_reason, message = 'no_snapshot', '尚无可翻译快照。'
    elif not counts['pending']:
        message = '当前快照的标题与正文已有中文内容。'
    elif not capability['available']:
        blocked_reason, message = 'translation_not_configured', '暂无中文译文：后台翻译模型未配置，缺译部分显示原文；PiChat 嵌入连接不等于后台翻译配置。'
    elif selected_id != latest_id:
        blocked_reason, message = 'worker_scope_latest', '当前为历史快照，后台批量翻译仅处理最新快照；缺译部分显示原文，可按条翻译。'
    elif running:
        message = '正在翻译当前快照，尚未完成的部分显示原文。'
    elif worker_running:
        blocked_reason, message = 'worker_scope_unknown', '后台翻译进程运行中，当前处理快照待确认；未将当前快照标记为翻译中。'
    else:
        blocked_reason, message = 'not_running', '当前快照尚有未译内容，后台翻译未运行；缺译部分显示原文。'
    return {
        **counts, **capability, 'snapshot_id': selected_id, 'running': running,
        'worker_running': worker_running, 'worker_snapshot_id': worker_id,
        'worker_scope': 'latest_snapshot_only', 'latest_snapshot_id': latest_id,
        'blocked_reason': blocked_reason, 'message': message,
    }

def localize_brief_markdown(markdown):
    cache=translation_cache()
    # Only change source-link labels in presentation; keep the stored archive immutable.
    return re.sub(r'\[([^\]\n]+)\]\((https?://[^\s)]+)\)',lambda m:'['+chinese_text(m[1],cache)+']('+m[2]+')',markdown).replace(' USD',' 美元')

def compare_brief_markdown(before, after, before_id, after_id):
    """Compare the same display projection used by the archived-brief panel.

    Stored originals remain immutable; link-label translations can evolve.
    """
    return '\n'.join(difflib.unified_diff(
        localize_brief_markdown(before).splitlines(),
        localize_brief_markdown(after).splitlines(),
        fromfile='前一版本 '+before_id, tofile='当前版本 '+after_id,
    ))
