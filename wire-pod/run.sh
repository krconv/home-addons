#!/usr/bin/env bash
set -euo pipefail

OPTIONS=/data/options.json

export WIREPOD_STT_SERVICE="$(jq -r '.stt_service' "${OPTIONS}")"
export WIREPOD_STT_LANGUAGE="$(jq -r '.stt_language' "${OPTIONS}")"
export WIREPOD_DEBUG_LOGGING="$(jq -r '.debug_logging' "${OPTIONS}")"
export WIREPOD_USE_INBUILT_BLE="$(jq -r '.use_inbuilt_ble' "${OPTIONS}")"

PICOVOICE_APIKEY="$(jq -r '.picovoice_apikey // empty' "${OPTIONS}")"
if [ -n "${PICOVOICE_APIKEY}" ]; then
    export WIREPOD_PICOVOICE_APIKEY="${PICOVOICE_APIKEY}"
fi

# These get baked into /data/chipper/source.sh by entrypoint.sh (the first
# arg below) before start.sh sources it, so they must be set before we exec.
exec "$@"
