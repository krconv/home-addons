# Scripts Add-on

A general-purpose Python automation platform for Home Assistant that provides advanced automation capabilities without requiring AppDaemon.

## Features

- **Modular App System**: Easily add new automation apps
- **ZigBee Integration**: Advanced ZigBee device management and health monitoring  
- **MQTT Auto-Discovery**: Automatically discovers MQTT broker from Home Assistant Services
- **Robust Logging**: Configurable logging levels with structured output
- **Health Monitoring**: Automatic device health checks and recovery

## Currently Included Apps

### Lights App
Advanced lighting automation with:
- Adaptive scheduling with smooth transitions
- ZigBee device health monitoring and automatic healing
- Circuit-based organization with group management
- Power cycling recovery for unresponsive devices
- Support for custom light curves (e.g., ABL-LIGHT-Z-001)
- Per-circuit night-light mode controlled over MQTT

#### Night-light mode

Each circuit with lights and a hardwired switch can be put into night-light mode, which turns the circuit off and keeps a single bulb on at a low level until the paddle is pressed or the mode is set back to auto.

- Command topic `scripts/lights/<circuit_id>/set` with payload `{"mode": "auto"}` or `{"mode": "nightlight", "light": "<bulb ieee>", "brightness": 1}` (`brightness` is a raw Zigbee level 1-254, default 1).
- State topic `scripts/lights/<circuit_id>/state` (retained) carries the current mode, e.g. `{"mode": "auto", "nightlight": null}` or `{"mode": "nightlight", "nightlight": {"light": "<bulb ieee>", "brightness": 1}}`. It is published for every circuit at startup and on every change.

### HVAC App

Runs the week of HVAC experiments from the lab plan. Every 60 seconds it reads the thermostats and sensors from Home Assistant, decides what each zone, the East fan, the ERV and `automation.erv_control` should be doing, and sends the service calls that differ.

- Talks to Home Assistant through the Supervisor's core API proxy (`homeassistant_api: true`), so it needs no token or URL in the options.
- Writes its status to `sensor.hvac_lab_status`. The state is `<test_id>/<phase>`, `baseline/day`, `baseline/night` or `disabled`; the attributes carry the night grade, voids, lockout state and the last events.
- `input_boolean.hvac_lab_enabled` is the kill switch. Turning it off restores the baseline once, turns `automation.erv_control` back on, then leaves everything alone until it is turned on again. The app does not create the helper; create it in Home Assistant first. A missing or unavailable helper reads as off.
- Keeps its memory in `/data/hvac/mem-live.json` (`mem-dry.json` in dry run, so switching modes starts clean) and appends every command and event to `/data/hvac/journal.jsonl`, so a restart picks up where it left off.
- Runs beside the lights app and never affects it. A failure while starting is logged and the add-on carries on without hvac. A failed tick is logged and retried on the next tick. The health endpoint the watchdog polls ignores hvac, so an hvac problem never restarts the add-on. Run it with lights (or another app) enabled; on its own the add-on has no app the health check counts.

Enable it by adding it to `apps`. Options live in the separate `hvac` block:

```yaml
apps:
  - name: lights
    enabled: true
    config_file: /config/lights.yaml
  - name: hvac
    enabled: true
hvac:
  dry_run: true             # log and journal every service call, send none
  lockout_auto_reset: false # on a suspected lockout, turn the heat pump off for 2 minutes once a night
```

`dry_run` defaults to `true`. Leave it on for a day and read the `DRY` lines in the add-on log or the journal before turning it off.

## Installation

1. Add this repository to your Home Assistant add-on store
2. Install the "Scripts" add-on
3. Configure the add-on options (see Configuration section)
4. Create configuration files for your apps (e.g., `/config/lights.yaml`)
5. Start the add-on

## Configuration

### Add-on Options

```yaml
log_level: info                    # debug, info, warning, error
zigbee_base_topics:               # ZigBee2MQTT base topics
  - zigbee2mqtt-a
  - zigbee2mqtt-b
apps:                             # List of apps to run
  - name: lights                  # App name
    enabled: true                 # Enable/disable app
    config_file: /config/lights.yaml  # Path to app config file
hvac:                             # Options for the hvac app (see HVAC App)
  dry_run: true
  lockout_auto_reset: false
```

### Lights App Configuration

Create `/config/lights.yaml`:

```yaml
circuits:
  - id: living_room
    group_id: a-1                 # Group ID in ZigBee network
    lights:
      - ieee: "00:12:34:56:78:9a:bc:de"  # Device IEEE address
      - ieee: "00:12:34:56:78:9a:bc:df"
    switches:
      - ieee: "00:12:34:56:78:9a:bc:e0"
        type: hardwired           # Required for power cycling recovery

schedule:
  - time: "06:00"                 # 24-hour format or integer (600 for 6:00)
    brightness: 20                # Brightness percentage (0-100)
    temperature: 2700             # Color temperature in Kelvin
    transition: "30m"             # Transition duration (s/m/h)
  - time: "08:00"
    brightness: 80
    temperature: 4000
    transition: "1h"
  - time: "22:00"
    brightness: 10
    temperature: 2200
    transition: "2h"
```

## MQTT Configuration

The add-on automatically discovers MQTT settings from Home Assistant Services. No manual MQTT configuration is required when running as a Home Assistant add-on.

For development/testing outside Home Assistant, set environment variables:
- `MQTT_HOST`
- `MQTT_PORT`
- `MQTT_USERNAME`
- `MQTT_PASSWORD`

## Adding New Apps

1. Create a new Python module in `src/` (e.g., `hvac_app.py`)
2. Implement your app class with `__init__(logger, addon_config, app_config)` and `initialize()` methods
3. Add app creation logic in `src/main.py` `_create_app()` method
4. Add app configuration to add-on options schema in `config.yaml`

Example app structure:

```python
class MyApp:
    def __init__(self, logger, addon_config, app_config):
        self.logger = logger
        self.addon_config = addon_config
        self.app_config = app_config
    
    async def initialize(self):
        # Initialize your app
        pass
```

## Development

The add-on uses Poetry for dependency management:

```bash
# Install dependencies
poetry install

# Run locally (requires MQTT environment variables)
poetry run python -m src.main
```

### Tests and the hvac simulation

The tests and the hvac house simulator live in `tests/` and are not copied into the image. They need `pytest`, plus `matplotlib` for the day plots. Run them from `scripts/`:

```bash
python -m pytest tests                    # unit tests, the AppManager isolation tests and a one-night sim
python -m tests.hvac.sim.run all          # the full scenario suite; writes tests/hvac/out/<scenario>/report.md
```

`sim.run all` exits 1 while West Wing comfort and floor shortfalls show up in `week_nominal`, `faults` and `faults_autoreset`. Those come from the house model, not the app, and are reported rather than fixed. Compare the invariant table with the previous run instead of relying on the exit code.

## Deployment

Supervisor builds this add-on on the Home Assistant host from the repository's `main` branch. A commit reaches the running add-on only when two things happen:

1. Supervisor reloads the add-on store, which fetches `main` again. It does this every 3 hours on its own.
2. The add-on is updated. Supervisor only offers an update when `version` in `config.yaml` differs from the installed one, so every change that should ship needs a version bump. Updating rebuilds the image on the host and restarts the add-on.

The add-on's `auto_update` switch does cover locally built add-ons, but Supervisor checks for auto updates every 16 hours and skips a version until a day after it first saw it. With it on, a change ships one to three days after merge.

For a fast deploy, the GitHub workflow `.github/workflows/scripts.yaml` runs the tests and a Docker build on every pull request and push. After a push to `main` passes, it calls a Home Assistant webhook. An automation behind that webhook asks Supervisor to reload the store and installs the update:

```yaml
alias: Deploy Scripts add-on
mode: queued
triggers:
  - trigger: webhook
    webhook_id: "<long random id>"
    allowed_methods: [POST]
    local_only: false
actions:
  # A manual refresh of an add-on update entity makes Supervisor reload the store first.
  - action: homeassistant.update_entity
    target:
      entity_id: update.scripts_update
  - wait_for_trigger:
      - trigger: state
        entity_id: update.scripts_update
        to: "on"
    timeout: "00:02:00"
    continue_on_timeout: true
  - condition: state
    entity_id: update.scripts_update
    state: "on"
  - action: update.install
    target:
      entity_id: update.scripts_update
    data:
      backup: true
```

If Home Assistant is not reachable from the internet, keep the same actions and replace the webhook trigger with a `time_pattern` trigger every 15 minutes. No GitHub secret is needed then, and the workflow's deploy step does nothing.

Setup:

1. In Home Assistant, create the automation above with a long random `webhook_id`.
2. In GitHub, add the repository secret `HA_DEPLOY_WEBHOOK_URL` with the full webhook URL, for example `https://<your Nabu Casa or external URL>/api/webhook/<webhook_id>`. The webhook id is the only credential, so treat the URL as a secret.

## Troubleshooting

### Common Issues

1. **Config file not found**: Ensure `/config/lights.yaml` exists and is readable
2. **MQTT connection failed**: Check that Home Assistant MQTT integration is configured
3. **ZigBee devices not found**: Verify `zigbee_base_topics` matches your ZigBee2MQTT configuration
4. **App initialization failed**: Check logs for specific error messages

### Logs

Use the Home Assistant add-on logs or set `log_level: debug` for detailed troubleshooting information.

## Contributing

This add-on is designed to be extensible. Submit pull requests with new apps or improvements to existing functionality.

## License

This project follows the same license as the containing repository.