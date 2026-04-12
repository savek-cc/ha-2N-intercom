"""Camera platform for 2N Intercom."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.mjpeg import MjpegCamera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CameraTransportInfo, TwoNIntercomAPI
from .const import (
    LIVE_VIEW_MODE_MJPEG,
    LIVE_VIEW_MODE_RTSP,
)
from .coordinator import TwoNIntercomCoordinator, TwoNIntercomRuntimeData

# Snapshots are served from the coordinator's cache and the live MJPEG stream
# is proxied by the core MjpegCamera helper, so the camera platform never hits
# the device on async_update — unlimited concurrency is correct here.
PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


def _transport_has_live_view(transport_info: CameraTransportInfo) -> bool:
    """Return whether the selected transport yields a live-view URL."""
    if not transport_info.resolved:
        return False
    return transport_info.selected_mode in (LIVE_VIEW_MODE_RTSP, LIVE_VIEW_MODE_MJPEG)


def get_stream_source_for_transport(
    api: TwoNIntercomAPI,
    transport_info: CameraTransportInfo,
) -> str | None:
    """Build a stream source URL for the chosen transport.

    The MJPEG path always returns a credentials-free URL — the camera
    entity passes username/password to ``MjpegCamera`` directly. RTSP
    keeps creds in the URI because that's how the protocol works.
    """
    if not _transport_has_live_view(transport_info):
        return None

    if transport_info.selected_mode == LIVE_VIEW_MODE_RTSP:
        result: str | None = api.get_rtsp_url_with_credentials()
        return result

    if transport_info.selected_mode == LIVE_VIEW_MODE_MJPEG:
        url: str = api.build_mjpeg_url(
            width=transport_info.mjpeg_width,
            height=transport_info.mjpeg_height,
            fps=transport_info.mjpeg_fps,
            source=transport_info.source,
        )
        return url

    return None


def get_supported_features_for_transport(
    transport_info: CameraTransportInfo,
) -> CameraEntityFeature:
    """Return the camera feature flags for the selected transport.

    Only RTSP transports advertise ``STREAM`` — the HA stream component
    (ffmpeg → HLS/WebRTC) requires a stream source URL from
    ``stream_source()``.  MJPEG is served natively by ``MjpegCamera``
    without ffmpeg, so advertising STREAM for it would make the frontend
    try the stream component, get ``None`` from ``stream_source()``, and
    fail with "does not support play stream service".
    """
    if transport_info.resolved and transport_info.selected_mode == LIVE_VIEW_MODE_RTSP:
        return CameraEntityFeature.STREAM
    return CameraEntityFeature(0)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up 2N Intercom camera platform."""
    runtime: TwoNIntercomRuntimeData = config_entry.runtime_data
    coordinator: TwoNIntercomCoordinator = runtime.coordinator

    async_add_entities(
        [TwoNIntercomCamera(coordinator, config_entry)],
        True,
    )


class TwoNIntercomCamera(
    CoordinatorEntity[TwoNIntercomCoordinator], MjpegCamera
):
    """Representation of a 2N Intercom camera.

    Inherits from ``MjpegCamera`` so the live MJPEG stream is served
    natively to the frontend without ffmpeg, and so credentials are
    passed via the ``username``/``password`` fields instead of being
    embedded in the URL (which would leak them into HA logs and
    diagnostics — see roadmap §8 H1).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "camera"

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the camera."""
        api = coordinator.api
        transport_info = coordinator.camera_transport_info

        # Build credentials-free URLs; auth is passed below as separate
        # username/password fields and applied via HTTP Basic at fetch time.
        mjpeg_url = api.build_mjpeg_url(
            width=transport_info.mjpeg_width,
            height=transport_info.mjpeg_height,
            fps=transport_info.mjpeg_fps,
            source=transport_info.source,
        )
        still_image_url = api.build_snapshot_url(
            width=transport_info.mjpeg_width,
            height=transport_info.mjpeg_height,
            source=transport_info.source,
        )

        CoordinatorEntity.__init__(self, coordinator)
        MjpegCamera.__init__(
            self,
            mjpeg_url=mjpeg_url,
            still_image_url=still_image_url,
            username=getattr(api, "username", None),
            password=getattr(api, "password", "") or "",
            verify_ssl=getattr(api, "verify_ssl", True),
            unique_id=f"{config_entry.entry_id}_camera",
        )

        # ``MjpegCamera.__init__`` unconditionally assigns ``self._attr_name =
        # name`` (defaulting to ``None``). HA's ``Entity._name_internal``
        # short-circuits on ``hasattr(self, "_attr_name")`` and never falls
        # through to the translation_key path, which would otherwise produce
        # the localized "Camera" suffix on top of the device name. Drop the
        # instance attribute so the class-level ``_attr_translation_key``
        # resolution wins.
        try:
            del self._attr_name
        except AttributeError:
            pass

        self._config_entry = config_entry
        self._transport_info = transport_info
        # Set frame interval from configured MJPEG FPS so the still-stream
        # approach produces a comparable frame rate to a native MJPEG proxy.
        if transport_info.mjpeg_fps and transport_info.mjpeg_fps > 0:
            self._attr_frame_interval = 1.0 / transport_info.mjpeg_fps

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
        return get_supported_features_for_transport(self.coordinator.camera_transport_info)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose camera capability and transport details."""
        transport_info = self.coordinator.camera_transport_info
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

    async def handle_async_mjpeg_stream(self, request: Any) -> Any:
        """Generate MJPEG stream from repeated snapshots.

        ``MjpegCamera`` would normally proxy the raw MJPEG stream from the
        device. That fails when a reverse proxy (e.g. Caddy, nginx) buffers
        the ``multipart/x-mixed-replace`` response — the browser receives
        headers but zero content bytes. Instead we let HA compose its own
        MJPEG boundary stream by polling ``async_camera_image()`` (which
        reads from the coordinator's snapshot cache). This works reliably
        through any proxy or HTTP version.
        """
        return await self.handle_async_still_stream(
            request, self.frame_interval
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return bytes of camera image, going through the coordinator's cache."""
        return await self.coordinator.async_get_snapshot(width=width, height=height)

    async def stream_source(self) -> str | None:  # type: ignore[override]
        """Return the stream source for RTSP transports.

        MJPEG transports are served by ``MjpegCamera`` directly without
        ffmpeg, so we only return a stream URL when the resolved transport
        is RTSP. The URL is read from the coordinator's cached transport
        info (resolved once at setup) — no per-call probe.
        """
        transport_info = self.coordinator.camera_transport_info
        if transport_info.selected_mode != LIVE_VIEW_MODE_RTSP:
            return None
        return get_stream_source_for_transport(self.coordinator.api, transport_info)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success
