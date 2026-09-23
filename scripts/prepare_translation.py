"""Download public translation model; no user evidence is transmitted."""
from pathlib import Path
import sys
import hashlib
import concurrent.futures
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from huggingface_hub import snapshot_download
from backend.store import data_dir

if __name__=='__main__':
    folder=data_dir()/'models'/'opus-mt-en-zh'
    snapshot_download('Helsinki-NLP/opus-mt-en-zh',allow_patterns=['README.md','config.json','generation_config.json','source.spm','target.spm','tokenizer_config.json','vocab.json'],local_dir=folder)
    target=folder/'pytorch_model.bin'
    if target.exists():sys.exit(0)
    url='https://huggingface.co/Helsinki-NLP/opus-mt-en-zh/resolve/main/pytorch_model.bin'
    meta=httpx.get('https://huggingface.co/api/models/Helsinki-NLP/opus-mt-en-zh?blobs=true',timeout=30).json()
    info=next(x['lfs'] for x in meta['siblings'] if x['rfilename']=='pytorch_model.bin')
    total=info['size']
    partial=target.with_suffix('.download')
    offset=partial.stat().st_size if partial.exists() else 0
    parts=folder/'download-parts';parts.mkdir(exist_ok=True)
    ranges=[(i,min(i+8*1024*1024,total)-1) for i in range(offset,total,8*1024*1024)]
    def fetch(span):
        start,end=span;file=parts/f'{start}-{end}.part'
        if file.exists() and file.stat().st_size==end-start+1:return file
        for attempt in range(3):
            try:
                with httpx.stream('GET',url,headers={'Range':f'bytes={start}-{end}'},follow_redirects=True,timeout=60) as response:
                    if response.status_code!=206 or response.headers.get('content-range')!=f'bytes {start}-{end}/{total}':raise ValueError('Download range not respected')
                    with file.open('wb') as out:
                        for chunk in response.iter_bytes(256*1024):out.write(chunk)
                if file.stat().st_size!=end-start+1:raise ValueError('Incomplete download range')
                print(f'Model segment ready: {start}-{end}',flush=True)
                return file
            except (httpx.HTTPError,ValueError):
                if attempt==2:raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        files=list(pool.map(fetch,ranges))
    with partial.open('ab') as out:
        for file in files:
            with file.open('rb') as source:
                while chunk:=source.read(1024*1024):out.write(chunk)
    if hashlib.file_digest(partial.open('rb'),'sha256').hexdigest()!=info['sha256']:raise ValueError('Model checksum mismatch')
    partial.replace(target)
    print('Verified model ready',flush=True)
