# Gemns™ Home Assistant Integration

A comprehensive Home Assistant integration for managing Gemns™ battery-less devices including BLE and Zigbee sensors and switches.

## Features

### 🔌 **BLE Support**
- **BLE (Bluetooth Low Energy)** - V0 manual and V1 automatic discovery modes
- **Using Home Assistant Built-in BLE Driver** - Uses built-in configuration of BLE from Home Assistant

### 📱 **Device Types**
- **Sensors**: Leak sensors, vibration sensors
- **Switches**: On/off switches, door switches, toggle switches

### 🎛️ **Control Features**
- **BLE Toggles** - Enable/disable protocols via UI
- **Device Status Tracking** - Real-time device status monitoring

## Installation

### Option 1: HACS (Recommended)
1. Install [HACS](https://hacs.xyz/)
2. Add this repository as a custom repository
3. Install "Gemns IoT" integration
4. Restart Home Assistant

### Option 2: Manual Installation
1. Copy the `integration` folder to your `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings** → **Devices & Services** → **Integrations**
4. Click **+ Add Integration** and search for "Gemns IoT"

## Configuration

### UI Configuration (Recommended)
1. Go to **Settings** → **Devices & Services** → **Integrations**
2. Click on **Gemns IoT** integration
3. Configure the following settings:
   - **MQTT Broker URL**: Your MQTT broker address
   - **MQTT Username/Password**: Optional authentication
   - **Enable BLE**: Toggle BLE functionality
   - **Scan Interval**: Device discovery frequency
   - **Heartbeat Interval**: Connection monitoring frequency

### YAML Configuration
```yaml
# Example configuration.yaml
gemns_iot:
  mqtt_broker: "mqtt://homeassistant:1883"
  enable_ble: true
  enable_zigbee: true
  scan_interval: 0.02
  heartbeat_interval: 10.0
```

## Usage

### Adding Devices

#### Automatic Discovery
- Devices are automatically discovered when connected to BLE dongles

#### Manual Device Addition
1. Go to **Settings** → **Devices & Services** → **Gemns IoT**
2. Click **Add Device**
3. Fill in the device information:
   - **Device ID**: Unique identifier
   - **Device Name**: Display name
   - **Category**: Sensor, Switch, Light, Door, or Toggle
   - **BLE Discovery Mode**: V1 Auto (for BLE devices)

### Controlling Devices

#### Sensors
- **Leak Sensors**: Show leak detection status
- **Vibration Sensors**: Display motion/vibration data

#### Switches
- **On/Off Switches**: Basic on/off control
- **Door Switches**: Door open/close status
- **Toggle Switches**: State-based switching

### BLE Management

#### Protocol Toggles
- Use the integration's configuration options to enable/disable BLE
- Toggles are available in the integration settings
- Changes take effect immediately

## Device Status

### Status Types
- **Connected**: Device is actively communicating
- **Offline**: Device is not responding (automatic after 5 minutes)
- **Connecting**: Device is in pairing mode
- **Identified**: Device is recognized but not fully paired
- **Paired**: Device is fully paired and managed
- **Error**: Device has encountered an error

## Troubleshooting

### Common Issues

#### Integration Not Loading
- Check that all required files are in the correct directory
- Verify MQTT broker is accessible
- Check Home Assistant logs for error messages

#### Devices Not Appearing
- Ensure BLE toggles are enabled
- Verify dongles are connected and responding

### Debug Mode
Enable debug logging in Home Assistant:
```yaml
logger:
  default: info
  logs:
    custom_components.gemns_iot: debug
```

## Development

### Architecture
- **Device Manager**: Handles device discovery and management
- **Platform Entities**: Home Assistant entity implementations
- **Configuration Flow**: UI-based setup and configuration

### Adding New Device Types
1. Create new platform file (e.g., `fan.py`)
2. Implement required entity methods
3. Add to platform list in `__init__.py`
4. Update constants and translations

## Support

### Documentation
- [Home Assistant Integration Documentation](https://developers.home-assistant.io/)
- [MQTT Integration Guide](https://www.home-assistant.io/integrations/mqtt/)
- [Custom Component Development](https://developers.home-assistant.io/docs/creating_component_index/)

### Issues and Feature Requests
- Report bugs via GitHub issues
- Request new features through GitHub discussions
- Contribute improvements via pull requests

## License

This integration is licensed under the MIT License. See the LICENSE file for details.

## Changelog

### Version 1.0.0
- Initial release
- BLE support
- Multi-device type support
- UI-based configuration
- Device management interface
