# OpenWISP Home Assistant Add-on

## About

OpenWISP is a complete, open source WiFi & network management system that provides features such as a WiFi controller, monitoring system, captive portal, and RADIUS server.

This add-on provides a unified installation of the entire OpenWISP stack in a single container for Home Assistant.

## Features

- Complete OpenWISP installation
- WiFi network management
- Monitoring and alerting
- RADIUS authentication server
- Firmware upgrades for compatible devices
- Network topology visualization
- VPN server for remote management

## Installation

1. Add the repository to your Home Assistant instance.
2. Install the OpenWISP add-on.
3. Configure the add-on (site name, admin credentials, etc.).
4. Start the add-on.
5. Access the OpenWISP web interface using the link provided in the add-on page.

## Configuration

The add-on can be configured with the following options:

```yaml
site_name: "OpenWISP"
site_url: ""
admin_email: "admin@example.com"
admin_password: "admin"
time_zone: "UTC"
language_code: "en-us"
debug: false
```

## Documentation

For detailed documentation and usage instructions, please refer to the official OpenWISP documentation:

- [OpenWISP Documentation](https://openwisp.org/docs/)
- [OpenWISP Controller](https://github.com/openwisp/openwisp-controller)
- [OpenWISP Network Topology](https://github.com/openwisp/openwisp-network-topology)
- [OpenWISP Firmware Upgrader](https://github.com/openwisp/openwisp-firmware-upgrader)
- [OpenWISP Monitoring](https://github.com/openwisp/openwisp-monitoring)
- [OpenWISP RADIUS](https://github.com/openwisp/openwisp-radius)

## Support

For issues with the add-on itself, please open an issue on GitHub.
For OpenWISP-specific questions, refer to the [OpenWISP community channels](https://openwisp.org/support.html).