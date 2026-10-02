"""Persistent service-level collector scheduling configuration.

The collector service currently schedules one enabled registry batch at a
time.  This module keeps the effective interval and exclusions in a project
owned JSON file and can re-install the local OS launcher when requested by the
local UI.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import project_root


CONFIG_PATH = project_root() / "data" / "service-config" / "collector-settings.json"
MIN_INTERVAL_SEC = 60
MAX_INTERVAL_SEC = 7 * 24 * 60 * 60


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    return "unsupported"


def _defaults() -> dict[str, Any]:
    return {
        "interval_sec": 86400,
        "exclude_ids": ["taco-monitor"],
        "auto_import": True,
        "allow_partial_import": False,
    }


def _from_launch_agent() -> dict[str, Any]:
    path = project_root() / "data" / "service-config" / "com.geoagent.collector.plist"
    if not path.is_file():
        return {}
    try:
        payload = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    args = [str(value) for value in payload.get("ProgramArguments", [])]
    result: dict[str, Any] = {}
    if "--interval-sec" in args:
        index = args.index("--interval-sec") + 1
        if index < len(args):
            try:
                result["interval_sec"] = int(args[index])
            except ValueError:
                pass
    result["exclude_ids"] = [args[index + 1] for index, value in enumerate(args[:-1]) if value == "--exclude-id"]
    result["auto_import"] = "--auto-import" in args
    result["allow_partial_import"] = "--allow-partial-import" in args
    return result


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    defaults = _defaults()
    result = {**defaults, **payload}
    interval = int(result.get("interval_sec", defaults["interval_sec"]))
    if not MIN_INTERVAL_SEC <= interval <= MAX_INTERVAL_SEC:
        raise ValueError(f"interval_sec 必须在 {MIN_INTERVAL_SEC} 到 {MAX_INTERVAL_SEC} 秒之间")
    exclude_ids = result.get("exclude_ids", [])
    if not isinstance(exclude_ids, list) or not all(isinstance(value, str) and value.strip() for value in exclude_ids):
        raise ValueError("exclude_ids 必须是非空字符串数组")
    result["interval_sec"] = interval
    result["exclude_ids"] = sorted(set(exclude_ids))
    result["auto_import"] = bool(result.get("auto_import", True))
    result["allow_partial_import"] = bool(result.get("allow_partial_import", False))
    return result


def load_config() -> dict[str, Any]:
    """Load project settings, falling back to the active macOS plist."""
    payload: dict[str, Any] = {}
    source = "defaults"
    if CONFIG_PATH.is_file():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
                source = "project-settings"
        except (OSError, ValueError):
            payload = {}
    if not payload:
        payload = _from_launch_agent()
        source = "launch-agent" if payload else "defaults"
    result = _normalize(payload)
    result.update({"path": str(CONFIG_PATH), "platform": platform_name(), "source": source})
    return result


def save_config(payload: dict[str, Any], *, known_ids: set[str]) -> dict[str, Any]:
    """Validate and persist project-local settings without touching the OS."""
    result = _normalize(payload)
    unknown = sorted(set(result["exclude_ids"]) - known_ids)
    if unknown:
        raise ValueError(f"排除列表包含未知采集器：{', '.join(unknown)}")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamped = {**result, "updated_at": datetime.now(timezone.utc).isoformat()}
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(stamped, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_PATH)
    return load_config()


def apply_config(config: dict[str, Any]) -> dict[str, Any]:
    """Re-install the local OS launcher with the saved project settings."""
    current_platform = platform_name()
    if current_platform not in {"macos", "windows"}:
        return {"applied": False, "platform": current_platform, "message": "当前平台没有可安装的后台任务适配器"}
    base = [sys.executable, "-m", "scripts.collector_service_ctl"]
    uninstall = subprocess.run(
        [*base, "uninstall", "--platform", current_platform],
        cwd=project_root(), capture_output=True, text=True, check=False,
    )
    install_args = [
        *base,
        "install",
        "--platform",
        current_platform,
        "--interval-sec",
        str(config["interval_sec"]),
    ]
    for collector_id in config["exclude_ids"]:
        install_args.extend(["--exclude-id", collector_id])
    if config.get("allow_partial_import"):
        install_args.append("--allow-partial-import")
    install = subprocess.run(
        install_args,
        cwd=project_root(), capture_output=True, text=True, check=False,
    )
    if install.returncode != 0:
        detail = (install.stderr or install.stdout or "后台任务安装失败").strip()[-1000:]
        return {"applied": False, "platform": current_platform, "message": detail, "uninstall_returncode": uninstall.returncode}
    return {
        "applied": True,
        "platform": current_platform,
        "message": "后台任务已重新加载；服务会按新配置开始下一轮采集",
        "uninstall_returncode": uninstall.returncode,
        "install_returncode": install.returncode,
    }
