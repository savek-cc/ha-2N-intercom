"""Diagnostics support for 2N Intercom.

Implements the HA quality-scale ``diagnostics`` rule: a redacted dump of
the config entry plus the coordinator's cached device payloads, intended
for users to attach to bug reports without leaking credentials.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import TwoNIntercomRuntimeData

if TYPE_CHECKING:
    from .coordinator import TwoNIntercomConfigEntry

# Credentials for the device web API. Host/port are intentionally NOT
# redacted because they're load-bearing for any meaningful triage.
TO_REDACT_ENTRY = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TwoNIntercomConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a 2N Intercom config entry."""
    runtime: TwoNIntercomRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    transport = coordinator.camera_transport_info

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT_ENTRY),
            "options": async_redact_data(dict(entry.options), TO_REDACT_ENTRY),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else None
            ),
            "loaded_platforms": list(runtime.loaded_platforms),
        },
        "device": {
            "system_info": coordinator.system_info,
            "phone_status": coordinator.phone_status,
            "switch_caps": coordinator.switch_caps,
            "switch_status": coordinator.switch_status,
            "io_caps": coordinator.io_caps,
            "io_status": coordinator.io_status,
        },
        "call_state": {
            "active_session_id": coordinator.active_session_id,
            "call_state": coordinator.call_state,
            "ring_active": coordinator.ring_active,
            "called_peer": coordinator.called_peer,
        },
        "camera_transport": {
            "requested_mode": transport.requested_mode,
            "selected_mode": transport.selected_mode,
            "resolved": transport.resolved,
            "live_view_available": transport.live_view_available,
            "rtsp_available": transport.rtsp_available,
            "mjpeg_available": transport.mjpeg_available,
            "mjpeg_public_url_available": transport.mjpeg_public_url_available,
            "jpeg_snapshot_available": transport.jpeg_snapshot_available,
            "source": transport.source,
            "mjpeg_width": transport.mjpeg_width,
            "mjpeg_height": transport.mjpeg_height,
            "mjpeg_fps": transport.mjpeg_fps,
            "available_sources": list(transport.capabilities.sources),
        },
    }
