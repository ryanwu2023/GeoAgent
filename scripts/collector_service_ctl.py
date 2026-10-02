"""Explicit install/status/uninstall wrapper for the collector service.

Configuration generation is pure and reviewable.  OS mutations only happen
when the caller explicitly selects ``install`` or ``uninstall``; no action is
performed merely by importing this module or generating a config file.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from scripts.collector_service_config import render_launch_agent, render_task_scheduler, target_platform


MAC_LABEL = "com.geoagent.collector"
WINDOWS_TASK = "GeoAgent Collector"


def config_path(root: Path, platform_name: str) -> Path:
    filename = "com.geoagent.collector.plist" if platform_name == "macos" else "GeoAgent-Collector.xml"
    return root / "data" / "service-config" / filename


def default_python(root: Path, platform_name: str) -> Path:
    relative = ".venv/Scripts/python.exe" if platform_name == "windows" else ".venv/bin/python"
    return (root / relative).absolute()


def require_project_python(root: Path, platform_name: str, configured: Path | None = None) -> Path:
    python = (configured or default_python(root, platform_name)).absolute()
    if not python.is_file():
        raise FileNotFoundError(
            f"项目虚拟环境不存在: {python}；先执行项目私有环境初始化命令"
        )
    return python


def command_for(action: str, platform_name: str, path: Path) -> list[str]:
    if platform_name == "macos":
        domain = f"gui/{os.getuid()}"
        if action == "install":
            return ["launchctl", "bootstrap", domain, str(path)]
        if action == "uninstall":
            return ["launchctl", "bootout", f"{domain}/{MAC_LABEL}"]
        return ["launchctl", "print", f"{domain}/{MAC_LABEL}"]
    if platform_name == "windows":
        if action == "install":
            return ["schtasks", "/Create", "/TN", WINDOWS_TASK, "/XML", str(path), "/F"]
        if action == "uninstall":
            return ["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"]
        return ["schtasks", "/Query", "/TN", WINDOWS_TASK, "/FO", "LIST"]
    raise ValueError(f"unsupported platform: {platform_name}")


def write_config(
    *,
    root: Path,
    platform_name: str,
    python: Path,
    interval_sec: int,
    allow_partial_import: bool,
    exclude_ids: list[str] | None = None,
) -> Path:
    path = config_path(root, platform_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    (root / "data" / "service-logs").mkdir(parents=True, exist_ok=True)
    if platform_name == "macos":
        payload = render_launch_agent(
            root=root,
            python=python,
            interval_sec=interval_sec,
            allow_partial_import=allow_partial_import,
            exclude_ids=exclude_ids,
            label=MAC_LABEL,
        )
    else:
        payload = render_task_scheduler(
            root=root,
            python=python,
            interval_sec=interval_sec,
            allow_partial_import=allow_partial_import,
            exclude_ids=exclude_ids,
        )
    path.write_text(payload, encoding="utf-16" if platform_name == "windows" else "utf-8")
    return path


def run_command(command: list[str], *, dry_run: bool = False) -> int:
    print("$", " ".join(command))
    if dry_run:
        return 0
    completed = subprocess.run(command, check=False)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="显式管理 GeoAgent 采集服务后台任务")
    parser.add_argument("action", choices=("install", "status", "uninstall"))
    parser.add_argument("--platform", choices=("auto", "macos", "windows"), default="auto")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", dest="python_path", type=Path)
    parser.add_argument("--interval-sec", type=int, default=86400)
    parser.add_argument("--allow-partial-import", action="store_true")
    parser.add_argument("--exclude-id", action="append", default=[], help="排除指定采集器，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不调用系统工具")
    args = parser.parse_args(argv)
    if args.interval_sec < 1:
        parser.error("--interval-sec 必须大于 0")
    root = args.root.resolve()
    platform_name = target_platform(args.platform)
    if platform_name not in {"macos", "windows"}:
        parser.error("当前仅支持 macOS 或 Windows 的后台服务控制")
    path = config_path(root, platform_name)
    if args.action == "install":
        python = require_project_python(root, platform_name, args.python_path)
        path = write_config(
            root=root,
            platform_name=platform_name,
            python=python,
            interval_sec=args.interval_sec,
            allow_partial_import=args.allow_partial_import,
            exclude_ids=args.exclude_id,
        )
        print(f"配置已写入: {path}")
    elif args.action == "uninstall" and not path.exists():
        print(f"配置文件不存在，仍尝试卸载任务: {path}")
    return run_command(command_for(args.action, platform_name, path), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
