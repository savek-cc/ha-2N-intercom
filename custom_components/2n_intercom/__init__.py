"""The 2N Intercom integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import TwoNIntercomAPI
from .const import (
    CONF_CALLED_ID,
    CONF_ENABLE_CAMERA,
    CONF_ENABLE_DOORBELL,
    CONF_PROTOCOL,
    CONF_RELAYS,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_ENABLE_CAMERA,
    DEFAULT_ENABLE_DOORBELL,
    DEFAULT_PROTOCOL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    SCAN_INTERVAL_MAX,
    SCAN_INTERVAL_MIN,
)
from .coordinator import TwoNIntercomCoordinator

_LOGGER = logging.getLogger(__name__)
_CALL_SERVICE_FLAG = "_call_services_registered"
_VALID_HANGUP_REASONS = {"normal", "rejected", "busy"}


def _get_entry_data(entry: ConfigEntry) -> dict[str, object]:
    """Return merged config data with options overriding defaults."""
    return {**entry.data, **entry.options}


def _get_platforms(entry: ConfigEntry) -> list[str]:
    """Get list of platforms to set up based on configuration."""
    data = _get_entry_data(entry)
    platforms: list[str] = []

    if data.get(CONF_ENABLE_CAMERA, DEFAULT_ENABLE_CAMERA):
        platforms.append("camera")

    if data.get(CONF_ENABLE_DOORBELL, DEFAULT_ENABLE_DOORBELL):
        platforms.append("binary_sensor")

    relays = data.get(CONF_RELAYS, [])
    if relays:
        platforms.extend(["switch", "cover"])
    else:
        platforms.append("lock")

    platforms.append("sensor")

    return platforms


def _get_loaded_entries(hass: HomeAssistant) -> dict[str, dict[str, object]]:
    """Return loaded config-entry data for this integration."""
    domain_data = hass.data.get(DOMAIN, {})
    entries: dict[str, dict[str, object]] = {}
    for entry_id, entry_data in domain_data.items():
        if not isinstance(entry_data, dict):
            continue
        if "coordinator" not in entry_data or "api" not in entry_data:
            continue
        entries[str(entry_id)] = entry_data
    return entries


def _resolve_service_entry(
    hass: HomeAssistant,
    service_data: dict[str, Any],
) -> dict[str, object]:
    """Return the target config-entry data for a service call."""
    entries = _get_loaded_entries(hass)
    if not entries:
        raise HomeAssistantError("2N Intercom has no loaded config entries.")

    config_entry_id = service_data.get("config_entry_id")
    if config_entry_id:
        entry = entries.get(str(config_entry_id))
        if entry is None:
            raise HomeAssistantError(
                f"Config entry {config_entry_id!r} is not loaded for 2N Intercom."
            )
        return entry

    if len(entries) > 1:
        raise HomeAssistantError(
            "Multiple 2N Intercom config entries are loaded; include config_entry_id."
        )

    return next(iter(entries.values()))


def _resolve_session_id(
    entry_data: dict[str, object],
    service_data: dict[str, Any],
) -> str:
    """Return the call session id to act on."""
    session_id = service_data.get("session_id")
    if session_id is not None and str(session_id).strip():
        return str(session_id).strip()

    coordinator = entry_data["coordinator"]
    coordinator_session_id = getattr(coordinator, "active_session_id", None)
    if coordinator_session_id is not None and str(coordinator_session_id).strip():
        return str(coordinator_session_id).strip()

    raise HomeAssistantError("No active call session is available to target.")


def _extract_session_ids_from_status(status: Any) -> list[str]:
    """Pull session ids out of /api/call/status `result` payload."""
    if not isinstance(status, dict):
        return []
    sessions = status.get("sessions")
    if not isinstance(sessions, list):
        return []
    out: list[str] = []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        raw = session.get("session")
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            out.append(text)
    return out


def _register_call_services(hass: HomeAssistant) -> None:
    """Register call-control services once per Home Assistant instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_CALL_SERVICE_FLAG):
        return

    async def _async_answer_call(call: Any) -> None:
        service_data = dict(getattr(call, "data", {}) or {})
        entry_data = _resolve_service_entry(hass, service_data)
        session_id = _resolve_session_id(entry_data, service_data)
        api = entry_data["api"]
        if not await api.async_answer_call(session_id):
            raise HomeAssistantError(f"Failed to answer call session {session_id}.")

    async def _async_hangup_call(call: Any) -> None:
        """Hang up active 2N call sessions.

        Idempotent: if no session id is supplied and no session can be
        discovered live on the device, the call is treated as a successful
        no-op (the desired post-condition — no active call — already holds).
        When the caller does not pin a specific session, every active session
        on the device is hung up so a stale cached id can never strand a real
        ringing call.

        ``reason`` is only forwarded to the device when the caller passes an
        explicit valid value. The default leaves it unset, because firmware
        2.50.0.76.2 silently ignores hangups carrying a ``reason`` for
        outgoing-ringing sessions while still answering ``success: true``.
        """
        service_data = dict(getattr(call, "data", {}) or {})
        entry_data = _resolve_service_entry(hass, service_data)
        api = entry_data["api"]

        raw_reason = service_data.get("reason")
        if isinstance(raw_reason, str):
            normalized_reason = raw_reason.strip().lower()
        else:
            normalized_reason = ""
        reason: str | None = (
            normalized_reason if normalized_reason in _VALID_HANGUP_REASONS else None
        )

        explicit_session = service_data.get("session_id")
        if explicit_session is not None and str(explicit_session).strip():
            session_id = str(explicit_session).strip()
            if not await api.async_hangup_call(session_id, reason=reason):
                raise HomeAssistantError(
                    f"Failed to hang up call session {session_id}"
                    + (f" with reason {reason}." if reason else ".")
                )
            return

        # No explicit session id — ask the device for the live truth instead
        # of trusting whatever the coordinator last cached. This avoids two
        # observed failure modes: (1) the cached active_session_id was already
        # cleared by the polling loop, and (2) it points at a session the
        # device has since terminated, in which case the device returns
        # code 14 "session not found".
        try:
            status = await api.async_get_call_status()
        except Exception as err:  # noqa: BLE001 — surface as service error
            raise HomeAssistantError(
                f"Could not query 2N call status before hangup: {err}"
            ) from err

        _LOGGER.debug(
            "2n_intercom.hangup_call: live /api/call/status payload = %s",
            status,
        )

        live_sessions = _extract_session_ids_from_status(status)
        coordinator = entry_data["coordinator"]
        cached = getattr(coordinator, "active_session_id", None)
        _LOGGER.debug(
            "2n_intercom.hangup_call: extracted live sessions=%s, coordinator cached=%s",
            live_sessions,
            cached,
        )
        if not live_sessions:
            # Fall back to whatever the coordinator thought was active so a
            # very-recently-ended call still gets a best-effort hangup;
            # otherwise this is a genuine no-op and we succeed silently.
            if cached is not None and str(cached).strip():
                live_sessions = [str(cached).strip()]
            else:
                _LOGGER.debug(
                    "2n_intercom.hangup_call: no active call sessions; nothing to do"
                )
                return

        failures: list[str] = []
        for session_id in live_sessions:
            _LOGGER.debug(
                "2n_intercom.hangup_call: dispatching hangup for session=%s reason=%s",
                session_id,
                reason,
            )
            ok = await api.async_hangup_call(session_id, reason=reason)
            _LOGGER.debug(
                "2n_intercom.hangup_call: hangup session=%s result=%s",
                session_id,
                ok,
            )
            if not ok:
                failures.append(session_id)

        if failures:
            raise HomeAssistantError(
                "Failed to hang up call session(s) "
                f"{', '.join(failures)}"
                + (f" with reason {reason}." if reason else ".")
            )

    hass.services.async_register(DOMAIN, "answer_call", _async_answer_call)
    hass.services.async_register(DOMAIN, "hangup_call", _async_hangup_call)
    domain_data[_CALL_SERVICE_FLAG] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up 2N Intercom from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    data = _get_entry_data(entry)
    api = TwoNIntercomAPI(
        host=data[CONF_HOST],
        port=data[CONF_PORT],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        protocol=data.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
        verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    # Honour the per-entry polling interval from the options flow when set,
    # falling back to the module default. Out-of-bounds values are clamped so
    # a hand-edited entry can't accidentally hammer the device or stall ring
    # detection — the options-flow selector enforces the same range.
    raw_scan_interval = data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    try:
        scan_interval = int(raw_scan_interval)
    except (TypeError, ValueError):
        scan_interval = DEFAULT_SCAN_INTERVAL
    scan_interval = max(SCAN_INTERVAL_MIN, min(scan_interval, SCAN_INTERVAL_MAX))

    coordinator = TwoNIntercomCoordinator(
        hass,
        api,
        scan_interval=scan_interval,
        called_id=data.get(CONF_CALLED_ID),
        config_entry=entry,
    )

    # Resolve static device descriptors (system info, switch/io caps, camera
    # transport) once at setup so the per-tick refresh stays focused on real
    # status data and doesn't burn ~17k requests/day re-fetching constants.
    await coordinator.async_initialize_static_caches()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "api": api,
    }

    _register_call_services(hass)
    await coordinator.async_start_log_listener()

    # Stop the long-poll log listener early in HA shutdown so the in-flight
    # /api/log/pull request can wind down before HA's final-writes stage,
    # otherwise the task is reported as "still running after final writes
    # shutdown stage". Wrapping in entry.async_on_unload makes the listener
    # auto-remove on entry reload so it never stacks.
    async def _async_stop_log_listener_on_shutdown(_event: Event | None = None) -> None:
        await coordinator.async_stop_log_listener()

    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _async_stop_log_listener_on_shutdown
        )
    )

    platforms = _get_platforms(entry)
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    # Remember exactly which platforms were forwarded so unload tears down the
    # same set even if the user later changes options that would shift
    # _get_platforms() output (e.g. enabling relays flips lock <-> switch+cover).
    hass.data[DOMAIN][entry.entry_id]["loaded_platforms"] = list(platforms)

    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Use the platform list captured at setup so we tear down exactly what was
    # forwarded; recomputing from the merged data here would lie if options
    # changed (e.g. relay_count went from 0 to 1) and try to unload platforms
    # that were never loaded.
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    platforms = entry_data.get("loaded_platforms") or _get_platforms(entry)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        if "coordinator" in data:
            await data["coordinator"].async_stop_log_listener()
        if "api" in data:
            await data["api"].async_close()

    return unload_ok
