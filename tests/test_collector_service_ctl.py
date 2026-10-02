from pathlib import Path

from scripts.collector_service_ctl import command_for, config_path, main


def test_platform_commands_are_explicit_and_shell_free(tmp_path):
    config = tmp_path / "collector.plist"
    assert command_for("install", "macos", config)[:2] == ["launchctl", "bootstrap"]
    assert command_for("uninstall", "macos", config)[1] == "bootout"
    assert command_for("status", "windows", config)[:4] == ["schtasks", "/Query", "/TN", "GeoAgent Collector"]


def test_install_dry_run_writes_config_but_does_not_call_system_tool(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise AssertionError("dry-run must not call launchctl")

    monkeypatch.setattr("scripts.collector_service_ctl.subprocess.run", fail)
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("stub", encoding="utf-8")
    assert main(["install", "--platform", "macos", "--root", str(tmp_path), "--dry-run"]) == 0
    assert config_path(tmp_path, "macos").is_file()
    assert "launchctl bootstrap" in capsys.readouterr().out


def test_status_uses_system_command_only_when_requested(monkeypatch, capsys):
    calls = []

    class Completed:
        returncode = 7

    monkeypatch.setattr("scripts.collector_service_ctl.subprocess.run", lambda command, check: calls.append((command, check)) or Completed())
    result = main(["status", "--platform", "windows", "--dry-run"])
    assert result == 0
    assert calls == []
    assert "schtasks /Query" in capsys.readouterr().out


def test_uninstall_returns_system_exit_code(monkeypatch):
    class Completed:
        returncode = 3

    monkeypatch.setattr("scripts.collector_service_ctl.subprocess.run", lambda command, check: Completed())
    assert main(["uninstall", "--platform", "windows", "--root", "/tmp/geoagent-test-root"]) == 3
