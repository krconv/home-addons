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