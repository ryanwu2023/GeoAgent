#!/usr/bin/env bash
# Upgrade the PiChat stack on this host (installed by deploy-pichat-docker.sh) to a new image tag.
# The running hft-66aa37f65 came from a local branch whose two sws-audit migrations are not in
# dev; the new code then sees the schema as "ahead" and refuses to start. Those migrations are
# rolled back here (their four tables must be empty), after a full pg_dump backup.
# Users, sessions, connections and settings are kept.
# Backup, verified before any change: $BACKUP_ROOT/<old tag>-<time>/ (pichat.dump, globals.sql,
# files.tar.gz with keys/runtime/provision/secrets/data, SHA256SUMS). Default BACKUP_ROOT=/srv/pichat-backup.
#
# Usage (on 172.16.91.119, files uploaded to /tmp):
#   cat /tmp/pichat-zs-images.part* > /tmp/pichat-zs-images.tar.gz
#   sudo TAG=hft-fb94c18c0 bash /tmp/upgrade-pichat.sh /tmp/pichat-zs-images.tar.gz /tmp/pichat-local-init.mjs /tmp/pichat-compose.yaml /tmp/deploy-pichat-docker.sh
set -euo pipefail
IMAGES=${1:?images tarball}; INIT=${2:?local-init.mjs}; COMPOSE_SRC=${3:?compose.yaml}; DEPLOY=${4:?deploy-pichat-docker.sh}
TAG=${TAG:?set TAG, e.g. TAG=hft-fb94c18c0}
ROOT=/srv/pichat
compose() { docker compose -p pichat --env-file "$ROOT/.env" -f "$ROOT/compose.yaml" "$@"; }
PY=$(command -v python3 || echo /usr/libexec/platform-python)
psql_() { docker exec -i pichat-postgres-1 psql -v ON_ERROR_STOP=1 -U postgres -d pichat -qtA "$@"; }

echo "== Images"
for ref in pichat-runner:$TAG pichat-sandbox:$TAG; do
  docker image inspect "$ref" >/dev/null 2>&1 || { docker load -i "$IMAGES" | tail -3; break; }
done
for ref in pichat-runner:$TAG pichat-sandbox:$TAG pichat-postgres:18; do docker image inspect "$ref" >/dev/null; done

counts() { psql_ -c "select 'users='||(select count(*) from app.users)||' sessions='||(select count(*) from app.sessions)
  ||' content_objects='||(select count(*) from app.content_objects)||' settings_revisions='||(select count(*) from app.site_settings_revisions)"; }
# What must survive: settings namespaces (models, search, speech...), MCP connections, stored secrets.
inventory() {
  echo "settings: $(psql_ -c "select string_agg(namespace, ',' order by namespace) from app.site_settings_pointers")"
  "$PY" - "$ROOT/data" <<'PYI'
import json, os, sys, glob
d = sys.argv[1]
def load(p):
    try: return json.load(open(p))
    except Exception: return None
site = load(os.path.join(d, "mcp/site-servers.json")) or []
print("mcp_site:", ",".join(sorted(str(x.get("id")) for x in site if isinstance(x, dict))))
personal = sum(len(load(p) or []) for p in glob.glob(os.path.join(d, "users/*/mcp/servers.json")))
print("mcp_personal_count:", personal)
rec = load(os.path.join(d, "runtime-capability-gateway/secrets/records.json"))
n = len(rec.get("records", rec)) if isinstance(rec, dict) else (len(rec) if isinstance(rec, list) else 0)
print("secret_records:", n)
PYI
}
echo "before: $(counts)"

echo "== Preflight"
# The new code refuses to start while a site connection uses one of these reserved ids.
reserved=$("$PY" -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except FileNotFoundError: d=[]
items=d if isinstance(d,list) else (d.get("servers") or [])
print(" ".join(x.get("id","") for x in items if x.get("id") in ("buyside","luozi-office")))' "$ROOT/data/mcp/site-servers.json")
[ -z "$reserved" ] || { echo "站点连接用了新版保留的 id: $reserved，先在后台删掉再升级" >&2; exit 1; }

echo "== Backup"
# Nothing below touches the database until this backup is written and verified.
OLD_TAG=$(sed -n 's/^TAG=//p' "$ROOT/.env")
BK=${BACKUP_ROOT:-/srv/pichat-backup}/$OLD_TAG-$(date +%Y%m%d%H%M%S)
install -d -m 700 "$BK"
DB_SIZE=$(psql_ -c "select pg_database_size('pichat')")
DATA_SIZE=$(du -sb "$ROOT/data" | cut -f1)
FREE=$(df -B1 --output=avail "$(dirname "$BK")" | tail -1)
[ "$FREE" -gt $(( (DB_SIZE + DATA_SIZE) * 3 )) ] || { echo "备份盘空间不足: free=$FREE db=$DB_SIZE data=$DATA_SIZE" >&2; exit 1; }
docker exec pichat-postgres-1 pg_dump -U postgres -Fc pichat > "$BK/pichat.dump"
docker exec pichat-postgres-1 pg_dumpall -U postgres --globals-only > "$BK/globals.sql"
# A dump that pg_restore cannot list is not a backup. grep reads to the end (no -q): an early
# exit would SIGPIPE the producer and pipefail would report a good dump as broken.
docker exec -i pichat-postgres-1 pg_restore --list < "$BK/pichat.dump" | grep 'TABLE DATA app users' >/dev/null \
  || { echo "备份校验失败: $BK/pichat.dump" >&2; exit 1; }
# Root key decrypts stored model/MCP secrets; keep it with the dump it belongs to.
# MCP site/personal connections and credentials live as files under data/ (mcp/site-servers.json, users/*/mcp).
tar -C "$ROOT" -czf "$BK/files.tar.gz" keys runtime provision pg-secret report-secret report-runtime report-data data .env compose.yaml
tar -tzf "$BK/files.tar.gz" | grep '^keys/root-key.json$' >/dev/null || { echo "备份缺根密钥" >&2; exit 1; }
{ counts; inventory; } > "$BK/inventory-before.txt"; cat "$BK/inventory-before.txt"
chmod -R go-rwx "$BK"; ls -lh "$BK"
( cd "$BK" && sha256sum pichat.dump globals.sql files.tar.gz > SHA256SUMS )
cp -a "$ROOT/.env" "$ROOT/.env.bak-$OLD_TAG"
BACKUP=$BK/pichat.dump

echo "== Roll back migrations the new code does not know"
SWS="'202610230001_sws_audit_review.mjs','202610230002_sws_audit_history.mjs'"
if [ "$(psql_ -c "select count(*) from knex_migrations where name in ($SWS)")" != 0 ]; then
  rows=$(psql_ -c "select coalesce(sum(n),0) from (
    select count(*) n from app.sws_audit_review_streams union all select count(*) from app.sws_audit_review_events
    union all select count(*) from app.sws_audit_history_lanes union all select count(*) from app.sws_audit_history_tasks) t")
  [ "$rows" = 0 ] || { echo "sws_audit 表里有 $rows 行数据，停止（备份在 $BACKUP）" >&2; exit 1; }
  compose stop api gateway runner report
  psql_ <<SQL
BEGIN;
DROP TABLE app.sws_audit_history_tasks, app.sws_audit_history_lanes, app.sws_audit_review_events, app.sws_audit_review_streams;
DELETE FROM knex_migrations WHERE name IN ($SWS);
COMMIT;
SQL
  echo "rolled back: sws_audit_review, sws_audit_history"
else
  echo "nothing to roll back"
fi

echo "== Retire old agent sandboxes"
# They run the old sandbox image; the api starts fresh ones from pichat-sandbox:$TAG on demand.
docker ps -aq --filter name=pichat-hft-test-agent- | xargs -r docker rm -f

echo "== Deploy $TAG"
TAG=$TAG bash "$DEPLOY" "$IMAGES" "$INIT" "$COMPOSE_SRC"
{ counts; inventory; } > "$BK/inventory-after.txt"; chmod 600 "$BK/inventory-after.txt"
echo "== Inventory diff (before → after; users/sessions/settings may only grow)"
diff "$BK/inventory-before.txt" "$BK/inventory-after.txt" && echo "inventory unchanged" || true

echo "== Rollback (if needed)"
echo "  docker compose -p pichat --env-file $ROOT/.env -f $ROOT/compose.yaml stop api gateway runner report"
echo "  docker exec -i pichat-postgres-1 pg_restore -U postgres -d pichat --clean --if-exists < $BACKUP"
echo "  cp $ROOT/.env.bak-$OLD_TAG $ROOT/.env && docker compose -p pichat --env-file $ROOT/.env -f $ROOT/compose.yaml up -d --force-recreate api gateway runner report"
