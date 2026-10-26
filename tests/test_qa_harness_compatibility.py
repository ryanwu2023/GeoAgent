from backend.qa_agent import TOOL_GUIDE, TOOL_NAMES


EXPECTED_TOOLS = {
    "geo_workspace_summary", "geo_evidence_search", "geo_evidence_get",
    "geo_metric_catalog", "geo_metric_series", "geo_analysis_view",
    "geo_internal_message_stats", "geo_internal_message_search", "geo_internal_message_get",
}


def test_local_harness_keeps_the_nine_tool_protocol_names():
    assert TOOL_NAMES == EXPECTED_TOOLS
    assert {item["name"] for item in TOOL_GUIDE} == EXPECTED_TOOLS


def test_tool_guide_keeps_expected_required_arguments():
    guide = {item["name"]: item["arguments"] for item in TOOL_GUIDE}
    assert "q" in guide["geo_evidence_search"]
    assert "record_id" in guide["geo_evidence_get"]
    assert "series" in guide["geo_metric_series"]
    assert "message_id" in guide["geo_internal_message_get"]
