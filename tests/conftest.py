import pytest


@pytest.fixture(autouse=True)
def _legacy_qa_mode_for_existing_contract_tests(monkeypatch):
    """Old contract tests target the retained compatibility pipeline.

    Agent-specific tests call the new bounded runner directly or explicitly
    override this flag.  Production defaults to tools mode.
    """
    monkeypatch.setenv("LLM_AGENT_MODE", "legacy")
