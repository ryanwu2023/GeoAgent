#!/bin/sh
# Import a snapshot exported from Sealos into the HFT GeoAgent and make it the
# working snapshot. The snapshot id is a content hash, so the imported id must
# equal the id in the file name; anything else aborts before touching the DB.
#
# Usage (as root on 172.16.91.120, after uploading via JumpServer to /tmp):
#   sh /tmp/import-snapshot.sh /tmp/geoagent-snapshot-<id>.json.gz
set -eu

SRC=${1:?usage: import-snapshot.sh /tmp/geoagent-snapshot-<id>.json.gz}
ROOT=/data/geoagent
NAME=$(basename "$SRC")
SID=$(printf '%s' "$NAME" | sed -n 's/^geoagent-snapshot-\([0-9a-f]\{24\}\)\.json\.gz$/\1/p')
[ -n "$SID" ] || { echo "file name must be geoagent-snapshot-<24 hex>.json.gz" >&2; exit 2; }
gzip -t "$SRC"

mkdir -p "$ROOT/data/imports"
cp "$SRC" "$ROOT/data/imports/$NAME"

podman exec -i -e SID="$SID" -e SRC="/app/data/imports/$NAME" geoagent python3 - <<'EOF'
import gzip, json, os, sys
sys.path.insert(0, "/app")
from backend import store

sid, src = os.environ["SID"], os.environ["SRC"]
with gzip.open(src, "rb") as f:
    payload = json.loads(f.read())
if store.digest(payload) != sid:
    sys.exit("content hash does not match snapshot id; file is corrupt or altered")
before = store.get_working_snapshot_id()
saved, created = store.save_snapshot(payload)
assert saved == sid, (saved, sid)
store.set_working_snapshot_id(sid)
print(json.dumps({"snapshot_id": sid, "created": created, "previous_working": before,
                  "records": len(payload["records"]),
                  "observations": len(payload.get("observations", []))}, ensure_ascii=False))
EOF

echo "readyz: $(curl -s -m 10 http://127.0.0.1:8000/readyz)"
echo "rollback: podman exec geoagent python3 -c \"import sys; sys.path.insert(0,'/app'); from backend import store; store.set_working_snapshot_id('<previous_working>')\""
