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

### HVAC Controller App

The whole-house heating controller that will replace the hvac app. It runs beside it with its own `dry_run`, which defaults to `true`; in dry run it reads Home Assistant and publishes `sensor.hvac_controller_status`, and nothing else. Every 60 seconds it fuses each zone's eye-level room sensors (a settled Airthings counts for its room; the thermostat's own reading is the fallback), and decides for each of the heat pump and three radiant circuits whether it should be on. A circuit is switched by moving its thermostat between two heat setpoints: a hold that keeps the coldest room above 64 °F if the app stops, and a bounded call.

- The status state is `rules (dry)`, `rules` or `disabled`; the attributes carry each zone's temperature and coldest room, each circuit's setpoint and the rule that won.
- Journals every intent (what it would set, the winning rule, and whether the thermostat shows something else) and a sample every 15 minutes to `/data/hvac-controller/shadow/journal.jsonl` (`live/` when live).
- Live only: `input_boolean.hvac_controller_enabled` is the kill switch; off sets every thermostat to its hold once, then leaves them alone. Dry run ignores it.
- Isolated like hvac: a failure never affects lights or the health check. Don't turn off `dry_run` while the hvac app is live.

```yaml
apps:
  - name: hvac_controller
    enabled: true
hvac_controller:
  dry_run: true
```

### LEDs App

Owns the LED bar on every Inovelli Blue switch (VZM30/31/35/36) on the Zigbee2MQTT bridges. Two layers decide what each bar shows.

**House setting.** A Home Assistant light, **Switch LED Bars** (`light.switch_led_bars`), made with MQTT discovery. Its color and brightness set the bar's color and brightness. Its effect dropdown has `solid` (the bar's normal level indicator in that color), the switches' built-in animations (pulse, chase, aurora, blink, falling, rising, siren and others), and red/green patterns: `christmas_checker` (each bar alternates LED by LED, flipped on every other switch), `christmas_alternate` (every other switch red, the rest green), `christmas_pulse` and `christmas_chase`. Off puts the factory indicator back (blue, 33% with the load on, 1% off). The light's retained state topic `scripts/leds/state` is the app's memory across restarts.

**Requests.** Anything can ask for a look on some rooms or switches, with a priority; on each switch the highest-priority request wins, and with none the house setting shows. A room is the part of the switch's Zigbee2MQTT name before "Switch" (`Kitchen Switch A` is in `kitchen`); the list is published retained on `scripts/leds/rooms`.

- MQTT: publish to `scripts/leds/request/<id>` (retain it to survive restarts); an empty payload releases it. Active requests are published retained on `scripts/leds/requests`.
- In-process: `leds_app.instance().request(id, data)` and `.release(id)` from the event loop.

```json
{"rooms": ["Kitchen"], "switches": ["Nook Switch"], "effect": "fast_blink",
 "color": 0, "color_b": 85, "level": 100, "priority": 50, "expires_s": 600}
```

All fields are optional. No `rooms` or `switches` means every switch. `effect` is `solid` (default), an animation, a pattern, or `off` (the factory indicator). `color` and `color_b` are Inovelli's 0-255 scale (0 red, 21 orange, 42 yellow, 85 green, 127 cyan, 170 blue, 212 purple, 234 pink, 255 white) or `{"h": 0-360, "s": 0-100}`; `color_b` alternates switches. `priority` defaults to 50.

**How a look is drawn.** Two of the switch's own features, from [Inovelli's parameter list](https://help.inovelli.com/en/articles/8189241-blue-series-dimmer-switch-parameters):

- The indicator, the bar that shows the dim level. Its color and brightness are parameters stored in the switch: P95/P96 color with the load on/off, P97/P98 intensity on/off, P60-P93 per-LED colors (255 and 101 mean "use the whole-bar value"). They follow the load's level and on/off, survive a power cut, and don't flash. `solid` and the checker/alternate patterns use only these. Brightness sets P97; P98 stays at 1% (or lower).
- A notification (`led_effect`), an animation over the whole bar. Only animations use it, always on top of an indicator in the same color: the switch drops a notification for a moment to show a level change, and it shows the indicator then.

**Delivery.** A reconcile loop writes only the parameters that differ from what each switch last reported (or was just sent), restarts from the top on every change, and every 10 minutes compares against the switches' reports again, so a change made elsewhere is put back.

What many switches need alike goes to a Zigbee group. On each network the app owns three groups: `leds_all` (every LED switch on that network), `leds_even` and `leds_odd` (every other switch, the same split the alternate and checker patterns use). It creates them at startup, fixes their membership when Zigbee2MQTT publishes its group list and every 10 minutes (only what's wrong is sent, since each change is a message to that switch), and never touches any other group. Zigbee2MQTT makes a Home Assistant light for every group; the app's groups are created with that entity disabled. A group is used only while its members are exactly the switches it should have; otherwise those switches get their own messages, so the app works the same with no groups at all. A parameter goes to a group when every member wants the same value and at least 3 members need it, so a room request keeps the parameters it changes off that network's `leds_all` (the rest of that network gets them one switch at a time). Notifications go to a group the same way: every member wants the same one and at least 3 need it. Members don't report a group write, so the app reads those parameters back with a `get` on the group, and every member reports its new values.

Whatever is left goes to each switch: all its parameters in one `/set` message. Any write (group or switch) carries at most 8 parameters, so a big change such as the checker pattern is split into several. Every parameter write, group or switch, goes out before any notification, so an animation always starts on top of an indicator in its own color. The two networks are sent to at the same time. On a network, switch messages are 60 ms apart and group messages (broadcasts) 1 second apart, since the coordinator can only track a few broadcasts at once. A house color change is one group write and, a second later, one group read per network; without groups it's one message per switch, 60 ms apart. An animation sent to a group starts on all its members from one broadcast; sent switch by switch, it starts when each switch's command arrives.

A switch that rejoins its network keeps its parameters and is sent its animation again. Zigbee2MQTT doesn't retain device state, so after a restart a switch reports nothing until it next talks; the app keeps what each switch last had (sent or reported) in `/data/leds/indicator.json` and uses it until a report comes in, so a restart sends nothing that hasn't changed. The notification last sent to each switch is saved in `/data/leds/notifications.json`; without that file (first run) every switch's notifications are cleared once.

It overwrites any LED notification something outside this app puts on a switch whenever it changes that switch's notification. Like hvac, a failure is logged and never stops or restarts the add-on.

Enable it by adding it to `apps`:

```yaml
apps:
  - name: leds
    enabled: true
```

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
hvac_controller:                  # Options for the hvac_controller app (see HVAC Controller App)
  dry_run: true
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