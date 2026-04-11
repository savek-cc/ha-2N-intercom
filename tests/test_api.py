"""Unit tests for camera transport helpers in api.py."""

from __future__ import annotations

from contextlib import asynccontextmanager
import unittest
from unittest.mock import patch

from _stubs import (
    API_PATH,
    CONST_PATH,
    ensure_package,
    install_api_stubs,
    load_module,
)


def load_api_module():
    install_api_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    load_module("custom_components.2n_intercom.const", CONST_PATH)
    return load_module("custom_components.2n_intercom.api", API_PATH)


class CameraTransportApiTests(unittest.TestCase):
    """Tests for pure camera transport helpers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.api_module = load_api_module()

    def test_parse_camera_caps_extracts_resolutions_and_sources(self) -> None:
        payload = {
            "success": True,
            "result": {
                "jpeg": {
                    "resolutions": [
                        "176x144",
                        {"width": 1280, "height": 960},
                        {"width": "640", "height": "480"},
                    ]
                },
                "sources": {
                    "available": ["internal", "external"],
                },
            },
        }

        caps = self.api_module.parse_camera_caps(payload)

        self.assertEqual(
            [resolution.as_tuple() for resolution in caps.jpeg_resolutions],
            [(176, 144), (1280, 960), (640, 480)],
        )
        self.assertEqual(caps.sources, ("internal", "external"))

    def test_invalid_fps_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.api_module.validate_mjpeg_fps(0)

        with self.assertRaises(ValueError):
            self.api_module.validate_mjpeg_fps(16)

    def test_build_mjpeg_url_includes_expected_query(self) -> None:
        api = self.api_module.TwoNIntercomAPI(
            host="intercom.local",
            username="user@example.com",
            password="p@ss word",
            port=8443,
            protocol="https",
        )

        # Default must NOT embed credentials. The camera entity passes
        # username/password to MjpegCamera separately so logs and HA
        # diagnostics never see them in the URL.
        url = api.build_mjpeg_url(width=640, height=480, fps=7, source="internal")

        self.assertEqual(
            url,
            "https://intercom.local:8443"
            "/api/camera/snapshot?source=internal&width=640&height=480&fps=7",
        )
        self.assertNotIn("user", url)
        self.assertNotIn("p%40ss", url)

    def test_build_mjpeg_url_can_omit_credentials(self) -> None:
        api = self.api_module.TwoNIntercomAPI(
            host="intercom.local",
            username="user@example.com",
            password="p@ss word",
            port=8443,
            protocol="https",
        )

        url = api.build_mjpeg_url(
            width=640,
            height=480,
            fps=7,
            source="internal",
            include_auth=False,
        )

        self.assertEqual(
            url,
            "https://intercom.local:8443"
            "/api/camera/snapshot?source=internal&width=640&height=480&fps=7",
        )

    def test_transport_selection_prefers_rtsp_then_mjpeg_then_jpeg_only(self) -> None:
        self.assertEqual(
            self.api_module.select_live_view_mode(
                rtsp_available=True,
                mjpeg_available=True,
            ),
            self.api_module.LIVE_VIEW_MODE_RTSP,
        )
        self.assertEqual(
            self.api_module.select_live_view_mode(
                rtsp_available=False,
                mjpeg_available=True,
            ),
            self.api_module.LIVE_VIEW_MODE_MJPEG,
        )
        self.assertEqual(
            self.api_module.select_live_view_mode(
                rtsp_available=False,
                mjpeg_available=False,
            ),
            self.api_module.LIVE_VIEW_MODE_JPEG_ONLY,
        )


class CameraTransportProbeRetryTests(unittest.IsolatedAsyncioTestCase):
    """Tests for transport detection behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.api_module = load_api_module()

    async def test_resolved_jpeg_only_result_is_cached(self) -> None:
        api_module = self.api_module

        class ProbeApi(api_module.TwoNIntercomAPI):
            def __init__(self) -> None:
                super().__init__(
                    host="intercom.local",
                    username="user",
                    password="secret",
                )
                self._rtsp_results = [False]
                self._mjpeg_results = [False]
                self._public_results = [False]

            async def async_get_camera_caps(self, *, force_refresh: bool = False):
                return api_module.CameraCapabilities()

            async def async_probe_rtsp(self) -> bool:
                return self._rtsp_results.pop(0)

            async def async_probe_mjpeg(self, **kwargs) -> bool:
                return self._mjpeg_results.pop(0)

            async def async_probe_mjpeg_public(self, **kwargs) -> bool:
                return self._public_results.pop(0)

        api = ProbeApi()

        first = await api.async_get_camera_transport_info()
        self.assertTrue(first.resolved)
        self.assertEqual(first.selected_mode, api_module.LIVE_VIEW_MODE_JPEG_ONLY)

        second = await api.async_get_camera_transport_info()
        self.assertTrue(second.resolved)
        self.assertEqual(second.selected_mode, api_module.LIVE_VIEW_MODE_JPEG_ONLY)

    async def test_async_probe_rtsp_treats_403_forbidden_as_unavailable(self) -> None:
        api_module = self.api_module

        class FakeReader:
            def __init__(self, response: bytes) -> None:
                self.response = response

            async def read(self, size: int) -> bytes:
                return self.response

        class FakeWriter:
            def __init__(self) -> None:
                self.buffer = bytearray()
                self.closed = False
                self.wait_closed_called = False

            def write(self, data: bytes) -> None:
                self.buffer.extend(data)

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                self.closed = True

            async def wait_closed(self) -> None:
                self.wait_closed_called = True

        api = api_module.TwoNIntercomAPI(
            host="intercom.local",
            username="user",
            password="secret",
        )
        response = (
            b"RTSP/1.0 403 Forbidden\r\n"
            b"Server: HIP 2.50.0.76.2\r\n"
            b"Content-Length: 0\r\n\r\n"
        )

        async def fake_open_connection(host: str, port: int):
            return FakeReader(response), FakeWriter()

        async def fake_wait_for(awaitable, timeout):
            return await awaitable

        with patch.object(api_module.asyncio, "open_connection", fake_open_connection), patch.object(
            api_module.asyncio, "wait_for", fake_wait_for
        ):
            result = await api.async_probe_rtsp()

        self.assertFalse(result)

    async def test_async_probe_rtsp_accepts_200_ok_as_available(self) -> None:
        api_module = self.api_module

        class FakeReader:
            def __init__(self, response: bytes) -> None:
                self.response = response

            async def read(self, size: int) -> bytes:
                return self.response

        class FakeWriter:
            def __init__(self) -> None:
                self.buffer = bytearray()
                self.closed = False
                self.wait_closed_called = False

            def write(self, data: bytes) -> None:
                self.buffer.extend(data)

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                self.closed = True

            async def wait_closed(self) -> None:
                self.wait_closed_called = True

        api = api_module.TwoNIntercomAPI(
            host="intercom.local",
            username="user",
            password="secret",
        )
        response = (
            b"RTSP/1.0 200 OK\r\n"
            b"Server: HIP 2.50.0.76.2\r\n"
            b"Content-Length: 0\r\n\r\n"
        )

        async def fake_open_connection(host: str, port: int):
            return FakeReader(response), FakeWriter()

        async def fake_wait_for(awaitable, timeout):
            return await awaitable

        with patch.object(api_module.asyncio, "open_connection", fake_open_connection), patch.object(
            api_module.asyncio, "wait_for", fake_wait_for
        ):
            result = await api.async_probe_rtsp()

        self.assertTrue(result)


class CallControlApiTests(unittest.IsolatedAsyncioTestCase):
    """Tests for call-control helpers in api.py."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.api_module = load_api_module()

    def _make_api(self, response_payload: dict[str, object] | None = None):
        api_module = self.api_module

        class FakeResponse:
            status = 200
            headers: dict[str, str] = {}

            def __init__(self, payload: dict[str, object] | None) -> None:
                self._payload = payload or {"success": True}

            async def json(self) -> dict[str, object]:
                return self._payload

            def raise_for_status(self) -> None:
                return None

        class CallApi(api_module.TwoNIntercomAPI):
            def __init__(self) -> None:
                super().__init__(
                    host="intercom.local",
                    username="user",
                    password="secret",
                    port=443,
                    protocol="https",
                )
                self.requests: list[dict[str, object]] = []
                self.response = FakeResponse(response_payload)

            @asynccontextmanager
            async def _async_request(
                self,
                method: str,
                path: str,
                *,
                params=None,
                json_data=None,
                headers=None,
            ):
                self.requests.append(
                    {
                        "method": method,
                        "path": path,
                        "params": params,
                        "json_data": json_data,
                        "headers": headers,
                    }
                )
                yield self.response

        return CallApi()

    async def test_answer_call_sends_session_id(self) -> None:
        api = self._make_api()

        result = await api.async_answer_call("session-123")

        self.assertTrue(result)
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "POST",
                    "path": "/api/call/answer",
                    "params": {"session": "session-123"},
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_answer_call_returns_false_when_device_reports_failure(self) -> None:
        api = self._make_api({"success": False})

        result = await api.async_answer_call("session-123")

        self.assertFalse(result)

    async def test_hangup_call_sends_session_id_and_reason(self) -> None:
        api = self._make_api()

        result = await api.async_hangup_call("session-456", reason="busy")

        self.assertTrue(result)
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "POST",
                    "path": "/api/call/hangup",
                    "params": {"session": "session-456", "reason": "busy"},
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_hangup_call_returns_false_when_device_reports_failure(self) -> None:
        api = self._make_api({"success": False})

        result = await api.async_hangup_call("session-456", reason="busy")

        self.assertFalse(result)

    async def test_get_phone_status_returns_result_payload(self) -> None:
        api = self._make_api(
            {
                "success": True,
                "result": {
                    "accounts": [
                        {"account": 1, "enabled": True, "registered": True}
                    ]
                },
            }
        )

        result = await api.async_get_phone_status()

        self.assertEqual(result, {"accounts": [{"account": 1, "enabled": True, "registered": True}]})
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "GET",
                    "path": "/api/phone/status",
                    "params": None,
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_get_switch_caps_returns_result_payload(self) -> None:
        api = self._make_api(
            {
                "success": True,
                "result": {
                    "switches": [
                        {"switch": 1, "enabled": True, "mode": "monostable"}
                    ]
                },
            }
        )

        result = await api.async_get_switch_caps()

        self.assertEqual(
            result,
            {"switches": [{"switch": 1, "enabled": True, "mode": "monostable"}]},
        )
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "GET",
                    "path": "/api/switch/caps",
                    "params": None,
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_get_switch_status_returns_result_payload(self) -> None:
        api = self._make_api(
            {
                "success": True,
                "result": {
                    "switches": [
                        {"switch": 1, "active": False, "locked": False, "held": False}
                    ]
                },
            }
        )

        result = await api.async_get_switch_status()

        self.assertEqual(
            result,
            {"switches": [{"switch": 1, "active": False, "locked": False, "held": False}]},
        )
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "GET",
                    "path": "/api/switch/status",
                    "params": None,
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_get_io_caps_returns_result_payload(self) -> None:
        api = self._make_api(
            {
                "success": True,
                "result": {
                    "ports": [{"port": "relay1", "type": "output"}]
                },
            }
        )

        result = await api.async_get_io_caps()

        self.assertEqual(result, {"ports": [{"port": "relay1", "type": "output"}]})
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "GET",
                    "path": "/api/io/caps",
                    "params": None,
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_get_io_status_returns_result_payload(self) -> None:
        api = self._make_api(
            {
                "success": True,
                "result": {
                    "ports": [{"port": "relay1", "state": 0}]
                },
            }
        )

        result = await api.async_get_io_status()

        self.assertEqual(result, {"ports": [{"port": "relay1", "state": 0}]})
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "GET",
                    "path": "/api/io/status",
                    "params": None,
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_subscribe_log_sends_filter_and_returns_subscription_id(self) -> None:
        api = self._make_api({"success": True, "result": {"id": 287363148}})

        result = await api.async_subscribe_log(
            ["CallStateChanged", "CallSessionStateChanged"]
        )

        self.assertEqual(result, 287363148)
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "GET",
                    "path": "/api/log/subscribe",
                    "params": {
                        "filter": "CallStateChanged,CallSessionStateChanged",
                    },
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_pull_log_sends_id_and_timeout_and_returns_events(self) -> None:
        api = self._make_api(
            {
                "success": True,
                "result": {
                    "events": [{"event": "CallStateChanged", "params": {"state": "ringing"}}]
                },
            }
        )

        result = await api.async_pull_log(287363148, timeout=5)

        self.assertEqual(
            result,
            [{"event": "CallStateChanged", "params": {"state": "ringing"}}],
        )
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "GET",
                    "path": "/api/log/pull",
                    "params": {"id": 287363148, "timeout": 5},
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_unsubscribe_log_sends_id_and_returns_success(self) -> None:
        api = self._make_api({"success": True})

        result = await api.async_unsubscribe_log(287363148)

        self.assertTrue(result)
        self.assertEqual(
            api.requests,
            [
                {
                    "method": "GET",
                    "path": "/api/log/unsubscribe",
                    "params": {"id": 287363148},
                    "json_data": None,
                    "headers": None,
                }
            ],
        )

    async def test_probe_mjpeg_uses_authenticated_request_flow(self) -> None:
        api_module = self.api_module

        class FakeResponse:
            status = 200
            headers = {
                "Content-Type": "multipart/x-mixed-replace; boundary=jpeg-video-boundary"
            }

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self) -> None:
                return None

        api = api_module.TwoNIntercomAPI(
            host="intercom.local",
            username="user@example.com",
            password="secret word",
            port=8443,
            protocol="https",
        )

        calls: list[tuple[str, str, dict[str, object] | None, dict[str, str] | None]] = []

        def fake_async_request(method, path, *, params=None, json_data=None, headers=None):
            calls.append((method, path, params, headers))

            class _ContextManager:
                async def __aenter__(self_inner):
                    return FakeResponse()

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _ContextManager()

        api._async_request = fake_async_request  # type: ignore[method-assign]

        result = await api.async_probe_mjpeg(
            capabilities=api_module.CameraCapabilities(),
            width=640,
            height=480,
            fps=10,
            source="internal",
        )

        self.assertTrue(result)
        self.assertEqual(
            calls,
            [
                (
                    "GET",
                    "/api/camera/snapshot",
                    {
                        "source": "internal",
                        "width": 640,
                        "height": 480,
                        "fps": 10,
                    },
                    {"Accept": "multipart/x-mixed-replace,image/jpeg"},
                )
            ],
        )

    async def test_probe_mjpeg_public_uses_unauthenticated_request_flow(self) -> None:
        api_module = self.api_module

        class FakeResponse:
            status = 200
            headers = {
                "Content-Type": "multipart/x-mixed-replace; boundary=jpeg-video-boundary"
            }

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self) -> None:
                return None

        api = api_module.TwoNIntercomAPI(
            host="intercom.local",
            username="user@example.com",
            password="secret word",
            port=8443,
            protocol="https",
        )

        calls: list[tuple[str, str, dict[str, object] | None, dict[str, str] | None]] = []

        def fake_async_request_without_auth(
            method,
            path,
            *,
            params=None,
            json_data=None,
            headers=None,
        ):
            del json_data
            calls.append((method, path, params, headers))

            class _ContextManager:
                async def __aenter__(self_inner):
                    return FakeResponse()

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _ContextManager()

        api._async_request_without_auth = fake_async_request_without_auth  # type: ignore[method-assign]

        result = await api.async_probe_mjpeg_public(
            capabilities=api_module.CameraCapabilities(),
            width=640,
            height=480,
            fps=10,
            source="internal",
        )

        self.assertTrue(result)
        self.assertEqual(
            calls,
            [
                (
                    "GET",
                    "/api/camera/snapshot",
                    {
                        "source": "internal",
                        "width": 640,
                        "height": 480,
                        "fps": 10,
                    },
                    {"Accept": "multipart/x-mixed-replace,image/jpeg"},
                )
            ],
        )

    async def test_public_mjpeg_only_still_selects_mjpeg_transport(self) -> None:
        api_module = self.api_module

        class ProbeApi(api_module.TwoNIntercomAPI):
            async def async_get_camera_caps(self, *, force_refresh: bool = False):
                return api_module.CameraCapabilities()

            async def async_probe_rtsp(self) -> bool:
                return False

            async def async_probe_mjpeg(self, **kwargs) -> bool:
                return False

            async def async_probe_mjpeg_public(self, **kwargs) -> bool:
                return True

        api = ProbeApi(
            host="intercom.local",
            username="user",
            password="secret",
        )

        transport_info = await api.async_get_camera_transport_info()

        self.assertTrue(transport_info.resolved)
        self.assertTrue(transport_info.live_view_available)
        self.assertTrue(transport_info.mjpeg_available)
        self.assertTrue(transport_info.mjpeg_public_url_available)
        self.assertEqual(transport_info.selected_mode, api_module.LIVE_VIEW_MODE_MJPEG)


if __name__ == "__main__":
    unittest.main()
