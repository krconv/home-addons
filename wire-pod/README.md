# WirePod

Self-hosted cloud replacement server for an Anki Vector robot running WireOS
custom firmware. Vector talks to this add-on instead of Digital Dream Labs'
cloud for speech recognition, intent handling, and text-to-speech.

## Prerequisites

- Vector must already be unlocked (community production unlock) and flashed
  to WireOS. This add-on will not work against stock DDL firmware.
- A 2.4 GHz Wi-Fi network Vector can join — he can't see 5 GHz-only networks.

## Networking

This add-on runs with `host_network: true`, so it binds directly to this
Home Assistant host's LAN ports (80, 443, 8080, 8084) rather than through
Supervisor's ingress proxy. That's required for two reasons: Vector talks to
the server directly over the LAN by IP, not through HA's UI, and wire-pod's
own mDNS advertisement (`escapepod.local`) needs host networking to reach
other devices on the network reliably.

## First-time setup

1. Start the add-on and open its web setup UI at
   `http://<home-assistant-ip>:8080`.
2. Walk through wire-pod's "Set up wire-pod" wizard — pick a speech-to-text
   engine (defaults to Vosk, the lighter option) and optionally add LLM API
   keys (OpenAI, Together, Ollama, or an OpenAI-compatible endpoint). These
   are stored by wire-pod itself under its persistent data directory, not in
   this add-on's options.

## Pairing Vector to this server

1. On Vector's charger, raise and lower his lift twice to open the Customer
   Care Info Screen, then scroll to the network page to find his IP address.
2. From WireOS's own settings page at `http://<vector-ip>:8080`, or via the
   websetup flow, point Vector at this Home Assistant host's IP address
   instead of the default cloud endpoint.
3. If `escapepod.local` resolves on your network, you can use that hostname
   instead of an IP — but pairing by IP is the more reliable fallback if mDNS
   doesn't traverse your network setup.

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
