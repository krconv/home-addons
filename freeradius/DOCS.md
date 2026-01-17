# FreeRADIUS 3.2.1

This add-on runs FreeRADIUS with the REST module configured to call OpenWISP's RADIUS API endpoints.

## Configuration

- `openwisp_url` (required): Base URL for OpenWISP (for example, `http://openwisp:80`).
- `openwisp_org_uuid` (required): OpenWISP organization UUID for the RADIUS API.
- `openwisp_radius_token` (required): OpenWISP RADIUS API token for the organization.
- `clients` (required): List of RADIUS clients/NAS definitions.
  Each entry must include:
  - `name`: A friendly name for the client.
  - `ipaddr`: Client IP address or subnet (for example, `192.0.2.10` or `192.0.2.0/24`).
  - `secret`: Shared secret used by the NAS device.

## Ports

- `1812/udp`: RADIUS auth (default 1812).
- `1813/udp`: RADIUS accounting (default 1813).
- `3799/udp`: RADIUS CoA (default 3799).
- `18120/udp`: RADIUS status (default 18120).

All host ports are disabled by default. Enable or map them in the add-on UI if you need LAN access.

## Usage

- Configure OpenWISP to allow this FreeRADIUS host under `freeradius_allowed_hosts` in the OpenWISP add-on.
- Add your NAS devices under `clients` so they can authenticate with the server.
- VLAN assignment and PSK-per-MAC are configured in OpenWISP and enforced by your NAS devices; this add-on only relays the RADIUS requests to OpenWISP.

## Not implemented (and what would be needed)

- HTTPS/TLS between FreeRADIUS and OpenWISP: add config options for HTTPS, map `/ssl`, and provide a REST `tls` block (CA bundle, client cert/key if needed).
- Automatic LetsEncrypt add-on integration: define which `/ssl` cert/key to use and ensure the REST module is configured to trust those certs.
- WPA-Enterprise (EAP) support: enable and configure the `eap` module plus certificates under `/etc/freeradius/3.0/certs` (or a persisted equivalent).
- Captive portal flows: add portal-specific policies/config or device-side integration beyond the REST module wiring.
