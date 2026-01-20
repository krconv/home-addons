# OpenWISP

This add-on runs OpenWISP on the Home Assistant Supervisor network and requires external PostgreSQL and Redis services.

## Configuration

- `admin_email` (required): Admin user email.
- `admin_password` (required): Admin user password.
- `public_domain`: Public domain used for OpenWISP. Defaults to `localhost`.
- `host_allowlist`: Django `ALLOWED_HOSTS`. Defaults to `*`.
- `cors_allowlist`: Comma-separated list of CORS origins, or `*` to allow all. Defaults to `*`.
- `db_host` (required): PostgreSQL host.
- `db_port`: PostgreSQL port. Defaults to `5432`.
- `db_name`: PostgreSQL database name. Defaults to `openwisp`.
- `db_user`: PostgreSQL user. Defaults to `openwisp`.
- `db_password`: PostgreSQL password.
- `db_engine`: PostgreSQL engine. Defaults to `django.contrib.gis.db.backends.postgis`.
- `redis_host` (required): Redis host.
- `redis_port`: Redis port. Defaults to `6379`.
- `redis_user`: Redis user (optional).
- `redis_password`: Redis password (optional).
- `use_radius`, `use_topology`, `use_firmware`, `use_monitoring`, `metric_collection`: Feature toggles.
- `freeradius_allowed_hosts`: Comma-separated list of host IPs/subnets allowed to call the OpenWISP RADIUS API (authorize, accounting, postauth). Defaults to `127.0.0.1/32`.
- `influxdb_user`: InfluxDB user. Defaults to `admin`.
- `influxdb_pass`: InfluxDB password. Defaults to `admin`.
- `influxdb_name`: InfluxDB database name. Defaults to `openwisp`.
- `influxdb_host`: InfluxDB host. Defaults to `influxdb`.
- `influxdb_port`: InfluxDB port. Defaults to `8086`.
- `influxdb_retention_policy`: InfluxDB default retention policy. Defaults to `26280h0m0s`.
- `debug`: Enable Django debug mode. Defaults to `false`.
- `secret_key`: Optional Django secret key; auto-generated if empty.

## Ports

- `80/tcp`: OpenWISP web UI (default 80).

## Usage

- Data is stored under `/data` (`static`, `media`, `private`, `logs`, `ssh`) and persists across restarts.
- Database migrations and admin user setup run automatically on startup.
