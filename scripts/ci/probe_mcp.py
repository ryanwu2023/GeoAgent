"""Public TLS/Bearer protocol smoke test; never emit credentials or data bodies."""
import json
import os
import re
import time
import urllib.error
import urllib.request


class ProbeError(RuntimeError):
    pass


def probe(base):
    """No automatic retry: retain the first failure, log metadata only."""
    url = base + "/mcp"
    sequence, session_id, protocol = 0, None, None
    def call(stage, method, params=None, authenticated=True, notification=False):
        nonlocal sequence, session_id
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if authenticated:
            headers["Authorization"] = "Bearer " + os.environ["MCP_LIVE_KEY"]
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        if protocol:
            headers["MCP-Protocol-Version"] = protocol
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            sequence += 1
            payload["id"] = sequence
        started = time.monotonic()
        meta = {"origin": base, "stage": stage, "method": method}
        try:
            try:
                response = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers), timeout=30)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.code
                raw = response.read(512 * 1024 + 1)
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                meta.update(status=status, content_type=content_type, response_bytes=len(raw))
                if len(raw) > 512 * 1024:
                    raise ProbeError("response_limit")
                result = {}
                if raw:
                    try:
                        if content_type == "text/event-stream":
                            events = [json.loads(line[5:].strip()) for line in raw.decode().splitlines() if line.startswith("data:")]
                            result = next((item for item in events if isinstance(item, dict) and item.get("id") == payload.get("id")), {})
                        else:
                            result = json.loads(raw)
                    except (ValueError, UnicodeError):
                        if status == 200:
                            raise ProbeError("invalid_json")
                if not isinstance(result, dict):
                    raise ProbeError("invalid_envelope")
                code = (result.get("error") or {}).get("code") if isinstance(result.get("error"), dict) else None
                if isinstance(code, int) or isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code):
                    meta["error_code"] = code
                if stage == "anonymous":
                    if status != 401:
                        raise ProbeError("anonymous_not_denied")
                elif notification:
                    if status not in (200, 202, 204):
                        raise ProbeError("notification_http_status")
                elif status != 200:
                    raise ProbeError("http_status")
                elif result.get("id") != payload["id"] or "result" not in result or result.get("error"):
                    raise ProbeError("rpc_envelope")
                if status == 200 and authenticated:
                    session_id = response.headers.get("Mcp-Session-Id", session_id)
                meta["protocol_ok"] = True
                return result.get("result", {})
        except Exception as error:
            meta.update(protocol_ok=False, failure=str(error) if isinstance(error, ProbeError) else type(error).__name__)
            raise ProbeError(f"{base} stage={stage} status={meta.get('status', 'unavailable')} failure={meta['failure']}") from None
        finally:
            meta["duration_ms"] = round((time.monotonic() - started) * 1000)
            print(json.dumps(meta), flush=True)
    def require(stage, condition, failure):
        if not condition:
            print(json.dumps({"origin": base, "stage": stage, "semantic_ok": False, "failure": failure}), flush=True)
            raise ProbeError(f"{base} stage={stage} failure={failure}")

    call("anonymous", "tools/list", authenticated=False)
    initialized = call("initialize", "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "release-live", "version": "2"}})
    protocol = initialized.get("protocolVersion") if isinstance(initialized, dict) else None
    require("initialize", isinstance(protocol, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", protocol), "invalid_protocol_version")
    call("initialized", "notifications/initialized", notification=True)
    catalog = call("list", "tools/list")
    require("list", isinstance(catalog, dict) and isinstance(catalog.get("tools"), list) and len(catalog["tools"]) >= 11, "incomplete_catalog")
    result = call("snapshot-call", "tools/call", {"name": "geo_snapshot_list", "arguments": {}})
    require("snapshot-call", isinstance(result, dict), "invalid_tool_envelope")
    try:
        tool = json.loads(next(item["text"] for item in result.get("content", []) if item.get("type") == "text"))
    except (ValueError, KeyError, StopIteration, TypeError, AttributeError):
        require("snapshot-call", False, "invalid_tool_envelope")
    require("snapshot-call", not result.get("isError") and isinstance(tool, dict) and tool.get("ok") is True, "tool_failed")
    print(f"Public MCP passed: {base}; anonymous=401, initialize/list/snapshot-call=200; tools={len(catalog['tools'])}", flush=True)


if __name__ == "__main__":
    for origin in ("https://geoagent.lhotsetech.com", "https://geoagentd40d.sealosgzg.site"):
        probe(origin)
