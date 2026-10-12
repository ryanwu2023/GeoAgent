"""Start the local GeoAgent development stack.

The development profile keeps Vite and FastAPI on separate, explicit ports:
the browser opens on 8010 and Vite proxies ``/api`` to FastAPI on 8011.  The
ports are configurable so a local service occupying either port does not
silently receive GeoAgent requests.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def npm_command() -> str:
    """Return the platform's npm executable without assuming a shell."""
    candidates = ["npm.cmd", "npm"] if os.name == "nt" else ["npm"]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise FileNotFoundError("npm not found; install Node.js/npm before starting the UI")


def command_plan(
    *,
    api_host: str,
    api_port: int,
    web_host: str,
    web_port: int,
    npm: str,
) -> tuple[list[str], list[str]]:
    backend = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        api_host,
        "--port",
        str(api_port),
    ]
    frontend = [
        npm,
        "run",
        "dev",
        "--",
        "--host",
        web_host,
        "--port",
        str(web_port),
    ]
    return backend, frontend


def wait_http(url: str, *, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 400:
                    return True
        except (OSError, URLError):
            time.sleep(0.25)
    return False


def port_available(host: str, port: int) -> bool:
    """Check the requested bind address before starting a child process."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 GeoAgent 本地前后端开发环境")
    parser.add_argument("--api-host", default=os.environ.get("GEOAGENT_API_HOST", "127.0.0.1"))
    parser.add_argument("--api-port", type=int, default=_env_int("GEOAGENT_API_PORT", 8011))
    parser.add_argument("--web-host", default=os.environ.get("GEOAGENT_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--web-port", type=int, default=_env_int("GEOAGENT_WEB_PORT", 8010))
    parser.add_argument("--no-frontend", action="store_true", help="只启动 API，便于后端调试")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不启动进程")
    args = parser.parse_args(argv)

    npm = "npm.cmd" if os.name == "nt" else "npm"
    if not args.dry_run:
        npm = npm_command()
    backend_cmd, frontend_cmd = command_plan(
        api_host=args.api_host,
        api_port=args.api_port,
        web_host=args.web_host,
        web_port=args.web_port,
        npm=npm,
    )
    api_url = f"http://{args.api_host}:{args.api_port}"
    web_url = f"http://{args.web_host}:{args.web_port}/"
    print("API:", " ".join(backend_cmd), flush=True)
    if not args.no_frontend:
        print("Web:", " ".join(frontend_cmd), flush=True)
    print("API status:", f"{api_url}/api/status", flush=True)
    print("MCP (same backend):", f"{api_url}/mcp", flush=True)
    if not args.no_frontend:
        print("Web URL:", web_url, flush=True)
        print("MCP public URL:", f"{web_url}mcp", flush=True)
    if args.dry_run:
        return 0

    environment = os.environ.copy()
    environment["GEOAGENT_API_TARGET"] = api_url
    processes: list[subprocess.Popen[bytes]] = []
    try:
        if not port_available(args.api_host, args.api_port):
            raise RuntimeError(f"API port {args.api_port} is already occupied; choose --api-port")
        if not args.no_frontend and not port_available(args.web_host, args.web_port):
            raise RuntimeError(f"Web port {args.web_port} is already occupied; choose --web-port")
        backend = subprocess.Popen(backend_cmd, cwd=ROOT, env=environment)
        processes.append(backend)
        if not wait_http(f"{api_url}/api/status"):
            raise RuntimeError("GeoAgent API did not become ready; inspect the backend output")
        if args.no_frontend:
            print("GeoAgent API is ready. Press Ctrl-C to stop.", flush=True)
            backend.wait()
            return backend.returncode or 0

        frontend = subprocess.Popen(frontend_cmd, cwd=ROOT, env=environment)
        processes.append(frontend)
        if not wait_http(web_url):
            raise RuntimeError("Vite did not become ready; inspect the frontend output")
        print(f"GeoAgent is ready: {web_url}", flush=True)
        print("Press Ctrl-C to stop both processes.", flush=True)
        while True:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f"child process exited with code {process.returncode}")
            time.sleep(0.5)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
