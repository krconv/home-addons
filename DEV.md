# Home Assistant Add-on Development Cheat Sheet

## Mental model

- An add-on is a container image managed by Supervisor (install/start/stop/update/backup/ingress), plus metadata + UI schema in `config.yaml`. ([Home Assistant Developer Docs][1])
- Prefer **pre-built multi-arch images** published to a registry; “local build on user’s HA box” is fine for early experiments but slower + flakier. ([Home Assistant Developer Docs][2])

---

## Repo + folder layout

### Add-on repository (multi add-on)

- Each add-on lives in its own folder.
- A repo is identified by `repository.yaml` at the repo root. ([Home Assistant Developer Docs][3])

**`repository.yaml`**

```yaml
name: "Your Repo Name"
url: "https://… (optional homepage)"
maintainer: "Your Name <you@…>"
```

(Only `name` is required.) ([Home Assistant Developer Docs][3])

### Single add-on folder skeleton

Typical structure (not every file is mandatory, but plan to have them):

```
addon_name/
  translations/
    en.yaml
  apparmor.txt
  build.yaml
  CHANGELOG.md
  config.yaml
  DOCS.md
  Dockerfile
  icon.png
  logo.png
  README.md
  run.sh
```

Translation files and `config`/`build` files can be `.json`, `.yml`, or `.yaml`. ([Home Assistant Developer Docs][4])

---

## Local dev workflow (fastest path)

### Recommended: official VS Code devcontainer

- Use the maintained devcontainer; it runs Supervisor + Home Assistant and mounts your repo’s add-ons as local add-ons. ([Home Assistant Developer Docs][5])
- You’ll reach onboarding at `http://localhost:7123/`. ([Home Assistant Developer Docs][5])

### Remote device dev (hardware access)

- Copy add-on folders into `/addons` on the HA machine (via Samba/SSH add-ons). ([Home Assistant Developer Docs][5])
- Forcing a **local build**: comment out `image:` in your add-on `config.yaml` so Supervisor builds instead of pulling. ([Home Assistant Developer Docs][5])

### Local build tooling

- If you’re not using the devcontainer, use the official build tool to build images locally. ([Home Assistant Developer Docs][5])

---

## `run.sh` + persistence + reading user config

### Key filesystem conventions inside the container

- `/data` is the persistent volume.
- `/data/options.json` contains the user’s configured options. ([Home Assistant Developer Docs][4])

### Bashio (strongly recommended)

- Base images ship with Bashio; use it to read options and common Supervisor/HA info. ([Home Assistant Developer Docs][4])
  Example pattern:

```bash
CONFIG_PATH=/data/options.json
TARGET="$(bashio::config 'target')"
```

([Home Assistant Developer Docs][4])

---

## Dockerfile basics (and what HA expects)

### Base image + multi-arch

- Standard pattern uses `ARG BUILD_FROM` then `FROM $BUILD_FROM` so HA build systems swap the correct architecture base image. ([Home Assistant Developer Docs][4])
- If you’re not using the build system/local build tooling, include HA labels:

  - `io.hass.version`
  - `io.hass.type=addon`
  - `io.hass.arch=…` ([Home Assistant Developer Docs][4])

### Build args available by default

- `BUILD_FROM`, `BUILD_VERSION`, `BUILD_ARCH` ([Home Assistant Developer Docs][4])

### Per-arch Dockerfiles

- You can suffix Dockerfiles like `Dockerfile.amd64` for architecture-specific builds. ([Home Assistant Developer Docs][4])

---

## `build.yaml` (extended build control)

Only needed when you’re not using defaults or need extra build options. ([Home Assistant Developer Docs][4])

**Supports:**

- `build_from`: per-arch base images
- `args`: extra docker build args
- `labels`: extra labels
- `codenotary` (+ signer settings) ([Home Assistant Developer Docs][4])

---

## `config.yaml` essentials (what the Supervisor reads)

### Required keys

- `name` (string)
- `version` (string) — if using `image`, must match the image tag used
- `version` should track the packaged software version; also pin upstream package versions in the `Dockerfile` so rebuilds don’t auto-upgrade.
- `slug` (string) — unique within the repository and URI-friendly
- `description` (string)
- `arch` (list) — `armhf`, `armv7`, `aarch64`, `amd64`, `i386` ([Home Assistant Developer Docs][4])

**Gotcha:** don’t use `config.yaml` for anything else in your repo; Supervisor searches recursively for it. ([Home Assistant Developer Docs][4])

### Commonly-used optional keys (high signal)

- **Where/when it runs**

  - `machine` (limit supported machine types; `!` to negate) ([Home Assistant Developer Docs][4])
  - `startup` (`initialize`, `system`, `services`, `application`, `once`) ([Home Assistant Developer Docs][4])
  - `boot` (`auto`, `manual`, `manual_only`) ([Home Assistant Developer Docs][4])
  - `homeassistant` (pin minimum HA Core version) ([Home Assistant Developer Docs][4])

- **Networking / UI**

  - `ports`: expose ports (`"container-port/type": host-port`, host `null` disables mapping). Include the default port number in `ports_description` (e.g., `PostgreSQL (default 5432)`). ([Home Assistant Developer Docs][4])
  - `webui`: templated URL like `http://[HOST]:[PORT:2839]/…` (supports `[PROTO:option_name]` selector) ([Home Assistant Developer Docs][4])
  - Ingress (store-friendly UI embedding): set `ingress: true`; if not using port `8099`, set `ingress_port`. Also restrict access to `172.30.32.2` only. ([Home Assistant Developer Docs][6])

- **Mounting HA folders into the container**

  - `map`: bind-mount HA-managed directories (defaults read-only; can set `read_only: false`; optional `path` override). Types include `homeassistant_config`, `addon_config`, `ssl`, `addons`, `backup`, `share`, `media`, `all_addon_configs`, `data`. ([Home Assistant Developer Docs][4])

- **API access**

  - `homeassistant_api: true` enables Core API via `http://supervisor/core/api/` using `SUPERVISOR_TOKEN` bearer. ([Home Assistant Developer Docs][7])
  - `hassio_api: true` enables Supervisor API via `http://supervisor/` using `SUPERVISOR_TOKEN` bearer; may require `hassio_role`. ([Home Assistant Developer Docs][7])
  - `hassio_role` supports role-based access (default/homeassistant/backup/manager/admin). ([Home Assistant Developer Docs][4])

- **Hardware / “this can break the host” knobs**

  - `devices`: map device nodes like `/dev/ttyAMA0` ([Home Assistant Developer Docs][4])
  - `privileged` (capability list), `full_access` (broad hardware access) ([Home Assistant Developer Docs][4])
  - `docker_api` (read-only Docker API; only for non-protected add-ons) ([Home Assistant Developer Docs][4])
  - `host_pid` (host PID namespace; warning about S6 overlay) ([Home Assistant Developer Docs][4])
  - `apparmor` (enable/disable or custom profile selection) ([Home Assistant Developer Docs][4])

- **Operational**

  - `backup` hot/cold + pre/post hooks + exclude list ([Home Assistant Developer Docs][4])
  - `watchdog` supports HTTP or `tcp://` health checks ([Home Assistant Developer Docs][4])
  - `stage` (`stable`, `experimental`, `deprecated`) + `advanced: true` gating in the store ([Home Assistant Developer Docs][4])
  - `journald` maps host journal read-only into the container ([Home Assistant Developer Docs][4])
  - `breaking_versions` forces manual update across breaking bumps ([Home Assistant Developer Docs][4])

---

## Options + Schema (UI config + validation)

### The rule

- `options` = defaults (what lands in `options.json` if user doesn’t change anything)
- `schema` = validation + UI type hints; can make fields required by setting default to `null` or making schema required. Nested arrays/dicts supported up to depth 2. ([Home Assistant Developer Docs][8])

### Supported schema types (core set)

- `str` (and `str(min,max)`), `bool`, `int(...)`, `float(...)`, `email`, `url`, `password`, `port`, `match(REGEX)`, `list(val1|val2|...)`, `device` / `device(filter)` ([Home Assistant Developer Docs][4])

### “Advanced” file-based config pattern

If your service needs complex config files:

- Add `addon_config` to `map`
- Tell users to place files under `/addon_configs/{REPO}_<your_slug>` (where `{REPO}` is `local` for local installs, otherwise a hash of the repo URL). ([Home Assistant Developer Docs][4])

---

## Translations (so the UI isn’t a pile of raw keys)

- Add `translations/{language_code}.yaml` with top-level keys `configuration` and `network`. ([Home Assistant Developer Docs][4])
- Keys under `configuration` must match keys in your `schema`; keys under `network` must match your `ports` entries. ([Home Assistant Developer Docs][4])

---

## Communication patterns (inside the Supervisor network)

### Naming + DNS

- Add-on instances have names like `{REPO}_{SLUG}`; DNS hostnames replace `_` with `-`. ([Home Assistant Developer Docs][7])

### Call Home Assistant Core API (no password juggling)

- Use `http://supervisor/core/api/` and `Authorization: Bearer ${SUPERVISOR_TOKEN}`; requires `homeassistant_api: true`. ([Home Assistant Developer Docs][7])
- WebSocket proxy: `ws://supervisor/core/websocket` using `SUPERVISOR_TOKEN` as password. ([Home Assistant Developer Docs][7])

### Call Supervisor API

- Use `http://supervisor/` with bearer token; requires `hassio_api: true` (and possibly `hassio_role`). ([Home Assistant Developer Docs][7])

### Services API (discover other add-ons like MQTT/MySQL)

- Mark service usage in your add-on config, then read service connection details (Bashio helper shown in docs). Supported examples include `mqtt` and `mysql`. ([Home Assistant Developer Docs][7])

---

## Security (don’t ship foot-guns)

### The baseline

- Add-ons run in “protection enabled” mode by default; disabling protection increases risk significantly. ([Home Assistant Developer Docs][9])

### Best practices checklist

- Avoid host networking unless you truly need it.
- Use AppArmor (ship `apparmor.txt` when possible).
- Mount folders read-only unless write is required.
- Don’t request API permissions you don’t need.
- Consider signing images (Codenotary CAS). ([Home Assistant Developer Docs][9])

### Ingress auth headers (multi-user done right)

When accessed via Supervisor ingress, requests include headers identifying the authenticated HA user:

- `X-Remote-User-Id`
- `X-Remote-User-Name`
- `X-Remote-User-Display-Name` ([Home Assistant Developer Docs][9])

---

## Publishing strategy

### Option A (preferred): pre-built images

- Build/push per-arch images to a registry.
- In `config.yaml`, set:

```yaml
image: "myhub/image-{arch}-addon-name"
```

`{arch}` is substituted at install time. ([Home Assistant Developer Docs][2])

### Option B: locally-built by the user’s HA

- Useful for “is this idea real?” phases; expect slower installs + higher failure risk. ([Home Assistant Developer Docs][2])

---

## Presentation (store UX that doesn’t look like a weekend project)

- Add **README.md** for the store “intro”. Only `README.md` is shown in the UI, so keep it about the packaged technology (not the add-on). A single short paragraph is enough. ([Home Assistant Developer Docs][6])
- Add **DOCS.md** for add-on usage and configuration details (the add-on-specific context belongs here). ([Home Assistant Developer Docs][6])
- Add images:

  - `logo.png` (recommended ~250×100)
  - `icon.png` (square, recommended 128×128) ([Home Assistant Developer Docs][6])

- Keep `CHANGELOG.md` (they recommend Keep a Changelog format). ([Home Assistant Developer Docs][6])
- Consider stable vs canary branches via `#branch` in the repo URL (document it). ([Home Assistant Developer Docs][6])

---

## Minimum “new add-on” checklist (print this)

1. Create `addon_slug/` with `config.yaml`, `Dockerfile`, `run.sh` (+ `README.md`, `DOCS.md`). ([Home Assistant Developer Docs][4])
2. Define `options` + `schema`; read from `/data/options.json` via Bashio. ([Home Assistant Developer Docs][4])
3. Expose ports only if needed; prefer ingress for web UI and lock ingress to `172.30.32.2`. ([Home Assistant Developer Docs][6])
4. Decide on publish model:

   - early: local build (comment out `image`)
   - real users: publish pre-built multi-arch images and set `image: "…-{arch}-…"`. ([Home Assistant Developer Docs][5])

5. Security pass: minimal mounts, minimal privileges, AppArmor if possible, least API access. ([Home Assistant Developer Docs][9])
6. Add translations if you have non-trivial config UI. ([Home Assistant Developer Docs][4])
7. Add `repository.yaml` at repo root if this is a shareable repo. ([Home Assistant Developer Docs][3])

If you want, paste your existing add-on repo structure (or a link) and I’ll tailor this into a repo-specific `DEVELOPMENT.md` template with your conventions (CI, builder workflow, naming, base images, etc.).

[1]: https://developers.home-assistant.io/docs/add-ons/?utm_source=chatgpt.com "Developing an add-on"
[2]: https://developers.home-assistant.io/docs/add-ons/publishing/ "Publishing your add-on | Home Assistant Developer Docs"
[3]: https://developers.home-assistant.io/docs/add-ons/repository/ "Create an add-on repository | Home Assistant Developer Docs"
[4]: https://developers.home-assistant.io/docs/add-ons/configuration/ "Add-on configuration | Home Assistant Developer Docs"
[5]: https://developers.home-assistant.io/docs/add-ons/testing/ "Local add-on testing | Home Assistant Developer Docs"
[6]: https://developers.home-assistant.io/docs/add-ons/presentation/ "Presenting your addon | Home Assistant Developer Docs"
[7]: https://developers.home-assistant.io/docs/add-ons/communication/ "Add-on communication | Home Assistant Developer Docs"
[8]: https://developers.home-assistant.io/docs/add-ons/configuration/?utm_source=chatgpt.com "Add-on configuration"
[9]: https://developers.home-assistant.io/docs/add-ons/security/ "Add-on security | Home Assistant Developer Docs"
