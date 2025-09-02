#!/usr/bin/env bash
set -euo pipefail

# Ensure UTF-8 in VM (also set in Dockerfile ENV)
export ELIXIR_ERL_OPTIONS="${ELIXIR_ERL_OPTIONS:-+fnu}"

# Downloads mapping (defaults to HA media share)
DOWNLOADS_DIR="${DOWNLOADS_DIR:-/media/youtube}"
mkdir -p "$DOWNLOADS_DIR"
ln -sfn "$DOWNLOADS_DIR" /downloads

# SECRET_KEY_BASE: load from /data or generate once
if [ -z "${SECRET_KEY_BASE:-}" ]; then
  if [ -f /data/secret_key_base ]; then
    export SECRET_KEY_BASE="$(cat /data/secret_key_base)"
  else
    if command -v openssl >/dev/null 2>&1; then
      SECRET="$(openssl rand -base64 64 | tr -d '\n' | head -c 64)"
    else
      SECRET="$(python3 - <<'PY'
import os, base64
print(base64.b64encode(os.urandom(64)).decode()[:64])
PY
)"
    fi
    printf "%s" "$SECRET" > /data/secret_key_base
    chmod 600 /data/secret_key_base
    export SECRET_KEY_BASE="$SECRET"
  fi
fi

# Phoenix wants this set for releases
export PHX_SERVER="${PHX_SERVER:-true}"

exec /app/bin/docker_start
