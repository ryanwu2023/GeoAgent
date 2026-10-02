from pathlib import Path

from scripts.collector_service_config import render_launch_agent, render_task_scheduler, service_args


def test_service_args_make_import_policy_explicit():
    assert service_args(interval_sec=60, auto_import=True, allow_partial_import=False) == [
        "-m", "scripts.collector_service", "--interval-sec", "60", "--auto-import"
    ]


def test_service_args_can_exclude_collectors():
    assert service_args(
        interval_sec=60,
        auto_import=True,
        allow_partial_import=False,
        exclude_ids=["taco-monitor", "demo"],
    ) == [
        "-m", "scripts.collector_service", "--interval-sec", "60",
        "--exclude-id", "taco-monitor", "--exclude-id", "demo",
        "--auto-import",
    ]


def test_launch_agent_contains_restart_and_log_contract(tmp_path):
    payload = render_launch_agent(
        root=tmp_path,
        python=Path("/opt/geo/.venv/bin/python"),
        interval_sec=300,
        exclude_ids=["taco-monitor"],
    )

    assert "com.geoagent.collector" in payload
    assert "<key>KeepAlive</key><true/>" in payload
    assert "<key>ThrottleInterval</key><integer>30</integer>" in payload
    assert "--auto-import" in payload
    assert "--exclude-id" in payload
    assert "taco-monitor" in payload
    assert "service-logs/collector.stderr.log" in payload


def test_task_scheduler_contains_logon_and_restart_contract(tmp_path):
    payload = render_task_scheduler(root=tmp_path, python=Path(r"C:\GeoAgent\.venv\Scripts\python.exe"), interval_sec=600, allow_partial_import=True)

    assert "<LogonTrigger>" in payload
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in payload
    assert "<RestartOnFailure>" in payload
    assert "--allow-partial-import" in payload
    assert "WorkingDirectory" in payload
