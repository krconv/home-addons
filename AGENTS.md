# AGENTS.md

## Repository Overview

This is a Home Assistant add-on repository (`home-addons`) maintained by krconv. It contains multiple add-ons, each in its own directory. The repo is identified by `repository.yaml` at the root.

## Repo Structure

```
home-addons/
  repository.yaml          # Repo manifest (name, maintainer, URL)
  <addon-name>/
    config.yaml            # Add-on manifest (required)
    Dockerfile             # Container build (required)
    run.sh                 # Entrypoint script (common pattern)
    build.yaml             # Per-arch base images (optional)
    rootfs/                # Files copied into the container
    translations/en.yaml   # UI translations (optional)
    CHANGELOG.md
    DOCS.md
    README.md
    icon.png / logo.png
  .github/workflows/
    build-images.yaml      # CI: builds and pushes images to GHCR
```

Each add-on is a self-contained Docker image managed by the Home Assistant Supervisor.

## Add-on Development Rules

### config.yaml

- **Required keys:** `name`, `version`, `slug`, `description`, `arch`.
- **Version format:** `x.y.z` where `x.y` matches upstream software version and `z` is the add-on patch release.
- Do not mention version numbers in `README.md` or `DOCS.md`.
- Use `image: ghcr.io/krconv/{arch}-addon-<slug>` for pre-built images. The `{arch}` placeholder is substituted at install time.
- Include all supported architectures (typically `aarch64`, `amd64`, `armv7`, `armhf`; sometimes `i386`).
- Include default port numbers in `ports_description` (e.g., `PostgreSQL (default 5432)`).
- Do not use `config.yaml` for anything other than the add-on manifest; Supervisor searches for it recursively.

### Dockerfile

- Use `ARG BUILD_FROM` then `FROM $BUILD_FROM` so the HA build system can swap in the correct architecture base image.
- If not using the build system, include HA labels: `io.hass.version`, `io.hass.type=addon`, `io.hass.arch`.
- Build args available by default: `BUILD_FROM`, `BUILD_VERSION`, `BUILD_ARCH`.
- Multi-stage builds are fine (see `mcp-proxy/Dockerfile` for an example).

### Persistence and Configuration

- `/data` is the persistent volume inside the container.
- `/data/options.json` contains user-configured options.
- Use bashio to read options: `bashio::config 'option_name'`.
- Never store data outside `/data/` — it won't persist across restarts.
- Never hardcode paths; use bashio helpers.

### Networking

- Expose ports only if needed; prefer ingress for web UIs.
- For ingress, restrict access to `172.30.32.2` only.
- Add-on DNS hostnames replace `_` with `-` in the slug.

### APIs

- Core API: `http://supervisor/core/api/` with `Authorization: Bearer ${SUPERVISOR_TOKEN}`. Requires `homeassistant_api: true`.
- Supervisor API: `http://supervisor/` with same token. Requires `hassio_api: true`.

### Security

- Avoid host networking unless truly needed.
- Mount folders read-only unless write is required.
- Don't request API permissions you don't need.
- Don't run services as root unless absolutely necessary.
- Implement graceful shutdown on SIGTERM.

## Home Assistant Best Practices

When writing or modifying automations, templates, or configuration that interacts with Home Assistant:

### Prefer Native Constructs Over Templates

Templates bypass validation and fail silently at runtime. Always check for native alternatives first:

| Instead of | Use |
|---|---|
| `{{ states('x') \| float > 25 }}` | `numeric_state` condition with `above: 25` |
| `{{ is_state('x', 'on') and is_state('y', 'on') }}` | `condition: and` with state conditions |
| `{{ now().hour >= 9 }}` | `condition: time` with `after: "09:00:00"` |
| `wait_template: "{{ is_state(...) }}"` | `wait_for_trigger` with state trigger |
| Template sensor for sum/mean | `min_max` helper |
| Template binary sensor with threshold | `threshold` helper |

### Automation Modes

| Scenario | Mode |
|----------|------|
| Motion light with timeout | `restart` |
| Sequential processing (door locks) | `queued` |
| Independent per-entity actions | `parallel` |
| One-shot notifications | `single` |

### Entity References

- Use `entity_id` over `device_id` — `device_id` breaks when devices are re-added.
- Exception: Zigbee2MQTT autodiscovered device triggers are acceptable.
- For ZHA buttons/remotes, use `event` trigger with `device_ieee` (persistent).

### Built-in Helpers Over Template Sensors

Before creating a template sensor, check if a built-in helper exists:
- Sum/average multiple sensors: `min_max` integration
- Binary any-on/all-on logic: `group` helper
- Rate of change: `derivative` integration
- Cross-threshold detection: `threshold` integration
- Consumption tracking: `utility_meter` helper
