"""Run a project-level GeoAgent -> PiChat API probe without exposing the API key.

The key is read only from ``PICHAT_API_KEY`` (or ``--key-env``) and is never
printed.  This is intentionally separate from the browser embed identity
check: an embed key may mint a panel token without having ``chat:invoke``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


def _walk_tool_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"tool", "name", "capabilityId"} and isinstance(item, str) and item.startswith("geo_"):
                names.add(item)
            names.update(_walk_tool_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_walk_tool_names(item))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("PICHAT_BASE_URL", "http://127.0.0.1:9172"))
    parser.add_argument("--key-env", default="PICHAT_API_KEY")
    parser.add_argument(
        "--message",
        default="本周哪些指标发生变化？请基于当前 GeoAgent 数据回答，并说明快照口径、证据和缺口。",
    )
    args = parser.parse_args()
    key = os.getenv(args.key_env, "").strip()
    if not key:
        print(json.dumps({"status": "blocked", "error": f"missing {args.key_env}"}, ensure_ascii=False))
        return 2

    endpoint = args.url.rstrip("/") + "/api/v1/chat"
    try:
        response = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {key}"},
            json={"message": args.message, "stream": False},
            timeout=180,
        )
    except httpx.HTTPError as exc:
        print(json.dumps({"status": "failed", "url": endpoint, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    if response.status_code >= 400:
        error = body.get("error") if isinstance(body, dict) else None
        print(json.dumps({"status": "failed", "http_status": response.status_code, "error": error or "PiChat API error"}, ensure_ascii=False))
        return 1

    text = body.get("text") if isinstance(body, dict) else None
    tool_names = sorted(_walk_tool_names(body))
    result = {
        "status": "response_received" if isinstance(text, str) and text.strip() else "failed",
        "business_gate": "unverified",
        "url": endpoint,
        "session_id_present": bool(body.get("sessionId")) if isinstance(body, dict) else False,
        "agent_id": body.get("agentId") if isinstance(body, dict) else None,
        "geo_tools_observed": tool_names,
        "answer_preview": text[:500] if isinstance(text, str) else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "response_received" else 1


if __name__ == "__main__":
    raise SystemExit(main())
