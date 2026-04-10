"""Camera platform for 2N Intercom."""
from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CameraTransportInfo
from .const import (
    DEFAULT_LIVE_VIEW_MODE,
    DOMAIN,
    LIVE_VIEW_MODE_MJPEG,
    LIVE_VIEW_MODE_RTSP,
)
from .coordinator import TwoNIntercomCoordinator

_LOGGER = logging.getLogger(__name__)

# Cache snapshots for 1 second to avoid excessive API calls
SNAPSHOT_CACHE_DURATION = timedelta(seconds=1)


def _transport_has_live_view(transport_info: CameraTransportInfo) -> bool:
    """Return whether the selected transport yields a live-view URL."""
    if not transport_info.resolved:
        return False
    return transport_info.selected_mode in (LIVE_VIEW_MODE_RTSP, LIVE_VIEW_MODE_MJPEG)


def get_stream_source_for_transport(
    api,
    transport_info: CameraTransportInfo,
) -> str | None:
    """Build a stream source URL for the chosen transport."""
    if not _transport_has_live_view(transport_info):
        return None

    if transport_info.selected_mode == LIVE_VIEW_MODE_RTSP:
        return api.get_rtsp_url_with_credentials()

    if transport_info.selected_mode == LIVE_VIEW_MODE_MJPEG:
        return api.build_mjpeg_url(
            width=transport_info.mjpeg_width,
            height=transport_info.mjpeg_height,
            fps=transport_info.mjpeg_fps,
            source=transport_info.source,
            include_auth=not transport_info.mjpeg_public_url_available,
        )

    return None


def get_supported_features_for_transport(
    transport_info: CameraTransportInfo,
) -> CameraEntityFeature:
    """Return the camera feature flags for the selected transport."""
    if _transport_has_live_view(transport_info):
        return CameraEntityFeature.STREAM
    return CameraEntityFeature(0)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up 2N Intercom camera platform."""
    coordinator: TwoNIntercomCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    
    async_add_entities(
        [TwoNIntercomCamera(coordinator, config_entry)],
        True,
    )


class TwoNIntercomCamera(CoordinatorEntity[TwoNIntercomCoordinator], Camera):
    """Representation of a 2N Intercom camera."""

    _attr_has_entity_name = True
    _attr_name = "Camera"
    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        Camera.__init__(self)
        
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_camera"
        self._last_snapshot: bytes | None = None
        self._last_snapshot_time: float = 0
        self._transport_info = coordinator.api.camera_transport_info

    async def async_added_to_hass(self) -> None:
        """Schedule transport detection after the entity is added."""
        await super().async_added_to_hass()
        if self.hass is not None:
            self.hass.async_create_task(self._async_refresh_transport_info())

    async def _async_refresh_transport_info(self) -> CameraTransportInfo:
        """Refresh cached transport info from the API."""
        self._transport_info = await self.coordinator.api.async_get_camera_transport_info(
            requested_mode=DEFAULT_LIVE_VIEW_MODE,
        )
        self.async_write_ha_state()
        return self._transport_info

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information about this camera."""
        name = self._config_entry.options.get(
            "name",
            self._config_entry.data.get("name", "2N Intercom"),
        )
        return self.coordinator.get_device_info(self._config_entry.entry_id, name)

    @property
    def is_recording(self) -> bool:
        """Return true if the device is recording."""
        return False

    @property
    def motion_detection_enabled(self) -> bool:
        """Return the camera motion detection status."""
        return False

    @property
    def brand(self) -> str:
        """Return the camera brand."""
        return "2N"

    @property
    def model(self) -> str:
        """Return the camera model."""
        return "IP Intercom"

    @property
    def supported_features(self) -> CameraEntityFeature:
        """Return supported features based on the selected live-view transport."""
        return get_supported_features_for_transport(self._transport_info)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose camera capability and transport details."""
        transport_info = self._transport_info
        return {
            "live_view_requested_mode": transport_info.requested_mode,
            "live_view_selected_mode": transport_info.selected_mode,
            "live_view_resolved": transport_info.resolved,
            "live_view_available": transport_info.live_view_available,
            "rtsp_available": transport_info.rtsp_available,
            "mjpeg_available": transport_info.mjpeg_available,
            "mjpeg_public_url_available": transport_info.mjpeg_public_url_available,
            "jpeg_snapshot_available": transport_info.jpeg_snapshot_available,
            "camera_source": transport_info.source,
            "mjpeg_width": transport_info.mjpeg_width,
            "mjpeg_height": transport_info.mjpeg_height,
            "mjpeg_fps": transport_info.mjpeg_fps,
            "camera_sources": list(transport_info.capabilities.sources),
            "camera_resolutions": [
                resolution.as_string()
                for resolution in transport_info.capabilities.jpeg_resolutions
            ],
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return bytes of camera image."""
        # Check cache to avoid excessive API calls
        current_time = time.time()
        
        if (
            self._last_snapshot is not None
            and current_time - self._last_snapshot_time < SNAPSHOT_CACHE_DURATION.total_seconds()
        ):
            return self._last_snapshot
        
        # Fetch new snapshot
        snapshot = await self.coordinator.async_get_snapshot(
            width=width, height=height
        )
        
        if snapshot:
            self._last_snapshot = snapshot
            self._last_snapshot_time = current_time
        
        return snapshot

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        transport_info = await self._async_refresh_transport_info()
        return get_stream_source_for_transport(self.coordinator.api, transport_info)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success
