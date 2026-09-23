"""Project-specific server entry point for identity-checked Windows start/stop."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
if __name__=='__main__':
    import uvicorn
    uvicorn.run('backend.app:app',host='127.0.0.1',port=8000)
