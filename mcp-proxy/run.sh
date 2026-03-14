#!/usr/bin/with-contenv bashio
set -euo pipefail

export PORT=9000
export DATA_DIR=/data

bashio::log.info "Starting MCP Proxy..."
cd /opt/mcp-proxy
exec node dist/index.js
