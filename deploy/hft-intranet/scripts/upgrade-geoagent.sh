#!/usr/bin/env bash
# Image-only GeoAgent upgrade on the HFT host: keeps /etc/geoagent/geoagent.env
# (remote-collection key), the auth file, data and the PostgreSQL database.
# Usage: sudo IMG_REF=geoagent:hft-<sha> bash upgrade-geoagent.sh /tmp/geoagent-hft-<sha>.tar.gz
set -euo pipefail
TARBALL=${1:?image tarball path}
IMG_REF=${IMG_REF:?set IMG_REF=geoagent:<tag>}
UNIT=/etc/containers/systemd/geoagent.container
[ -f "$UNIT" ] || { echo "$UNIT 不存在：首次部署请用 deploy-geoagent.sh" >&2; exit 1; }

# podman load prefixes names differently (docker.io/library/ or localhost/); match on name:tag.
resolve_image() { podman images --format '{{.Repository}}:{{.Tag}}' | grep -E "(^|/)$1\$" | head -1; }
[ -n "$(resolve_image "$IMG_REF")" ] || podman load -i "$TARBALL" | tail -1
IMG=$(resolve_image "$IMG_REF")
if [ -z "$IMG" ]; then echo "image $IMG_REF not found; loaded images:" >&2; podman images >&2; exit 1; fi

OLD=$(sed -n 's/^Image=//p' "$UNIT")
cp -p "$UNIT" "$UNIT.bak-$(date +%Y%m%d%H%M%S)"
sed -i "s|^Image=.*|Image=${IMG}|" "$UNIT"
# The macro collector runs the same image; keep it in step when it is installed.
MACRO_UNIT=/etc/containers/systemd/geoagent-macro.container
[ -f "$MACRO_UNIT" ] && sed -i "s|^Image=.*|Image=${IMG}|" "$MACRO_UNIT"
systemctl daemon-reload
systemctl restart geoagent
[ -f "$MACRO_UNIT" ] && systemctl restart geoagent-macro
echo "== $OLD -> $IMG，等待就绪"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8000/readyz; then
    echo "GeoAgent 已就绪"
    echo "回滚：sudo sed -i 's|^Image=.*|Image=${OLD}|' $UNIT && sudo systemctl daemon-reload && sudo systemctl restart geoagent"
    exit 0
  fi
  sleep 2
done
echo "120 秒内未就绪，最近日志：" >&2
journalctl -u geoagent -n 60 --no-pager >&2
echo "回滚：sudo sed -i 's|^Image=.*|Image=${OLD}|' $UNIT && sudo systemctl daemon-reload && sudo systemctl restart geoagent" >&2
exit 1
