"""Unit tests for the 2N Intercom coordinator."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest

from _stubs import (
    API_PATH,
    CONST_PATH,
    COORDINATOR_PATH,
    ensure_package,
    install_api_stubs,
    load_module,
)


def _install_homeassistant_stubs() -> None:
    ensure_package("homeassistant")

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
    ensure_package("homeassistant.helpers")


def load_coordinator_module():
    install_api_stubs()
    _install_homeassistant_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    load_module("custom_components.2n_intercom.const", CONST_PATH)
    load_module("custom_components.2n_intercom.api", API_PATH)
    return load_module("custom_components.2n_intercom.coordinator", COORDINATOR_PATH)


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

    async def test_refresh_caches_phone_switch_and_io_data(self) -> None:
        coordinator_module = self.coordinator_module

        class FakeAPI:
            async def async_get_system_info(self):
                return {"model": "2N"}

            async def async_get_call_status(self):
                return {"state": "idle", "sessions": []}

            async def async_get_phone_status(self):
                return {"accounts": [{"account": 1, "registered": True}]}

            async def async_get_switch_caps(self):
                return {"switches": [{"switch": 1, "enabled": True}]}

            async def async_get_switch_status(self):
                return {"switches": [{"switch": 1, "active": False}]}

            async def async_get_io_caps(self):
                return {"ports": [{"port": "relay1", "type": "output"}]}

            async def async_get_io_status(self):
                return {"ports": [{"port": "relay1", "state": 0}]}

        hass = types.SimpleNamespace()
        coordinator = coordinator_module.TwoNIntercomCoordinator(hass, FakeAPI())

        data = await coordinator._async_update_data()
        coordinator.data = data

        self.assertEqual(data.phone_status, {"accounts": [{"account": 1, "registered": True}]})
        self.assertEqual(data.switch_caps, {"switches": [{"switch": 1, "enabled": True}]})
        self.assertEqual(data.switch_status, {"switches": [{"switch": 1, "active": False}]})
        self.assertEqual(data.io_caps, {"ports": [{"port": "relay1", "type": "output"}]})
        self.assertEqual(data.io_status, {"ports": [{"port": "relay1", "state": 0}]})
        self.assertEqual(coordinator.phone_status, data.phone_status)
        self.assertEqual(coordinator.switch_caps, data.switch_caps)
        self.assertEqual(coordinator.switch_status, data.switch_status)
        self.assertEqual(coordinator.io_caps, data.io_caps)
        self.assertEqual(coordinator.io_status, data.io_status)

    async def test_secondary_refresh_failure_keeps_previous_cache(self) -> None:
        coordinator_module = self.coordinator_module

        class FakeAPI:
            def __init__(self) -> None:
                self.calls = 0

            async def async_get_system_info(self):
                return {"model": "2N"}

            async def async_get_call_status(self):
                return {"state": "idle", "sessions": []}

            async def async_get_phone_status(self):
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("temporary phone status failure")
                return {"accounts": [{"account": 1, "registered": True}]}

            async def async_get_switch_caps(self):
                return {"switches": [{"switch": 1, "enabled": True}]}

            async def async_get_switch_status(self):
                return {"switches": [{"switch": 1, "active": False}]}

            async def async_get_io_caps(self):
                return {"ports": [{"port": "relay1", "type": "output"}]}

            async def async_get_io_status(self):
                return {"ports": [{"port": "relay1", "state": 0}]}

        hass = types.SimpleNamespace()
        coordinator = coordinator_module.TwoNIntercomCoordinator(hass, FakeAPI())

        first = await coordinator._async_update_data()
        coordinator.data = first
        second = await coordinator._async_update_data()
        coordinator.data = second

        self.assertEqual(first.phone_status, {"accounts": [{"account": 1, "registered": True}]})
        self.assertEqual(second.phone_status, {"accounts": [{"account": 1, "registered": True}]})
        self.assertEqual(coordinator.phone_status, {"accounts": [{"account": 1, "registered": True}]})

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

    async def test_process_call_session_state_event_sets_ring_and_active_session(self) -> None:
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
                "event": "CallSessionStateChanged",
                "params": {
                    "direction": "incoming",
                    "state": "ringing",
                    "address": "sip:100@example.com",
                    "sessionNumber": 42,
                    "callSequenceNumber": 7,
                },
            }
        )

        self.assertEqual(coordinator.active_session_id, "42")
        self.assertEqual(coordinator.called_peer, "100")
        self.assertEqual(coordinator.call_state, "ringing")
        self.assertTrue(coordinator.ring_active)

    async def test_process_call_session_state_event_clears_ring_and_session_on_idle(self) -> None:
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
                "event": "CallSessionStateChanged",
                "params": {
                    "direction": "incoming",
                    "state": "ringing",
                    "address": "sip:100@example.com",
                    "sessionNumber": 42,
                    "callSequenceNumber": 7,
                },
            }
        )
        self.assertEqual(coordinator.active_session_id, "42")
        self.assertTrue(coordinator.ring_active)

        coordinator._process_log_event(
            {
                "event": "CallSessionStateChanged",
                "params": {
                    "direction": "incoming",
                    "state": "idle",
                    "sessionNumber": 42,
                    "callSequenceNumber": 7,
                },
            }
        )

        self.assertIsNone(coordinator.active_session_id)
        self.assertEqual(coordinator.call_state, "idle")
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
