#!/usr/bin/env bash
# GeoAgent deployment on HFT intranet RHEL 8 host (no internet; internal dnf repo).
# Run as: sudo IMG_REF=geoagent:<tag> bash deploy-geoagent.sh /path/to/image.tar.gz  (passwords generated into /etc/geoagent/secrets)
set -euo pipefail
TARBALL=${1:?image tarball path}
IMG_REF=${IMG_REF:-geoagent:hft-e7ac703}
APP_USER=${APP_USER:-admin}
install -d -m 700 /etc/geoagent
if [ -f /etc/geoagent/secrets ]; then . /etc/geoagent/secrets; else
  DB_PW=$(openssl rand -hex 16); APP_PW=$(openssl rand -base64 12 | tr -d "/+=")
  printf "DB_PW=%s\nAPP_PW=%s\n" "$DB_PW" "$APP_PW" > /etc/geoagent/secrets; chmod 600 /etc/geoagent/secrets
fi
ROOT=/data/geoagent
HOST_IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)

echo "== PostgreSQL 16"
if ! rpm -q postgresql-server >/dev/null 2>&1; then
  dnf -y -q module reset postgresql
  dnf -y -q module enable postgresql:16
  dnf -y -q install postgresql-server postgresql
fi
PGDATA=/var/lib/pgsql/data
[ -f "$PGDATA/PG_VERSION" ] || postgresql-setup --initdb
# Only local TCP with password; app runs with host networking.
if ! grep -q "geoagent-managed" "$PGDATA/pg_hba.conf"; then
  sed -i -E 's/^(host\s+all\s+all\s+127\.0\.0\.1\/32\s+)ident/\1scram-sha-256/; s/^(host\s+all\s+all\s+::1\/128\s+)ident/\1scram-sha-256/' "$PGDATA/pg_hba.conf"
  echo "# geoagent-managed" >> "$PGDATA/pg_hba.conf"
fi
systemctl enable --now postgresql
systemctl reload postgresql
sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='geoagent') THEN CREATE ROLE geoagent LOGIN; END IF;
END\$\$;
ALTER ROLE geoagent PASSWORD '${DB_PW}';
SQL
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='geoagent'" | grep -q 1 \
  || sudo -u postgres createdb -O geoagent geoagent

echo "== Image"
# podman load prefixes names differently (docker.io/library/ or localhost/); match on name:tag.
resolve_image() { podman images --format '{{.Repository}}:{{.Tag}}' | grep -E "(^|/)$1\$" | head -1; }
[ -n "$(resolve_image "$IMG_REF")" ] || podman load -i "$TARBALL" | tail -1
IMG=$(resolve_image "$IMG_REF")
if [ -z "$IMG" ]; then echo "image $IMG_REF not found; loaded images:" >&2; podman images >&2; exit 1; fi
echo "image=$IMG"

echo "== Config"
install -d -m 755 "$ROOT/data"
install -d -m 700 "$ROOT/auth" /etc/geoagent
HASH=$(podman run --rm --network none -e P="$APP_PW" "$IMG" python -c "import os;from passlib.hash import sha512_crypt;print(sha512_crypt.hash(os.environ['P']))")
# Refresh only the admin entry; accounts added with add-geo-users.sh survive redeploys.
{ printf '%s:%s\n' "$APP_USER" "$HASH"; grep -v "^${APP_USER}:" "$ROOT/auth/auth" 2>/dev/null || true; } > "$ROOT/auth/auth.new"
mv "$ROOT/auth/auth.new" "$ROOT/auth/auth"
chmod 644 "$ROOT/auth/auth"
# The env file is regenerated; operator-entered HT_MACRO_* (internal macro account) carry over.
MACRO=$(grep -E '^HT_MACRO_' /etc/geoagent/geoagent.env 2>/dev/null || true)
[ -n "$MACRO" ] || MACRO=HT_MACRO_ENABLED=false
cat > /etc/geoagent/geoagent.env <<ENV
GEO_DATABASE_URL=postgresql://geoagent:${DB_PW}@127.0.0.1:5432/geoagent
GEO_BACKGROUND_TASKS_ENABLED=true
GEO_WEB_AUTH_ENABLED=true
GEO_WEB_AUTH_FILE=/etc/geoagent-auth/auth
GEO_WEB_AUTH_SECURE_COOKIE=false
GEOAGENT_ALLOWED_ORIGINS=http://${HOST_IP}:8000
${MACRO}
ENV
# Remote collection through Sealos (see deploy/mcp-data-sync.md). Only enabled
# when the operator passes the Key; HFT itself has no overseas egress.
if [ -n "${GEO_REMOTE_MCP_KEY:-}" ]; then
cat >> /etc/geoagent/geoagent.env <<ENV
GEO_COLLECTION_MODE=remote
GEO_REMOTE_MCP_URL=${GEO_REMOTE_MCP_URL:-https://geoagent.lhotsetech.com/mcp}
GEO_REMOTE_MCP_KEY=${GEO_REMOTE_MCP_KEY}
GEO_REMOTE_SYNC_INTERVAL_SEC=${GEO_REMOTE_SYNC_INTERVAL_SEC:-1800}
ENV
fi
chmod 600 /etc/geoagent/geoagent.env

echo "== Service"
install -d /etc/containers/systemd
cat > /etc/containers/systemd/geoagent.container <<UNIT
[Unit]
Description=GeoAgent
After=postgresql.service
Requires=postgresql.service

[Container]
Image=${IMG}
ContainerName=geoagent
Network=host
EnvironmentFile=/etc/geoagent/geoagent.env
Volume=${ROOT}/data:/app/data
Volume=${ROOT}/auth:/etc/geoagent-auth:ro

[Service]
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
# Macro messages come from a separate long-running collector (login, token refresh,
# 08/14/20 fetches); the web container never starts it. Idles while HT_MACRO_ENABLED=false.
cat > /etc/containers/systemd/geoagent-macro.container <<UNIT
[Unit]
Description=GeoAgent HT macro message collector
After=postgresql.service geoagent.service
Requires=postgresql.service

[Container]
Image=${IMG}
ContainerName=geoagent-macro
Network=host
EnvironmentFile=/etc/geoagent/geoagent.env
Volume=${ROOT}/data:/app/data
Exec=python scripts/ht_macro_service.py

[Service]
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl restart geoagent geoagent-macro
if systemctl is-active -q firewalld; then
  firewall-cmd -q --permanent --add-port=8000/tcp && firewall-cmd -q --reload
fi

echo "== Verify"
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/healthz || true)
  [ "$code" = 200 ] && break; sleep 2
done
echo "healthz=$code login=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/login) status_anon=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/status)"
systemctl is-active geoagent postgresql
echo "URL: http://${HOST_IP}:8000/  login user: ${APP_USER}  password: see /etc/geoagent/secrets (APP_PW)"
