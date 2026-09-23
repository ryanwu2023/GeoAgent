"""Create a bounded, path-sanitized demo snapshot from collector output."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
from pathlib import Path


PERSONAL_PATH = re.compile(r"(?i)[A-Z]:[/\\]Users[/\\][^/\\\"\s]+[/\\]WorkBuddy[/\\][^\"\r\n]*")


def _sanitize(text: str) -> str:
    return PERSONAL_PATH.sub("[local-path-removed]", text)


def _write(destination: Path, text: str, base: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    payload = text.encode("utf-8")
    return {"path": destination.relative_to(base).as_posix(), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def build_demo(source_root: Path, destination: Path, max_records: int = 100) -> dict:
    files = []
    record_count = 0
    for source in sorted(source_root.glob("*/output/ALL-records.jsonl")):
        lines = [line for line in source.read_text(encoding="utf-8-sig").splitlines() if line.strip()][-max_records:]
        if not lines:
            continue
        text = "\n".join(_sanitize(line) for line in lines) + "\n"
        target = destination / source.relative_to(source_root)
        files.append(_write(target, text, destination))
        record_count += len(lines)
    for source in sorted(source_root.glob("*/output/geo-v6.json")):
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        payload["records"] = payload.get("records", [])[-max_records:]
        for item in payload.get("observations", []):
            item["history"] = item.get("history", [])[-180:]
        text = _sanitize(json.dumps(payload, ensure_ascii=False, indent=2)) + "\n"
        files.append(_write(destination / source.relative_to(source_root), text, destination))
        record_count += len(payload["records"])
    taco = source_root / "taco-monitor" / "output" / "taco-latest.csv"
    if taco.is_file():
        rows = list(csv.reader(io.StringIO(taco.read_text(encoding="utf-8-sig"))))
        bounded = rows[:1] + rows[-180:] if rows else []
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerows(bounded)
        files.append(_write(destination / "taco-monitor/output/taco-latest.csv", buffer.getvalue(), destination))
        status = taco.parent / "_source_status.json"
        if status.is_file():
            files.append(_write(destination / "taco-monitor/output/_source_status.json", _sanitize(status.read_text(encoding="utf-8-sig")), destination))
    manifest = {"schema_version": "demo-data/1", "record_count": record_count, "max_records_per_collector": max_records, "files": files}
    (destination / "manifest.json").parent.mkdir(parents=True, exist_ok=True)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parents[1] / "examples" / "demo-data")
    parser.add_argument("--max-records", type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(build_demo(args.source_root, args.destination, args.max_records), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
