#!/usr/bin/with-contenv bashio

ulimit -n 1048576

bashio::log.info "Starting CUPS test..."

cupsd -t

cupsd -f
cupsd -F

cat /var/log/cups/error_log