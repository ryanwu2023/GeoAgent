#!/usr/bin/env bash
# PiChat core services (postgres, api, gateway, report) on an offline RHEL 8 host with Podman.
# No Docker daemon: sandbox agents stay unavailable until a runner is added.
# Run as: sudo bash deploy-pichat.sh <images.tar.gz (app + postgres)> <local-init.mjs>
set -euo pipefail
IMAGES=${1:?images tarball}; INIT=${2:?local-init.mjs}
APP_REF=${APP_REF:-pichat-app:hft-66aa37f65}
PG_REF=pichat-postgres:18
ENV_ID=${ENV_ID:-hft-test}
ROOT=/srv/pichat
HOST_IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)
PUBLIC_URL=http://${HOST_IP}:8080

# podman load prefixes names differently (docker.io/library/ or localhost/), so
# match on name:tag instead of hard-coding the full reference.
resolve_image() {
  podman images --format '{{.Repository}}:{{.Tag}}' | grep -E "(^|/)$1\$" | head -1
}
echo "== Images"
if [ -z "$(resolve_image "$APP_REF")" ] || [ -z "$(resolve_image "$PG_REF")" ]; then
  podman load -i "$IMAGES" | tail -2
fi
APP_IMG=$(resolve_image "$APP_REF"); PG_IMG=$(resolve_image "$PG_REF")
if [ -z "$APP_IMG" ] || [ -z "$PG_IMG" ]; then
  echo "image not found: $APP_REF / $PG_REF; loaded images:" >&2; podman images >&2; exit 1
fi
echo "app=$APP_IMG postgres=$PG_IMG"
REV=$(podman image inspect "$APP_IMG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[ -n "$REV" ]

echo "== Directories"
for d in runtime data logs keys postgres pg-secret report-secret report-runtime report-data provision assets; do
  install -d -m 700 "$ROOT/$d"
done
[ -s "$ROOT/pg-secret/password" ] || { openssl rand -hex 32 > "$ROOT/pg-secret/password"; }
chmod 644 "$ROOT/pg-secret/password"   # read by the postgres uid inside its container
chmod 755 "$ROOT/pg-secret"
# The postgres image creates its data directory as its own user, not root.
PG_UID=$(podman run --rm --network none --entrypoint id "$PG_IMG" -u postgres)
chown "$PG_UID:$PG_UID" "$ROOT/postgres"

echo "== PostgreSQL 18"
install -d /etc/containers/systemd
cat > /etc/containers/systemd/pichat-postgres.container <<UNIT
[Unit]
Description=PiChat PostgreSQL
[Container]
Image=${PG_IMG}
ContainerName=pichat-postgres
Network=host
Environment=POSTGRES_DB=pichat POSTGRES_PASSWORD_FILE=/run/pg-secret/password
Volume=${ROOT}/postgres:/var/lib/postgresql
Volume=${ROOT}/pg-secret:/run/pg-secret:ro
PodmanArgs=--shm-size=256m
Exec=postgres -c listen_addresses=127.0.0.1 -c port=5432
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl start pichat-postgres
for i in $(seq 1 60); do podman exec pichat-postgres pg_isready -h 127.0.0.1 -U postgres -d pichat >/dev/null 2>&1 && break; sleep 2; done
podman exec pichat-postgres pg_isready -h 127.0.0.1 -U postgres -d pichat

echo "== Initialize (roles, migrations, profile, admin)"
if [ ! -f "$ROOT/runtime/environment-owner.json" ]; then
  /usr/libexec/platform-python - "$ROOT/provision/input.json" "$ENV_ID" "$PUBLIC_URL" "$REV" <<'PY'
import json, sys
path, env_id, public_url, rev = sys.argv[1:5]
config = {
  "siteId": env_id, "port": 8080, "host": "0.0.0.0", "publicUrl": public_url,
  "dataDir": "/var/lib/pi-chat/data", "agentsDir": "/opt/pi-chat/agents",
  "storage": {"engine": "postgres", "postgres": {"pool": {"max": 8}},
              "content": {"enabled": True, "backend": "postgres", "bucketPrefix": env_id + "-"}},
  "workflow": {"engine": "dbos", "mode": "active", "applicationName": "pi-chat-" + env_id},
  "secrets": {"rootKeyDir": "/var/lib/pi-chat/keys"},
  "static": {"enabled": True, "distDir": "/opt/pi-chat/public/chat-dist"},
  "runtimeGateway": {"mode": "standalone", "host": "0.0.0.0", "port": 8081},
  "serviceEndpoints": {"internalApi": "http://127.0.0.1:8080", "capabilityGateway": "http://127.0.0.1:8081",
                       "containerCapabilityGateway": "http://127.0.0.1:8081"},
  "agentRunner": {"mode": "in-container", "port": 8082, "serviceTransport": "local-controlled",
                  "serviceUrl": "ws://127.0.0.1:8082", "runtimePort": 8083},
  "docker": {"image": "pichat-sandbox-not-installed", "containerPrefix": "pichat-" + env_id + "-agent-",
             "memoryLimit": "2g", "cpuLimit": "1", "pidsLimit": 512},
}
json.dump({"environmentId": env_id, "config": config, "assetRevision": rev}, open(path, "w"))
PY
  chmod 600 "$ROOT/provision/input.json"
  podman run --rm -i --network host --add-host postgres:127.0.0.1 --user 0 --entrypoint node \
    -v "$ROOT/runtime:/var/lib/pi-chat/runtime" -v "$ROOT/data:/var/lib/pi-chat/data" \
    -v "$ROOT/logs:/var/lib/pi-chat/logs" -v "$ROOT/keys:/var/lib/pi-chat/keys" \
    -v "$ROOT/pg-secret:/run/pg-secret:ro" -v "$ROOT/report-secret:/run/report-secret" \
    -v "$ROOT/report-runtime:/run/report-runtime" -v "$ROOT/report-data:/run/report-data" \
    -v "$ROOT/provision:/run/provision" -v "$ROOT/assets:/run/assets" \
    "$APP_IMG" --input-type=module < "$INIT"
fi

echo "== Services"
common() {
  cat <<COMMON
Image=${APP_IMG}
Network=host
ReadOnly=true
DropCapability=ALL
NoNewPrivileges=true
PodmanArgs=--add-host=postgres:127.0.0.1 --tmpfs=/tmp:size=1g,mode=1777 --tmpfs=/home/node:size=128m,mode=1777
COMMON
}
for role in api gateway; do
  cat > /etc/containers/systemd/pichat-$role.container <<UNIT
[Unit]
Description=PiChat $role
After=pichat-postgres.service
Requires=pichat-postgres.service
[Container]
ContainerName=pichat-$role
$(common)
Exec=$role
Environment=PICHAT_ENVIRONMENT=local PI_CHAT_RUNTIME_DIR=/var/lib/pi-chat/runtime PI_CHAT_RUNTIME_PROFILE=/var/lib/pi-chat/runtime/runtime-profile.json PI_CHAT_REPORT_SERVICE_URL=http://127.0.0.1:9182
Volume=${ROOT}/runtime:/var/lib/pi-chat/runtime
Volume=${ROOT}/data:/var/lib/pi-chat/data
Volume=${ROOT}/logs:/var/lib/pi-chat/logs
Volume=${ROOT}/keys:/var/lib/pi-chat/keys
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
done
cat > /etc/containers/systemd/pichat-report.container <<UNIT
[Unit]
Description=PiChat report
After=pichat-postgres.service
Requires=pichat-postgres.service
[Container]
ContainerName=pichat-report
$(common)
Exec=report
Environment=NODE_ENV=production PICHAT_ENVIRONMENT=local PI_CHAT_RUNTIME_DIR=/var/lib/pi-chat/report-runtime
Environment=PI_CHAT_DATABASE_URL_FILE=/run/report-secret/database-url PI_CHAT_REPORT_DATABASE_URL_FILE=/run/report-secret/database-url DBOS_SYSTEM_DATABASE_URL_FILE=/run/report-secret/database-url
Environment=PI_CHAT_REPORT_DATABASE_ROLE=pichat_report PI_CHAT_REPORT_SERVICE_HOST=127.0.0.1 PI_CHAT_REPORT_DB_POOL_MAX=8
Volume=${ROOT}/report-runtime:/var/lib/pi-chat/report-runtime:ro
Volume=${ROOT}/report-data:/var/lib/pi-chat/report-data
Volume=${ROOT}/report-secret:/run/report-secret:ro
[Service]
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl restart pichat-report pichat-gateway pichat-api
if systemctl is-active -q firewalld; then
  firewall-cmd -q --permanent --add-port=8080/tcp && firewall-cmd -q --reload
fi

echo "== Verify"
for i in $(seq 1 60); do
  h=$(curl -s -m 3 http://127.0.0.1:8080/api/health || true)
  echo "$h" | grep -q '"status":"ok"' && break; sleep 3
done
echo "api_health=$h"
echo "gateway=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/healthz) report=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:9182/readyz) ui=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)"
systemctl is-active pichat-postgres pichat-api pichat-gateway pichat-report || true
echo "URL: ${PUBLIC_URL}/  admin password: ${ROOT}/provision/admin-password"
