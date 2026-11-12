#!/usr/bin/env bash
# PiChat on a single offline host with Docker CE: postgres, api, gateway, runner, report + agent sandboxes.
# Idempotent: safe to re-run after changing images or settings.
# Run as: sudo bash deploy-pichat-docker.sh <images.tar.gz> <local-init.mjs> <compose.yaml>
set -euo pipefail
IMAGES=${1:?images tarball}; INIT=${2:?local-init.mjs}; COMPOSE_SRC=${3:?compose.yaml}
TAG=${TAG:-hft-66aa37f65}
ENV_ID=${ENV_ID:-hft-test}
ROOT=/srv/pichat
AGENT_SUBNET=${AGENT_SUBNET:-10.203.1.0/24}
AGENT_GW=${AGENT_SUBNET%.0/24}.1
HOST_IP=${HOST_IP:-$(ip -4 -o addr show scope global | grep -v -E 'docker0|pichat-agents|br-' | awk '{print $4}' | cut -d/ -f1 | head -1)}
PUBLIC_URL=${PUBLIC_URL:-http://${HOST_IP}:8080}
PY=$(command -v python3 || echo /usr/libexec/platform-python)

echo "== Images"
# docker load keeps the saved name; check by exact name:tag.
need_load=0
for ref in pichat-runner:$TAG pichat-sandbox:$TAG pichat-postgres:18; do
  docker image inspect "$ref" >/dev/null 2>&1 || need_load=1
done
[ $need_load = 0 ] || docker load -i "$IMAGES" | tail -4
for ref in pichat-runner:$TAG pichat-sandbox:$TAG pichat-postgres:18; do
  docker image inspect "$ref" >/dev/null 2>&1 || { echo "missing image $ref; loaded:" >&2; docker images >&2; exit 1; }
done
REV=$(docker image inspect pichat-runner:$TAG --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[ -n "$REV" ]

echo "== Directories"
for d in runtime data logs keys postgres pg-secret report-secret report-runtime report-data provision assets; do
  install -d -m 700 "$ROOT/$d"
done
[ -s "$ROOT/pg-secret/password" ] || openssl rand -hex 32 > "$ROOT/pg-secret/password"
chmod 755 "$ROOT/pg-secret"; chmod 644 "$ROOT/pg-secret/password"
# The postgres image creates its data directory as its own user, not root.
PG_UID=$(docker run --rm --network none --entrypoint id pichat-postgres:18 -u postgres)
chown "$PG_UID:$PG_UID" "$ROOT/postgres"

echo "== Agent network"
docker network inspect pichat_agents >/dev/null 2>&1 || docker network create --driver bridge \
  --subnet "$AGENT_SUBNET" --gateway "$AGENT_GW" -o com.docker.network.bridge.name=pichat-agents pichat_agents >/dev/null
docker network inspect pichat_agents --format 'agents={{(index .IPAM.Config 0).Subnet}} gw={{(index .IPAM.Config 0).Gateway}}'

echo "== Compose"
install -m 644 "$COMPOSE_SRC" "$ROOT/compose.yaml"
cat > "$ROOT/.env" <<ENV
TAG=$TAG
DOCKER_GID=$(getent group docker | cut -d: -f3)
ENV
compose() { docker compose -p pichat --env-file "$ROOT/.env" -f "$ROOT/compose.yaml" "$@"; }
compose up -d --wait postgres

echo "== Initialize (roles, migrations, settings, profile, admin)"
"$PY" - "$ROOT/provision/input.json" "$ENV_ID" "$PUBLIC_URL" "$REV" "$TAG" "$AGENT_GW" "$ROOT" <<'PY'
import json, sys
path, env_id, public_url, rev, tag, gw, root = sys.argv[1:8]
config = {
  "siteId": env_id, "port": 8080, "host": "0.0.0.0", "publicUrl": public_url,
  "dataDir": "/var/lib/pi-chat/data", "agentsDir": "/opt/pi-chat/agents",
  "storage": {"engine": "postgres", "postgres": {"pool": {"max": 8}},
              "content": {"enabled": True, "backend": "postgres", "bucketPrefix": env_id + "-"}},
  "workflow": {"engine": "dbos", "mode": "active", "applicationName": "pi-chat-" + env_id},
  "secrets": {"rootKeyDir": "/var/lib/pi-chat/keys"},
  "static": {"enabled": True, "distDir": "/opt/pi-chat/public/chat-dist"},
  "runtimeGateway": {"mode": "standalone", "host": "0.0.0.0", "port": 8081},
  # Platform services use host networking; sandboxes reach them through the agent bridge gateway.
  "serviceEndpoints": {"internalApi": "http://127.0.0.1:8080", "capabilityGateway": "http://127.0.0.1:8081",
                       "containerCapabilityGateway": "http://%s:8081" % gw},
  "agentRunner": {"mode": "in-container", "port": 8082, "serviceTransport": "local-controlled",
                  "serviceUrl": "ws://127.0.0.1:8082", "runtimePort": 8083},
  "docker": {"image": "pichat-sandbox:" + tag, "containerPrefix": "pichat-" + env_id + "-agent-",
             "memoryLimit": "2g", "cpuLimit": "1", "pidsLimit": 512},
  "containerRuntime": {"endpoint": "unix:///var/run/docker.sock", "network": "pichat_agents", "hostAddress": gw,
                       "daemonPathRoots": [{"host": "/var/lib/pi-chat/data", "daemon": root + "/data"},
                                           {"host": "/opt/pi-chat", "daemon": "%s/assets/%s" % (root, rev)}]},
}
json.dump({"environmentId": env_id, "config": config, "assetRevision": rev}, open(path, "w"), indent=1)
PY
chmod 600 "$ROOT/provision/input.json"
docker run --rm -i --network host --add-host postgres:127.0.0.1 --user 0 --entrypoint node \
  -v "$ROOT/runtime:/var/lib/pi-chat/runtime" -v "$ROOT/data:/var/lib/pi-chat/data" \
  -v "$ROOT/logs:/var/lib/pi-chat/logs" -v "$ROOT/keys:/var/lib/pi-chat/keys" \
  -v "$ROOT/pg-secret:/run/pg-secret:ro" -v "$ROOT/report-secret:/run/report-secret" \
  -v "$ROOT/report-runtime:/run/report-runtime" -v "$ROOT/report-data:/run/report-data" \
  -v "$ROOT/provision:/run/provision" -v "$ROOT/assets:/run/assets" \
  pichat-runner:$TAG --input-type=module < "$INIT" 2>&1 | grep -E 'ensure-admin|INITIALIZED|Error|error' || true
test -f "$ROOT/runtime/runtime-profile.json"

echo "== Services"
compose up -d --force-recreate api gateway runner report

echo "== Verify"
h=""
for i in $(seq 1 60); do
  h=$(curl -s -m 3 http://127.0.0.1:8080/api/health || true)
  echo "$h" | grep -q '"status":"ok"' && break; sleep 3
done
echo "api_health=$(echo "$h" | cut -c1-60)"
echo "gateway=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/healthz) runner=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8082/healthz) report=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:9182/readyz) ui=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)"
compose ps --format '{{.Service}} {{.State}} {{.Status}}'
echo "URL: ${PUBLIC_URL}/  admin password file: ${ROOT}/provision/admin-password"
