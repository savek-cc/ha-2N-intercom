# Implementation Summary: 2N Intercom Integration

## Overview

This integration drives a 2N IP Intercom from Home Assistant. It started as a basic lock entity, was redesigned around `DataUpdateCoordinator`, and was hardened against the 2N HTTP API 2.50 LTS for the **2N IP Verso** (firmware `2.50.0.76.2`) — a single-family-house deployment with no RTSP licence.

The remediation pass that produced the current shape (version `1.1.0`) added MJPEG-first live view, push-driven event handling, real-state status entities, answer/hangup services, reauth + reconfigure flows, and HA 2026.4+ compliance.

## What is implemented

### 1. Core architecture

- `api.py` — async aiohttp client with **dual auth** (HTTP Basic for `camera/phone/call/system`, HTTP Digest for `switch/io/log` via `aiohttp` `DigestAuthMiddleware` with Basic fallback)
- `coordinator.py` — `DataUpdateCoordinator` with status polling, static-caps caching, and a background **log subscription loop** with re-subscribe + exponential backoff
- `entity.py` — shared `TwoNIntercomEntity` base class providing `device_info`, `available`, and `_attr_has_entity_name` for every platform
- `__init__.py` — entry setup, log-listener lifecycle, service registration

### 2. Configuration system

`config_flow.py` provides:

- **User flow** — multi-step (connection → device → relays)
- **Reauth flow** — auto-triggered when the device starts rejecting credentials (raises `ConfigEntryAuthFailed`, surfaces a notification, walks the user through re-entering the password)
- **Reconfigure flow** — HA 2024.10+ flow for changing host/port/protocol/credentials/SSL without removing the entry
- **Options flow** — change device features and per-relay settings post-setup

Validation happens against `system/info` so credential mistakes fail at the form layer instead of mid-poll.

### 3. Platform implementations

#### Camera (`camera.py`)

- Inherits from `CoordinatorEntity` **and** `homeassistant.components.mjpeg.MjpegCamera`
- Native MJPEG live view through `/api/camera/snapshot?fps=<n>` — no ffmpeg, no HLS round-trip
- JPEG snapshot via `coordinator.async_get_snapshot()` (single-layer cache; the duplicated entity-level cache was removed)
- **Credentials never embedded in URLs** — `MjpegCamera` receives `username`/`password` separately
- RTSP returned from `stream_source()` only when the device exposes the RTSP server licence
- Camera transport (RTSP vs MJPEG vs MJPEG-public) is resolved **once** at coordinator setup; the entity reads `coordinator.camera_transport_info` instead of probing on every property access

#### Binary sensors (`binary_sensor.py`)

- `TwoNIntercomDoorbell` — push-driven ring detection from the log subscription, polling fallback from `call/status`. Caller name/number/button + last ring timestamp attributes. Device class `OCCUPANCY` (HomeKit doorbell tile is provided by the linked-camera-accessory pattern, not the binary-sensor class)
- `TwoNIntercomInput1Sensor` — real `io/status` input 1 state
- `TwoNIntercomRelay1ActiveSensor` — real cached `switch/status` relay 1 active flag

#### Diagnostic sensors (`sensor.py`)

- `TwoNIntercomSipRegistrationStatusSensor` — derived from `phone/status`, exposes `registered_accounts` count attribute
- `TwoNIntercomCallStateSensor` — derived from coordinator's `call_state`, exposes `active_session_id` attribute. **This is the attribute downstream automations use to terminate the exact session they answered.**

#### Switch (`switch.py`)

- Momentary relay control for door-type relays
- Self-resets after the configured pulse duration
- One entity per configured relay

#### Cover (`cover.py`)

- Garage-door-opener style control for gate-type relays
- Optimistic open/close with configurable duration (the IP Verso has no gate-position feedback)

#### Lock (`lock.py`)

- Backward-compatible legacy fallback used only when no relays are configured
- `is_locked` prefers cached `switch/status` for relay 1 and only falls back to optimistic state when `switch/caps` confirms relay 1 doesn't exist (transient missing payloads keep the optimistic state instead of flipping)

### 4. 2N API endpoints in use

| Endpoint | Auth | Purpose |
|---|---|---|
| `/api/system/info` | Basic | Device identity, credential validation |
| `/api/call/status` | Basic | Polling fallback for ring detection |
| `/api/call/answer`, `/api/call/hangup` | Basic | Service backends |
| `/api/log/subscribe`, `/api/log/pull`, `/api/log/unsubscribe` | Digest | Push-driven event channel |
| `/api/log/caps` | Digest | Discover supported event names |
| `/api/switch/caps`, `/api/switch/status`, `/api/switch/ctrl` | Digest | Relay caps + cached state + control |
| `/api/io/caps`, `/api/io/status` | Digest | Input caps + cached state |
| `/api/phone/status` | Basic | SIP registration sensor |
| `/api/camera/caps` | Basic | Discover MJPEG fps range and resolutions |
| `/api/camera/snapshot` | Basic | JPEG snapshot + MJPEG live view (`fps=1..15`) |
| RTSP stream | RTSP creds | Optional, only when licensed |

### 5. Services

Registered in `__init__.py` and declared in `services.yaml` with `target.config_entry` and proper selectors:

| Service | Selectors | Purpose |
|---|---|---|
| `2n_intercom.answer_call` | `config_entry_id`, `session_id` | Answer the active call (or specific session) |
| `2n_intercom.hangup_call` | `config_entry_id`, `session_id`, `reason` (`normal`/`rejected`/`busy`) | Hang up the active call (or specific session) |

### 6. HomeKit integration

- Camera + linked doorbell sensor → **Video Doorbell** accessory in HomeKit
- Door relay switch → Switch / Lock (depends on bridge filter)
- Gate relay cover → **Garage Door Opener** accessory
- Legacy lock entity → Lock or Garage Door Opener depending on `device_class`

See [HOMEKIT_INTEGRATION.md](HOMEKIT_INTEGRATION.md) for the YAML link snippet that's still needed for the doorbell tile.

### 7. Translations

- English (`en.json`)
- Czech (`cs.json`)

Both translations cover all config / options / reauth / reconfigure / abort / progress strings, and the new `services` section (`answer_call` and `hangup_call`).

### 8. Code quality

- All Python files compile cleanly (`python3 -m py_compile`)
- All JSON files validate
- `validate.py` enforces manifest compliance (`requirements: []`, `iot_class: local_push`, `integration_type: device`, `config_flow: true`, `version` present) and HACS HA min version (`2026.4.x`)
- 53/53 unit tests passing (`unittest.IsolatedAsyncioTestCase` + hand-rolled HA stubs, no `pytest-homeassistant-custom-component`)

## Entity summary

### Per configuration

**Camera + doorbell, no relays:**
- `camera.<name>_camera`
- `binary_sensor.<name>_doorbell`
- `binary_sensor.<name>_input_1`
- `binary_sensor.<name>_relay_1_active`
- `sensor.<name>_sip_registration`
- `sensor.<name>_call_state`
- `lock.<name>_lock` (legacy fallback)

**+ 1 door relay:**
- All of the above (without the legacy lock) plus
- `switch.<name>_<relay_name>`

**+ 1 door + 1 gate relay:**
- Add `cover.<name>_<gate_relay_name>`

**Maximum (4 relays):**
- Camera, doorbell, input/relay-active binary sensors, both diagnostic sensors, plus up to 4 switch and/or cover entities

## Configuration flow

1. **Add integration** → "2N Intercom"
2. **Connection step** → host, port, protocol, credentials, SSL verification (validated against `system/info`)
3. **Device step** → name, camera, doorbell, relay count, optional ringing peer
4. **Per-relay step** → name, physical relay number, type (door/gate), pulse duration
5. **Done** → entities created, log listener starts, services available

If credentials later become invalid, HA automatically opens the **reauth** flow. To change connection details without removing the entry, use the **reconfigure** flow from the integration's overflow menu.

## Technical highlights

### Async / I/O

- All HTTP I/O is async via aiohttp
- Single coordinator owns polling, caching, and the log subscription loop
- Static caps (`switch/caps`, `io/caps`, camera transport) are fetched **once** at setup, not on every poll
- The 5-second poll interval drives only status endpoints (`switch/status`, `io/status`, `phone/status`, `call/status`)

### Error handling and resilience

- `ConfigEntryAuthFailed` on credential rejection → reauth flow
- Persistent notification API uses the imported module (the legacy `hass.components.X` accessor was removed in HA 2025.1)
- Log listener loop catches subscribe failures and re-subscribes with exponential backoff (capped at ~60 s)
- Polling fallback keeps ringing detection alive even when the push channel is degraded
- Coordinator constructed with `config_entry=` so HA tags traces with the entry

### Performance

- Snapshot caching at the coordinator (single layer)
- Static caps cached once, not refetched per poll
- Push-driven events when available (no polling latency for ring detection)
- `MjpegCamera` serves frames natively to the HA frontend — no ffmpeg / HLS

### Compliance with HA 2026.4+

- Imported `homeassistant.components.persistent_notification` API
- OptionsFlow does not store `config_entry` (uses `self.config_entry` from the framework)
- `DataUpdateCoordinator` constructed with the `config_entry` kwarg
- `manifest.json`: `requirements: []`, `iot_class: local_push`, `integration_type: device`, `version: 1.1.0`
- `hacs.json`: `homeassistant: 2026.4.0`

## What's intentionally **not** implemented

These belong outside the fork or to a separate licence:

- Two-way audio
- Multi-tenant directory UX, keypad workflow, lift control
- License-dependent endpoints (Automation API, Audio Test, NFC, Noise Detection, SMTP, FTP, SNMP, TR069)
- The downstream HA automation that converts the entities into mobile actionable notifications + KNX door opening — that lives in the consuming HA configuration, not in the integration

## Breaking changes

### From 1.0.x → 1.1.0

- Camera entity is now backed by `MjpegCamera` — credentials are no longer embedded in stream URLs. Anything reading the previous credential-leaking URL out of HA logs/diagnostics needs to be updated; the camera entity itself works the same in dashboards and HomeKit
- Auth failures now raise `ConfigEntryAuthFailed` instead of looping on `ConfigEntryNotReady` + persistent notification — you'll see a reauth notification instead
- New shared `TwoNIntercomEntity` base — third-party patches that subclassed individual platform entities for `device_info` may need to drop their override

No data-model breakage; existing config entries load unchanged.

## Testing

```bash
python3 -m unittest discover -s tests -t tests   # 53/53 (or higher with new test_config_flow.py)
python3 validate.py                               # all green
python3 -m py_compile custom_components/2n_intercom/*.py
```

For end-to-end smoke testing against a real device, see [TESTING_GUIDE.md](TESTING_GUIDE.md).

## Statistics (as of 1.1.0)

- **Platforms:** 6 (camera, binary_sensor, sensor, switch, cover, lock)
- **APIs:** 11 endpoint families (Basic + Digest)
- **Services:** 2 (`answer_call`, `hangup_call`)
- **Languages:** 2 (English, Czech)
- **Tests:** 53+ unit tests
- **HA target:** 2026.4.0+

## Conclusion

The integration is feature-complete for the single-family-house IP Verso baseline: native MJPEG live view, push-driven ring detection with polling fallback, real-state status entities, answer/hangup services, reauth + reconfigure flows, and HA 2026.4+ compliance — all without ffmpeg in the camera path and without leaking credentials into logs.

---

*Status:* Production-ready against 2N IP Verso firmware `2.50.0.76.2`
*Version:* 1.1.0
*Repository:* mastalir1980/ha-2N-intercom (HA 2026.4+ remediation in the dsm-docker fork)
