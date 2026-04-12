# ha-2N-intercom

Home Assistant custom integration for 2N IP Intercom systems with camera, doorbell, relay, call control, and HomeKit support.

Verified against two 2N IP Verso devices running firmware **2.50.0.76.2** — one without RTSP license (MJPEG only) and one with RTSP license and separate RTSP credentials.

## Features

### Camera
- **JPEG snapshot** via `/api/camera/snapshot`
- **Native MJPEG live view** via `/api/camera/snapshot?...&fps=<n>` — served through Home Assistant's `MjpegCamera`, no ffmpeg/HLS round-trip
- **RTSP stream source** when the device exposes it — requires separate RTSP credentials configured in the options flow (the 2N RTSP server has its own user database independent of the HTTP API accounts)
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

### Authentication

The 2N HTTP API exposes a **per-service-group** authentication setting in the device web UI under **Services → HTTP API**. Each service group (Camera, Switch, I/O, Phone, Call, Log, …) can be set to **None / Basic / Digest** independently, so a single account can end up answering some endpoint families with Basic and others with Digest depending on how the operator has configured the device.

The integration handles every combination transparently:

- The HTTP client uses `aiohttp`'s `DigestAuthMiddleware` (with `preemptive=False`) so it answers Digest challenges automatically.
- When the device answers a request with `401 + WWW-Authenticate: Basic`, the client retries the same request with Basic auth.

The same username and password must work for every service group the integration touches. **Don't try to "simplify" `_async_request`** by hard-coding one scheme — the dual-auth path exists exactly because the device exposes the choice per service group.

#### RTSP authentication

The 2N RTSP server has its **own user database**, completely independent of the HTTP API accounts. RTSP credentials are configured on the device under **Services → Streaming → RTSP Server → User Database**. There is no fallback from HTTP API credentials to RTSP credentials — they are separate systems.

The integration provides optional **RTSP Username** and **RTSP Password** fields in the camera options step. When configured:

- The RTSP probe performs a full Digest authentication handshake (unauthenticated OPTIONS → 401 challenge → authenticated OPTIONS) to validate that the credentials actually work before marking RTSP as available.
- RTSP URLs embed the credentials for the HA stream worker (`rtsp://user:pass@host:554/h264_stream`). Special characters in credentials are URL-encoded.
- Without RTSP credentials, the integration will not attempt RTSP even if the device has a valid RTSP license — it falls back to MJPEG.

## Architecture

- **DataUpdateCoordinator** centralises polling, caching, and the background log listener
- **MJPEG-first camera** built on `homeassistant.components.mjpeg.MjpegCamera`
- **Static caps cached once** at setup (`switch/caps`, `io/caps`, camera transport); only status endpoints poll on the 5-second interval
- **`TwoNIntercomEntity`** base class shared by all platforms — single source for `device_info`, `available`, and `_attr_has_entity_name`
- Platform-based: `camera`, `binary_sensor`, `switch`, `cover`, `lock`, `sensor`

## Manual

- Install and setup: [INSTALLATION.md](INSTALLATION.md)
- HomeKit details: [HOMEKIT_INTEGRATION.md](HOMEKIT_INTEGRATION.md)
- Implementation overview: [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)
- Release notes: [CHANGELOG.md](CHANGELOG.md)

## Installation

### HACS (recommended)

1. Open HACS → Integrations
2. Three-dot menu → Custom repositories
3. Add `https://github.com/savek-cc/ha-2N-intercom` as an integration
4. Install **2N Intercom**
5. Restart Home Assistant

### Manual installation

1. Copy `custom_components/2n_intercom` into your HA `config/custom_components/`
2. Restart Home Assistant
3. Settings → Devices & Services → **+ Add Integration** → 2N Intercom

### Removing the integration

1. Settings → Devices & Services → 2N Intercom
2. Click the three-dot menu (⋮) on the integration card → **Delete**
3. Confirm the removal

All entities, devices, and automation references created by this integration are removed immediately. No restart is required. If you installed via HACS, you can also uninstall the repository entry from HACS → Integrations afterwards to stop receiving update notifications.

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

### Initial setup parameters

| Step | Parameter | Type | Default | Description |
|---|---|---|---|---|
| Connection | `host` | string | *(required)* | IP address or hostname of the intercom |
| Connection | `port` | int | 443 (HTTPS) / 80 (HTTP) | HTTP API port |
| Connection | `protocol` | `http` \| `https` | `https` | Transport protocol |
| Connection | `username` | string | *(required)* | Device API username |
| Connection | `password` | string | *(required)* | Device API password |
| Connection | `verify_ssl` | bool | `false` | Validate the HTTPS certificate (enable only if trusted by HA) |
| Device | `name` | string | `2N Intercom` | Display name in Home Assistant |
| Device | `enable_camera` | bool | `true` | Create the camera entity |
| Device | `enable_doorbell` | bool | `true` | Create the doorbell binary sensor |
| Device | `relay_count` | 0-4 | 1 | Number of relays to configure |
| Device | `called_id` | string | `All calls` | Ringing account / peer filter |
| Relay | `relay_name` | string | `Relay N` | Display name for this relay |
| Relay | `relay_number` | 1-4 | *(sequential)* | Physical relay number on the device |
| Relay | `relay_device_type` | `door` \| `gate` | `door` | Door → switch entity, Gate → cover entity |
| Relay | `relay_pulse_duration` | int (ms) | 2000 (door) / 15000 (gate) | How long the relay stays triggered |

### Options flow parameters

After initial setup, open the integration's **Options** (Settings → Devices & Services → 2N Intercom → **Configure**) to change behavioral settings without removing the entry. Connection settings are changed through the **Reconfigure** flow instead.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | string | *(from setup)* | Display name |
| `enable_camera` | bool | `true` | Toggle camera entity |
| `enable_doorbell` | bool | `true` | Toggle doorbell entity |
| `scan_interval` | 2-300 (s) | 5 | Polling interval. Lower = faster ring detection, higher device load |
| `relay_count` | 0-4 | *(from setup)* | Number of relays |
| `door_type` | `door` \| `gate` | *(derived)* | Legacy lock device type |
| `called_id` | string | `All calls` | Ringing account / peer filter |
| `live_view_mode` | `auto` \| `rtsp` \| `mjpeg` \| `jpeg_only` | `auto` | Camera live view transport. `auto` picks RTSP if licensed and RTSP credentials are set, then MJPEG, then snapshots |
| `rtsp_username` | string | *(empty)* | RTSP server username (from the 2N RTSP user database, **not** the HTTP API account) |
| `rtsp_password` | string | *(empty)* | RTSP server password |
| `camera_source` | `internal` \| `external` | `internal` | Which camera sensor to stream (external = secondary module) |
| `mjpeg_width` | 160-2592 (px) | 1280 | MJPEG stream width |
| `mjpeg_height` | 160-2592 (px) | 960 | MJPEG stream height |
| `mjpeg_fps` | 1-15 | 10 | MJPEG frame rate. Lower values reduce bandwidth |

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

The integration negotiates Basic vs Digest per request — see the **Authentication** section above.

| Endpoint | Purpose |
|---|---|
| `/api/system/info` | Device identity |
| `/api/call/status` | Polling fallback for ring detection |
| `/api/call/answer`, `/api/call/hangup` | Service backends |
| `/api/log/subscribe`, `/api/log/pull`, `/api/log/unsubscribe` | Push-driven event stream |
| `/api/switch/caps`, `/api/switch/status`, `/api/switch/ctrl` | Relay control + cached state |
| `/api/io/caps`, `/api/io/status` | Cached input state |
| `/api/phone/status` | SIP registration sensor |
| `/api/camera/caps` | Discover MJPEG capability + resolutions |
| `/api/camera/snapshot` (with/without `fps`) | JPEG snapshot + MJPEG live view |
| RTSP stream | Optional, only when the device licence exposes it |

## Troubleshooting

### Cannot Connect during setup
- Verify IP/port/credentials and protocol; firewall; SSL trust if HTTPS
- The same username and password must be valid for **both** Basic and Digest auth on the device — if the device rejects one of the schemes, every endpoint that uses it will fail. Configure the account under **Services → HTTP API → Account** on the 2N web UI

### Camera entity has no live view
- Confirm `camera/caps` returns `mjpeg.fps_min`/`fps_max` (the integration's transport probe runs once at setup; reload the entry after changing camera caps on the device)
- Open the entity attributes — `live_view_selected_mode` should be `mjpeg`, `mjpeg_available` should be `true`

### RTSP stream not working
- Make sure the RTSP server licence is enabled on the device
- Configure RTSP credentials in the integration's options flow (Settings → Devices & Services → 2N Intercom → Configure → Camera step). The RTSP server has its own user database — HTTP API credentials do **not** work for RTSP
- Create an RTSP user on the device: **Services → Streaming → RTSP Server → User Database**
- The entity attributes should show `rtsp_available: true` and `live_view_selected_mode: rtsp` when RTSP is working
- If RTSP credentials are wrong, the integration logs a warning and falls back to MJPEG

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
python3 -m unittest discover -s tests -t tests   # 411/411
python3 validate.py                               # manifest + HACS compliance
python3 -m py_compile custom_components/2n_intercom/*.py
```

## Version History

See [CHANGELOG.md](CHANGELOG.md) for the full release notes.

## License

See [LICENSE](LICENSE).

## Credits

Originally created by [mastalir1980](https://github.com/mastalir1980/ha-2N-intercom) for the Home Assistant community. The HA 2026.4+ remediation, MJPEG-first camera, push-driven event handling, real-state status entities, answer/hangup services, and dual-auth client live in this [savek-cc](https://github.com/savek-cc/ha-2N-intercom) fork.

## Support

Open an issue on the [savek-cc fork](https://github.com/savek-cc/ha-2N-intercom/issues).
