"""Unit tests for diagnostics output."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import timedelta

from _stubs import (
    API_PATH,
    CONST_PATH,
    COORDINATOR_PATH,
    INIT_PATH,
    ensure_package,
    install_api_stubs,
    load_module,
)

DIAGNOSTICS_PATH = (
    __import__("pathlib").Path(__file__).resolve().parents[1]
    / "custom_components"
    / "2n_intercom"
    / "diagnostics.py"
)


def _install_homeassistant_stubs() -> None:
    ensure_package("homeassistant")

    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PASSWORD = "password"
    const.CONF_PORT = "port"
    const.CONF_USERNAME = "username"
    const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    sys.modules["homeassistant.const"] = const

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.ServiceCall = object
    core.Event = object
    sys.modules["homeassistant.core"] = core

    exceptions = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    class ConfigEntryNotReady(Exception):
        pass

    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ConfigEntryNotReady = ConfigEntryNotReady
    sys.modules["homeassistant.exceptions"] = exceptions

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    sys.modules["homeassistant.config_entries"] = config_entries

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, logger, name, update_interval) -> None:
            self.hass = hass
            self.data = None
            self.last_update_success = True
            self.update_interval = update_interval

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()
            return self.data

    class UpdateFailed(Exception):
        pass

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    ensure_package("homeassistant.helpers")

    # Stub the diagnostics module with async_redact_data
    diagnostics_module = types.ModuleType("homeassistant.components.diagnostics")

    def async_redact_data(data, to_redact):
        """Simple stub: replace redacted keys with '**REDACTED**'."""
        if isinstance(data, dict):
            return {
                k: ("**REDACTED**" if k in to_redact else v)
                for k, v in data.items()
            }
        return data

    diagnostics_module.async_redact_data = async_redact_data
    sys.modules["homeassistant.components.diagnostics"] = diagnostics_module
    ensure_package("homeassistant.components")

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_create_clientsession = lambda hass, **kwargs: None
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    helpers_typing = types.ModuleType("homeassistant.helpers.typing")
    helpers_typing.ConfigType = dict
    sys.modules["homeassistant.helpers.typing"] = helpers_typing


def load_diagnostics_module():
    install_api_stubs()
    _install_homeassistant_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    load_module("custom_components.2n_intercom.const", CONST_PATH)
    load_module("custom_components.2n_intercom.api", API_PATH)
    load_module("custom_components.2n_intercom.coordinator", COORDINATOR_PATH)
    return load_module("custom_components.2n_intercom.diagnostics", DIAGNOSTICS_PATH)


class FakeCoordinator:
    def __init__(self) -> None:
        self.last_update_success = True
        self.update_interval = timedelta(seconds=5)
        self.system_info = {"model": "2N IP Verso"}
        self.phone_status = {"accounts": []}
        self.switch_caps = {}
        self.switch_status = {}
        self.io_caps = {}
        self.io_status = {}
        self.active_session_id = "session-abc"
        self.call_state = "ringing"
        self.ring_active = True
        self.called_peer = "100"

    @property
    def camera_transport_info(self):
        api_module = sys.modules["custom_components.2n_intercom.api"]
        return api_module.CameraTransportInfo(
            resolved=True,
            selected_mode="mjpeg",
        )


class FakeConfigEntry:
    def __init__(self) -> None:
        self.title = "Front Door Intercom"
        self.data = {
            "host": "192.0.2.20",
            "port": 443,
            "username": "admin",
            "password": "super-secret",
        }
        self.options = {"scan_interval": 5}
        self.runtime_data = None


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    """Tests for diagnostics output shape and credential redaction."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.diag_module = load_diagnostics_module()

    async def test_diagnostics_returns_expected_top_level_keys(self) -> None:
        diag_module = self.diag_module
        coordinator_module = sys.modules["custom_components.2n_intercom.coordinator"]

        coordinator = FakeCoordinator()
        entry = FakeConfigEntry()
        entry.runtime_data = coordinator_module.TwoNIntercomRuntimeData(
            coordinator=coordinator,
            api=types.SimpleNamespace(),
            loaded_platforms=["camera", "binary_sensor", "lock", "sensor"],
        )

        result = await diag_module.async_get_config_entry_diagnostics(None, entry)

        self.assertIn("entry", result)
        self.assertIn("coordinator", result)
        self.assertIn("device", result)
        self.assertIn("call_state", result)
        self.assertIn("camera_transport", result)

    async def test_diagnostics_redacts_credentials(self) -> None:
        diag_module = self.diag_module
        coordinator_module = sys.modules["custom_components.2n_intercom.coordinator"]

        coordinator = FakeCoordinator()
        entry = FakeConfigEntry()
        entry.runtime_data = coordinator_module.TwoNIntercomRuntimeData(
            coordinator=coordinator,
            api=types.SimpleNamespace(),
            loaded_platforms=["camera"],
        )

        result = await diag_module.async_get_config_entry_diagnostics(None, entry)

        entry_data = result["entry"]["data"]
        self.assertEqual(entry_data["username"], "**REDACTED**")
        self.assertEqual(entry_data["password"], "**REDACTED**")
        # Host and port must NOT be redacted
        self.assertEqual(entry_data["host"], "192.0.2.20")
        self.assertEqual(entry_data["port"], 443)

    async def test_diagnostics_includes_device_state(self) -> None:
        diag_module = self.diag_module
        coordinator_module = sys.modules["custom_components.2n_intercom.coordinator"]

        coordinator = FakeCoordinator()
        entry = FakeConfigEntry()
        entry.runtime_data = coordinator_module.TwoNIntercomRuntimeData(
            coordinator=coordinator,
            api=types.SimpleNamespace(),
            loaded_platforms=["camera"],
        )

        result = await diag_module.async_get_config_entry_diagnostics(None, entry)

        self.assertEqual(result["device"]["system_info"], {"model": "2N IP Verso"})
        self.assertEqual(result["call_state"]["active_session_id"], "session-abc")
        self.assertEqual(result["call_state"]["call_state"], "ringing")
        self.assertTrue(result["call_state"]["ring_active"])

    async def test_diagnostics_includes_coordinator_metadata(self) -> None:
        diag_module = self.diag_module
        coordinator_module = sys.modules["custom_components.2n_intercom.coordinator"]

        coordinator = FakeCoordinator()
        entry = FakeConfigEntry()
        entry.runtime_data = coordinator_module.TwoNIntercomRuntimeData(
            coordinator=coordinator,
            api=types.SimpleNamespace(),
            loaded_platforms=["camera", "binary_sensor", "lock", "sensor"],
        )

        result = await diag_module.async_get_config_entry_diagnostics(None, entry)

        self.assertTrue(result["coordinator"]["last_update_success"])
        self.assertEqual(result["coordinator"]["update_interval"], 5.0)
        self.assertEqual(
            result["coordinator"]["loaded_platforms"],
            ["camera", "binary_sensor", "lock", "sensor"],
        )


if __name__ == "__main__":
    unittest.main()
