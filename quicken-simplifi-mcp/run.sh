#!/usr/bin/with-contenv bashio
set -euo pipefail

DATA_DIR="/data/simplifi-mcp"
mkdir -p "$DATA_DIR"

# ── Read configuration ──
PUBLIC_BASE_URL="$(bashio::config 'public_base_url')"
SIMPLIFI_EMAIL="$(bashio::config 'simplifi_email')"
SIMPLIFI_PASSWORD="$(bashio::config 'simplifi_password')"
SIMPLIFI_DATASET_ID="$(bashio::config 'simplifi_dataset_id')"
SIMPLIFI_THREAT_METRIX_SESSION_ID="$(bashio::config 'simplifi_threat_metrix_session_id')"
OAUTH_LOGIN_USERNAME="$(bashio::config 'oauth_login_username')"
OAUTH_LOGIN_PASSWORD="$(bashio::config 'oauth_login_password')"
OAUTH_JWT_SECRET="$(bashio::config 'oauth_jwt_secret')"
OAUTH_ALLOWED_REDIRECT_URIS="$(bashio::config 'oauth_allowed_redirect_uris' || true)"

# ── Validate required fields ──
for field in public_base_url simplifi_email simplifi_password simplifi_dataset_id simplifi_threat_metrix_session_id oauth_login_password oauth_jwt_secret; do
  val="$(bashio::config "$field")"
  if [ -z "$val" ] || [ "$val" = "null" ]; then
    bashio::log.fatal "${field} is required"
    exit 1
  fi
done

# ── Export environment for the Node.js app ──
export PORT=8788
export HOST=127.0.0.1
export PUBLIC_BASE_URL
export CORS_ORIGIN="*"
export CACHE_DB_PATH="${DATA_DIR}/cache.sqlite"

export OAUTH_ISSUER="${PUBLIC_BASE_URL}"
export OAUTH_AUDIENCE="simplifi-mcp"
export OAUTH_JWT_SECRET
export OAUTH_ACCESS_TOKEN_TTL_SECONDS=900
export OAUTH_REFRESH_TOKEN_TTL_SECONDS=2592000
export OAUTH_LOGIN_USERNAME
export OAUTH_LOGIN_PASSWORD
if [ -n "$OAUTH_ALLOWED_REDIRECT_URIS" ] && [ "$OAUTH_ALLOWED_REDIRECT_URIS" != "null" ]; then
  export OAUTH_ALLOWED_REDIRECT_URIS
fi

export SIMPLIFI_EMAIL
export SIMPLIFI_PASSWORD
export SIMPLIFI_DATASET_ID
export SIMPLIFI_THREAT_METRIX_SESSION_ID

bashio::log.info "Starting nginx..."
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/simplifi-mcp
nginx &

bashio::log.info "Starting Quicken Simplifi MCP server..."
cd /opt/simplifi-mcp
exec node dist/index.js
