#!/bin/sh
# Rebuild pichat-sandbox:$TAG so its container-side code equals the runner
# image's. Fixes "这位专家的沙箱版本与服务版本不一致" (sandbox_plugin_drift):
# the sandbox was built with a host-compiled dist/, the runner compiles its own.
# Offline: builds FROM the sandbox already on this host; only ~13MB uploaded.
#
# Usage (on 172.16.91.119, after uploading via JumpServer to /tmp):
#   sudo sh /tmp/fix-sandbox-code.sh /tmp/pichat-sandbox-code-fix.tar.gz
set -eu

SRC=${1:?usage: fix-sandbox-code.sh /tmp/pichat-sandbox-code-fix.tar.gz}
TAG=${TAG:-hft-66aa37f65}
SANDBOX=pichat-sandbox:$TAG
ORIG=pichat-sandbox:$TAG-orig
RUNNER=pichat-runner:$TAG
WORK=/srv/pichat/sandbox-fix
COMPOSE="docker compose -p pichat --env-file /srv/pichat/.env -f /srv/pichat/compose.yaml"

code_hash() {
	docker run --rm --entrypoint node "$1" -e \
		'console.log(require("/opt/pi-chat/PLUGIN_ARTIFACT_MANIFEST.json").containerCode.hash)'
}

docker image inspect "$RUNNER" >/dev/null
# Keep the untouched original once; reruns build from it, never from a patched image.
docker image inspect "$ORIG" >/dev/null 2>&1 || docker tag "$SANDBOX" "$ORIG"

rm -rf "$WORK" && mkdir -p "$WORK"
tar -xzf "$SRC" -C "$WORK"
# A tarball made by macOS tar carries AppleDouble ._* files; inside a plugin
# dist/ they change the artifact hash (PLUGIN_ARTIFACT_MANIFEST_DRIFT).
find "$WORK" -name '._*' -delete
docker build --build-arg BASE="$ORIG" -t "$SANDBOX" "$WORK"

want=$(code_hash "$RUNNER")
got=$(code_hash "$SANDBOX")
echo "runner code hash:  $want"
echo "sandbox code hash: $got"
[ "$want" = "$got" ] || { echo "MISMATCH: sandbox still differs from runner" >&2; exit 1; }

# The consistency preflight runs at startup, so restart the two sandbox clients.
$COMPOSE restart api runner
sleep 20
docker logs --since 2m pichat-api-1 2>&1 | grep '\[sandbox\]' || echo "no [sandbox] line yet; rerun: docker logs pichat-api-1 2>&1 | grep sandbox"
echo "rollback: docker tag $ORIG $SANDBOX && $COMPOSE restart api runner"
