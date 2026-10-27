from fastapi.testclient import TestClient

from backend.app import app
from backend.qa_harness_state import HarnessBudget, HarnessScope, ToolCallState
from backend.qa_harness_store import HarnessStore


def test_tool_call_detail_endpoint_enforces_run_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    store = HarnessStore()
    run = store.create_run(
        run_id="api-run", answer_id="a", scope=HarnessScope("s1"), question="问题",
        plan={}, budget=HarnessBudget(), model="test",
    )
    artifact_id = store.save_artifact(
        run.run_id, "api-call", {"records": [{"id": "r1", "title": "证据"}]},
        {"records": [{"id": "r1"}]}, 12,
    )
    run.tool_calls["api-call"] = ToolCallState(
        "api-call", "geo_evidence_search", {"q": "Iran", "password": "unsafe"},
        "completed", 1, artifact_id, 1, 9, None, phase="discovering",
        arguments_summary={"q": "Iran", "password": "[已脱敏]"},
        result_summary="返回 1 条结果", evidence_ids=["r1"],
    )
    store.save_checkpoint(run)

    client = TestClient(app)
    response = client.get("/api/qa-runs/api-run/tool-calls/api-call")
    assert response.status_code == 200
    body = response.json()
    assert body["mcp_server"] == "geoagent"
    assert body["artifact"]["payload"]["records"][0]["id"] == "r1"
    assert "unsafe" not in response.text
    assert client.get("/api/qa-runs/api-run/tool-calls/missing").status_code == 404
    assert client.get("/api/qa-runs/missing/tool-calls/api-call").status_code == 404
