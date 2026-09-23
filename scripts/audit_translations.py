"""Export mechanical translation checks; does not claim semantic verification."""
import csv,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.store import snapshots,get_snapshot,data_dir
from backend.chinese import translation_cache,present_record

sid=sys.argv[1] if len(sys.argv)>1 else snapshots()[0]['id']
cache=translation_cache();rows=[]
for r in get_snapshot(sid)['records']:
    shown=present_record(r,cache);q=shown['translation_quality']
    rows.append({'record_id':r['id'],'language':q['language'],'status':q['status'],'issues':'；'.join(q['issues']),'title':shown['title'],'original_title':r['title']})
folder=data_dir()/'translation-audits';folder.mkdir(exist_ok=True)
with (folder/(sid+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
summary={'snapshot_id':sid,'records':len(rows),'statuses':dict(Counter(r['status'] for r in rows)),'languages':dict(Counter(r['language'] for r in rows)),'boundary':'自动检查，不代表逐条语义核验'}
(folder/(sid+'.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
