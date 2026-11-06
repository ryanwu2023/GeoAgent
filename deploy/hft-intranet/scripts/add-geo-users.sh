#!/usr/bin/env bash
# Add or reset GeoAgent web login accounts on the HFT host. The password is
# typed once at the prompt (never on the command line) and shared by all
# accounts given; it is hashed inside the GeoAgent image with SHA512-crypt.
# Usage: sudo bash add-geo-users.sh user1 [user2 ...]
#        sudo bash add-geo-users.sh --password-is-name user1 [user2 ...]
#        (each account's password equals its own name; no prompt)
set -euo pipefail
SAME_AS_NAME=false
if [ "${1:-}" = "--password-is-name" ]; then SAME_AS_NAME=true; shift; fi
[ $# -ge 1 ] || { echo "usage: sudo bash add-geo-users.sh [--password-is-name] user1 [user2 ...]" >&2; exit 2; }
AUTH=/data/geoagent/auth/auth
IMG=$(podman inspect geoagent --format '{{.ImageName}}')
if [ "$SAME_AS_NAME" = false ]; then
  read -rsp "新账号的密码: " PW; echo
  read -rsp "再输入一次: " PW2; echo
  [ "$PW" = "$PW2" ] && [ -n "$PW" ] || { echo "两次输入不一致或为空" >&2; exit 1; }
fi
for NAME in "$@"; do
  [[ $NAME =~ ^[A-Za-z0-9_.@-]{1,64}$ ]] || { echo "非法账号名: $NAME" >&2; exit 1; }
  [ "$SAME_AS_NAME" = false ] || PW=$NAME
  HASH=$(podman run --rm --network none -e P="$PW" "$IMG" python -c "import os;from passlib.hash import sha512_crypt;print(sha512_crypt.hash(os.environ['P']))")
  { grep -v "^${NAME}:" "$AUTH" || true; printf '%s:%s\n' "$NAME" "$HASH"; } > "$AUTH.new"
  chmod 644 "$AUTH.new" && mv "$AUTH.new" "$AUTH"
  echo "已添加/更新账号: $NAME"
done
unset PW PW2 2>/dev/null || true
echo "账号文件即时生效，无需重启。当前账号: $(cut -d: -f1 "$AUTH" | paste -sd, -)"
