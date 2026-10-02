import plistlib

import pytest

from backend import collector_config


def test_save_and_load_project_owned_config(tmp_path, monkeypatch):
    config_path = tmp_path / "service-config" / "collector-settings.json"
    monkeypatch.setattr(collector_config, "CONFIG_PATH", config_path)

    saved = collector_config.save_config(
        {
            "interval_sec": 3600,
            "exclude_ids": ["taco-monitor", "taco-monitor"],
            "allow_partial_import": True,
        },
        known_ids={"taco-monitor", "market-context-monitor"},
    )

    assert saved["interval_sec"] == 3600
    assert saved["exclude_ids"] == ["taco-monitor"]
    assert saved["allow_partial_import"] is True
    assert saved["source"] == "project-settings"
    assert config_path.is_file()


def test_save_rejects_unknown_collector_and_invalid_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_config, "CONFIG_PATH", tmp_path / "settings.json")

    with pytest.raises(ValueError, match="未知采集器"):
        collector_config.save_config({"exclude_ids": ["missing"]}, known_ids={"known"})
    with pytest.raises(ValueError, match="interval_sec"):
        collector_config.save_config({"interval_sec": 30}, known_ids=set())


def test_load_falls_back_to_active_launch_agent(tmp_path, monkeypatch):
    service_dir = tmp_path / "data" / "service-config"
    service_dir.mkdir(parents=True)
    plist_path = service_dir / "com.geoagent.collector.plist"
    plist_path.write_bytes(
        plistlib.dumps(
            {
                "ProgramArguments": [
                    "/project/.venv/bin/python",
                    "-m",
                    "scripts.collector_service",
                    "--interval-sec",
                    "7200",
                    "--exclude-id",
                    "taco-monitor",
                    "--auto-import",
                ]
            }
        )
    )
    monkeypatch.setattr(collector_config, "CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(collector_config, "project_root", lambda: tmp_path)

    loaded = collector_config.load_config()

    assert loaded["source"] == "launch-agent"
    assert loaded["interval_sec"] == 7200
    assert loaded["exclude_ids"] == ["taco-monitor"]
    assert loaded["auto_import"] is True


def test_apply_config_reports_install_failure(tmp_path, monkeypatch):
    calls = []

    class Completed:
        def __init__(self, returncode):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = "安装失败"

    monkeypatch.setattr(collector_config, "platform_name", lambda: "macos")
    monkeypatch.setattr(collector_config, "project_root", lambda: tmp_path)
    monkeypatch.setattr(
        collector_config.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or Completed(1 if command[3] == "install" else 0),
    )

    result = collector_config.apply_config(
        {"interval_sec": 3600, "exclude_ids": ["taco-monitor"], "allow_partial_import": False}
    )

    assert result["applied"] is False
    assert "安装失败" in result["message"]
    assert calls[0][3] == "uninstall"
    assert calls[0][-1] == "macos"
    assert calls[1][3] == "install"
