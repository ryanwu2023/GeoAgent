"""Start the independent GeoAgent MCP server."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 GeoAgent 独立 MCP Server")
    parser.add_argument("--host", default=os.environ.get("GEOAGENT_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GEOAGENT_MCP_PORT", "8401")))
    args = parser.parse_args(argv)
    os.environ["GEOAGENT_MCP_HOST"] = args.host
    os.environ["GEOAGENT_MCP_PORT"] = str(args.port)
    import uvicorn

    uvicorn.run("backend.geo_mcp.server:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
