"""2N Intercom API Client."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from datetime import datetime
import logging
import re
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp
import async_timeout

from .const import (
    CAMERA_MJPEG_FPS_MAX,
    CAMERA_MJPEG_FPS_MIN,
    DEFAULT_CAMERA_MJPEG_FPS,
    DEFAULT_CAMERA_MJPEG_HEIGHT,
    DEFAULT_CAMERA_MJPEG_WIDTH,
    DEFAULT_CAMERA_SOURCE,
    DEFAULT_LIVE_VIEW_MODE,
    LIVE_VIEW_MODE_JPEG_ONLY,
    LIVE_VIEW_MODE_MJPEG,
    LIVE_VIEW_MODE_RTSP,
)

_LOGGER = logging.getLogger(__name__)

API_TIMEOUT = 10
RTSP_PATH = "h264_stream"
CAMERA_CAPS_PATH = "/api/camera/caps"
CAMERA_SNAPSHOT_PATH = "/api/camera/snapshot"
PHONE_STATUS_PATH = "/api/phone/status"
SWITCH_CAPS_PATH = "/api/switch/caps"
SWITCH_STATUS_PATH = "/api/switch/status"
IO_CAPS_PATH = "/api/io/caps"
IO_STATUS_PATH = "/api/io/status"
RTSP_PROBE_TIMEOUT = 3

_RESOLUTION_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$")
_CAMERA_SOURCE_KEYS = {"source", "sources", "videosource", "camerasource"}
_CAMERA_CAPS_KEYS = {"caps", "camera", "jpeg", "resolution", "resolutions"}


@dataclass(frozen=True)
class CameraResolution:
    """Normalized camera resolution."""

    width: int
    height: int

    def as_tuple(self) -> tuple[int, int]:
        """Return the resolution as a tuple."""
        return (self.width, self.height)

    def as_string(self) -> str:
        """Return the resolution as WIDTHxHEIGHT."""
        return f"{self.width}x{self.height}"


@dataclass(frozen=True)
class CameraCapabilities:
    """Normalized camera capabilities from camera/caps."""

    jpeg_resolutions: tuple[CameraResolution, ...] = ()
    sources: tuple[str, ...] = ()

    def preferred_source(self) -> str:
        """Return the preferred source for snapshot/MJPEG URLs."""
        if self.sources:
            return self.sources[0]
        return DEFAULT_CAMERA_SOURCE


@dataclass(frozen=True)
class CameraTransportInfo:
    """Normalized camera transport decision and capability state."""

    requested_mode: str = DEFAULT_LIVE_VIEW_MODE
    selected_mode: str = LIVE_VIEW_MODE_JPEG_ONLY
    resolved: bool = False
    live_view_available: bool = False
    rtsp_available: bool = False
    mjpeg_available: bool = False
    mjpeg_public_url_available: bool = False
    jpeg_snapshot_available: bool = True
    capabilities: CameraCapabilities = CameraCapabilities()
    mjpeg_width: int = DEFAULT_CAMERA_MJPEG_WIDTH
    mjpeg_height: int = DEFAULT_CAMERA_MJPEG_HEIGHT
    mjpeg_fps: int = DEFAULT_CAMERA_MJPEG_FPS
    source: str = DEFAULT_CAMERA_SOURCE


def _unique_in_order(values: list[str]) -> tuple[str, ...]:
    """Return unique strings preserving first-seen order."""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return tuple(unique)


def _coerce_int(value: Any) -> int | None:
    """Coerce a value to int when possible."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _parse_resolution_string(value: str) -> CameraResolution | None:
    """Parse WIDTHxHEIGHT strings into CameraResolution."""
    match = _RESOLUTION_RE.match(value)
    if match is None:
        return None
    return CameraResolution(width=int(match.group(1)), height=int(match.group(2)))


def _collect_camera_sources(value: Any) -> list[str]:
    """Collect normalized camera sources from a source subtree."""
    sources: list[str] = []
    metadata_keys = {"available", "default", "enabled", "supported"}

    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            sources.append(normalized)
        return sources

    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip()
            if isinstance(child, (dict, list, tuple, set)):
                if (
                    isinstance(child, dict)
                    and normalized_key
                    and normalized_key.lower() not in _CAMERA_SOURCE_KEYS
                    and normalized_key.lower() not in metadata_keys
                ):
                    sources.append(normalized_key)
                sources.extend(_collect_camera_sources(child))
                continue
            if isinstance(child, str):
                normalized = child.strip()
                if normalized:
                    sources.append(normalized)
        return sources

    if isinstance(value, (list, tuple, set)):
        for child in value:
            sources.extend(_collect_camera_sources(child))

    return sources


def parse_camera_caps(payload: dict[str, Any] | None) -> CameraCapabilities:
    """Parse camera/caps into a normalized capability object."""
    if not isinstance(payload, dict):
        return CameraCapabilities()

    root = payload.get("result", payload)
    resolutions: list[CameraResolution] = []
    sources: list[str] = []
    resolution_keys_seen: set[tuple[int, int]] = set()

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            width = _coerce_int(value.get("width"))
            height = _coerce_int(value.get("height"))
            if width is not None and height is not None:
                resolution = CameraResolution(width=width, height=height)
                if resolution.as_tuple() not in resolution_keys_seen:
                    resolution_keys_seen.add(resolution.as_tuple())
                    resolutions.append(resolution)

            for key, child in value.items():
                normalized_key = str(key).lower()
                if normalized_key in _CAMERA_SOURCE_KEYS:
                    sources.extend(_collect_camera_sources(child))

                if isinstance(child, str):
                    resolution = _parse_resolution_string(child)
                    if resolution is not None and resolution.as_tuple() not in resolution_keys_seen:
                        resolution_keys_seen.add(resolution.as_tuple())
                        resolutions.append(resolution)

                if normalized_key in _CAMERA_CAPS_KEYS or isinstance(child, (dict, list, tuple, set)):
                    visit(child, normalized_key)
            return

        if isinstance(value, (list, tuple, set)):
            for child in value:
                visit(child, parent_key)
            return

        if isinstance(value, str):
            resolution = _parse_resolution_string(value)
            if resolution is not None and resolution.as_tuple() not in resolution_keys_seen:
                resolution_keys_seen.add(resolution.as_tuple())
                resolutions.append(resolution)
            elif parent_key in _CAMERA_SOURCE_KEYS:
                sources.extend(_collect_camera_sources(value))

    visit(root)

    return CameraCapabilities(
        jpeg_resolutions=tuple(resolutions),
        sources=_unique_in_order(sources),
    )


def validate_mjpeg_fps(fps: int) -> int:
    """Validate and normalize MJPEG FPS."""
    if not CAMERA_MJPEG_FPS_MIN <= fps <= CAMERA_MJPEG_FPS_MAX:
        raise ValueError(
            f"MJPEG fps must be between {CAMERA_MJPEG_FPS_MIN} and {CAMERA_MJPEG_FPS_MAX}"
        )
    return fps


def select_live_view_mode(
    *,
    rtsp_available: bool,
    mjpeg_available: bool,
    requested_mode: str = DEFAULT_LIVE_VIEW_MODE,
) -> str:
    """Select the best live view mode for the device."""
    if requested_mode == LIVE_VIEW_MODE_JPEG_ONLY:
        return LIVE_VIEW_MODE_JPEG_ONLY

    if requested_mode == LIVE_VIEW_MODE_RTSP:
        if rtsp_available:
            return LIVE_VIEW_MODE_RTSP
        if mjpeg_available:
            return LIVE_VIEW_MODE_MJPEG
        return LIVE_VIEW_MODE_JPEG_ONLY

    if requested_mode == LIVE_VIEW_MODE_MJPEG:
        if mjpeg_available:
            return LIVE_VIEW_MODE_MJPEG
        if rtsp_available:
            return LIVE_VIEW_MODE_RTSP
        return LIVE_VIEW_MODE_JPEG_ONLY

    if rtsp_available:
        return LIVE_VIEW_MODE_RTSP
    if mjpeg_available:
        return LIVE_VIEW_MODE_MJPEG
    return LIVE_VIEW_MODE_JPEG_ONLY


class TwoNIntercomAPI:
    """API client for 2N Intercom."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 443,
        protocol: str = "https",
        verify_ssl: bool = False,
    ) -> None:
        """Initialize the API client."""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.protocol = protocol
        self.verify_ssl = verify_ssl
        self._session: aiohttp.ClientSession | None = None
        self._base_url = f"{protocol}://{host}:{port}"
        self._camera_capabilities = CameraCapabilities()
        self._camera_transport_info = CameraTransportInfo(
            requested_mode=DEFAULT_LIVE_VIEW_MODE,
            selected_mode=LIVE_VIEW_MODE_RTSP,
            resolved=False,
        )
        self._camera_transport_resolved = False

    async def async_get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=self.verify_ssl),
                middlewares=(
                    aiohttp.DigestAuthMiddleware(
                        self.username,
                        self.password,
                        preemptive=False,
                    ),
                ),
            )
        return self._session

    def _get_basic_auth(self) -> aiohttp.BasicAuth:
        """Return HTTP BasicAuth for all requests."""
        return aiohttp.BasicAuth(self.username, self.password)

    @staticmethod
    def _requires_basic_auth(response: aiohttp.ClientResponse) -> bool:
        """Return whether a 401 challenge requires HTTP Basic auth."""
        challenge = response.headers.get("WWW-Authenticate", "")
        return response.status == 401 and "basic" in challenge.lower()

    @property
    def camera_capabilities(self) -> CameraCapabilities:
        """Return the last known normalized camera capabilities."""
        return self._camera_capabilities

    @property
    def camera_transport_info(self) -> CameraTransportInfo:
        """Return the last known normalized transport info."""
        return self._camera_transport_info

    @property
    def camera_transport_resolved(self) -> bool:
        """Return whether transport detection has resolved to a stable mode."""
        return self._camera_transport_resolved

    def _build_http_url(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        include_auth: bool = True,
    ) -> str:
        """Build an HTTP/HTTPS URL for external consumers."""
        auth_prefix = ""
        if include_auth:
            auth_prefix = (
                f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
            )
        query = f"?{urlencode(params)}" if params else ""
        return f"{self.protocol}://{auth_prefix}{self.host}:{self.port}{path}{query}"

    @staticmethod
    def _select_mjpeg_resolution(
        capabilities: CameraCapabilities,
        *,
        width: int,
        height: int,
    ) -> tuple[int, int]:
        """Return a suitable MJPEG resolution, falling back to device capabilities."""
        requested = (width, height)
        available = [resolution.as_tuple() for resolution in capabilities.jpeg_resolutions]
        if not available:
            return requested
        if requested in available:
            return requested

        sorted_available = sorted(
            available,
            key=lambda item: (item[0] * item[1], item[0], item[1]),
            reverse=True,
        )
        return sorted_available[0]

    def build_snapshot_url(
        self,
        *,
        width: int = DEFAULT_CAMERA_MJPEG_WIDTH,
        height: int = DEFAULT_CAMERA_MJPEG_HEIGHT,
        source: str = DEFAULT_CAMERA_SOURCE,
        include_auth: bool = True,
    ) -> str:
        """Build a direct snapshot URL."""
        return self._build_http_url(
            CAMERA_SNAPSHOT_PATH,
            params={
                "source": source,
                "width": width,
                "height": height,
            },
            include_auth=include_auth,
        )

    def build_mjpeg_url(
        self,
        *,
        width: int = DEFAULT_CAMERA_MJPEG_WIDTH,
        height: int = DEFAULT_CAMERA_MJPEG_HEIGHT,
        fps: int = DEFAULT_CAMERA_MJPEG_FPS,
        source: str = DEFAULT_CAMERA_SOURCE,
        include_auth: bool = True,
    ) -> str:
        """Build a direct MJPEG URL for consumers that need a ffmpeg-usable source URL."""
        validated_fps = validate_mjpeg_fps(fps)
        return self._build_http_url(
            CAMERA_SNAPSHOT_PATH,
            params={
                "source": source,
                "width": width,
                "height": height,
                "fps": validated_fps,
            },
            include_auth=include_auth,
        )

    @asynccontextmanager
    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        """Send a request using Digest auth middleware with Basic fallback."""
        session = await self.async_get_session()
        url = f"{self._base_url}{path}"
        request_kwargs: dict[str, Any] = {}
        if params is not None:
            request_kwargs["params"] = params
        if json_data is not None:
            request_kwargs["json"] = json_data
        if headers is not None:
            request_kwargs["headers"] = headers

        async with session.request(method, url, **request_kwargs) as response:
            if not self._requires_basic_auth(response):
                yield response
                return

        async with session.request(
            method,
            url,
            auth=self._get_basic_auth(),
            **request_kwargs,
        ) as response:
            yield response

    @asynccontextmanager
    async def _async_request_without_auth(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        """Send a request without Digest/Basic auth handling."""
        url = f"{self._base_url}{path}"
        request_kwargs: dict[str, Any] = {
            "connector": aiohttp.TCPConnector(ssl=self.verify_ssl),
        }
        if params is not None:
            request_kwargs["params"] = params
        if json_data is not None:
            request_kwargs["json"] = json_data
        if headers is not None:
            request_kwargs["headers"] = headers

        async with aiohttp.ClientSession(
            connector=request_kwargs.pop("connector"),
        ) as session:
            async with session.request(method, url, **request_kwargs) as response:
                yield response

    async def async_close(self) -> None:
        """Close the API session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def async_connect(self) -> bool:
        """
        Establish and validate connection to the intercom.
        
        This method creates a new session and validates connectivity.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            # Close any existing session first
            await self.async_close()
            
            # Create new session
            await self.async_get_session()
            
            # Test connection by getting call status
            await self.async_get_call_status()
            
            _LOGGER.info("Successfully connected to 2N Intercom at %s", self._base_url)
            return True
            
        except Exception as err:
            _LOGGER.error("Failed to connect to 2N Intercom: %s", err)
            await self.async_close()
            return False

    async def async_reconnect(self) -> bool:
        """
        Force reconnection after error.
        
        Returns:
            True if reconnection successful, False otherwise
        """
        _LOGGER.info("Attempting to reconnect to 2N Intercom")
        return await self.async_connect()

    async def async_test_connection(self) -> bool:
        """Test connection to the intercom."""
        try:
            await self.async_get_call_status()
            return True
        except Exception as err:
            _LOGGER.error("Connection test failed: %s", err)
            return False

    async def async_get_directory(self) -> list[dict[str, Any]]:
        """Get directory entries from /api/dir/query."""
        try:
            payload = {
                "iterator": {"timestamp": 0},
                "fields": ["name", "callPos.peer"],
            }

            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "POST",
                    "/api/dir/query",
                    json_data=payload,
                ) as response:
                    # Check for authentication errors
                    if response.status == 401:
                        raise TwoNAuthenticationError(
                            "Authentication failed - invalid credentials"
                        )
                    
                    response.raise_for_status()
                    data = await response.json()
                    
            # Parse directory data
            # Expected format: {"success": true, "result": {...}} or list
            if isinstance(data, dict) and data.get("success") is False:
                return data

            if isinstance(data, dict) and "result" in data:
                result = data.get("result")
                return result or []

            if isinstance(data, dict) and "users" in data:
                return data

            return []
            
        except TwoNAuthenticationError:
            # Re-raise authentication errors
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout getting directory: %s", err)
            raise TwoNConnectionError(f"Timeout: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Error getting directory: %s", err)
            raise TwoNConnectionError(f"Connection error: {err}") from err
        except Exception as err:
            _LOGGER.error("Unexpected error getting directory: %s", err)
            raise TwoNAPIError(f"API error: {err}") from err

    async def async_get_call_status(self) -> dict[str, Any]:
        """Get current call status from /api/call/status."""
        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "GET",
                    "/api/call/status",
                ) as response:
                    # Check for authentication errors
                    if response.status == 401:
                        raise TwoNAuthenticationError(
                            "Authentication failed - invalid credentials"
                        )
                    
                    response.raise_for_status()
                    data = await response.json()
                    
            # Expected format: {"success": true, "result": {...}}
            if isinstance(data, dict):
                return data.get("result", {})
            return {}
            
        except TwoNAuthenticationError:
            # Re-raise authentication errors
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout getting call status: %s", err)
            raise TwoNConnectionError(f"Timeout: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Error getting call status: %s", err)
            raise TwoNConnectionError(f"Connection error: {err}") from err
        except Exception as err:
            _LOGGER.error("Unexpected error getting call status: %s", err)
            raise TwoNAPIError(f"API error: {err}") from err

    async def _async_get_result_dict(self, path: str, label: str) -> dict[str, Any]:
        """Fetch a JSON result object from a GET endpoint."""
        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request("GET", path) as response:
                    if response.status == 401:
                        raise TwoNAuthenticationError(
                            "Authentication failed - invalid credentials"
                        )

                    response.raise_for_status()
                    data = await response.json()

            if isinstance(data, dict):
                result = data.get("result", {})
                if isinstance(result, dict):
                    return result
            return {}

        except TwoNAuthenticationError:
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout getting %s: %s", label, err)
            raise TwoNConnectionError(f"Timeout: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Error getting %s: %s", label, err)
            raise TwoNConnectionError(f"Connection error: {err}") from err
        except Exception as err:
            _LOGGER.error("Unexpected error getting %s: %s", label, err)
            raise TwoNAPIError(f"API error: {err}") from err

    async def async_get_phone_status(self) -> dict[str, Any]:
        """Get current phone status from /api/phone/status."""
        return await self._async_get_result_dict(PHONE_STATUS_PATH, "phone status")

    async def async_get_switch_caps(self) -> dict[str, Any]:
        """Get switch capabilities from /api/switch/caps."""
        return await self._async_get_result_dict(SWITCH_CAPS_PATH, "switch caps")

    async def async_get_switch_status(self) -> dict[str, Any]:
        """Get switch status from /api/switch/status."""
        return await self._async_get_result_dict(SWITCH_STATUS_PATH, "switch status")

    async def async_get_io_caps(self) -> dict[str, Any]:
        """Get IO capabilities from /api/io/caps."""
        return await self._async_get_result_dict(IO_CAPS_PATH, "io caps")

    async def async_get_io_status(self) -> dict[str, Any]:
        """Get IO status from /api/io/status."""
        return await self._async_get_result_dict(IO_STATUS_PATH, "io status")

    async def _async_call_action(
        self,
        path: str,
        *,
        params: dict[str, Any],
    ) -> bool:
        """Send a call-control action request and return success state."""
        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "POST",
                    path,
                    params=params,
                ) as response:
                    if response.status == 401:
                        raise TwoNAuthenticationError(
                            "Authentication failed - invalid credentials"
                        )

                    response.raise_for_status()
                    data = await response.json()

            if isinstance(data, dict):
                return bool(data.get("success", False))
            return False

        except TwoNAuthenticationError:
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout calling %s: %s", path, err)
            return False
        except aiohttp.ClientError as err:
            _LOGGER.error("Error calling %s: %s", path, err)
            return False
        except Exception as err:
            _LOGGER.error("Unexpected error calling %s: %s", path, err)
            return False

    async def async_answer_call(self, session_id: str) -> bool:
        """Answer an active call session."""
        return await self._async_call_action(
            "/api/call/answer",
            params={"session": session_id},
        )

    async def async_hangup_call(self, session_id: str, reason: str = "normal") -> bool:
        """Hang up an active call session."""
        return await self._async_call_action(
            "/api/call/hangup",
            params={"session": session_id, "reason": reason},
        )

    async def async_subscribe_log(self, events: list[str] | tuple[str, ...]) -> int | None:
        """Subscribe to log events and return the subscription id."""
        event_filter = ",".join(
            event.strip()
            for event in events
            if isinstance(event, str) and event.strip()
        )
        if not event_filter:
            return None

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "GET",
                    "/api/log/subscribe",
                    params={"filter": event_filter},
                ) as response:
                    if response.status == 401:
                        raise TwoNAuthenticationError(
                            "Authentication failed - invalid credentials"
                        )

                    response.raise_for_status()
                    data = await response.json()

            if not isinstance(data, dict) or not data.get("success", False):
                return None

            result = data.get("result", {})
            if not isinstance(result, dict):
                return None

            subscription_id = result.get("id")
            if isinstance(subscription_id, int):
                return subscription_id
            if isinstance(subscription_id, str) and subscription_id.strip().isdigit():
                return int(subscription_id.strip())
            return None

        except TwoNAuthenticationError:
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout subscribing to log events: %s", err)
            return None
        except aiohttp.ClientError as err:
            _LOGGER.error("Error subscribing to log events: %s", err)
            return None
        except Exception as err:
            _LOGGER.error("Unexpected error subscribing to log events: %s", err)
            return None

    async def async_pull_log(
        self,
        subscription_id: int,
        *,
        timeout: int | None = None,
    ) -> list[dict[str, Any]]:
        """Pull pending events for a log subscription."""
        params: dict[str, Any] = {"id": subscription_id}
        if timeout is not None:
            params["timeout"] = timeout

        try:
            async with async_timeout.timeout(API_TIMEOUT + (timeout or 0)):
                async with self._async_request(
                    "GET",
                    "/api/log/pull",
                    params=params,
                ) as response:
                    if response.status == 401:
                        raise TwoNAuthenticationError(
                            "Authentication failed - invalid credentials"
                        )

                    response.raise_for_status()
                    data = await response.json()

            if not isinstance(data, dict) or not data.get("success", False):
                return []

            result = data.get("result", {})
            if not isinstance(result, dict):
                return []

            events = result.get("events", [])
            if isinstance(events, list):
                return [event for event in events if isinstance(event, dict)]
            return []

        except TwoNAuthenticationError:
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout pulling log events: %s", err)
            return []
        except aiohttp.ClientError as err:
            _LOGGER.error("Error pulling log events: %s", err)
            return []
        except Exception as err:
            _LOGGER.error("Unexpected error pulling log events: %s", err)
            return []

    async def async_unsubscribe_log(self, subscription_id: int) -> bool:
        """Unsubscribe from a log-event channel."""
        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "GET",
                    "/api/log/unsubscribe",
                    params={"id": subscription_id},
                ) as response:
                    if response.status == 401:
                        raise TwoNAuthenticationError(
                            "Authentication failed - invalid credentials"
                        )

                    response.raise_for_status()
                    data = await response.json()

            if isinstance(data, dict):
                return bool(data.get("success", False))
            return False

        except TwoNAuthenticationError:
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout unsubscribing from log events: %s", err)
            return False
        except aiohttp.ClientError as err:
            _LOGGER.error("Error unsubscribing from log events: %s", err)
            return False
        except Exception as err:
            _LOGGER.error("Unexpected error unsubscribing from log events: %s", err)
            return False

    async def async_get_system_info(self) -> dict[str, Any]:
        """Get system info from /api/system/info."""
        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "GET",
                    "/api/system/info",
                ) as response:
                    if response.status == 401:
                        raise TwoNAuthenticationError(
                            "Authentication failed - invalid credentials"
                        )

                    response.raise_for_status()
                    data = await response.json()

            if isinstance(data, dict):
                return data.get("result", {})
            return {}

        except TwoNAuthenticationError:
            raise
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout getting system info: %s", err)
            raise TwoNConnectionError(f"Timeout: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Error getting system info: %s", err)
            raise TwoNConnectionError(f"Connection error: {err}") from err
        except Exception as err:
            _LOGGER.error("Unexpected error getting system info: %s", err)
            raise TwoNAPIError(f"API error: {err}") from err

    async def async_get_camera_caps(
        self,
        *,
        force_refresh: bool = False,
    ) -> CameraCapabilities:
        """Fetch and normalize camera/caps, degrading gracefully on failures."""
        if (
            self._camera_capabilities.jpeg_resolutions
            or self._camera_capabilities.sources
        ) and not force_refresh:
            return self._camera_capabilities

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request("GET", CAMERA_CAPS_PATH) as response:
                    if response.status >= 400:
                        response.raise_for_status()
                    data = await response.json()

            self._camera_capabilities = parse_camera_caps(data)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Unable to fetch camera capabilities: %s", err)
            if force_refresh:
                self._camera_capabilities = CameraCapabilities()

        return self._camera_capabilities

    async def async_probe_mjpeg(
        self,
        *,
        capabilities: CameraCapabilities | None = None,
        width: int = DEFAULT_CAMERA_MJPEG_WIDTH,
        height: int = DEFAULT_CAMERA_MJPEG_HEIGHT,
        fps: int = DEFAULT_CAMERA_MJPEG_FPS,
        source: str | None = None,
    ) -> bool:
        """Check whether MJPEG appears available without crashing the integration.

        This detects device capability through the integration's normal auth
        path. Actual camera-entity playback decisions can then choose how to
        use that capability without breaking Digest/Basic fallback semantics.
        """
        try:
            validated_fps = validate_mjpeg_fps(fps)
        except ValueError as err:
            _LOGGER.debug("Skipping MJPEG probe because of invalid fps: %s", err)
            return False

        normalized_caps = capabilities or await self.async_get_camera_caps()
        chosen_width, chosen_height = self._select_mjpeg_resolution(
            normalized_caps,
            width=width,
            height=height,
        )
        chosen_source = source or normalized_caps.preferred_source()

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "GET",
                    CAMERA_SNAPSHOT_PATH,
                    params={
                        "source": chosen_source,
                        "width": chosen_width,
                        "height": chosen_height,
                        "fps": validated_fps,
                    },
                    headers={"Accept": "multipart/x-mixed-replace,image/jpeg"},
                ) as response:
                    if response.status >= 400:
                        response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").lower()

            return any(
                token in content_type
                for token in ("multipart/", "x-mixed-replace", "mjpeg")
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("MJPEG probe failed: %s", err)
            return False

    async def async_probe_mjpeg_public(
        self,
        *,
        capabilities: CameraCapabilities | None = None,
        width: int = DEFAULT_CAMERA_MJPEG_WIDTH,
        height: int = DEFAULT_CAMERA_MJPEG_HEIGHT,
        fps: int = DEFAULT_CAMERA_MJPEG_FPS,
        source: str | None = None,
    ) -> bool:
        """Check whether MJPEG is available without including credentials in the URL."""
        try:
            validated_fps = validate_mjpeg_fps(fps)
        except ValueError as err:
            _LOGGER.debug("Skipping public MJPEG probe because of invalid fps: %s", err)
            return False

        normalized_caps = capabilities or await self.async_get_camera_caps()
        chosen_width, chosen_height = self._select_mjpeg_resolution(
            normalized_caps,
            width=width,
            height=height,
        )
        chosen_source = source or normalized_caps.preferred_source()

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request_without_auth(
                    "GET",
                    CAMERA_SNAPSHOT_PATH,
                    params={
                        "source": chosen_source,
                        "width": chosen_width,
                        "height": chosen_height,
                        "fps": validated_fps,
                    },
                    headers={"Accept": "multipart/x-mixed-replace,image/jpeg"},
                ) as response:
                    if response.status >= 400:
                        response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").lower()

            return any(
                token in content_type
                for token in ("multipart/", "x-mixed-replace", "mjpeg")
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Public MJPEG probe failed: %s", err)
            return False

    async def async_probe_rtsp(self) -> bool:
        """Check whether RTSP appears available without crashing the integration."""
        reader = None
        writer = None
        try:
            rtsp_port = self._get_rtsp_port()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, rtsp_port),
                timeout=RTSP_PROBE_TIMEOUT,
            )
            request = (
                f"OPTIONS rtsp://{self.host}:{rtsp_port}/{RTSP_PATH} RTSP/1.0\r\n"
                "CSeq: 1\r\n"
                "User-Agent: HomeAssistant-2NIntercom\r\n\r\n"
            )
            writer.write(request.encode("ascii"))
            await writer.drain()
            response = await asyncio.wait_for(reader.read(256), timeout=RTSP_PROBE_TIMEOUT)
            response_text = response.decode("utf-8", errors="ignore")
            if not response_text.startswith("RTSP/1.0"):
                return False
            return not any(code in response_text for code in (" 403 ", " 404 ", " 454 "))
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("RTSP probe failed: %s", err)
            return False
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # pylint: disable=broad-except
                    pass

    async def async_get_camera_transport_info(
        self,
        *,
        requested_mode: str = DEFAULT_LIVE_VIEW_MODE,
        force_refresh: bool = False,
    ) -> CameraTransportInfo:
        """Return a normalized transport-info object for the camera entity."""
        if (
            self._camera_transport_resolved
            and not force_refresh
            and self._camera_transport_info.requested_mode == requested_mode
        ):
            return self._camera_transport_info

        capabilities = await self.async_get_camera_caps(force_refresh=force_refresh)
        source = capabilities.preferred_source()
        mjpeg_width, mjpeg_height = self._select_mjpeg_resolution(
            capabilities,
            width=DEFAULT_CAMERA_MJPEG_WIDTH,
            height=DEFAULT_CAMERA_MJPEG_HEIGHT,
        )

        rtsp_available = await self.async_probe_rtsp()
        mjpeg_authenticated_available = await self.async_probe_mjpeg(
            capabilities=capabilities,
            width=mjpeg_width,
            height=mjpeg_height,
            fps=DEFAULT_CAMERA_MJPEG_FPS,
            source=source,
        )
        mjpeg_public_url_available = await self.async_probe_mjpeg_public(
            capabilities=capabilities,
            width=mjpeg_width,
            height=mjpeg_height,
            fps=DEFAULT_CAMERA_MJPEG_FPS,
            source=source,
        )
        mjpeg_available = (
            mjpeg_authenticated_available or mjpeg_public_url_available
        )
        selected_mode = select_live_view_mode(
            rtsp_available=rtsp_available,
            mjpeg_available=mjpeg_available,
            requested_mode=requested_mode,
        )
        live_view_available = selected_mode in (LIVE_VIEW_MODE_RTSP, LIVE_VIEW_MODE_MJPEG)
        resolved = True

        self._camera_transport_info = CameraTransportInfo(
            requested_mode=requested_mode,
            selected_mode=selected_mode,
            resolved=resolved,
            live_view_available=live_view_available,
            rtsp_available=rtsp_available,
            mjpeg_available=mjpeg_available,
            mjpeg_public_url_available=mjpeg_public_url_available,
            jpeg_snapshot_available=True,
            capabilities=capabilities,
            mjpeg_width=mjpeg_width,
            mjpeg_height=mjpeg_height,
            mjpeg_fps=DEFAULT_CAMERA_MJPEG_FPS,
            source=source,
        )
        self._camera_transport_resolved = resolved
        return self._camera_transport_info

    async def async_switch_control(
        self, relay: int, action: str = "on", duration: int = 0
    ) -> bool:
        """
        Control relay via /api/switch/ctrl.
        
        Args:
            relay: Relay number (1-4)
            action: Action to perform ("on", "off", "trigger")
            duration: Duration in milliseconds for trigger action
            
        Returns:
            True if successful, False otherwise
        """
        try:
            params = {
                "switch": relay,
                "action": action,
            }
            
            if duration > 0:
                params["duration"] = duration
            
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "GET",
                    "/api/switch/ctrl",
                    params=params,
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    
            # Check if action was successful
            if isinstance(data, dict):
                return data.get("success", False)
            return False
            
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout controlling switch %s: %s", relay, err)
            return False
        except aiohttp.ClientError as err:
            _LOGGER.error("Error controlling switch %s: %s", relay, err)
            return False
        except Exception as err:
            _LOGGER.error("Unexpected error controlling switch %s: %s", relay, err)
            return False

    async def async_get_snapshot(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Get camera snapshot from /api/camera/snapshot."""
        try:
            params: dict[str, Any] = {"source": DEFAULT_CAMERA_SOURCE}
            if width is None:
                width = 640
            if height is None:
                height = 480
            params["width"] = width
            params["height"] = height
            
            async with async_timeout.timeout(API_TIMEOUT):
                async with self._async_request(
                    "GET",
                    CAMERA_SNAPSHOT_PATH,
                    params=params,
                    headers={"Accept": "image/jpeg"},
                ) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "")
                    if "image" not in content_type:
                        error_body = await response.text()
                        request_url = str(response.url)
                        _LOGGER.error(
                            "Snapshot returned non-image content-type: %s body=%s request_url=%s params=%s",
                            content_type,
                            error_body,
                            request_url,
                            params,
                        )

                        error_code = None
                        try:
                            payload = json.loads(error_body)
                            error_code = payload.get("error", {}).get("code")
                        except json.JSONDecodeError:
                            error_code = None

                        if error_code == 12 and (width, height) != (640, 480):
                            _LOGGER.warning(
                                "Retrying snapshot with fallback resolution 640x480 request_url=%s params=%s",
                                request_url,
                                params,
                            )
                            return await self.async_get_snapshot(width=640, height=480)

                        return None
                    return await response.read()
                    
        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout getting snapshot: %s", err)
            return None
        except aiohttp.ClientError as err:
            _LOGGER.error("Error getting snapshot: %s", err)
            return None
        except Exception as err:
            _LOGGER.error("Unexpected error getting snapshot: %s", err)
            return None

    def _get_rtsp_port(self) -> int:
        """Return RTSP port, avoiding HTTP/HTTPS ports."""
        if self.port in (80, 443):
            return 554
        return self.port

    def get_rtsp_url(self) -> str:
        """
        Get RTSP stream URL.
            
        Returns:
            RTSP URL with embedded credentials
        """
        # Redact password in logs
        rtsp_port = self._get_rtsp_port()
        return (
            f"rtsp://{self.username}:****@{self.host}:{rtsp_port}/{RTSP_PATH}"
        )

    def get_rtsp_url_with_credentials(self) -> str:
        """
        Get RTSP stream URL with credentials (for actual use).
            
        Returns:
            RTSP URL with embedded credentials
        """
        rtsp_port = self._get_rtsp_port()
        return (
            f"rtsp://{self.username}:{self.password}@{self.host}:{rtsp_port}/{RTSP_PATH}"
        )


class TwoNAuthenticationError(Exception):
    """Authentication failed."""


class TwoNConnectionError(Exception):
    """Connection to intercom failed."""


class TwoNAPIError(Exception):
    """Generic API error."""
