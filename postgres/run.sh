#!/usr/bin/with-contenv bashio
set -euo pipefail

PG_VERSION="17"
PG_BIN="/usr/lib/postgresql/${PG_VERSION}/bin"
PGDATA="/data/pgdata"
HASS_CONF="/data/postgresql.hass.conf"
PASSWORD_FILE="/data/.postgres_password"

POSTGRES_PASSWORD="$(bashio::config 'postgres_password' || true)"
if [ -z "$POSTGRES_PASSWORD" ] || [ "$POSTGRES_PASSWORD" = "null" ]; then
  bashio::log.fatal "postgres_password is required"
  exit 1
fi

run_as_postgres() {
  gosu postgres "$@"
}

exec_as_postgres() {
  exec gosu postgres "$@"
}

write_configs() {
  if ! grep -Fq "include_if_exists = '${HASS_CONF}'" "$PGDATA/postgresql.conf"; then
    echo "include_if_exists = '${HASS_CONF}'" >> "$PGDATA/postgresql.conf"
  fi

  cat > "$HASS_CONF" <<'EOCONF'
listen_addresses = '*'
password_encryption = 'scram-sha-256'
EOCONF
  chown postgres:postgres "$HASS_CONF"
  chmod 0644 "$HASS_CONF"

  cat > "$PGDATA/pg_hba.conf" <<'EOHBA'
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             postgres                                peer
local   all             all                                     scram-sha-256
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256
host    all             all             0.0.0.0/0               scram-sha-256
host    all             all             ::/0                    scram-sha-256
EOHBA
  chown postgres:postgres "$PGDATA/pg_hba.conf"
  chmod 0600 "$PGDATA/pg_hba.conf"
}

store_password() {
  printf "%s" "$POSTGRES_PASSWORD" > "$PASSWORD_FILE"
  chmod 0600 "$PASSWORD_FILE"
  chown postgres:postgres "$PASSWORD_FILE"
}

update_password() {
  local sql_file
  local sql_password

  sql_password="$(printf "%s" "$POSTGRES_PASSWORD" | sed "s/'/''/g")"
  sql_file="$(mktemp -p /data)"
  chmod 0600 "$sql_file"
  printf "ALTER USER postgres WITH PASSWORD '%s';\n" "$sql_password" > "$sql_file"
  chown postgres:postgres "$sql_file"
  run_as_postgres "$PG_BIN/psql" -v ON_ERROR_STOP=1 -f "$sql_file"
  rm -f "$sql_file"
}

mkdir -p /data
if [ ! -d "$PGDATA" ]; then
  install -d -m 0700 -o postgres -g postgres "$PGDATA"
fi

initialized="false"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  bashio::log.info "Initializing PostgreSQL data directory..."
  pwfile="$(mktemp -p /data)"
  chmod 0600 "$pwfile"
  printf "%s" "$POSTGRES_PASSWORD" > "$pwfile"
  chown postgres:postgres "$pwfile"
  run_as_postgres "$PG_BIN/initdb" -D "$PGDATA" --username=postgres --pwfile="$pwfile" \
    --auth-host=scram-sha-256 --auth-local=peer --encoding=UTF8 --locale=C.UTF-8
  rm -f "$pwfile"
  initialized="true"
fi

write_configs

if [ "$initialized" = "true" ]; then
  store_password
else
  stored_password=""
  if [ -f "$PASSWORD_FILE" ]; then
    stored_password="$(cat "$PASSWORD_FILE")"
  fi
  if [ "$stored_password" != "$POSTGRES_PASSWORD" ]; then
    bashio::log.info "Updating postgres password..."
    run_as_postgres "$PG_BIN/pg_ctl" -D "$PGDATA" -o "-c listen_addresses=" -w start
    update_password
    run_as_postgres "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast -w stop
    store_password
  fi
fi

bashio::log.info "Starting PostgreSQL..."
exec_as_postgres "$PG_BIN/postgres" -D "$PGDATA"
