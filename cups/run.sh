#!/usr/bin/with-contenv bashio

cupsd -f

cat /var/log/cups/error_log