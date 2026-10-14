import io
import json
import urllib.error

import pytest

from scripts.ci import probe_mcp as module


class Response(io.BytesIO):
    def __init__(self, body, status=200, content_type="application/json", session=None):
        super().__init__(json.dumps(body).encode() if isinstance(body, dict) else body)
        self.code = status
        self.headers = {"Content-Type": content_type}
        if session:
            self.headers["Mcp-Session-Id"] = session


def setup(monkeypatch, responses):
    calls = []
    monkeypatch.setenv("MCP_LIVE_KEY", "test-key-never-log")
    def open_request(request, timeout):
        calls.append((json.loads(request.data), dict(request.header_items())))
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(module.urllib.request, "urlopen", open_request)
    return calls


def successful_responses():
    return [Response({"error": {"code": "GEO_AUTH_REQUIRED"}}, 401),
            Response({"jsonrpc": "2.0", "id": 2, "result": {"protocolVersion": "2025-03-26"}}, session="test-session"),
            Response(b"", 202),
            Response({"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "tool"}] * 17}}),
            Response({"jsonrpc": "2.0", "id": 4, "result": {"content": [{"type": "text", "text": '{"ok":true}'}]}})]


def test_protocol_lifecycle_and_redacted_logs(monkeypatch, capsys):
    calls = setup(monkeypatch, successful_responses())
    module.probe("https://example.org")
    assert calls[2][0]["method"] == "notifications/initialized"
    assert "id" not in calls[2][0]
    assert [p["id"] for p, _ in calls if "id" in p] == [1, 2, 3, 4]
    assert calls[3][1]["Mcp-session-id"] == "test-session"
    assert calls[3][1]["Mcp-protocol-version"] == "2025-03-26"
    assert "Authorization" not in calls[0][1]
    logs = capsys.readouterr().out
    assert "test-key-never-log" not in logs and "test-session" not in logs
    assert "Public MCP passed" in logs


def test_http_failure_reports_stage_status_without_retry_or_body(monkeypatch, capsys):
    error = urllib.error.HTTPError("https://example.org/mcp", 503, "private upstream detail", {"Content-Type": "text/plain"}, io.BytesIO(b"private-response-body"))
    calls = setup(monkeypatch, [Response({}, 401), error])
    with pytest.raises(module.ProbeError, match="stage=initialize status=503"):
        module.probe("https://example.org")
    logs = capsys.readouterr().out
    assert '"status": 503' in logs and '"stage": "initialize"' in logs
    assert "private" not in logs and "test-key" not in logs
    assert len(calls) == 2


def test_rpc_error_code_is_visible_not_error_message(monkeypatch, capsys):
    setup(monkeypatch, [Response({}, 401), Response({"id": 2, "error": {"code": -32001, "message": "private-secret"}})])
    with pytest.raises(module.ProbeError, match="rpc_envelope"):
        module.probe("https://example.org")
    logs = capsys.readouterr().out
    assert '"error_code": -32001' in logs and "private-secret" not in logs


def test_sse_envelope_matches_request_id(monkeypatch):
    responses = successful_responses()
    responses[1] = Response(b'data: {"method":"notification"}\n\ndata: {"id":2,"result":{"protocolVersion":"2025-03-26"}}\n\n', content_type="text/event-stream")
    setup(monkeypatch, responses)
    module.probe("https://example.org")


@pytest.mark.parametrize("body, failure", [(b"<html>private</html>", "invalid_json"), (b"x" * (512 * 1024 + 1), "response_limit")])
def test_invalid_or_large_response_fails_closed(monkeypatch, capsys, body, failure):
    setup(monkeypatch, [Response({}, 401), Response(body)])
    with pytest.raises(module.ProbeError, match=failure):
        module.probe("https://example.org")
    assert "private" not in capsys.readouterr().out


def test_business_failure_not_reported_as_probe_pass(monkeypatch, capsys):
    responses = successful_responses()
    responses[-1] = Response({"id": 4, "result": {"content": [{"type": "text", "text": '{"ok":false}'}]}})
    setup(monkeypatch, responses)
    with pytest.raises(module.ProbeError, match="stage=snapshot-call failure=tool_failed"):
        module.probe("https://example.org")
    logs = capsys.readouterr().out
    assert '"semantic_ok": false' in logs
    assert "Public MCP passed" not in logs
