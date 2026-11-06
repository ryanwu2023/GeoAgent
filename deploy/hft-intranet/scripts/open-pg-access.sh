#!/usr/bin/env bash
# Open the GeoAgent PostgreSQL on this host to one intranet CIDR with two
# dedicated accounts; the app's own `geoagent` owner role is never handed out.
#   geo_reader  read-only: SELECT on every table, now and future, read-only sessions
#   geo_admin   member of `geoagent`: full DML/DDL on GeoAgent objects, not superuser
# Passwords are generated once into /etc/geoagent/secrets (RO_PW / ADMIN_PW);
# reruns keep them, refresh grants and replace only this script's pg_hba block.
#
# Usage (on 172.16.91.120): sudo bash open-pg-access.sh 172.16.0.0/16
set -euo pipefail
CIDR=${1:?usage: sudo bash open-pg-access.sh <employee CIDR, e.g. 172.16.0.0/16>}
[[ $CIDR =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]] || { echo "非法网段: $CIDR" >&2; exit 2; }
SECRETS=/etc/geoagent/secrets
PGDATA=${PGDATA:-/var/lib/pgsql/data}
HOST_IP=${HOST_IP:-$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)}

[ -f "$SECRETS" ] || { echo "$SECRETS 不存在，先运行 deploy-geoagent.sh" >&2; exit 1; }
grep -q '^RO_PW=' "$SECRETS" || printf 'RO_PW=%s\n' "$(openssl rand -hex 16)" >> "$SECRETS"
grep -q '^ADMIN_PW=' "$SECRETS" || printf 'ADMIN_PW=%s\n' "$(openssl rand -hex 16)" >> "$SECRETS"
chmod 600 "$SECRETS"
. "$SECRETS"

echo "== Accounts"
sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='geo_reader') THEN CREATE ROLE geo_reader LOGIN; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='geo_admin') THEN CREATE ROLE geo_admin LOGIN; END IF;
END\$\$;
ALTER ROLE geo_reader PASSWORD '${RO_PW}' NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 20;
ALTER ROLE geo_reader SET default_transaction_read_only = on;
ALTER ROLE geo_reader SET statement_timeout = '5min';
ALTER ROLE geo_admin PASSWORD '${ADMIN_PW}' NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 10;
GRANT CONNECT ON DATABASE geoagent TO geo_reader, geo_admin;
GRANT geoagent TO geo_admin;
SQL
# Grants live inside the geoagent database; default privileges cover tables
# GeoAgent creates later (they are created by the geoagent role).
sudo -u postgres psql -v ON_ERROR_STOP=1 -q -d geoagent <<'SQL'
GRANT USAGE ON SCHEMA public TO geo_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO geo_reader;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO geo_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE geoagent IN SCHEMA public GRANT SELECT ON TABLES TO geo_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE geoagent IN SCHEMA public GRANT SELECT ON SEQUENCES TO geo_reader;
SQL

echo "== Listen and pg_hba"
sudo -u postgres psql -q -c "ALTER SYSTEM SET listen_addresses = 'localhost,${HOST_IP}'"
HBA="$PGDATA/pg_hba.conf"
cp -a "$HBA" "$HBA.bak-open-pg-access"
sed -i '/^# geoagent-intranet-begin$/,/^# geoagent-intranet-end$/d' "$HBA"
cat >> "$HBA" <<HBA
# geoagent-intranet-begin
host    geoagent    geo_reader,geo_admin    ${CIDR}    scram-sha-256
# geoagent-intranet-end
HBA

echo "== Firewall"
if systemctl is-active -q firewalld; then
  # Replace any earlier rule from this script, then allow only the CIDR.
  for rule in $(firewall-cmd --permanent --list-rich-rules | grep -o 'source address="[^"]*"' | cut -d'"' -f2); do
    firewall-cmd -q --permanent --remove-rich-rule="rule family=ipv4 source address=$rule port port=5432 protocol=tcp accept" 2>/dev/null || true
  done
  firewall-cmd -q --permanent --add-rich-rule="rule family=ipv4 source address=${CIDR} port port=5432 protocol=tcp accept"
  firewall-cmd -q --reload
else
  echo "firewalld 未运行，跳过"
fi

echo "== Restart"
# listen_addresses needs a restart; GeoAgent opens a connection per request and recovers on its own.
systemctl restart postgresql
for i in $(seq 1 15); do sudo -u postgres pg_isready -q -h 127.0.0.1 && break; sleep 1; done

echo "== Verify"
ss -ltn | grep ':5432 '
PGPASSWORD=$RO_PW psql -h "$HOST_IP" -U geo_reader -d geoagent -tAc \
  "SELECT 'reader ok, tables=' || count(*) FROM information_schema.tables WHERE table_schema='public'"
if PGPASSWORD=$RO_PW psql -h "$HOST_IP" -U geo_reader -d geoagent -qc "CREATE TABLE geo_reader_probe(x int)" 2>/dev/null; then
  echo "ERROR: geo_reader can write" >&2; exit 1
fi
echo "reader write refused: ok"
PGPASSWORD=$ADMIN_PW psql -h "$HOST_IP" -U geo_admin -d geoagent -tAc "SELECT 'admin ok, snapshots=' || count(*) FROM snapshots"
echo "geoagent healthz=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/healthz)"
echo "连接: host=${HOST_IP} port=5432 dbname=geoagent  只读用户 geo_reader / 管理用户 geo_admin"
echo "密码: sudo grep -E '^(RO_PW|ADMIN_PW)=' $SECRETS"
echo "回滚: cp -a $HBA.bak-open-pg-access $HBA && sudo -u postgres psql -c \"ALTER SYSTEM RESET listen_addresses\" && systemctl restart postgresql"
