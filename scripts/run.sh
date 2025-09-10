#!/usr/bin/env bash
set -e

# Log startup
echo "Starting Scripts add-on..."

# Change to app directory
cd /app

# Start the Python application
exec python3 -m src.main