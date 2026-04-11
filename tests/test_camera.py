"""Unit tests for camera stream-source selection."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from enum import IntFlag


REPO_ROOT = Path(__file__).resolve().parents[1]
API_PATH = REPO_ROOT / "custom_components" / "2n_intercom" / "api.py"
CAMERA_PATH = REPO_ROOT / "custom_components" / "2n_intercom" / "camera.py"
CONST_PATH = REPO_ROOT / "custom_components" / "2n_intercom" / "const.py"


def _ensure_package(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = module
    return module


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _install_api_stubs() -> None:
    aiohttp = types.ModuleType("aiohttp")

    class BasicAuth:
        def __init__(self, login: str, password: str) -> None:
            self.login = login
            self.password = password

    class TCPConnector:
        def __init__(self, ssl: bool) -> None:
            self.ssl = ssl

    class ClientSession:
        def __init__(self, *args, **kwargs) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class ClientResponse:
        status = 200
        headers: dict[str, str] = {}

    class ClientError(Exception):
        """Stub aiohttp client error."""

    def digest_auth_middleware(*args, **kwargs):
        return object()

    aiohttp.BasicAuth = BasicAuth
    aiohttp.TCPConnector = TCPConnector
    aiohttp.ClientSession = ClientSession
    aiohttp.ClientResponse = ClientResponse
    aiohttp.ClientError = ClientError
    aiohttp.DigestAuthMiddleware = digest_auth_middleware
    sys.modules["aiohttp"] = aiohttp

    async_timeout = types.ModuleType("async_timeout")

    class _Timeout:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def timeout(*args, **kwargs):
        return _Timeout()

    async_timeout.timeout = timeout
    sys.modules["async_timeout"] = async_timeout


def _install_homeassistant_stubs() -> None:
    ha = _ensure_package("homeassistant")
    del ha

    camera_module = types.ModuleType("homeassistant.components.camera")

    class Camera:
        def __init__(self) -> None:
            self.hass = None

        async def async_added_to_hass(self) -> None:
            return None

        def async_write_ha_state(self) -> None:
            return None

    class CameraEntityFeature(IntFlag):
        STREAM = 1

    camera_module.Camera = Camera
    camera_module.CameraEntityFeature = CameraEntityFeature
    sys.modules["homeassistant.components.camera"] = camera_module
    _ensure_package("homeassistant.components")

    mjpeg_module = types.ModuleType("homeassistant.components.mjpeg")

    class MjpegCamera(Camera):
        def __init__(
            self,
            *,
            mjpeg_url: str,
            still_image_url: str | None = None,
            username: str | None = None,
            password: str = "",
            verify_ssl: bool = True,
            unique_id: str | None = None,
            **kwargs,
        ) -> None:
            super().__init__()
            self._mjpeg_url = mjpeg_url
            self._still_image_url = still_image_url
            self._username = username
            self._password = password
            self._verify_ssl = verify_ssl
            self._attr_unique_id = unique_id

    mjpeg_module.MjpegCamera = MjpegCamera
    sys.modules["homeassistant.components.mjpeg"] = mjpeg_module

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    sys.modules["homeassistant.config_entries"] = config_entries

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    sys.modules["homeassistant.core"] = core

    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class CoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator) -> None:
            self.coordinator = coordinator
            self.hass = getattr(coordinator, "hass", None)

        async def async_added_to_hass(self) -> None:
            return None

        def async_write_ha_state(self) -> None:
            return None

    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    _ensure_package("homeassistant.helpers")


def load_camera_and_api_modules():
    _install_api_stubs()
    _install_homeassistant_stubs()
    _ensure_package("custom_components")
    _ensure_package("custom_components.2n_intercom")
    const_module = _load_module("custom_components.2n_intercom.const", CONST_PATH)
    api_module = _load_module("custom_components.2n_intercom.api", API_PATH)
    coordinator_module = types.ModuleType("custom_components.2n_intercom.coordinator")
    coordinator_module.TwoNIntercomCoordinator = object
    sys.modules["custom_components.2n_intercom.coordinator"] = coordinator_module
    camera_module = _load_module("custom_components.2n_intercom.camera", CAMERA_PATH)
    return camera_module, api_module, const_module


class CameraStreamSourceTests(unittest.IsolatedAsyncioTestCase):
    """Tests for camera stream transport selection."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.camera_module, cls.api_module, cls.const_module = load_camera_and_api_modules()

    def _make_camera_entity(self, transport_info):
        config_entry = types.SimpleNamespace(
            entry_id="entry-1",
            data={"name": "Front Door"},
            options={},
        )

        class FakeApi:
            def __init__(self, api_module, chosen_transport):
                self.camera_transport_info = chosen_transport
                self.username = "user"
                self.password = "secret"
                self.verify_ssl = True
                self._api_module = api_module

            async def async_get_camera_transport_info(self, requested_mode):
                del requested_mode
                return self.camera_transport_info

            def get_rtsp_url_with_credentials(self):
                return "rtsp://user:secret@intercom.local:554/h264_stream"

            def build_mjpeg_url(self, *, include_auth=False, **kwargs):
                # Default is no credentials. Camera entity must not opt in.
                assert include_auth is False, "camera must not embed credentials"
                del kwargs
                return (
                    "https://intercom.local:443"
                    "/api/camera/snapshot?source=internal&width=1280&height=960&fps=10"
                )

            def build_snapshot_url(self, *, include_auth=False, **kwargs):
                assert include_auth is False, "camera must not embed credentials"
                del kwargs
                return (
                    "https://intercom.local:443"
                    "/api/camera/snapshot?source=internal&width=1280&height=960"
                )

        class FakeCoordinator:
            def __init__(self, api):
                self.api = api
                self.camera_transport_info = api.camera_transport_info
                self.last_update_success = True
                self.hass = None

            def get_device_info(self, entry_id, name):
                return {"entry_id": entry_id, "name": name}

            async def async_get_snapshot(self, width=None, height=None):
                del width, height
                return b""

        api = FakeApi(self.api_module, transport_info)
        coordinator = FakeCoordinator(api)
        return self.camera_module.TwoNIntercomCamera(coordinator, config_entry), api

    async def test_stream_source_and_supported_features_stay_aligned(self) -> None:
        api = self.api_module.TwoNIntercomAPI(
            host="intercom.local",
            username="user",
            password="secret",
            port=443,
            protocol="https",
        )

        mjpeg_public_info = self.api_module.CameraTransportInfo(
            selected_mode=self.const_module.LIVE_VIEW_MODE_MJPEG,
            resolved=True,
            mjpeg_available=True,
            mjpeg_public_url_available=True,
        )
        mjpeg_auth_info = self.api_module.CameraTransportInfo(
            selected_mode=self.const_module.LIVE_VIEW_MODE_MJPEG,
            resolved=True,
            mjpeg_available=True,
            mjpeg_public_url_available=False,
        )
        rtsp_info = self.api_module.CameraTransportInfo(
            selected_mode=self.const_module.LIVE_VIEW_MODE_RTSP,
            resolved=True,
            rtsp_available=True,
        )
        jpeg_only_info = self.api_module.CameraTransportInfo(
            selected_mode=self.const_module.LIVE_VIEW_MODE_JPEG_ONLY,
            resolved=True,
        )
        unresolved_info = self.api_module.CameraTransportInfo(
            selected_mode=self.const_module.LIVE_VIEW_MODE_JPEG_ONLY,
            resolved=False,
        )

        # RTSP keeps creds in URL — that is how the protocol works.
        self.assertEqual(
            self.camera_module.get_stream_source_for_transport(api, rtsp_info),
            "rtsp://user:secret@intercom.local:554/h264_stream",
        )
        # MJPEG must NEVER embed credentials regardless of public-URL flag.
        expected_mjpeg = (
            "https://intercom.local:443"
            "/api/camera/snapshot?source=internal&width=1280&height=960&fps=10"
        )
        self.assertEqual(
            self.camera_module.get_stream_source_for_transport(api, mjpeg_public_info),
            expected_mjpeg,
        )
        self.assertEqual(
            self.camera_module.get_stream_source_for_transport(api, mjpeg_auth_info),
            expected_mjpeg,
        )
        self.assertNotIn(
            "user:secret",
            self.camera_module.get_stream_source_for_transport(api, mjpeg_auth_info),
        )
        self.assertIsNone(
            self.camera_module.get_stream_source_for_transport(api, jpeg_only_info)
        )
        self.assertIsNone(
            self.camera_module.get_stream_source_for_transport(api, unresolved_info)
        )

    async def test_camera_entity_contract_for_live_view_states(self) -> None:
        # ``stream_source`` only fires for the RTSP transport. MJPEG is
        # served by ``MjpegCamera`` directly without ffmpeg, so a None
        # stream source there is correct (and a key part of H1: the
        # MJPEG URL never carries credentials anywhere).
        cases = [
            (
                "unresolved",
                self.api_module.CameraTransportInfo(
                    selected_mode=self.const_module.LIVE_VIEW_MODE_JPEG_ONLY,
                    resolved=False,
                ),
                0,
                None,
            ),
            (
                "mjpeg-public",
                self.api_module.CameraTransportInfo(
                    selected_mode=self.const_module.LIVE_VIEW_MODE_MJPEG,
                    resolved=True,
                    mjpeg_available=True,
                    mjpeg_public_url_available=True,
                ),
                self.camera_module.CameraEntityFeature.STREAM,
                None,
            ),
            (
                "rtsp",
                self.api_module.CameraTransportInfo(
                    selected_mode=self.const_module.LIVE_VIEW_MODE_RTSP,
                    resolved=True,
                    rtsp_available=True,
                ),
                self.camera_module.CameraEntityFeature.STREAM,
                "rtsp://user:secret@intercom.local:554/h264_stream",
            ),
            (
                "jpeg-only",
                self.api_module.CameraTransportInfo(
                    selected_mode=self.const_module.LIVE_VIEW_MODE_JPEG_ONLY,
                    resolved=True,
                ),
                0,
                None,
            ),
        ]

        for _, transport_info, expected_features, expected_source in cases:
            camera_entity, _ = self._make_camera_entity(transport_info)
            self.assertEqual(camera_entity.supported_features, expected_features)
            self.assertIsInstance(
                camera_entity.supported_features, self.camera_module.CameraEntityFeature
            )
            if expected_features:
                self.assertIn(
                    self.camera_module.CameraEntityFeature.STREAM,
                    camera_entity.supported_features,
                )
            else:
                self.assertNotIn(
                    self.camera_module.CameraEntityFeature.STREAM,
                    camera_entity.supported_features,
                )
            self.assertEqual(await camera_entity.stream_source(), expected_source)

    async def test_camera_entity_passes_credentials_separately(self) -> None:
        """Camera must pass username/password to MjpegCamera, not embed in URL."""
        transport_info = self.api_module.CameraTransportInfo(
            selected_mode=self.const_module.LIVE_VIEW_MODE_MJPEG,
            resolved=True,
            mjpeg_available=True,
            mjpeg_public_url_available=False,
        )
        camera_entity, _ = self._make_camera_entity(transport_info)

        # MjpegCamera stub captured the constructor kwargs; the URL must
        # be credentials-free, and the credentials must be on the entity.
        self.assertNotIn("user:secret", camera_entity._mjpeg_url)
        self.assertNotIn("user:secret", camera_entity._still_image_url or "")
        self.assertEqual(camera_entity._username, "user")
        self.assertEqual(camera_entity._password, "secret")


if __name__ == "__main__":
    unittest.main()
