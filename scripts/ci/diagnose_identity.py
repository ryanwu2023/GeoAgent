"""Run inside the production pod; emit transport evidence, never identity payloads."""
import asyncio
import json
import socket
import time
from urllib.parse import urlsplit

import httpx

from backend.integrations import settings as integration_settings, pichat_tls_context


async def main():
    origin = integration_settings()["origin"].rstrip("/")
    host = urlsplit(origin).hostname
    print(json.dumps({"stage": "dns", "host": host, "addresses": sorted({
        item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    })}), flush=True)
    for name, url in (("upstream_unauthenticated", origin + "/api/v1/embed/identity"),
                      ("host_identity", "http://127.0.0.1:8000/api/pichat/embed-identity")):
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=15, verify=pichat_tls_context()) as client:
                response = await client.get(url)
            result = {"stage": name, "status": response.status_code}
            if name == "host_identity":
                try:
                    body = response.json()
                    result["identity_present"] = bool(body.get("token"))
                except ValueError:
                    result["json"] = False
        except Exception as exc:
            result = {"stage": name, "error_type": type(exc).__name__}
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
