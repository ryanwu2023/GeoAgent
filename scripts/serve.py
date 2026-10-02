"""Project-specific server entry point for identity-checked Windows start/stop."""
import os
import sys
import argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
if __name__=='__main__':
    import uvicorn
    parser = argparse.ArgumentParser(description='GeoAgent 单进程本地服务')
    parser.add_argument('--host', default=os.environ.get('GEOAGENT_API_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('GEOAGENT_API_PORT', '8000')))
    args = parser.parse_args()
    uvicorn.run('backend.app:app', host=args.host, port=args.port)
