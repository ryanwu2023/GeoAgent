"""Run via kubectl exec stdin; create PostgreSQL custom-format recovery points."""
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from psycopg.conninfo import conninfo_to_dict


def postgres_environment(dsn: str) -> dict[str, str]:
    """libpq does not expand a URI supplied through PGDATABASE itself."""
    variables = {
        "host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT",
        "dbname": "PGDATABASE", "user": "PGUSER", "password": "PGPASSWORD",
        "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
        "connect_timeout": "PGCONNECT_TIMEOUT", "options": "PGOPTIONS",
        "application_name": "PGAPPNAME", "target_session_attrs": "PGTARGETSESSIONATTRS",
        "channel_binding": "PGCHANNELBINDING", "service": "PGSERVICE",
    }
    params = conninfo_to_dict(dsn)
    unsupported = params.keys() - variables.keys()
    if unsupported:
        raise ValueError("Unsupported backup connection parameters: " + ",".join(sorted(unsupported)))
    env = {key: value for key, value in os.environ.items() if key not in variables.values()}
    env["PGCONNECT_TIMEOUT"] = "10"
    env.update({variables[key]: value for key, value in params.items()})
    return env


def backup(root: Path, release_id: str) -> Path:
    if not re.fullmatch(r"[0-9]+", release_id):
        raise ValueError("Invalid release ID")
    locks = [root / "collection-runs/.collector-service.lock"]
    locks += list((root / "collector-output/.collector-locks").glob("*.lock"))
    if any(path.exists() for path in locks):
        raise RuntimeError("Collector lock exists; finish/recover collection before deploying")
    dsn = os.environ.get("GEO_DATABASE_URL", "")
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("GEO_DATABASE_URL must be configured for PostgreSQL backup")
    destination = root / "release-backups" / release_id
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    temporary = destination / "database.dump.partial"
    # Supply individual libpq environment variables, keeping credentials out of argv/logs.
    env = postgres_environment(dsn)
    try:
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(temporary)], env=env, check=True, capture_output=True)
        subprocess.run(["pg_restore", "--list", str(temporary)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("PostgreSQL backup failed; check connectivity and pg_dump server compatibility") from None
    temporary.chmod(0o600)
    temporary.rename(destination / "database.dump")
    dump = destination / "database.dump"
    with dump.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = destination / "manifest.json"
    manifest.write_text(json.dumps({"release": release_id, "format": "postgresql-custom", "files": ["database.dump"],
                                   "sha256": {"database.dump": checksum}, "bytes": dump.stat().st_size}))
    manifest.chmod(0o600)
    return destination


if __name__ == "__main__":
    print(backup(Path(os.getenv("GEO_DATA_DIR", "/app/data")), sys.argv[1]))
