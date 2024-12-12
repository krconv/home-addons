#!/usr/bin/with-contenv bashio

ulimit -n 1048576

bashio::log.info "Starting CUPS..."

cupsd -f

cat /var/log/cups/error_log