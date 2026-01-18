#!/usr/bin/with-contenv bashio
set -euo pipefail

BASE_DIR="/etc/freeradius/3.0"
RADIUS_DIR="/data/freeradius"

OPENWISP_URL="$(bashio::config 'openwisp_url' || true)"
OPENWISP_ORG_UUID="$(bashio::config 'openwisp_org_uuid' || true)"
OPENWISP_RADIUS_TOKEN="$(bashio::config 'openwisp_radius_token' || true)"
CLIENTS_STREAM="$(bashio::config 'clients' || true)"

if [ -z "$OPENWISP_URL" ] || [ "$OPENWISP_URL" = "null" ]; then
  bashio::log.fatal "openwisp_url is required"
  exit 1
fi

if [ -z "$OPENWISP_ORG_UUID" ] || [ "$OPENWISP_ORG_UUID" = "null" ]; then
  bashio::log.fatal "openwisp_org_uuid is required"
  exit 1
fi

if [ -z "$OPENWISP_RADIUS_TOKEN" ] || [ "$OPENWISP_RADIUS_TOKEN" = "null" ]; then
  bashio::log.fatal "openwisp_radius_token is required"
  exit 1
fi

if [ -z "$CLIENTS_STREAM" ] || [ "$CLIENTS_STREAM" = "null" ]; then
  bashio::log.fatal "clients are required"
  exit 1
fi

client_count="$(printf "%s\n" "$CLIENTS_STREAM" | jq -s 'length')"
if [ "$client_count" -eq 0 ]; then
  bashio::log.fatal "clients list cannot be empty"
  exit 1
fi

bashio::log.debug "OpenWISP URL: ${OPENWISP_URL}"
bashio::log.debug "OpenWISP Org UUID: ${OPENWISP_ORG_UUID}"
bashio::log.debug "Configured clients: ${client_count}"

init_config() {
  if [ -d "$RADIUS_DIR" ]; then
    return
  fi

  bashio::log.info "Initializing FreeRADIUS configuration..."
  mkdir -p /data
  cp -a "$BASE_DIR" "$RADIUS_DIR"
}

ensure_dirs() {
  mkdir -p "$RADIUS_DIR/mods-enabled" "$RADIUS_DIR/sites-enabled"
}

escape_value() {
  printf "%s" "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

write_clients() {
  local clients_conf
  local index

  clients_conf="$RADIUS_DIR/clients.conf"
  index=0

  {
    echo "# Managed by the Home Assistant FreeRADIUS add-on"
    echo
  } > "$clients_conf"

  while IFS= read -r row; do
    index=$((index + 1))
    name="$(printf "%s" "$row" | base64 -d | jq -r '.name')"
    ipaddr="$(printf "%s" "$row" | base64 -d | jq -r '.ipaddr')"
    secret="$(printf "%s" "$row" | base64 -d | jq -r '.secret')"

    if [ -z "$name" ] || [ "$name" = "null" ]; then
      bashio::log.fatal "clients[$index].name is required"
      exit 1
    fi
    if [ -z "$ipaddr" ] || [ "$ipaddr" = "null" ]; then
      bashio::log.fatal "clients[$index].ipaddr is required"
      exit 1
    fi
    if [ -z "$secret" ] || [ "$secret" = "null" ]; then
      bashio::log.fatal "clients[$index].secret is required"
      exit 1
    fi

    client_id="$(printf "%s" "$name" | tr -c 'A-Za-z0-9_-' '_')"
    if [ -z "$client_id" ]; then
      client_id="client_${index}"
    fi

    name_escaped="$(escape_value "$name")"
    secret_escaped="$(escape_value "$secret")"

    bashio::log.debug "Client ${index}: name=${name} ipaddr=${ipaddr}"

    cat >> "$clients_conf" <<EOF
client ${client_id} {
  ipaddr = ${ipaddr}
  secret = "${secret_escaped}"
  shortname = "${name_escaped}"
}

EOF
  done < <(printf "%s\n" "$CLIENTS_STREAM" | jq -c '.' | jq -r '@base64')
}

write_rest_module() {
  cat > "$RADIUS_DIR/mods-enabled/rest" <<EOF
# Managed by the Home Assistant FreeRADIUS add-on

connect_uri = "${OPENWISP_URL}"

authorize {
    uri = "${OPENWISP_URL}/api/v1/freeradius/authorize/"
    method = 'post'
    body = 'json'
    data = '{"username": "%{User-Name}", "password": "%{User-Password}", "called_station_id": "%{Called-Station-ID}", "calling_station_id": "%{Calling-Station-ID}"}'
}

# this section can be left empty
authenticate {}

post-auth {
    uri = "${OPENWISP_URL}/api/v1/freeradius/postauth/"
    method = 'post'
    body = 'json'
    data = '{"username": "%{User-Name}", "password": "%{User-Password}", "reply": "%{reply:Packet-Type}", "called_station_id": "%{Called-Station-ID}", "calling_station_id": "%{Calling-Station-ID}"}'
}

accounting {
    uri = "${OPENWISP_URL}/api/v1/freeradius/accounting/"
    method = 'post'
    body = 'json'
    data = '{"status_type": "%{Acct-Status-Type}", "session_id": "%{Acct-Session-Id}", "unique_id": "%{Acct-Unique-Session-Id}", "username": "%{User-Name}", "realm": "%{Realm}", "nas_ip_address": "%{NAS-IP-Address}", "nas_port_id": "%{NAS-Port}", "nas_port_type": "%{NAS-Port-Type}", "session_time": "%{Acct-Session-Time}", "authentication": "%{Acct-Authentic}", "input_octets": "%{Acct-Input-Octets}", "output_octets": "%{Acct-Output-Octets}", "called_station_id": "%{Called-Station-Id}", "calling_station_id": "%{Calling-Station-Id}", "terminate_cause": "%{Acct-Terminate-Cause}", "service_type": "%{Service-Type}", "framed_protocol": "%{Framed-Protocol}", "framed_ip_address": "%{Framed-IP-Address}"}'
}
EOF
}

write_site_default() {
  cat > "$RADIUS_DIR/sites-enabled/default" <<EOF
# Managed by the Home Assistant FreeRADIUS add-on

server default {
  listen {
    type = auth
    ipaddr = *
    port = 1812
  }

  listen {
    type = acct
    ipaddr = *
    port = 1813
  }

  authorize {
    update control {
      &REST-HTTP-Header += "Authorization: Bearer ${OPENWISP_ORG_UUID} ${OPENWISP_RADIUS_TOKEN}"
    }
    rest
  }

  authenticate {}

  post-auth {
    update control {
      &REST-HTTP-Header += "Authorization: Bearer ${OPENWISP_ORG_UUID} ${OPENWISP_RADIUS_TOKEN}"
    }
    rest

    Post-Auth-Type REJECT {
      update control {
        &REST-HTTP-Header += "Authorization: Bearer ${OPENWISP_ORG_UUID} ${OPENWISP_RADIUS_TOKEN}"
      }
      rest
    }
  }

  preacct {
    acct_unique
  }

  accounting {
    update control {
      &REST-HTTP-Header += "Authorization: Bearer ${OPENWISP_ORG_UUID} ${OPENWISP_RADIUS_TOKEN}"
    }
    rest
  }
}
EOF
}

init_config
ensure_dirs
write_clients
write_rest_module
write_site_default

bashio::log.info "Starting FreeRADIUS..."
exec freeradius -f -l stdout -d "$RADIUS_DIR"
