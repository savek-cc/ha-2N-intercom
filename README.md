# ha-2N-intercom

Home Assistant custom integration for 2N IP Intercom systems with camera, doorbell, relay, and HomeKit support.

## Features

### 🎥 Camera
- **Still image via JPEG snapshot**
- **Live video via RTSP** on devices that expose it
- **Verified device baseline:** MJPEG is available from compatible 2N devices when RTSP is unavailable
- **HomeKit-compatible** video streaming

### 🔔 Doorbell
- **Ring event detection** from call status API
- **Binary sensor** with proper doorbell device class
- **Caller information** attributes (name, number, button)
- **HomeKit doorbell** notifications
- Event timestamps and call state tracking

### 🚪 Door/Gate Control
- **Switch entities** for doors (momentary relay action)
- **Cover entities** for gates (garage door opener style)
- **Multiple relay support** (up to 4 relays)
- **Configurable pulse duration** for each relay
- **HomeKit integration** with proper accessory types
  - Doors: Exposed as switches or locks
  - Gates: Exposed as garage door openers

### ⚙️ Configuration
- **Full UI-based integration setup** (HomeKit doorbell linking still requires YAML)
- **Multi-step setup wizard**:
  1. Connection settings (IP, port, protocol, credentials)
  2. Device features (camera, doorbell)
  3. Relay configuration (per-relay device type and settings)
- **Options flow** for changing settings without re-setup
- **Credential validation** during setup

### 🏠 HomeKit Bridge Support
- Automatic device classification
- Camera with doorbell button
- Lock or garage door opener based on configuration
- Natural Siri voice commands
- Home Assistant automations can drive downstream workflows such as app notifications or KNX door opening; those are not features of this fork.

## Capability Matrix

| Category | Status | Notes |
|----------|--------|-------|
| Core supported now | JPEG snapshot, RTSP stream source on devices that expose it, doorbell ring detection, relay control, HomeKit bridge mapping, and HTTP/HTTPS setup | Current camera entity uses snapshots plus RTSP on RTSP-capable models |
| Planned next | Wire the verified MJPEG capability into the camera entity for RTSP-unavailable devices, improve capability detection from `camera/caps`, and add more Home Assistant usage examples | MJPEG baseline verified on a 2N IP Verso running firmware `2.50.0.76.2` |
| Device-license-dependent but not planned | Automation, Audio Test, NFC, Noise Detection, SMTP, FTP, SNMP, TR069, Lift Control | Only available when the device license exposes them, but this fork is not planning feature work here |
| Irrelevant for single-family-house deployment | Multi-tenant directory UX, keypad workflow, lift workflow, multi-button routing UI | Not needed for the one-bell house baseline |

### Camera Support Baseline

Verified on a 2N IP Verso with firmware `2.50.0.76.2`:

- RTSP Server license: `NO`
- JPEG snapshot works without `fps`
- MJPEG works via `/api/camera/snapshot?...&fps=<n>` over both HTTP and HTTPS
- Valid `fps` values are `1..15`
- `fps >= 16` returns error code `12`
- Supported JPEG resolutions should be taken from `camera/caps`
- Supported resolutions observed from `camera/caps`:
  `176x144`, `320x240`, `352x288`, `640x480`, `800x600`, `1280x960`, `160x120`, `352x272`, `480x272`, `1024x600`, `1280x720`, `640x360`

RTSP is optional and only applies to devices that expose it. On the tested IP Verso baseline, the device itself provides live video via MJPEG when RTSP is unavailable. The current fork still uses `stream_source()` for RTSP-capable models and does not yet expose an MJPEG live-view fallback from the camera entity.

## Architecture

This integration uses modern Home Assistant best practices:

- **DataUpdateCoordinator** for centralized polling and state management
- **Async-first** implementation with aiohttp
- **Platform-based** architecture (camera, binary_sensor, switch, cover, lock)
- **Proper error handling** and automatic reconnection
- **Backward compatibility** with legacy lock entity

For detailed architecture information, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Manual

- Install and setup: [INSTALLATION.md](INSTALLATION.md)
- HomeKit details: [HOMEKIT_INTEGRATION.md](HOMEKIT_INTEGRATION.md)
- Quick reference: [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- Release notes: [CHANGELOG.md](CHANGELOG.md)

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add `https://github.com/mastalir1980/ha-2N-intercom` as an integration
6. Install "2N Intercom"
7. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/2n_intercom` directory to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to Configuration → Integrations
4. Click the "+ Add Integration" button
5. Search for "2N Intercom"

## Configuration

### Initial Setup

1. **Connection Settings**
   - IP Address: Your 2N intercom IP address
   - Port: HTTP port (default: 80) or HTTPS port (default: 443)
   - Protocol: HTTP or HTTPS
   - Username: 2N intercom username
   - Password: 2N intercom password
   - Verify SSL: Enable SSL certificate verification (HTTPS only)

2. **Device Features**
   - Name: Device name (default: "2N Intercom")
   - Enable Camera: Enable camera platform
   - Enable Doorbell: Enable doorbell binary sensor
   - Number of Relays: Select 0-4 relays to configure
  - Ringing account (peer): Optional filter for multi-button setups (All calls = every button)

3. **Relay Configuration** (for each relay)
   - Relay Name: Display name (e.g., "Front Door", "Garden Gate")
   - Relay Number: Physical relay number (1-4)
   - Device Type: 
     - **Door**: Creates switch entity (momentary action)
     - **Gate**: Creates cover entity (garage door style)
   - Pulse Duration: How long to activate relay (milliseconds)
     - Door default: 2000ms (2 seconds)
     - Gate default: 15000ms (15 seconds)

### Example Configurations

#### Apartment Building (Door Only)
```
Connection:
  - IP: 192.168.1.100
  - Port: 80
  - Protocol: HTTP
  - Username: admin
  - Password: ****

Device:
  - Name: Building Entrance
  - Enable Camera: Yes
  - Enable Doorbell: Yes
  - Number of Relays: 1

Relay 1:
  - Name: Entrance Door
  - Number: 1
  - Type: Door
  - Duration: 2000ms
```

Result:
- `camera.building_entrance_camera` - Live stream and snapshots
- `binary_sensor.building_entrance_doorbell` - Ring events
- `switch.building_entrance_entrance_door` - Door unlock

#### Private House (Door + Gate)
```
Connection:
  - IP: 192.168.1.101
  - Protocol: HTTPS
  - Username: admin
  - Password: ****

Device:
  - Name: Home Intercom
  - Enable Camera: Yes
  - Enable Doorbell: Yes
  - Number of Relays: 2

Relay 1:
  - Name: Front Door
  - Number: 1
  - Type: Door
  - Duration: 2000ms

Relay 2:
  - Name: Driveway Gate
  - Number: 2
  - Type: Gate
  - Duration: 15000ms
```

Result:
- `camera.home_intercom_camera`
- `binary_sensor.home_intercom_doorbell`
- `switch.home_intercom_front_door`
- `cover.home_intercom_driveway_gate`

## 2N API Endpoints Used

| Endpoint | Purpose | Platform |
|----------|---------|----------|
| `/api/call/status` | Monitor doorbell rings and call state | binary_sensor |
| `/api/switch/ctrl` | Control relays (open door/gate) | switch, cover |
| `/api/camera/snapshot` | Get JPEG snapshot; the same endpoint also offers MJPEG live video via `fps` on supporting devices | camera |
| RTSP stream | Live video streaming when the device exposes RTSP | camera |

On the tested 2N IP Verso baseline, the next-priority runtime API families are `log/*`, `call/*`, `switch/*`, `io/*`, and `phone/*`.
The config flow can also use directory lookup during setup when peer data is available.

## HomeKit Integration

### Setup

1. Ensure Home Assistant HomeKit Bridge is configured
2. Add the 2N Intercom integration
3. Link the doorbell sensor to the camera (YAML only)

```yaml
homekit:
  - name: 2N Intercom Doorbell
    port: 21065
    filter:
      include_entities:
        - camera.2n_intercom_camera
    entity_config:
      camera.2n_intercom_camera:
        linked_doorbell_sensor: binary_sensor.2n_intercom_doorbell
```

Notes:
- Do not include `binary_sensor.*_doorbell` in the filter.
- Do not include the camera or doorbell in any other HomeKit bridge.
- After YAML change, restart HA and re-add the HomeKit bridge in the Home app.

### Accessory Types

- **Camera** → Video Doorbell (if doorbell enabled)
- **Binary Sensor** → Linked to camera via `linked_doorbell_sensor`
- **Switch (Door)** → Switch or Lock accessory
- **Cover (Gate)** → Garage Door Opener accessory

### Siri Commands

**Camera:**
- "Show me the front door camera"
- "What does the camera see?"

**Doorbell:**
- Ring events appear as HomeKit notifications

**Door (Switch):**
- "Turn on the front door" (triggers relay)
- "Open the front door"

**Gate (Cover):**
- "Open the driveway gate"
- "Close the driveway gate"
- "Is the gate open?"

## Troubleshooting

### Cannot Connect Error

**Symptoms:** "Failed to connect to the device" during setup

**Solutions:**
1. Verify IP address and port are correct
2. Check network connectivity to intercom
3. Verify username and password
4. Try HTTP instead of HTTPS if SSL verification fails
5. Check firewall rules

### Camera Not Streaming

**Symptoms:** Camera entity exists but no video

**Solutions:**
1. Test the snapshot endpoint without `fps` first.
2. If RTSP is not licensed or not exposed by the device, verify the device's MJPEG capability via `/api/camera/snapshot?...&fps=<n>`.
3. For MJPEG, keep `fps` between `1` and `15`.
4. The current fork still exposes RTSP from the camera entity; if your device has no RTSP path, live video needs the planned MJPEG camera-entity fallback.

### Doorbell Not Triggering

**Symptoms:** Binary sensor not changing to "on" when doorbell rings

**Solutions:**
1. Verify doorbell is enabled in configuration
2. Check coordinator polling interval (default: 5 seconds)
3. Verify `/api/call/status` returns "ringing" state during ring
4. Check Home Assistant logs for API errors

### Relay Not Working

**Symptoms:** Switch/cover doesn't control relay

**Solutions:**
1. Verify relay number is correct (1-4)
2. Check relay is configured and enabled on 2N intercom
3. Test relay via 2N web interface
4. Verify credentials have permission to control relays
5. Check Home Assistant logs for API errors

### HomeKit Not Showing Entities

**Doorbell appears as occupancy sensor:**
1. Remove `binary_sensor.*_doorbell` from HomeKit filter.
2. Link doorbell to camera via `linked_doorbell_sensor` (see setup above).
3. Ensure the camera and doorbell are not present in any other HomeKit bridge.
4. Restart Home Assistant and re-add the HomeKit bridge in the Home app.

**Symptoms:** Entities visible in HA but not in Home app

**Solutions:**
1. Verify HomeKit Bridge is running
2. Check HomeKit Bridge includes the entities
3. Reset HomeKit Bridge and re-pair
4. Check entity is not in "unavailable" state

## Development

### Project Structure

```
custom_components/2n_intercom/
├── __init__.py              # Integration setup
├── api.py                   # 2N API client
├── coordinator.py           # DataUpdateCoordinator
├── config_flow.py           # Configuration UI
├── const.py                 # Constants
├── camera.py                # Camera platform
├── binary_sensor.py         # Doorbell platform
├── switch.py                # Door relay platform
├── cover.py                 # Gate relay platform
├── lock.py                  # Legacy lock platform
├── manifest.json            # Integration metadata
├── strings.json             # UI strings
└── translations/
    ├── en.json              # English
    └── cs.json              # Czech
```

### Testing

To test the integration:

1. Install in development mode
2. Check Home Assistant logs for any errors
3. Test all features (camera, doorbell, relays)

### Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Version History

### 1.0.1 (Current)
- Fix HomeKit entity exposure when relays are configured
- Ensure relay entities load from options
- Document current snapshot/RTSP behavior and the verified MJPEG device baseline

### 1.0.0
- Initial public release
- Camera platform with JPEG snapshot and RTSP stream source
- Doorbell binary sensor
- Switch platform for doors
- Cover platform for gates
- DataUpdateCoordinator-based polling
- Full config flow with multi-step wizard
- HomeKit support

## License

This project is open source. See [LICENSE](LICENSE).

## Credits

Created for the Home Assistant community
Developed by: mastalir1980

## Support

For issues, questions, or feature requests, please:
- Open an issue on GitHub
- Check existing documentation
- Review troubleshooting section
