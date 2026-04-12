"""Unit tests for integration setup and service registration."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from contextlib import asynccontextmanager

from _stubs import (
    API_PATH,
    CONST_PATH,
    COORDINATOR_PATH,
    INIT_PATH,
    ensure_package,
    install_api_stubs,
    load_module,
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
            del logger, name, update_interval
            self.hass = hass
            self.data = None
            self.last_update_success = True

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()
            return self.data

    class UpdateFailed(Exception):
        pass

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    ensure_package("homeassistant.helpers")

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")

    def async_create_clientsession(hass, **kwargs):
        # Return a fake session from the aiohttp stubs already installed
        aiohttp_mod = sys.modules["aiohttp"]
        return aiohttp_mod.ClientSession()

    aiohttp_client.async_create_clientsession = async_create_clientsession
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

    helpers_typing = types.ModuleType("homeassistant.helpers.typing")
    helpers_typing.ConfigType = dict
    sys.modules["homeassistant.helpers.typing"] = helpers_typing


def load_init_module():
    install_api_stubs()
    _install_homeassistant_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    load_module("custom_components.2n_intercom.const", CONST_PATH)
    load_module("custom_components.2n_intercom.api", API_PATH)
    load_module("custom_components.2n_intercom.coordinator", COORDINATOR_PATH)
    return load_module("custom_components.2n_intercom.__init__", INIT_PATH)


class FakeServiceRegistry:
    def __init__(self) -> None:
        self.handlers: dict[tuple[str, str], object] = {}

    def async_register(self, domain, service, handler, schema=None) -> None:
        del schema
        self.handlers[(domain, service)] = handler

    def has_service(self, domain, service) -> bool:
        return (domain, service) in self.handlers


class FakeConfigEntry:
    def __init__(self, entry_id: str, data: dict[str, object], options=None) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = options or {}
        self.runtime_data: object = None
        self._unload_callbacks: list[object] = []
        self._update_listener = None
        self._background_tasks: list[asyncio.Task] = []

    def add_update_listener(self, listener):
        self._update_listener = listener
        return listener

    def async_on_unload(self, callback) -> None:
        self._unload_callbacks.append(callback)

    def async_create_background_task(self, hass, coro, name=None, eager_start=False):
        del hass, name, eager_start
        task = asyncio.ensure_future(coro)
        self._background_tasks.append(task)
        return task


class FakeConfigEntries:
    def __init__(self, entries: list[FakeConfigEntry]) -> None:
        self._entries = entries
        self.forwarded: list[tuple[str, tuple[str, ...]]] = []

    async def async_forward_entry_setups(self, entry, platforms) -> None:
        self.forwarded.append((entry.entry_id, tuple(platforms)))

    async def async_unload_platforms(self, entry, platforms) -> bool:
        del entry, platforms
        return True

    async def async_reload(self, entry_id) -> None:
        del entry_id

    def async_entries(self, domain):
        del domain
        return list(self._entries)


class FakeBus:
    """Minimal stand-in for hass.bus used by the shutdown listener wiring."""

    def __init__(self) -> None:
        self.listeners: list[tuple[str, object]] = []

    def async_listen_once(self, event_type, callback):  # noqa: D401 — match HA shape
        self.listeners.append((event_type, callback))
        return lambda: self.listeners.remove((event_type, callback))


class FakeHass:
    def __init__(self, entries: list[FakeConfigEntry]) -> None:
        self.services = FakeServiceRegistry()
        self.config_entries = FakeConfigEntries(entries)
        self.bus = FakeBus()
        self.data: dict[str, object] = {}
        self.components = types.SimpleNamespace(
            persistent_notification=types.SimpleNamespace(async_create=lambda *a, **k: None)
        )


class FakeAPI:
    def __init__(self, *args, call_status=None, answer_result=True, hangup_result=True, **kwargs) -> None:
        del args, kwargs
        self._call_status = call_status or {
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
        self._answer_result = answer_result
        self._hangup_result = hangup_result
        self.answer_calls: list[object] = []
        self.hangup_calls: list[tuple[object, str]] = []

    async def async_get_system_info(self):
        return {"model": "2N"}

    async def async_get_call_status(self):
        return self._call_status

    async def async_answer_call(self, session_id):
        self.answer_calls.append(session_id)
        return self._answer_result

    async def async_hangup_call(self, session_id, reason="normal"):
        self.hangup_calls.append((session_id, reason))
        return self._hangup_result

    async def async_subscribe_log(self, events):
        del events
        return None

    async def async_pull_log(self, subscription_id, *, timeout=None):
        del subscription_id, timeout
        return []

    async def async_unsubscribe_log(self, subscription_id):
        del subscription_id
        return True

    async def async_close(self):
        return None


class IntegrationSetupTests(unittest.IsolatedAsyncioTestCase):
    """Tests for setup entry and service dispatch."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.init_module = load_init_module()
        cls.const_module = sys.modules["custom_components.2n_intercom.const"]

    async def test_setup_registers_and_dispatches_call_services(self) -> None:
        init_module = self.init_module
        const_module = self.const_module
        ha_const = sys.modules["homeassistant.const"]
        init_module.TwoNIntercomAPI = FakeAPI

        entry = FakeConfigEntry(
            "entry-1",
            {
                ha_const.CONF_HOST: "intercom.local",
                ha_const.CONF_PORT: 443,
                ha_const.CONF_USERNAME: "user",
                ha_const.CONF_PASSWORD: "secret",
                ha_const.CONF_PORT: 443,
            },
        )
        hass = FakeHass([entry])

        await init_module.async_setup(hass, {})
        result = await init_module.async_setup_entry(hass, entry)

        self.assertTrue(result)
        self.assertTrue(hass.services.has_service(const_module.DOMAIN, "answer_call"))
        self.assertTrue(hass.services.has_service(const_module.DOMAIN, "hangup_call"))
        self.assertEqual(
            hass.config_entries.forwarded,
            [
                (
                    entry.entry_id,
                    ("camera", "binary_sensor", "lock", "sensor"),
                )
            ],
        )

        answer_call = hass.services.handlers[(const_module.DOMAIN, "answer_call")]
        hangup_call = hass.services.handlers[(const_module.DOMAIN, "hangup_call")]

        await answer_call(types.SimpleNamespace(data={}))
        await hangup_call(types.SimpleNamespace(data={"reason": "busy"}))

        stored = entry.runtime_data
        self.assertEqual(stored.api.answer_calls, ["session-123"])
        self.assertEqual(stored.api.hangup_calls, [("session-123", "busy")])

    async def test_service_raises_when_api_reports_failure(self) -> None:
        init_module = self.init_module
        const_module = self.const_module
        ha_const = sys.modules["homeassistant.const"]
        init_module.TwoNIntercomAPI = FakeAPI

        entry = FakeConfigEntry(
            "entry-1",
            {
                ha_const.CONF_HOST: "intercom.local",
                ha_const.CONF_PORT: 443,
                ha_const.CONF_USERNAME: "user",
                ha_const.CONF_PASSWORD: "secret",
            },
        )
        hass = FakeHass([entry])

        await init_module.async_setup(hass, {})
        await init_module.async_setup_entry(hass, entry)
        entry.runtime_data.api = FakeAPI(
            answer_result=False,
            hangup_result=False,
        )

        answer_call = hass.services.handlers[(const_module.DOMAIN, "answer_call")]
        hangup_call = hass.services.handlers[(const_module.DOMAIN, "hangup_call")]

        with self.assertRaises(
            sys.modules["homeassistant.exceptions"].HomeAssistantError
        ):
            await answer_call(types.SimpleNamespace(data={}))

        with self.assertRaises(
            sys.modules["homeassistant.exceptions"].HomeAssistantError
        ):
            await hangup_call(types.SimpleNamespace(data={}))

    async def test_hangup_reason_defaults_to_none_when_unset(self) -> None:
        """When the caller does not pass a valid reason the service must
        forward ``None`` to the API layer, NOT default to ``"normal"``.
        Firmware 2.50.0.76.2 ignores ``reason=normal`` for outgoing-ringing
        sessions while still answering ``success: true``, so the only
        reliable contract is to omit the parameter entirely.
        """
        init_module = self.init_module
        const_module = self.const_module
        ha_const = sys.modules["homeassistant.const"]
        init_module.TwoNIntercomAPI = FakeAPI

        entry = FakeConfigEntry(
            "entry-1",
            {
                ha_const.CONF_HOST: "intercom.local",
                ha_const.CONF_PORT: 443,
                ha_const.CONF_USERNAME: "user",
                ha_const.CONF_PASSWORD: "secret",
            },
        )
        hass = FakeHass([entry])

        await init_module.async_setup(hass, {})
        await init_module.async_setup_entry(hass, entry)

        hangup_call = hass.services.handlers[(const_module.DOMAIN, "hangup_call")]
        await hangup_call(types.SimpleNamespace(data={"reason": None}))

        stored = entry.runtime_data
        self.assertEqual(stored.api.hangup_calls, [("session-123", None)])

    async def test_hangup_reason_forwarded_when_explicitly_valid(self) -> None:
        init_module = self.init_module
        const_module = self.const_module
        ha_const = sys.modules["homeassistant.const"]
        init_module.TwoNIntercomAPI = FakeAPI

        entry = FakeConfigEntry(
            "entry-1",
            {
                ha_const.CONF_HOST: "intercom.local",
                ha_const.CONF_PORT: 443,
                ha_const.CONF_USERNAME: "user",
                ha_const.CONF_PASSWORD: "secret",
            },
        )
        hass = FakeHass([entry])

        await init_module.async_setup(hass, {})
        await init_module.async_setup_entry(hass, entry)

        hangup_call = hass.services.handlers[(const_module.DOMAIN, "hangup_call")]
        await hangup_call(types.SimpleNamespace(data={"reason": "rejected"}))

        stored = entry.runtime_data
        self.assertEqual(stored.api.hangup_calls, [("session-123", "rejected")])

    async def test_service_rejects_ambiguous_target_without_config_entry_id(self) -> None:
        init_module = self.init_module
        const_module = self.const_module
        ha_const = sys.modules["homeassistant.const"]
        init_module.TwoNIntercomAPI = FakeAPI

        entry_1 = FakeConfigEntry(
            "entry-1",
            {
                ha_const.CONF_HOST: "intercom-1.local",
                ha_const.CONF_PORT: 443,
                ha_const.CONF_USERNAME: "user",
                ha_const.CONF_PASSWORD: "secret",
            },
        )
        entry_2 = FakeConfigEntry(
            "entry-2",
            {
                ha_const.CONF_HOST: "intercom-2.local",
                ha_const.CONF_PORT: 443,
                ha_const.CONF_USERNAME: "user",
                ha_const.CONF_PASSWORD: "secret",
            },
        )
        hass = FakeHass([entry_1, entry_2])

        await init_module.async_setup(hass, {})
        await init_module.async_setup_entry(hass, entry_1)
        entry_2.runtime_data = init_module.TwoNIntercomRuntimeData(
            coordinator=types.SimpleNamespace(active_session_id="session-999"),
            api=FakeAPI(),
        )

        answer_call = hass.services.handlers[(const_module.DOMAIN, "answer_call")]

        with self.assertRaises(
            sys.modules["homeassistant.exceptions"].HomeAssistantError
        ):
            await answer_call(types.SimpleNamespace(data={}))

    async def test_service_allows_config_entry_id_to_disambiguate_multi_entry(self) -> None:
        init_module = self.init_module
        const_module = self.const_module
        ha_const = sys.modules["homeassistant.const"]
        init_module.TwoNIntercomAPI = FakeAPI

        entry_1 = FakeConfigEntry(
            "entry-1",
            {
                ha_const.CONF_HOST: "intercom-1.local",
                ha_const.CONF_PORT: 443,
                ha_const.CONF_USERNAME: "user",
                ha_const.CONF_PASSWORD: "secret",
            },
        )
        entry_2 = FakeConfigEntry(
            "entry-2",
            {
                ha_const.CONF_HOST: "intercom-2.local",
                ha_const.CONF_PORT: 443,
                ha_const.CONF_USERNAME: "user",
                ha_const.CONF_PASSWORD: "secret",
            },
        )
        hass = FakeHass([entry_1, entry_2])

        await init_module.async_setup(hass, {})
        await init_module.async_setup_entry(hass, entry_1)
        await init_module.async_setup_entry(hass, entry_2)

        stored_1 = entry_1.runtime_data
        stored_2 = entry_2.runtime_data
        stored_1.coordinator._active_session_id = "session-one"
        stored_2.coordinator._active_session_id = "session-two"

        answer_call = hass.services.handlers[(const_module.DOMAIN, "answer_call")]

        await answer_call(
            types.SimpleNamespace(
                data={
                    "config_entry_id": entry_2.entry_id,
                }
            )
        )

        self.assertEqual(stored_1.api.answer_calls, [])
        self.assertEqual(stored_2.api.answer_calls, ["session-two"])

    async def test_setup_and_unload_manage_log_listener_lifecycle(self) -> None:
        init_module = self.init_module
        ha_const = sys.modules["homeassistant.const"]
        init_module.TwoNIntercomAPI = FakeAPI

        start_calls: list[str] = []
        stop_calls: list[str] = []

        original_start = init_module.TwoNIntercomCoordinator.async_start_log_listener
        original_stop = init_module.TwoNIntercomCoordinator.async_stop_log_listener

        async def fake_start(self):
            start_calls.append(self.__class__.__name__)

        async def fake_stop(self):
            stop_calls.append(self.__class__.__name__)

        init_module.TwoNIntercomCoordinator.async_start_log_listener = fake_start
        init_module.TwoNIntercomCoordinator.async_stop_log_listener = fake_stop

        try:
            entry = FakeConfigEntry(
                "entry-1",
                {
                    ha_const.CONF_HOST: "intercom.local",
                    ha_const.CONF_PORT: 443,
                    ha_const.CONF_USERNAME: "user",
                    ha_const.CONF_PASSWORD: "secret",
                },
            )
            hass = FakeHass([entry])

            await init_module.async_setup_entry(hass, entry)
            await init_module.async_unload_entry(hass, entry)
        finally:
            init_module.TwoNIntercomCoordinator.async_start_log_listener = original_start
            init_module.TwoNIntercomCoordinator.async_stop_log_listener = original_stop

        self.assertEqual(start_calls, ["TwoNIntercomCoordinator"])
        self.assertEqual(stop_calls, ["TwoNIntercomCoordinator"])
