# Redis 8.4.0

This add-on runs Redis 8.4.0 on the Home Assistant Supervisor network, with data stored under `/data` and configuration managed through add-on options.

## Configuration

- `redis_password` (required): Password for the `redis` user.
- `appendonly`: Enable AOF persistence. Defaults to true.
- `save`: List of RDB snapshot intervals in `"seconds changes"` format (for example, `"900 1"`).
  Use an empty list to disable RDB snapshots.

## Ports

- `6379/tcp`: Redis. The host port is disabled by default. Enable or map it in the add-on UI if you need host/LAN access.

## Usage

- Data is stored in `/data` and persists across restarts and updates.
- The default user is `redis`.
- AOF persistence is enabled by default; adjust it and the RDB save schedule via the add-on options.
- Connect with ACL user auth:

```bash
redis-cli -u redis://redis:<password>@<host>:6379
```
