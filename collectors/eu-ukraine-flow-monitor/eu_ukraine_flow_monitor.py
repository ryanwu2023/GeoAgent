from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixed_channel_common import run

if __name__ == "__main__":
    raise SystemExit(run("eu-flow", __file__))
