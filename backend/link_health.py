import json
from functools import lru_cache
from .store import data_dir

@lru_cache(maxsize=8)
def _load(path,mtime):
    result={}
    with open(path,encoding='utf-8') as f:
        for line in f:
            try:
                row=json.loads(line);result[row['url']]=row
            except (ValueError,KeyError):pass
    return result

def link_health(sid,url):
    path=data_dir()/'link-audits'/(sid+'.jsonl')
    if not path.exists():return {'status':'尚未检查','url':url}
    return _load(str(path),path.stat().st_mtime_ns).get(url,{'status':'尚未检查','url':url})

def summary(sid):
    path=data_dir()/'link-audits'/(sid+'-summary.json')
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    path=data_dir()/'link-audits'/(sid+'.jsonl')
    rows=_load(str(path),path.stat().st_mtime_ns) if path.exists() else {}
    return {'snapshot_id':sid,'checked':len(rows),'status':'检查进行中' if rows else '尚未检查'}
