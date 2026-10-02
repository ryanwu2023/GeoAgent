"""Create and optionally populate the repository-local Python environment."""

from __future__ import annotations

import argparse
import os
import subprocess
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def venv_dir(root: Path = ROOT) -> Path:
    return root / ".venv"


def venv_python(root: Path = ROOT) -> Path:
    relative = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    return root / relative


def ensure_venv(root: Path = ROOT) -> Path:
    python = venv_python(root)
    if not python.is_file():
        venv.EnvBuilder(with_pip=True, clear=False, symlinks=(os.name != "nt"), upgrade_deps=False).create(venv_dir(root))
    if not python.is_file():
        raise RuntimeError(f"项目虚拟环境创建失败: {python}")
    return python


def install_requirements(root: Path = ROOT, *, requirements: Path | None = None) -> Path:
    python = ensure_venv(root)
    requirement_file = requirements or root / "requirements.txt"
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirement_file)], cwd=root, check=True)
    return python


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创建 GeoAgent 项目私有 .venv")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--no-install", action="store_true", help="只创建虚拟环境，不安装 requirements.txt")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    python = ensure_venv(root)
    if not args.no_install:
        install_requirements(root)
    print(python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
