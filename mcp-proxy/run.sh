#!/usr/bin/with-contenv bashio
set -euo pipefail

export PORT=9000
export DATA_DIR=/data
export UPSTREAMS_JSON="$(jq -c '.upstreams' /data/options.json)"

bashio::log.info "Starting MCP Proxy..."
cd /opt/mcp-proxy
exec node dist/index.js
