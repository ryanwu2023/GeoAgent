#!/usr/bin/env bash
# Turn htpasswd-file accounts into ordinary GeoAgent users (stored in PostgreSQL
# and mirrored to PiChat). Accounts left in the file stay administrators.
# The new user's password is its own name (the HFT accounts already use that).
# Run after upgrading to a build with user management:
#   sudo bash migrate-file-users.sh user1 [user2 ...]
# The administrator password is typed once at the prompt.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: sudo bash migrate-file-users.sh user1 [user2 ...]" >&2; exit 2; }
AUTH=${AUTH:-/data/geoagent/auth/auth}
BASE=${BASE:-http://127.0.0.1:8000}
ORIGIN=$(sed -n 's/^GEOAGENT_ALLOWED_ORIGINS=//p' "${ENV_FILE:-/etc/geoagent/geoagent.env}" 2>/dev/null | cut -d, -f1 || true)
ORIGIN=${ORIGIN:-$BASE}
ADMIN=${ADMIN:-admin}
for NAME in "$@"; do
  [ "$NAME" != "$ADMIN" ] || { echo "不能迁移管理员 $ADMIN" >&2; exit 1; }
  grep -q "^${NAME}:" "$AUTH" || { echo "账号文件里没有 $NAME" >&2; exit 1; }
done
grep -q "^${ADMIN}:" "$AUTH" || { echo "账号文件里没有管理员 $ADMIN" >&2; exit 1; }

BACKUP="$AUTH.bak-$(date +%Y%m%d%H%M%S)"
cp -p "$AUTH" "$BACKUP"
JAR=$(mktemp); trap 'rm -f "$JAR"' EXIT
read -rsp "管理员 $ADMIN 的密码: " PW; echo
HDR=(-H 'Content-Type: application/json' -H 'X-GeoAgent-Login: 1' -H "Origin: $ORIGIN")
# The hosts have no python3; escape for JSON in bash. The password goes to curl
# on stdin, never on a command line.
json_escape() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '%s' "$s"; }
LOGIN=$(printf '{"username":"%s","password":"%s"}' "$(json_escape "$ADMIN")" "$(json_escape "$PW")")
unset PW
CODE=$(curl -sS -o /dev/null -w '%{http_code}' -c "$JAR" "${HDR[@]}" "$BASE/api/auth/login" --data-binary @- <<<"$LOGIN")
unset LOGIN
[ "$CODE" = 200 ] || { echo "管理员登录失败（HTTP $CODE）" >&2; exit 1; }

FAILED=()
for NAME in "$@"; do
  LINE=$(grep "^${NAME}:" "$AUTH")
  # The file name blocks creation, so remove it first and restore it on failure.
  grep -v "^${NAME}:" "$AUTH" > "$AUTH.new"; chmod 644 "$AUTH.new"; mv "$AUTH.new" "$AUTH"
  OUT=$(curl -sS -w '\n%{http_code}' -b "$JAR" "${HDR[@]}" "$BASE/api/auth/users" -d "{\"username\":\"$NAME\"}")
  if [ "$(tail -n1 <<<"$OUT")" = 201 ]; then
    echo "已迁移 $NAME：$(head -n1 <<<"$OUT")"
  else
    printf '%s\n' "$LINE" >> "$AUTH"
    echo "迁移 $NAME 失败，已恢复为文件账号：$(head -n1 <<<"$OUT")" >&2
    FAILED+=("$NAME")
  fi
done
echo "账号文件备份：$BACKUP；当前管理员：$(cut -d: -f1 "$AUTH" | paste -sd, -)"
[ ${#FAILED[@]} -eq 0 ] || { echo "未迁移：${FAILED[*]}" >&2; exit 1; }
