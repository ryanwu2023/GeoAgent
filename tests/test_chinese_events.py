from copy import deepcopy
from backend.adapters import normalize
from backend.chinese import present_record,save_translation,translation_cache,localize_brief_markdown
from backend.events import event_metadata
from scripts.translate_snapshot import locally_supported
from backend.translation_quality import quality

def test_chinese_presentation_preserves_originals(tmp_path,monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path))
    record=normalize({'title':'Talks in Tehran','summary':'Talks will continue.','link':'https://example.org/a'},'readiness-monitor')
    before=deepcopy(record)
    save_translation(record['title'],'德黑兰举行会谈')
    save_translation(record['text'],'会谈将继续进行。')
    result=present_record(record)
    assert result['title']=='德黑兰举行会谈'
    assert result['text']=='会谈将继续进行。'
    assert result['url']==record['url']
    assert record==before

def test_untranslated_is_not_fake_chinese(tmp_path,monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path))
    record=normalize({'title':'New report','summary':'Details not translated'},'new')
    result=present_record(record)
    assert result['title']=='New report'
    assert result['translation_state']=='untranslated'
    assert result['original_title']=='New report'

def test_non_naval_news_gets_regional_anchor():
    record=normalize({'title':'Fire in Zaporizhzhia after Russian strike','summary':'Ukraine report'},'new')
    result=event_metadata(record)
    assert result['event_category']=='military'
    assert result['situation_signal']=='escalation'
    assert result['points'][0]['label']=='扎波罗热'
    assert result['points'][0]['precision']=='area'
    assert record['points']==[]

def test_ceasefire_collapse_not_easing_and_unknown_not_sustain():
    collapse=normalize({'title':'Iran ceasefire collapse','summary':''},'new')
    assert event_metadata(collapse)['situation_signal']=='escalation'
    neutral=normalize({'title':'Iran economic report','summary':''},'new')
    assert event_metadata(neutral)['situation_signal']=='unknown'
    easing=normalize({'title':'Iran seeks de-escalation','summary':''},'new')
    assert event_metadata(easing)['situation_signal']=='easing'
    quote=normalize({'title':'Officials said drone strike hit Ukraine','summary':''},'new')
    assert event_metadata(quote)['event_category']=='military'

def test_chinese_brief_label_preserves_original_link(tmp_path,monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path))
    save_translation('Original title','中文标题')
    assert localize_brief_markdown('[Original title](https://example.org/a)')=='[中文标题](https://example.org/a)'

def test_short_latin_headlines_are_sent_to_local_translation():
    assert locally_supported('Live show')
    assert locally_supported("Russia's Ruling Party Wins Duma Election - i24NEWS")
    assert not locally_supported('Дмитриев назвал отказ Германии ошибкой')

def test_batch_model_translation_is_reused_for_cyrillic(tmp_path,monkeypatch):
    monkeypatch.setenv('GEO_DATA_DIR',str(tmp_path))
    record=normalize({'title':'Дмитриев назвал отказ Германии ошибкой','summary':'Дмитриев выступил с заявлением.','link':'https://example.org/ru'},'new')
    save_translation(record['title'],'德米特里耶夫称德国的拒绝是一个错误','模型批量翻译，待语义核验')
    save_translation(record['text'],'德米特里耶夫发表了声明。','模型翻译（批量），待语义核验')
    result=present_record(record)
    assert result['title']=='德米特里耶夫称德国的拒绝是一个错误'
    assert result['text']=='德米特里耶夫发表了声明。'

def test_month_words_do_not_trigger_false_numeric_translation_failure():
    assert quality('Главные новости к утру 15 октября','10月15日早间主要新闻')['issues']==[]
    assert quality('depuis février 2022','自2022年2月以来')['issues']==[]
    assert '数字表达发生变化，需核对原文' in quality('15 октября','10月16日')['issues']


def test_post_import_translation_starts_complete_pipeline(tmp_path, monkeypatch):
    from backend import chinese

    calls = []

    class Worker:
        def poll(self):
            return None

    monkeypatch.setattr(chinese, '_worker', None)
    monkeypatch.setattr(chinese, '_worker_snapshot_id', None)
    monkeypatch.setattr(chinese, 'translation_running', lambda: False)
    monkeypatch.setattr(chinese, 'snapshots', lambda: [{'id': 'latest'}])
    monkeypatch.setattr(chinese, 'translation_status', lambda _sid: {'pending': 2, 'available': True})
    monkeypatch.setattr(chinese, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(
        chinese.subprocess,
        'Popen',
        lambda command, **kwargs: calls.append((command, kwargs)) or Worker(),
    )

    chinese.start_translation()

    assert calls
    assert calls[0][0][-1].endswith('translate_pipeline.py')
    assert (tmp_path / 'translation.request').is_file()
    assert chinese._worker_snapshot_id == 'latest'


def test_import_during_translation_queues_another_pass(tmp_path, monkeypatch):
    from backend import chinese

    monkeypatch.setattr(chinese, 'translation_running', lambda: True)
    monkeypatch.setattr(chinese, 'snapshots', lambda: [{'id': 'latest'}])
    monkeypatch.setattr(chinese, 'translation_status', lambda _sid: {'pending': 2, 'available': True})
    monkeypatch.setattr(chinese, 'data_dir', lambda: tmp_path)

    chinese.start_translation()

    assert (tmp_path / 'translation.request').is_file()


def test_translation_pipeline_retries_while_items_remain(tmp_path, monkeypatch):
    from scripts import translate_pipeline

    # Three passes: progress, then two no-progress passes before stopping.
    counts = iter([6, 3, 3, 3, 3, 3])
    retry_modes = []
    monkeypatch.setattr(translate_pipeline, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(translate_pipeline, 'pending_text_count', lambda: next(counts))
    monkeypatch.setattr(
        translate_pipeline,
        'run_pass',
        lambda *, retry=False: retry_modes.append(retry) or 0,
    )
    monkeypatch.setattr(translate_pipeline.time, 'sleep', lambda _seconds: None)
    monkeypatch.setenv('TRANSLATION_MAX_PASSES', '5')

    assert translate_pipeline.main() == 0
    assert retry_modes == [False, True, True]
