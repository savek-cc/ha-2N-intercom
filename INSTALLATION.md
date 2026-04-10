# Installation and Usage Guide

## Quick Start

### 1. Installation

Copy the integration to your Home Assistant configuration directory:

```bash
# Navigate to your Home Assistant config directory
cd /config

# Create custom_components directory if it doesn't exist
mkdir -p custom_components

# Copy the integration
cp -r /path/to/ha-2N-intercom/custom_components/2n_intercom custom_components/
```

### 2. Restart Home Assistant

After copying the files, restart Home Assistant to load the integration.

### 3. Add Integration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for **2N Intercom**
4. Click on it to start the setup wizard

### 4. Configure Integration

**Setup Wizard:**

![Setup Wizard](docs/setup_wizard.png)

Fields:
- **Connection step**: Host, port, protocol, username, password, SSL verification
- **Device step**: Name, camera toggle, doorbell toggle, relay count, optional called peer
- **Relay steps**: Shown only when relays are configured; choose door/gate type, relay number, and pulse duration for each relay

Example configurations:

**Connection + device example:**
```
Host: 192.168.1.100
Protocol: HTTP
Name: Front Door
Enable Camera: Yes
Enable Doorbell: Yes
Relay Count: 1
Called Peer: All calls
```

**Relay example:**
```
Relay 1: Front Door
Type: Door
Relay Number: 1
Pulse Duration: 2000
```

### 5. Verify in Home Assistant

After setup, you should see:
- A camera entity named `camera.<your_device_name>_camera` when camera is enabled
- A doorbell binary sensor named `binary_sensor.<your_device_name>_doorbell` when doorbell is enabled
- Switch entities for door relays and cover entities for gate relays
- A legacy lock entity named `lock.<your_device_name>_lock` only when no relays are configured
- Device information in the Devices view

### 6. HomeKit Integration

If you have the HomeKit integration enabled in Home Assistant:

1. The relevant entities will be available to HomeKit based on your bridge filter and YAML link configuration
2. Open the Home app on your iOS/macOS device
3. You should see the door/gate with the appropriate icon:
   - **Door relay**: Lock or switch icon depending on your HomeKit bridge setup
   - **Gate relay**: Garage door icon 🏠

### 7. Using the Lock

**In Home Assistant:**
- Use the camera entity for snapshots and RTSP preview
- Use the doorbell binary sensor for automations and alerts
- Click a door relay switch to trigger the opening pulse
- Click a gate cover to open or close the gate
- Use the legacy lock entity only if no relays are configured

**In HomeKit:**
- For doors: Tap to lock/unlock or trigger the mapped switch
- For gates: Tap to open/close

**With Siri:**
- "Hey Siri, unlock the front door"
- "Hey Siri, open the garden gate"

## Changing Device Or Relay Settings

To change the device or relay behavior after initial setup:

1. Go to **Settings** → **Devices & Services**
2. Find the **2N Intercom** integration
3. Click **Configure**
4. Update the device options or relay type/duration settings
5. Click **Submit**

The integration will reload with the new configuration, and the HomeKit accessory mapping will update accordingly. If no relays are configured, the legacy lock path still uses the door/gate type for HomeKit behavior.

## Troubleshooting

### Integration not appearing
- Ensure the files are in the correct location: `config/custom_components/2n_intercom/`
- Restart Home Assistant
- Check the logs for any errors

### HomeKit not showing the device
- Ensure HomeKit integration is set up in Home Assistant
- Check that the camera, doorbell, and relay entities are included in the HomeKit bridge as intended
- Try restarting the HomeKit bridge

### Wrong accessory type in HomeKit
- Check the relay type or legacy no-relay lock configuration in the integration options
- Change the affected setting and reload the integration
- Restart the HomeKit bridge

## Advanced Configuration

### Ringing Account Filter

If you have multiple buttons, you can limit doorbell events to a single
account by selecting **Ringing account (peer)** in options. This is an optional runtime filter and can also use the configuration-time peer list when the device exposes it.

### Multiple Doors/Gates

You can add multiple instances of the integration for different doors:

1. Add the integration again
2. Give it a different name
3. Configure the appropriate device and relay model for that instance

Each instance will create its own camera, doorbell, and relay entities; if an instance has no relays, it will expose the legacy lock entity instead.

### Automation Examples

**Unlock when arriving home:**
```yaml
automation:
  - alias: "Unlock front door when arriving"
    trigger:
      - platform: zone
        entity_id: person.john
        zone: zone.home
        event: enter
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.front_door
```

**Open gate at specific times:**
```yaml
automation:
  - alias: "Open gate in the morning"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: cover.open_cover
        target:
          entity_id: cover.garden_gate
```

If an instance has no relays configured, use the legacy `lock.*` entity instead.

## Development Status

This implementation currently provides:
- ✅ Connection, device, and relay config flow
- ✅ HomeKit integration
- ✅ Camera entity with snapshot and RTSP stream source
- ✅ Doorbell binary sensor
- ✅ Relay control through the 2N API
- ✅ Switch and cover entities for configured relays
- ✅ Legacy lock entity when no relays are configured

Downstream Home Assistant automations can turn these entities into app notifications or KNX door-opening workflows, but those are not fork features.

## Support

For issues or questions, please open an issue on the GitHub repository.
