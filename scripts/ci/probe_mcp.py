"""Public TLS/Bearer protocol smoke test; never emit credentials or data bodies."""
import json
import os
import urllib.error
import urllib.request


def probe(base):
    url = base + "/mcp"
    def call(method, params=None, authenticated=True):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if authenticated:
            headers["Authorization"] = "Bearer " + os.environ["MCP_LIVE_KEY"]
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=payload, headers=headers), timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, {}
    assert call("tools/list", authenticated=False)[0] == 401
    status, result = call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "release-live", "version": "1"}})
    assert status == 200 and "result" in result
    status, result = call("tools/list")
    assert status == 200 and len(result["result"]["tools"]) >= 11
    status, result = call("tools/call", {"name": "geo_snapshot_list", "arguments": {}})
    assert status == 200 and json.loads(result["result"]["content"][0]["text"])["ok"]
    print(f"Public MCP passed: {base}; anonymous=401, initialize/list/snapshot-call=200")


if __name__ == "__main__":
    for origin in ("https://geoagent.lhotsetech.com", "https://geoagentd40d.sealosgzg.site"):
        probe(origin)
