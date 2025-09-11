#!/usr/bin/env bash
set -e

# Log startup
echo "Starting Scripts add-on..."

# Change to app directory
cd /app

# Start the Python application using Poetry-managed venv
exec /app/.venv/bin/python -m src.main
