import socket
import sys

import pytest

from scripts.start_local import _env_int, command_plan, port_available


def test_command_plan_keeps_api_and_web_ports_explicit():
    backend, frontend = command_plan(
        api_host="127.0.0.1",
        api_port=8011,
        web_host="127.0.0.1",
        web_port=8010,
        npm="npm",
    )

    assert backend == [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8011",
    ]
    assert frontend[-4:] == ["--host", "127.0.0.1", "--port", "8010"]


def test_env_int_uses_default_and_rejects_invalid(monkeypatch):
    monkeypatch.delenv("GEOAGENT_TEST_PORT", raising=False)
    assert _env_int("GEOAGENT_TEST_PORT", 8011) == 8011

    monkeypatch.setenv("GEOAGENT_TEST_PORT", "8123")
    assert _env_int("GEOAGENT_TEST_PORT", 8011) == 8123

    monkeypatch.setenv("GEOAGENT_TEST_PORT", "not-a-port")
    with pytest.raises(ValueError, match="must be an integer"):
        _env_int("GEOAGENT_TEST_PORT", 8011)


def test_port_available_detects_a_listener():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert port_available("127.0.0.1", port) is False
    finally:
        listener.close()

    assert port_available("127.0.0.1", port) is True
