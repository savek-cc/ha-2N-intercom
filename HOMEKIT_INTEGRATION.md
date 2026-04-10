# HomeKit Integration

## Overview

This integration exposes HomeKit-friendly entities for camera, doorbell, and door/gate control.

- **Relay-based path**: door relays are exposed as switches and gate relays as covers
- **Legacy no-relay path**: the fallback lock entity still uses door/gate semantics for HomeKit mapping
- **Doorbell path**: the doorbell sensor must be linked to the camera via YAML so HomeKit shows it as a video doorbell

## Doorbell in HomeKit (Important)

HomeKit shows the doorbell **on the camera accessory**, not as a standalone binary sensor.
To get the doorbell tile and notifications in the Home app, you must link the
doorbell binary sensor to the camera in the HomeKit bridge configuration.

**Required setup (YAML only):**

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

**Rules that must be followed:**
- Do not add the `binary_sensor.*_doorbell` to the HomeKit filter.
- Do not include the camera or doorbell in any other HomeKit bridge.
- HomeKit bridge configuration is YAML-only; it cannot be set from UI.

After changing YAML, restart Home Assistant and re-add the HomeKit bridge in the Home app.

## How It Works

### 1. Configuration Flow

When setting up the integration, users go through the current multi-step flow:

- Connection settings: host, port, protocol, username, password, SSL verification
- Device settings: name, camera, doorbell, relay count, optional called peer
- Relay settings: per-relay door/gate type, relay number, and pulse duration when relays are configured

### 2. Relay Type And Legacy Lock Mapping

Relay-based installs store door/gate behavior per configured relay. The legacy no-relay path still uses door-type semantics through the fallback lock entity and options flow.

### 3. HomeKit Entity Mapping

The integration exposes different entity types depending on how it is configured:

- **Relay-based path**: door relays are exposed as switches and gate relays as covers
- **Legacy no-relay path**: the fallback lock entity still maps door type to a HomeKit-friendly device class

```python
# Legacy lock path in lock.py
def __init__(self, config_entry: ConfigEntry, door_type: str) -> None:
    if door_type == DOOR_TYPE_GATE:
        self._attr_device_class = "gate"
    # Door type has no device class (default lock behavior)
```

### 4. HomeKit Accessory Types

Home Assistant's HomeKit integration uses the device class to determine the accessory type:

- **Camera + linked doorbell sensor**: Exposed as a **Video Doorbell** accessory in HomeKit
  - The camera must be linked to the doorbell sensor via `linked_doorbell_sensor`
  - HomeKit shows the doorbell on the camera accessory, not as a standalone tile

- **Door relay switch**: Exposed according to your HomeKit bridge filter and entity type
  - Typically used for momentary open/unlock actions

- **Gate relay cover**: Exposed as **Garage Door Opener** accessory in HomeKit
  - Appears as a garage door in the Home app
  - Shows open/closed states
  - Can be opened/closed
  - More appropriate for gates and large doors

- **Legacy lock entity** (no relays configured):
  - `device_class=None` behaves like a standard lock
  - `device_class="gate"` behaves like a garage door opener

## User Experience

### Setup Flow

1. User adds the "2N Intercom" integration
2. User enters connection settings
3. User configures device options such as camera, doorbell, relay count, and optional called peer
4. User configures relay type and duration when relays are present
5. Integration creates camera, doorbell, and relay entities, or the legacy lock entity when no relays are configured

### Changing Device Or Relay Settings

1. User goes to integration options
2. User updates device or relay settings
3. Integration reloads with new configuration
4. HomeKit accessory mapping updates to match the recreated entities

### HomeKit Integration

Once the integration is set up and the HomeKit bridge is configured in Home Assistant:

1. The camera is included in the HomeKit bridge and linked to the doorbell sensor via YAML
2. The relevant relay entities or legacy lock entity are included in the HomeKit bridge
3. The device appears in the Home app with the correct accessory type
4. Users can control the door/gate from their iOS/macOS devices
5. Siri can control the door/gate using appropriate commands

## Technical Details

### HomeKit Bridge Declaration

The integration declares HomeKit support in `manifest.json`:

```json
{
  "domain": "2n_intercom",
  "homekit": {}
}
```

This tells Home Assistant that this integration is HomeKit-compatible.

### Device Class Values

- `None` (default): Standard lock
- `"gate"`: Gate/garage door opener

### Legacy Lock Entity Features

The legacy lock entity supports:
- `LockEntityFeature.OPEN`: Allows opening the door/gate
- Locked/unlocked states
- Device information for proper identification in HomeKit

## Translations

The legacy door/gate terminology is translated in both English and Czech:

**English:**
- Door Type → "Door" or "Gate"

**Czech:**
- Typ dveří → "Dveře" nebo "Vrata"

## Example Configuration

### Example 1: Door Relay

```yaml
relay_name: "Front Door"
relay_device_type: "door"
```

Result in HomeKit: Switch/lock-like door control, depending on your bridge mapping

### Example 2: Gate Relay

```yaml
relay_name: "Garden Gate"
relay_device_type: "gate"
```

Result in HomeKit: Garage Door Opener accessory

## Future Enhancements

Potential future improvements:
- Support for multiple doors/gates per device
- Door state sensors (open/closed)
- Video doorbell support
- Call notification support
