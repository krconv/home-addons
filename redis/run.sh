#!/usr/bin/with-contenv bashio
set -euo pipefail

CONFIG_PATH="/data/redis.conf"
ACL_PATH="/data/users.acl"
DATA_DIR="/data"

REDIS_PASSWORD="$(bashio::config 'redis_password' || true)"
if [ -z "$REDIS_PASSWORD" ] || [ "$REDIS_PASSWORD" = "null" ]; then
  bashio::log.fatal "redis_password is required"
  exit 1
fi

APPENDONLY="no"
if bashio::config.true 'appendonly'; then
  APPENDONLY="yes"
fi

SAVE_JSON="$(bashio::config 'save' || true)"
SAVE_ENTRIES=()
if [ -n "$SAVE_JSON" ] && [ "$SAVE_JSON" != "null" ]; then
  while IFS= read -r entry; do
    if [ -n "$entry" ]; then
      SAVE_ENTRIES+=("$entry")
    fi
  done < <(echo "$SAVE_JSON" | jq -r '.[]?')
fi

mkdir -p "$DATA_DIR"
chown -R redis:redis "$DATA_DIR"
chmod 0700 "$DATA_DIR"

{
  echo "user default off"
  printf "user redis on >%s ~* +@all\n" "$REDIS_PASSWORD"
} > "$ACL_PATH"
chown redis:redis "$ACL_PATH"
chmod 0600 "$ACL_PATH"

{
  echo "port 6379"
  echo "bind 0.0.0.0"
  echo "protected-mode yes"
  echo "daemonize no"
  echo "supervised no"
  echo "logfile \"\""
  echo "dir ${DATA_DIR}"
  echo "dbfilename dump.rdb"
  echo "appendonly ${APPENDONLY}"
  echo "appendfilename appendonly.aof"
  echo "aclfile ${ACL_PATH}"
  if [ "${#SAVE_ENTRIES[@]}" -eq 0 ]; then
    echo 'save ""'
  else
    for entry in "${SAVE_ENTRIES[@]}"; do
      echo "save ${entry}"
    done
  fi
} > "$CONFIG_PATH"

chown redis:redis "$CONFIG_PATH"
chmod 0644 "$CONFIG_PATH"

bashio::log.info "Starting Redis..."
exec gosu redis redis-server "$CONFIG_PATH"
