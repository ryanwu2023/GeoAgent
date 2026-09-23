"""Audit the source package for material that should not be published."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SKIP_DIRS = {".git", ".superpowers", "node_modules", "dist", "data", "__pycache__", ".pytest_cache"}
BLOCKED_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".bin", ".argomodel", ".pem", ".p12"}
PERSONAL_PATH = re.compile(r"(?i)[A-Z]:[/\\]Users[/\\][^/\\\"'\s]+")
SECRET_ASSIGNMENT = re.compile(r"(?im)(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)[ \t]*[\"']?[ \t]*[:=][ \t]*[\"']?([^\"'\s,}]+)")


def _skip(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in SKIP_DIRS for part in relative.parts):
        return True
    return bool(relative.parts and relative.parts[0] == "collectors" and any(part in {"output", "logs"} for part in relative.parts))


def audit_repository(root: Path, max_file_mb: float = 20) -> list[dict]:
    findings: list[dict] = []
    limit = int(max_file_mb * 1024 * 1024)
    for file in sorted(root.rglob("*")):
        if not file.is_file() or _skip(file, root):
            continue
        relative = file.relative_to(root).as_posix()
        if relative == ".env":
            continue
        size = file.stat().st_size
        if size > limit:
            findings.append({"path": relative, "kind": "large_file", "size": size})
        if file.name != ".env.example" and (file.name == ".env" or file.suffix.lower() == ".env"):
            findings.append({"path": relative, "kind": "environment_file"})
        if file.suffix.lower() in BLOCKED_SUFFIXES:
            findings.append({"path": relative, "kind": "blocked_binary", "size": size})
        if size > 5 * 1024 * 1024:
            continue
        try:
            text = file.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        if PERSONAL_PATH.search(text):
            findings.append({"path": relative, "kind": "personal_path"})
        if ("-----BEGIN " + "PRIVATE KEY-----") in text:
            findings.append({"path": relative, "kind": "private_key"})
        for match in SECRET_ASSIGNMENT.finditer(text):
            value = match.group(1).strip()
            if len(value) >= 12 and value.lower() not in {"your-api-key", "replace-me", "example-value"}:
                findings.append({"path": relative, "kind": "possible_secret"})
                break
    unique = {(item["path"], item["kind"]): item for item in findings}
    return [unique[key] for key in sorted(unique)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--max-file-mb", type=float, default=20)
    args = parser.parse_args()
    findings = audit_repository(args.root.resolve(), args.max_file_mb)
    print(json.dumps({"ok": not findings, "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
