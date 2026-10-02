"""Generate auditable OS service configuration for the collector service.

This module does not call launchctl or schtasks.  It creates the platform
configuration so installation can be reviewed and performed explicitly.
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path


def service_args(
    *,
    interval_sec: int,
    auto_import: bool,
    allow_partial_import: bool,
    exclude_ids: list[str] | None = None,
) -> list[str]:
    args = ["-m", "scripts.collector_service", "--interval-sec", str(interval_sec)]
    for collector_id in exclude_ids or []:
        args.extend(["--exclude-id", collector_id])
    if auto_import:
        args.append("--auto-import")
    if allow_partial_import:
        args.append("--allow-partial-import")
    return args


def render_launch_agent(
    *,
    root: Path,
    python: Path,
    interval_sec: int = 86400,
    auto_import: bool = True,
    allow_partial_import: bool = False,
    exclude_ids: list[str] | None = None,
    label: str = "com.geoagent.collector",
) -> str:
    log_dir = root / "data" / "service-logs"
    arguments = [str(python), *service_args(
        interval_sec=interval_sec,
        auto_import=auto_import,
        allow_partial_import=allow_partial_import,
        exclude_ids=exclude_ids,
    )]
    def text(value: str) -> str:
        return html.escape(value, quote=True)
    argument_xml = "\n".join(f"        <string>{text(value)}</string>" for value in arguments)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{text(label)}</string>
    <key>ProgramArguments</key>
    <array>
{argument_xml}
    </array>
    <key>WorkingDirectory</key><string>{text(str(root))}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>{text(str(log_dir / "collector.stdout.log"))}</string>
    <key>StandardErrorPath</key><string>{text(str(log_dir / "collector.stderr.log"))}</string>
</dict>
</plist>
'''


def render_task_scheduler(
    *,
    root: Path,
    python: Path,
    interval_sec: int = 86400,
    auto_import: bool = True,
    allow_partial_import: bool = False,
    exclude_ids: list[str] | None = None,
) -> str:
    arguments = " ".join(service_args(
        interval_sec=interval_sec,
        auto_import=auto_import,
        allow_partial_import=allow_partial_import,
        exclude_ids=exclude_ids,
    ))
    def text(value: str) -> str:
        return html.escape(value, quote=True)
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>GeoAgent collector service</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RestartOnFailure><Interval>PT5M</Interval><Count>3</Count></RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author"><Exec>
    <Command>{text(str(python))}</Command>
    <Arguments>{text(arguments)}</Arguments>
    <WorkingDirectory>{text(str(root))}</WorkingDirectory>
  </Exec></Actions>
</Task>
'''


def target_platform(value: str) -> str:
    if value != "auto":
        return value
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    return "linux"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 GeoAgent 采集服务的跨平台后台配置")
    parser.add_argument("--platform", choices=("auto", "macos", "windows"), default="auto")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", dest="python_path", type=Path, help="服务使用的 Python 可执行文件")
    parser.add_argument("--interval-sec", type=int, default=86400)
    parser.add_argument("--allow-partial-import", action="store_true")
    parser.add_argument("--exclude-id", action="append", default=[], help="排除指定采集器，可重复")
    parser.add_argument("--output", type=Path, help="写入配置文件；未指定时打印到 stdout")
    args = parser.parse_args(argv)
    if args.interval_sec < 1:
        parser.error("--interval-sec 必须大于 0")
    root = args.root.resolve()
    # Keep the repository-local venv path intact.  On macOS .venv/bin/python
    # is often a symlink; resolving it would make launchd bypass the venv.
    python = (args.python_path or (root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"))).absolute()
    platform_name = target_platform(args.platform)
    if platform_name == "macos":
        payload = render_launch_agent(root=root, python=python, interval_sec=args.interval_sec, allow_partial_import=args.allow_partial_import, exclude_ids=args.exclude_id)
        default_name = "com.geoagent.collector.plist"
    elif platform_name == "windows":
        payload = render_task_scheduler(root=root, python=python, interval_sec=args.interval_sec, allow_partial_import=args.allow_partial_import, exclude_ids=args.exclude_id)
        default_name = "GeoAgent-Collector.xml"
    else:
        parser.error("当前仅生成 macOS LaunchAgent 或 Windows Task Scheduler 配置")
    if args.output:
        destination = args.output if args.output.suffix else args.output / default_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-16" if platform_name == "windows" else "utf-8")
        print(destination)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
