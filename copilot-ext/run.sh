#!/usr/bin/with-contenv bashio
set -euo pipefail

# ── Read configuration ──
FIREBASE_API_KEY="$(bashio::config 'firebase_api_key')"
FIREBASE_REFRESH_TOKEN="$(bashio::config 'firebase_refresh_token')"
ANTHROPIC_API_KEY="$(bashio::config 'anthropic_api_key')"
DATABASE_URL="$(bashio::config 'database_url')"
LLM_MODEL="$(bashio::config 'llm_model')"
MCP_SERVER_URL="$(bashio::config 'mcp_server_url')"
DRY_RUN="$(bashio::config 'dry_run')"
SLEEP_BETWEEN_AGENT_RUNS="$(bashio::config 'sleep_between_agent_runs' || true)"
DD_LLMOBS_ENABLED="$(bashio::config 'dd_llmobs_enabled')"
DD_LLMOBS_ML_APP="$(bashio::config 'dd_llmobs_ml_app')"
DD_API_KEY="$(bashio::config 'dd_api_key')"
DD_SITE="$(bashio::config 'dd_site')"
DD_LLMOBS_AGENTLESS_ENABLED="$(bashio::config 'dd_llmobs_agentless_enabled')"

# ── Validate required fields ──
for field in firebase_api_key firebase_refresh_token anthropic_api_key database_url; do
  val="$(bashio::config "$field")"
  if [ -z "$val" ] || [ "$val" = "null" ]; then
    bashio::log.fatal "${field} is required"
    exit 1
  fi
done

# ── Export environment for the Node.js app ──
export PORT=3000
export FIREBASE_API_KEY
export FIREBASE_REFRESH_TOKEN
export ANTHROPIC_API_KEY
export DATABASE_URL
export LLM_MODEL
export MCP_SERVER_URL
export DRY_RUN

if [ -n "$SLEEP_BETWEEN_AGENT_RUNS" ] && [ "$SLEEP_BETWEEN_AGENT_RUNS" != "null" ]; then
  export SLEEP_BETWEEN_AGENT_RUNS
fi

# ── Datadog LLM Observability (only initialized when enabled by the app) ──
export DD_LLMOBS_ENABLED
export DD_LLMOBS_ML_APP
export DD_API_KEY
export DD_SITE
export DD_LLMOBS_AGENTLESS_ENABLED

bashio::log.info "Starting Copilot Ext server..."
cd /opt/copilot-ext
exec node --import dd-trace/register.js dist/server.js
