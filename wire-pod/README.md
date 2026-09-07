# WirePod

Self-hosted cloud replacement server for an Anki Vector robot running WireOS
custom firmware. Vector talks to this add-on instead of Digital Dream Labs'
cloud for speech recognition, intent handling, and text-to-speech.

## Prerequisites

- Vector must already be unlocked (community production unlock) and flashed
  to WireOS. This add-on will not work against stock DDL firmware.
- A 2.4 GHz Wi-Fi network Vector can join — he can't see 5 GHz-only networks.

## Networking

- The web setup UI (port 8080 inside the container) is published directly
  at `http://<home-assistant-ip>:8080`. **Do not use ingress for this
  add-on** — wire-pod's frontend JS calls its own API with hardcoded
  absolute paths (`/api/is_api_v3`, `/api/get_config`, etc.), which escape
  Supervisor's ingress subpath and hit Home Assistant's own core API
  instead of wire-pod's backend. This produces both a false-positive
  "webroot does not match" alert and real breakage (e.g. the "Add Robot"
  flow 404ing on submit). You can still front port 8080 with your own
  reverse proxy for an external domain, as long as it's mounted at the
  proxy's root path and not a subpath.
- The robot-facing endpoint (port 443 inside the container, a raw
  TLS+gRPC listener carrying a self-signed cert wire-pod generates itself)
  is published directly to the host at **8443**. This also stays outside
  any reverse proxy: Vector validates the TLS handshake against wire-pod's
  own self-signed certificate, so anything that terminates TLS in front of
  it would break pairing. It gets its own port instead of sharing 443 with
  other services on this host.

## First-time setup

1. Start the add-on and open its web setup UI at
   `http://<home-assistant-ip>:8080` directly (not through the HA
   sidebar/ingress — see Networking above).
2. Walk through wire-pod's "Set up wire-pod" wizard — pick a speech-to-text
   engine (defaults to Vosk, the lighter option) and optionally add LLM API
   keys (OpenAI, Together, Ollama, or an OpenAI-compatible endpoint). These
   are stored by wire-pod itself under its persistent data directory, not in
   this add-on's options.

## Pairing Vector to this server

1. On Vector's charger, raise and lower his lift twice to open the Customer
   Care Info Screen, then scroll to the network page to find his IP address.
2. From WireOS's own settings page at `http://<vector-ip>:8080`, or via the
   websetup flow, point Vector at `<home-assistant-ip>:8443` instead of the
   default cloud endpoint.
3. If the pairing flow only accepts an IP (no port field) and assumes 443,
   pair normally, then edit `/data/data/server_config.json` on the robot
   over root SSH afterward, changing the `jdocs`/`tms`/`chipper` entries
   from `<ip>:443` to `<ip>:8443`.

## Add-on options

- `stt_service` — speech-to-text engine: `vosk` (default, light), `whisper`
  / `whisper.cpp` (more accurate, heavier), `coqui`, `leopard`, `rhino`, or
  `houndify`. `leopard` and `rhino` require `picovoice_apikey`.
- `stt_language` — language code for the STT engine (default `en-US`).
- `debug_logging` — verbose logging from the wire-pod server.
- `use_inbuilt_ble` — enables wire-pod's own BLE pairing support, if your
  host machine has Bluetooth hardware you want wire-pod to use directly.
- `picovoice_apikey` — API key for the `leopard`/`rhino` STT engines.

## Upstream updates

This add-on wraps the upstream `ghcr.io/kercre123/wire-pod` image directly
rather than building wire-pod from source. Upstream only publishes `main`
and `nightly` image tags (their tagged GitHub releases don't have matching
images), so the [Dockerfile](./Dockerfile) pins to a `main` digest rather
than a version tag. To pick up a newer upstream commit:

1. Resolve the current digest for `main` (e.g. `docker buildx imagetools
   inspect ghcr.io/kercre123/wire-pod:main`) and update the `FROM` line in
   [Dockerfile](./Dockerfile), along with the comment noting which commit
   it corresponds to.
2. Bump `version` in [config.yaml](./config.yaml).
3. Check `docker/entrypoint.sh` upstream in case the `WIREPOD_*` env var
   names [run.sh](./run.sh) relies on have changed.
