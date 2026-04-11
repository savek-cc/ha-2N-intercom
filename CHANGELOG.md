# Changelog

## 1.1.0 - 2026-04-11

### HA 2026.4+ compliance
- Imported `homeassistant.components.persistent_notification` API (`hass.components.X` accessor was removed in HA 2025.1)
- `OptionsFlow` no longer stores `config_entry`; uses `self.config_entry` from the framework
- `DataUpdateCoordinator` constructed with the `config_entry` kwarg so HA tags traces with the entry
- Manifest: `requirements: []`, `iot_class: local_push`, `integration_type: device`
- HACS metadata bumped to `homeassistant: 2026.4.0`

### Camera
- Switched from `Camera + stream_source + ffmpeg` to `homeassistant.components.mjpeg.MjpegCamera`
- Credentials are passed to `MjpegCamera` separately and **never** appear in the URL exposed to logs, diagnostics, or dashboards
- Camera transport (RTSP / MJPEG / public-MJPEG) is resolved **once** at coordinator setup; the entity reads `coordinator.camera_transport_info` instead of probing on every property access

### Event handling and call lifecycle
- Push-driven log subscription via `/api/log/subscribe` + `/api/log/pull` + `/api/log/unsubscribe` with re-subscribe and exponential backoff (capped at ~60 s)
- Polling fallback through `/api/call/status` keeps ringing detection alive when the push channel is degraded
- New `2n_intercom.answer_call` and `2n_intercom.hangup_call` services with `target.config_entry` and a `reason` selector (`normal` / `rejected` / `busy`)
- `sensor.<intercom>_call_state` exposes an `active_session_id` attribute so automations can hang up the exact session they answered
- Hangup logic: only the firmware's idempotent `code 14 + "session not found"` response is treated as success — every other rejection (including `code 14 + "Unsupported Content-Type"`) is logged as a real failure
- Centralised device-error parsing across the entire client: any non-success response surfaces as a WARNING with `code` / `description` / `param`; only the idempotent hangup case stays at DEBUG

### Diagnostics and real-state entities
- `sensor.<intercom>_sip_registration` — derived from `/api/phone/status`
- `sensor.<intercom>_call_state` — exposes `active_session_id`
- `binary_sensor.<intercom>_input_1` — real `/api/io/status` input state
- `binary_sensor.<intercom>_relay_1_active` — real cached `/api/switch/status` relay state
- Lock fallback `is_locked` prefers cached `switch/status` for relay 1 and only falls back to optimistic state when `switch/caps` confirms relay 1 doesn't exist

### Configuration flows
- Reauth flow (`async_step_reauth`) — credential rejection raises `ConfigEntryAuthFailed` instead of looping on `ConfigEntryNotReady`
- Reconfigure flow (`async_step_reconfigure`) — change host/port/protocol/credentials/SSL without removing the entry (HA 2024.10+)

### Performance / KISS
- Static caps (`switch/caps`, `io/caps`, camera transport) fetched **once** at setup, not refetched per poll — only `switch/status`, `io/status`, `phone/status`, and the `call/status` fallback poll on the 5-second interval
- Shared `TwoNIntercomEntity` base class — `device_info`, `available`, and `_attr_has_entity_name` deduplicated across every platform
- Snapshot caching collapsed into a single layer at the coordinator
- Removed dead `PLATFORMS` constant; `_get_platforms()` now returns `Platform` enum members
- Dropped legacy `BinarySensorDeviceClass.DOORBELL` `getattr` fallback
- `services.yaml` gained per-service `target.config_entry` and a `reason` selector

## 1.0.1 - 2026-02-22

- Do not expose lock entity when relays are configured
- Read relay configuration from options for switch/cover entities

## 1.0.0 - 2026-02-20

- Initial public release
- Camera platform with RTSP H.264 streaming and snapshots
- Doorbell binary sensor with ring detection
- Switch platform for door relays
- Cover platform for gate relays
- Multi-step config flow and options flow
- HomeKit bridge support
- Ringing account filter based on directory peers
- Gate/door lock type inferred from relay configuration
