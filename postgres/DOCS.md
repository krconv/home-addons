# PostgreSQL 17.7

This add-on runs PostgreSQL 17.7 on the Home Assistant Supervisor network, with data stored under `/data/pgdata` and configuration managed through add-on options.

## Configuration

- `postgres_password` (required): Password for the `postgres` superuser.

## Ports

- `5432/tcp`: PostgreSQL. The host port is disabled by default. Enable or map it in the add-on UI if you need host/LAN access.

## Usage

- Data is stored in `/data/pgdata` and persists across restarts and updates.
- The add-on updates the `postgres` password from the config when it changes.
- The admin user is `postgres`.
- The add-on manages `pg_hba.conf` on startup (scram for all hosts, peer for local `postgres`).
- `listen_addresses` is set to `*`; use a firewall if you expose the port to your LAN.
- PostGIS is installed but not enabled by default. Enable it per database:

```sql
CREATE EXTENSION postgis;
CREATE EXTENSION postgis_topology;
```
