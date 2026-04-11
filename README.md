# ha-2N-intercom

Home Assistant custom integration for 2N IP Intercom systems with camera, doorbell, relay, call control, and HomeKit support.

Verified against a 2N IP Verso running firmware **2.50.0.76.2** (no RTSP server license).

## Features

### Camera
- **JPEG snapshot** via `/api/camera/snapshot`
- **Native MJPEG live view** via `/api/camera/snapshot?...&fps=<n>` — served through Home Assistant's `MjpegCamera`, no ffmpeg/HLS round-trip
- **RTSP stream source** when the device exposes it (the integration probes once at setup and prefers MJPEG when RTSP is unlicensed/unavailable)
- **Credentials are passed to `MjpegCamera` separately** — they never appear in the URL exposed to logs, diagnostics, or dashboards
- HomeKit-compatible video doorbell

### Doorbell and call lifecycle
- **Push-driven ring detection** via `/api/log/subscribe` + `/api/log/pull` background loop with automatic re-subscribe and exponential backoff
- **Polling fallback** through `/api/call/status` when the push channel is degraded
- Binary sensor with caller name/number/button attributes
- **`2n_intercom.answer_call`** and **`2n_intercom.hangup_call`** services that target a config entry and (optionally) a specific session id, with `reason` selector (`normal`/`rejected`/`busy`)
- Diagnostic sensors: SIP registration status, call state with `active_session_id` attribute

### Door / Gate / Relay control
- **Switch** entities for door relays (momentary)
- **Cover** entities for gate relays (garage-door style)
- **Lock** fallback entity when no relays are configured
- Up to 4 relays, configurable pulse duration and per-relay name
- Relay/input states are read from the device (`switch/status`, `io/status`), not optimistic
- HomeKit accessory mapping per relay type

### Configuration
- UI-driven multi-step setup (connection → device → relays)
- **Reauth flow** — when credentials are rejected the integration raises `ConfigEntryAuthFailed`, so HA opens a notification asking the user to re-enter credentials instead of looping on `ConfigEntryNotReady`
- **Reconfigure flow** — change host/port/credentials/SSL without removing the entry (HA 2024.10+)
- Options flow for changing device features and per-relay settings
- Optional "Ringing account (peer)" filter for multi-button setups (`All calls` matches every button)

## Capability Matrix

| Category | Status | Notes |
|---|---|---|
| **Done** | JPEG snapshot, native MJPEG live view, RTSP stream source (when licensed), push-driven ring detection, polling fallback, relay/cover/lock control with real device state, SIP/call diagnostic sensors, answer/hangup services, reauth and reconfigure flows, HomeKit bridge mapping | All verified against 2N IP Verso 2.50.0.76.2 |
| **Out of fork scope** | Two-way audio, multi-tenant directory UX, keypad workflow, lift control, mass-notify | Not needed for a single-family-house deployment |
| **License-dependent** | Automation API, Audio Test, NFC, Noise Detection, SMTP, FTP, SNMP, TR069, Lift Control | Only available when the device license exposes them; not planned in this fork |

### Camera Support Baseline

Verified on a 2N IP Verso with firmware `2.50.0.76.2`:

- RTSP server license: `NO`
- JPEG snapshot works without `fps`
- MJPEG works via `/api/camera/snapshot?...&fps=<n>` over both HTTP and HTTPS
- Valid `fps` values are **`1..15`** (`fps >= 16` returns API error code `12`)
- Resolutions are taken from `camera/caps`. Observed values include
  `176x144`, `320x240`, `352x288`, `640x480`, `800x600`, `1280x960`, `160x120`, `352x272`, `480x272`, `1024x600`, `1280x720`, `640x360`

Helper: [`api.validate_mjpeg_fps`](custom_components/2n_intercom/api.py).

### Dual authentication scheme

The 2N HTTP API splits authentication between endpoint families. The integration's HTTP client handles this transparently — **don't try to "simplify" `_async_request`**.

| Endpoint family | Auth |
|---|---|
| `camera/*`, `phone/*`, `system/info`, `call/status` | HTTP Basic |
| `switch/*`, `io/*`, `log/*`, most control endpoints | HTTP Digest |

The client uses Digest by default and falls back to Basic when the device responds with `401 + WWW-Authenticate: Basic`.

## Architecture

- **DataUpdateCoordinator** centralises polling, caching, and the background log listener
- **MJPEG-first camera** built on `homeassistant.components.mjpeg.MjpegCamera`
- **Static caps cached once** at setup (`switch/caps`, `io/caps`, camera transport); only status endpoints poll on the 5-second interval
- **`TwoNIntercomEntity`** base class shared by all platforms — single source for `device_info`, `available`, and `_attr_has_entity_name`
- Platform-based: `camera`, `binary_sensor`, `switch`, `cover`, `lock`, `sensor`

For deeper architecture notes see [ARCHITECTURE.md](ARCHITECTURE.md).

## Manual

- Install and setup: [INSTALLATION.md](INSTALLATION.md)
- HomeKit details: [HOMEKIT_INTEGRATION.md](HOMEKIT_INTEGRATION.md)
- Quick reference: [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- Release notes: [CHANGELOG.md](CHANGELOG.md)
- MJPEG roadmap: [docs/superpowers/plans/2026-04-10-2n-ip-verso-mjpeg-roadmap.md](docs/superpowers/plans/2026-04-10-2n-ip-verso-mjpeg-roadmap.md)

## Installation

### HACS (recommended)

1. Open HACS → Integrations
2. Three-dot menu → Custom repositories
3. Add `https://github.com/mastalir1980/ha-2N-intercom` as an integration
4. Install **2N Intercom**
5. Restart Home Assistant

### Manual installation

1. Copy `custom_components/2n_intercom` into your HA `config/custom_components/`
2. Restart Home Assistant
3. Settings → Devices & Services → **+ Add Integration** → 2N Intercom

### Reconfiguring or re-authenticating

- **Reconfigure**: Settings → Devices & Services → 2N Intercom → ⋮ → **Reconfigure**. Lets you change host/port/protocol/credentials without removing the entry.
- **Reauth**: triggered automatically when the device starts rejecting credentials. HA shows a notification — click it to re-enter the password.

## Configuration

### Initial setup

1. **Connection**
   - IP address, port, protocol (HTTP/HTTPS), username, password, verify SSL

2. **Device features**
   - Display name, enable camera, enable doorbell, number of relays (0-4)
   - Optional **Ringing account (peer)** to limit doorbell events to one button (`All calls` rings on every button)

3. **Relays** (one step per relay)
   - Name, physical relay number (1-4), device type (door / gate), pulse duration (ms)
   - Door default: 2000 ms — Gate default: 15000 ms

### Example: single-family house (door + gate)

```
Connection
  Host: 192.168.2.20
  Protocol: HTTPS
  Username: homeassistant
  Password: ****

Device
  Name: Home Intercom
  Camera: yes
  Doorbell: yes
  Relays: 2

Relay 1 (Front Door, door, 2000 ms)
Relay 2 (Driveway Gate, gate, 15000 ms)
```

Resulting entities:

- `camera.home_intercom_camera`
- `binary_sensor.home_intercom_doorbell`
- `binary_sensor.home_intercom_input_1` *(real device input)*
- `binary_sensor.home_intercom_relay_1_active` *(real cached relay state)*
- `sensor.home_intercom_sip_registration`
- `sensor.home_intercom_call_state` *(attribute: `active_session_id`)*
- `switch.home_intercom_front_door`
- `cover.home_intercom_driveway_gate`

## Services

| Service | Purpose |
|---|---|
| `2n_intercom.answer_call` | Answer the active call session (or a specific `session_id`) |
| `2n_intercom.hangup_call` | Hang up the active call session (or a specific `session_id`) with optional `reason` (`normal` / `rejected` / `busy`) |

Both services target a config entry. From the UI use the **Target → Integration** picker; from YAML use `data.config_entry_id`. Pair `hangup_call` with `sensor.<intercom>_call_state`'s `active_session_id` attribute to terminate the exact session that triggered an automation.

Example actionable-notification snippet:

```yaml
- service: 2n_intercom.hangup_call
  data:
    session_id: "{{ state_attr('sensor.home_intercom_call_state', 'active_session_id') }}"
    reason: normal
```

## 2N API Endpoints Used

| Endpoint | Auth | Purpose |
|---|---|---|
| `/api/system/info` | Basic | Device identity |
| `/api/call/status` | Basic | Polling fallback for ring detection |
| `/api/call/answer`, `/api/call/hangup` | Basic | Service backends |
| `/api/log/subscribe`, `/api/log/pull`, `/api/log/unsubscribe` | Digest | Push-driven event stream |
| `/api/switch/caps`, `/api/switch/status`, `/api/switch/ctrl` | Digest | Relay control + cached state |
| `/api/io/caps`, `/api/io/status` | Digest | Cached input state |
| `/api/phone/status` | Basic | SIP registration sensor |
| `/api/camera/caps` | Basic | Discover MJPEG capability + resolutions |
| `/api/camera/snapshot` (with/without `fps`) | Basic | JPEG snapshot + MJPEG live view |
| RTSP stream | RTSP creds | Optional, only when device exposes it |

## Troubleshooting

### Cannot Connect during setup
- Verify IP/port/credentials and protocol; firewall; SSL trust if HTTPS
- The HA log will show whether the integration is hitting a Basic-only or Digest-only endpoint — credentials need to be valid for both

### Camera entity has no live view
- Confirm `camera/caps` returns `mjpeg.fps_min`/`fps_max` (the integration's transport probe runs once at setup; reload the entry after changing camera caps on the device)
- Open the entity attributes — `live_view_selected_mode` should be `mjpeg`, `mjpeg_available` should be `true`
- For RTSP, make sure the RTSP server licence is enabled on the device

### Doorbell not triggering
- Open the integration's diagnostics; the log subscription id should be set
- Press the button and watch HA logs — the push path should flip the binary sensor within ~1 s
- The polling fallback (5 s) keeps ringing detection alive even if the push path drops

### Reauth notification keeps appearing
- Credentials really are wrong, or the device locked the account. Check the 2N web UI under **Services → HTTP API → Account** and re-enter the password through the reauth flow

## Development

### Project Structure

```
custom_components/2n_intercom/
├── __init__.py              # Integration setup, services, listener lifecycle
├── api.py                   # 2N API client (Basic + Digest auth)
├── coordinator.py           # DataUpdateCoordinator + push log loop
├── config_flow.py           # User / reauth / reconfigure / options flows
├── const.py                 # Constants
├── entity.py                # Shared TwoNIntercomEntity base
├── camera.py                # MjpegCamera-based camera platform
├── binary_sensor.py         # Doorbell + input + relay-active sensors
├── sensor.py                # SIP registration + call state diagnostic sensors
├── switch.py                # Door relay platform
├── cover.py                 # Gate relay platform
├── lock.py                  # Legacy lock fallback
├── manifest.json
├── services.yaml
├── strings.json
└── translations/
    ├── en.json
    └── cs.json
```

### Tests

```bash
python3 -m unittest discover -s tests -t tests   # 53/53
python3 validate.py                               # manifest + HACS compliance
python3 -m py_compile custom_components/2n_intercom/*.py
```

## Version History

### 1.1.0
- HA 2026.4+ compliance: imported `persistent_notification` API; OptionsFlow no longer stores `config_entry`; coordinator constructed with `config_entry` kwarg
- Manifest: `requirements: []`, `iot_class: local_push`, `integration_type: device`
- Camera switched to `MjpegCamera`; credentials are no longer embedded in stream URLs
- Push-driven log subscription with re-subscribe and exponential backoff
- Reauth flow (`async_step_reauth`) and reconfigure flow (`async_step_reconfigure`)
- New `2n_intercom.answer_call` / `2n_intercom.hangup_call` services with `target.config_entry`
- New diagnostic sensors (`sip_registration`, `call_state`) and real-state binary sensors (`input_1`, `relay_1_active`)
- Static caps (switch / io / camera transport) resolved once at setup
- Shared `TwoNIntercomEntity` base; deduped `device_info` across all platforms
- HACS metadata bumped to HA 2026.4.0

### 1.0.1
- Fix HomeKit entity exposure when relays are configured
- Ensure relay entities load from options
- Document MJPEG device baseline

### 1.0.0
- Initial public release

## License

See [LICENSE](LICENSE).

## Credits

Created for the Home Assistant community. Developed by mastalir1980; HA 2026.4+ remediation by the dsm-docker fork.

## Support

Open an issue on GitHub.
