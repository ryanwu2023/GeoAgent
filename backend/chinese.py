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
    # QA citations are deliberately compact and may omit map/source display
    # fields that exist on full snapshot records.  Supply presentation-only
    # defaults without changing the persisted evidence object.
    record = {
        **record,
        'title': str(record.get('title') or ''),
        'text': str(record.get('text') or record.get('summary') or ''),
        'source_name': str(record.get('source_name') or record.get('source_id') or record.get('source') or '未知来源'),
    }
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

def _present_answer_citation_summary(record, cache):
    """Project one citation for the interactive answer UI.

    The complete immutable record remains available from the snapshot-bound
    evidence endpoint and in the persisted answer used by HTML exports.  A
    streamed answer only needs enough metadata to render its source list.
    """
    translation = record_translation(record, cache)
    return {
        "id": record.get("id", ""),
        "title": translation.get("title") or record.get("title") or "未提供标题",
        "source_name": source_name(record.get("source_name", ""), cache),
        "publisher": record.get("publisher", ""),
        "published_at": record.get("published_at"),
        "fetched_at": record.get("fetched_at"),
        "tier": record.get("tier", ""),
        "verification": record.get("verification", ""),
    }


def _present_internal_source_summary(record):
    """Keep the local-message card useful without returning its full body."""
    keys = (
        "id", "title", "ai_title",
        "text_class", "primary_tag", "tags", "group_name", "message_at",
        "receive_time", "source", "verification",
    )
    result = {key: record.get(key) for key in keys if record.get(key) not in (None, "", [])}
    text = str(record.get("content_abstract") or record.get("ai_short") or record.get("ai_content") or "").strip()
    if text:
        result["content_abstract"] = text[:600].rstrip() + ("…" if len(text) > 600 else "")
    return result


def _shorten(value, limit):
    text = str(value or "").strip()
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def _repair_reference_only_summary(answer):
    """Keep old persisted placeholder answers readable after the validator fix."""
    summary = answer.get("summary")
    text = str(summary.get("text") or "").strip() if isinstance(summary, dict) else ""
    if not re.fullmatch(r"[（(]?\s*(?:见上|同上|如上|见前文|答案见上)[。.!！]?\s*[）)]?", text):
        return answer
    topic = str(answer.get("topic") or "")
    subject = "美伊局势" if topic == "usiran" else "俄乌局势" if topic == "ukraine" else "当前地缘局势"
    citations = [item for item in answer.get("citations", []) if isinstance(item, dict)]
    facts = []
    for item in citations[:6]:
        title = str(item.get("title_zh") or item.get("title") or "").strip()
        source = str(item.get("source_name") or item.get("source") or "公开来源").strip()
        published = str(item.get("published_at") or "时间未标注")[:16]
        if title:
            facts.append(f"- {title}（{source}，{published}）")
    gaps = [str(item).strip() for item in answer.get("gaps", []) if str(item).strip()][:5]
    repaired = [
        "## 结论摘要",
        f"本次模型最终轮只返回了过程引用，未形成可展示正文。系统已保留{subject}本轮实际检索到的证据，并按可追溯内容生成以下回答；这些材料可说明近期报道重点，仍不能仅凭标题或单一来源判定局势已经升级、持续或缓和。",
        "\n## 当前可确认内容",
        *(facts or ["- 本轮没有取得足以形成明确结论的公开证据。"]),
        "\n## 判断与不确定性",
        "当事方声明只能确认其作出过相关表态，媒体转述仍需回到原始来源核验；市场同步变化不能单独证明由冲突导致。",
        "\n## 数据缺口",
        *(f"- {item}" for item in gaps),
        "- 原模型最终综合正文缺失，现有内容为系统依据已保存证据生成的降级回答。",
        "\n## 后续观察",
        "继续核对最新官方声明、实际行动、反向证据与关键量化指标；取得新增材料后重新运行问答可获得完整模型综合。",
    ]
    repaired_answer = dict(answer)
    repaired_answer["summary"] = {
        **(summary or {}),
        "text": "\n".join(repaired),
        "evidence_ids": [str(item.get("id")) for item in citations[:6] if item.get("id")],
    }
    repaired_answer["message"] = "历史回答的最终正文曾缺失；已根据已保存证据恢复为可阅读的降级回答。"
    return repaired_answer


def _present_analysis_trace(trace):
    """Project the visible audit trail without duplicating the final answer.

    Full trace events remain in the persisted answer and harness checkpoints.
    The interactive response already carries the final Markdown under
    ``summary.text``, so repeating it in the synthesis trace can double the
    payload while adding no information to the process view.
    """
    result = []
    for item in trace or []:
        projected = {
            "round": item.get("round", len(result) + 1),
            "title": item.get("title") or "分析步骤",
            "status": item.get("status") or "completed",
        }
        for key in ("stage", "kind", "phase", "call_id", "tool"):
            if item.get(key):
                projected[key] = item[key]
        for key in ("duration_ms", "result_count", "context_tokens"):
            if item.get(key) is not None:
                projected[key] = item[key]
        for key in ("retry_count", "compacted", "recovered"):
            if item.get(key):
                projected[key] = item[key]
        if item.get("stage") == "synthesis":
            projected["summary"] = "已完成最终复核与结论，完整内容见下方总结回答。"
        else:
            projected["summary"] = _shorten(item.get("summary"), 900)
        details = [
            _shorten(value, 420) for value in (item.get("details") or [])[:12]
        ]
        searches = [
            _shorten(value, 180) for value in (item.get("search_queries") or [])[:12]
        ]
        evidence_ids = list((item.get("evidence_ids") or [])[:6])
        if details:
            projected["details"] = details
        if searches:
            projected["search_queries"] = searches
        if evidence_ids:
            projected["evidence_ids"] = evidence_ids
        result.append(projected)
    return result


def present_answer(answer, compact=False):
    """Localize an answer, optionally projecting it for interactive delivery.

    ``compact=False`` preserves the historical API/report shape.  Compact
    delivery is opt-in so non-UI clients that consume full citation records do
    not change behavior.
    """
    answer = _repair_reference_only_summary(answer)
    cache = translation_cache()
    result = {
        **answer,
        "facts": [
            {**fact, "text": fact["text"].replace(" USD", " 美元")}
            for fact in answer.get("facts", [])
        ],
    }
    if not compact:
        result["citations"] = [present_record(record, cache) for record in answer.get("citations", [])]
        return result

    result["citations"] = [
        _present_answer_citation_summary(record, cache)
        for record in answer.get("citations", [])
    ]
    result["internal_sources"] = [
        _present_internal_source_summary(record)
        for record in answer.get("internal_sources", [])
    ]
    result["analysis_trace"] = _present_analysis_trace(answer.get("analysis_trace", []))
    # These objects are useful while producing/debugging an answer, but the UI
    # renders the auditable analysis trace and citation map instead.  Keeping
    # them in the persisted answer preserves resume and report behavior.
    result.pop("question_plan", None)
    result.pop("tool_calls", None)
    result["response_projection"] = "compact-v1"
    return result

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
