"""Unit tests for the 2N Intercom coordinator."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
API_PATH = REPO_ROOT / "custom_components" / "2n_intercom" / "api.py"
CONST_PATH = REPO_ROOT / "custom_components" / "2n_intercom" / "const.py"
COORDINATOR_PATH = REPO_ROOT / "custom_components" / "2n_intercom" / "coordinator.py"


def _ensure_package(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = module
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
    _ensure_package("homeassistant")

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    sys.modules["homeassistant.core"] = core

    exceptions = types.ModuleType("homeassistant.exceptions")

    class ConfigEntryNotReady(Exception):
        pass

    exceptions.ConfigEntryNotReady = ConfigEntryNotReady
    sys.modules["homeassistant.exceptions"] = exceptions

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, name, update_interval) -> None:
            del logger, name, update_interval
            self.hass = hass
            self.data = None
            self.last_update_success = True

    class UpdateFailed(Exception):
        pass

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    _ensure_package("homeassistant.helpers")


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_coordinator_module():
    _install_api_stubs()
    _install_homeassistant_stubs()
    _ensure_package("custom_components")
    _ensure_package("custom_components.2n_intercom")
    _load_module("custom_components.2n_intercom.const", CONST_PATH)
    _load_module("custom_components.2n_intercom.api", API_PATH)
    return _load_module("custom_components.2n_intercom.coordinator", COORDINATOR_PATH)


class TwoNIntercomCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    """Tests for coordinator call-state tracking."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.coordinator_module = load_coordinator_module()

    async def test_tracks_active_session_id_from_call_status(self) -> None:
        coordinator_module = self.coordinator_module

        class FakeAPI:
            async def async_get_system_info(self):
                return {"model": "2N"}

            async def async_get_call_status(self):
                return {
                    "state": "ringing",
                    "sessions": [
                        {
                            "session": "session-123",
                            "direction": "incoming",
                            "state": "ringing",
                            "calls": [{"peer": "100"}],
                        }
                    ],
                }

        hass = types.SimpleNamespace()
        coordinator = coordinator_module.TwoNIntercomCoordinator(hass, FakeAPI())

        data = await coordinator._async_update_data()
        coordinator.data = data

        self.assertEqual(data.active_session_id, "session-123")
        self.assertEqual(coordinator.active_session_id, "session-123")
        self.assertEqual(coordinator.call_state, "ringing")

    async def test_does_not_fall_back_to_ended_session_ids(self) -> None:
        coordinator_module = self.coordinator_module

        class FakeAPI:
            async def async_get_system_info(self):
                return {"model": "2N"}

            async def async_get_call_status(self):
                return {
                    "state": "idle",
                    "sessions": [
                        {
                            "session": "session-ended-1",
                            "direction": "incoming",
                            "state": "ended",
                            "calls": [{"peer": "100"}],
                        },
                        {
                            "session": "session-idle-2",
                            "direction": "incoming",
                            "state": "idle",
                            "calls": [{"peer": "101"}],
                        },
                    ],
                }

        hass = types.SimpleNamespace()
        coordinator = coordinator_module.TwoNIntercomCoordinator(hass, FakeAPI())

        data = await coordinator._async_update_data()
        coordinator.data = data

        self.assertIsNone(data.active_session_id)
        self.assertIsNone(coordinator.active_session_id)

    async def test_no_sessions_means_no_active_session(self) -> None:
        coordinator_module = self.coordinator_module

        class FakeAPI:
            async def async_get_system_info(self):
                return {"model": "2N"}

            async def async_get_call_status(self):
                return {"state": "idle", "sessions": []}

        hass = types.SimpleNamespace()
        coordinator = coordinator_module.TwoNIntercomCoordinator(hass, FakeAPI())

        data = await coordinator._async_update_data()
        coordinator.data = data

        self.assertIsNone(data.active_session_id)
        self.assertIsNone(coordinator.active_session_id)

    async def test_process_call_state_event_sets_ring_and_active_session(self) -> None:
        coordinator_module = self.coordinator_module

        class FakeAPI:
            async def async_get_system_info(self):
                return {"model": "2N"}

            async def async_get_call_status(self):
                return {"state": "idle", "sessions": []}

        hass = types.SimpleNamespace()
        coordinator = coordinator_module.TwoNIntercomCoordinator(hass, FakeAPI())

        data = await coordinator._async_update_data()
        coordinator.data = data

        coordinator._process_log_event(
            {
                "event": "CallStateChanged",
                "params": {
                    "direction": "incoming",
                    "state": "ringing",
                    "peer": "sip:100@example.com",
                    "session": 42,
                },
            }
        )

        self.assertEqual(coordinator.active_session_id, "42")
        self.assertEqual(coordinator.called_peer, "100")
        self.assertEqual(coordinator.call_state, "ringing")
        self.assertTrue(coordinator.ring_active)

    async def test_process_call_state_event_clears_ring_and_session_on_terminated(self) -> None:
        coordinator_module = self.coordinator_module

        class FakeAPI:
            async def async_get_system_info(self):
                return {"model": "2N"}

            async def async_get_call_status(self):
                return {"state": "idle", "sessions": []}

        hass = types.SimpleNamespace()
        coordinator = coordinator_module.TwoNIntercomCoordinator(hass, FakeAPI())

        data = await coordinator._async_update_data()
        coordinator.data = data

        coordinator._process_log_event(
            {
                "event": "CallStateChanged",
                "params": {
                    "direction": "incoming",
                    "state": "ringing",
                    "peer": "sip:100@example.com",
                    "session": 42,
                },
            }
        )
        coordinator._process_log_event(
            {
                "event": "CallStateChanged",
                "params": {
                    "direction": "incoming",
                    "state": "terminated",
                    "session": 42,
                },
            }
        )

        self.assertIsNone(coordinator.active_session_id)
        self.assertEqual(coordinator.call_state, "terminated")
        self.assertFalse(coordinator.ring_active)

    async def test_stop_log_listener_unsubscribes_active_channel(self) -> None:
        coordinator_module = self.coordinator_module

        class FakeAPI:
            def __init__(self) -> None:
                self.unsubscribed: list[int] = []

            async def async_get_system_info(self):
                return {"model": "2N"}

            async def async_get_call_status(self):
                return {"state": "idle", "sessions": []}

            async def async_unsubscribe_log(self, subscription_id: int) -> bool:
                self.unsubscribed.append(subscription_id)
                return True

        hass = types.SimpleNamespace(async_create_task=asyncio.create_task)
        api = FakeAPI()
        coordinator = coordinator_module.TwoNIntercomCoordinator(hass, api)

        coordinator._log_subscription_id = 287363148
        coordinator._log_listener_task = asyncio.create_task(asyncio.sleep(3600))

        await coordinator.async_stop_log_listener()

        self.assertEqual(api.unsubscribed, [287363148])
        self.assertIsNone(coordinator._log_subscription_id)
        self.assertIsNone(coordinator._log_listener_task)
