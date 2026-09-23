"""Prepare the Persian-to-English bridge from the official Argos package index."""
from pathlib import Path
import sys
import zipfile
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.store import data_dir

def run():
    folder=data_dir()/'models'
    destination=folder/'persian-english'
    if (destination/'translate-fa_en-1_5'/'model'/'model.bin').exists():return
    index=httpx.get('https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json',timeout=30)
    index.raise_for_status()
    package=next(p for p in index.json() if p['from_code']=='fa' and p['to_code']=='en' and p['package_version']=='1.5')
    url=next(u for u in package['links'] if u.startswith('https://'))
    folder.mkdir(parents=True,exist_ok=True)
    archive=folder/'translate-fa_en-1_5.argosmodel'
    with httpx.stream('GET',url,follow_redirects=True,timeout=60) as response:
        response.raise_for_status()
        with archive.open('wb') as out:
            for chunk in response.iter_bytes(1024*1024):out.write(chunk)
    with zipfile.ZipFile(archive) as package_file:
        for item in package_file.namelist():
            if not (destination/item).resolve().is_relative_to(destination.resolve()):raise ValueError('Invalid archive path')
        package_file.extractall(destination)

if __name__=='__main__':run()
