"""The 2N Intercom integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import TwoNIntercomAPI
from .const import (
    CONF_CALLED_ID,
    CONF_ENABLE_CAMERA,
    CONF_ENABLE_DOORBELL,
    CONF_PROTOCOL,
    CONF_RELAYS,
    CONF_VERIFY_SSL,
    DEFAULT_ENABLE_CAMERA,
    DEFAULT_ENABLE_DOORBELL,
    DEFAULT_PROTOCOL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
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
        service_data = dict(getattr(call, "data", {}) or {})
        entry_data = _resolve_service_entry(hass, service_data)
        session_id = _resolve_session_id(entry_data, service_data)
        raw_reason = service_data.get("reason")
        if isinstance(raw_reason, str):
            normalized_reason = raw_reason.strip().lower()
        else:
            normalized_reason = ""
        reason = (
            normalized_reason
            if normalized_reason in _VALID_HANGUP_REASONS
            else "normal"
        )
        api = entry_data["api"]
        if not await api.async_hangup_call(session_id, reason=reason):
            raise HomeAssistantError(
                f"Failed to hang up call session {session_id} with reason {reason}."
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

    coordinator = TwoNIntercomCoordinator(
        hass,
        api,
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

    platforms = _get_platforms(entry)
    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = _get_platforms(entry)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        if "coordinator" in data:
            await data["coordinator"].async_stop_log_listener()
        if "api" in data:
            await data["api"].async_close()

    return unload_ok
