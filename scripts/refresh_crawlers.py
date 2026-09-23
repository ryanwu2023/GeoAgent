"""One bounded collection round; each project retains its own verification gate."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from backend.paths import collector_source_root

ROOT=collector_source_root()
OUT=Path(__file__).resolve().parents[1]/'data'/'collection-runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('batch',ROOT/'tools/rerun_batch.py')
    batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)
    def run(item):
        project,script=item
        result={'project':project,'started_at':datetime.now(timezone.utc).isoformat()}
        for stage,args,timeout in [('selftest',[script,'--selftest'],120),('collect',[script],900),('verify',[str(Path('tools')/('verify_numbers.py' if project=='defense-spend-monitor' else 'verify_report.py'))]+(['--skip-months'] if project=='defense-spend-monitor' else []),300)]:
            if stage=='verify' and not (ROOT/project/args[0]).exists():
                result[stage]='not_provided';continue
            with (OUT/f'{project}-{stage}.log').open('w',encoding='utf-8') as log:
                try:
                    r=subprocess.run([sys.executable,*args],cwd=ROOT/project,stdout=log,stderr=log,timeout=timeout,env={**os.environ,'PYTHONUTF8':'1'},creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    result[stage]=r.returncode
                except subprocess.TimeoutExpired:result[stage]='timeout'
            if result[stage]!=0:break
        result['finished_at']=datetime.now(timezone.utc).isoformat()
        (OUT/f'{project}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(run,[item for group in batch.GROUPS.values() for item in group]+[('xinhua-monitor','xinhua_monitor.py')]):
            results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
    with (OUT/'market-context-monitor.log').open('w',encoding='utf-8') as log:
        try:
            code=subprocess.run([sys.executable,str(Path(__file__).with_name('supplement_market.py'))],stdout=log,stderr=log,timeout=180).returncode
        except subprocess.TimeoutExpired:code='timeout'
    results.append({'project':'market-context-monitor','collect':code})
    (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT '+str(OUT),flush=True)

if __name__=='__main__':main()
