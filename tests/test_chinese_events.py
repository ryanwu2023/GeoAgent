from copy import deepcopy
from backend.adapters import normalize
from backend.chinese import present_record,save_translation,translation_cache,localize_brief_markdown
from backend.events import event_metadata

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
    assert '准备中' in result['title']
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
