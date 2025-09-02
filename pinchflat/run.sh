#!/usr/bin/env bash
set -euo pipefail

# Allow override; default to HA's media share
DOWNLOADS_DIR="${DOWNLOADS_DIR:-/media/YouTube}"
mkdir -p "$DOWNLOADS_DIR"
ln -sfn "$DOWNLOADS_DIR" /downloads

exec /app/bin/docker_start
