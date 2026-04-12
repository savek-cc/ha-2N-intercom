"""Unit tests for the cover platform."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from enum import IntFlag

from _stubs import (
    CONST_PATH,
    COVER_PATH,
    ENTITY_PATH,
    ensure_package,
    load_module,
)


def _install_homeassistant_stubs() -> None:
    ensure_package("homeassistant")

    cover_module = types.ModuleType("homeassistant.components.cover")

    class CoverDeviceClass:
        GATE = "gate"

    class CoverEntityFeature(IntFlag):
        OPEN = 1
        CLOSE = 2

    class CoverEntity:
        def __init__(self) -> None:
            self.hass = None

        def async_write_ha_state(self) -> None:
            return None

    cover_module.CoverDeviceClass = CoverDeviceClass
    cover_module.CoverEntity = CoverEntity
    cover_module.CoverEntityFeature = CoverEntityFeature
    sys.modules["homeassistant.components.cover"] = cover_module
    ensure_package("homeassistant.components")

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

        def async_write_ha_state(self) -> None:
            return None

    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    ensure_package("homeassistant.helpers")


def load_cover_module():
    _install_homeassistant_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    load_module("custom_components.2n_intercom.const", CONST_PATH)
    coordinator_module = types.ModuleType("custom_components.2n_intercom.coordinator")
    coordinator_module.TwoNIntercomCoordinator = object
    coordinator_module.TwoNIntercomRuntimeData = object
    sys.modules["custom_components.2n_intercom.coordinator"] = coordinator_module
    load_module("custom_components.2n_intercom.entity", ENTITY_PATH)
    return load_module("custom_components.2n_intercom.cover", COVER_PATH)


class FakeConfigEntry:
    def __init__(self, entry_id: str, data: dict[str, object]) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = {}


class FakeCoordinator:
    def __init__(self, trigger_result: bool = True) -> None:
        self.last_update_success = True
        self._trigger_result = trigger_result
        self.trigger_calls: list[dict] = []

    def get_device_info(self, entry_id, name):
        return {"entry_id": entry_id, "name": name}

    async def async_trigger_relay(self, relay, duration):
        self.trigger_calls.append({"relay": relay, "duration": duration})
        return self._trigger_result


class CoverPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Tests for cover platform behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cover_module = load_cover_module()

    def _make_relay_config(self, **overrides):
        cfg = {
            "relay_number": 1,
            "relay_name": "Main Gate",
            "relay_device_type": "gate",
            "relay_pulse_duration": 15000,
        }
        cfg.update(overrides)
        return cfg

    async def test_setup_entry_creates_cover_for_gate_relay(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()
        entry.options = {"relays": [relay_config]}
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        added: list[object] = []
        await cover_module.async_setup_entry(
            None,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]._attr_unique_id, "entry-1_cover_1")

    async def test_setup_entry_skips_door_type_relay(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_device_type="door")
        entry.options = {"relays": [relay_config]}
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        added: list[object] = []
        await cover_module.async_setup_entry(
            None,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
        self.assertEqual(len(added), 0)

    async def test_setup_entry_no_relays(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        entry.options = {}
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        added: list[object] = []
        await cover_module.async_setup_entry(
            None,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
        self.assertEqual(len(added), 0)

    async def test_setup_entry_reads_relays_from_data_fallback(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator()
        relay_config = self._make_relay_config()
        entry = FakeConfigEntry("entry-1", {"name": "Door", "relays": [relay_config]})
        entry.options = {}
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        added: list[object] = []
        await cover_module.async_setup_entry(
            None,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
        self.assertEqual(len(added), 1)

    def test_cover_initial_state(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)

        self.assertTrue(cover._attr_is_closed)
        self.assertFalse(cover._attr_is_opening)
        self.assertFalse(cover._attr_is_closing)
        self.assertEqual(cover._attr_name, "Main Gate")
        self.assertEqual(cover._attr_unique_id, "entry-1_cover_1")

    def test_cover_uses_default_gate_duration(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = {
            "relay_number": 2,
            "relay_name": "Side Gate",
            "relay_device_type": "gate",
        }
        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)
        self.assertEqual(cover._pulse_duration, 15000)

    async def test_open_cover_triggers_relay_and_sets_opening(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator(trigger_result=True)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_pulse_duration=10)

        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)
        await cover.async_open_cover()

        self.assertEqual(len(coordinator.trigger_calls), 1)
        self.assertEqual(coordinator.trigger_calls[0]["relay"], 1)
        self.assertTrue(cover._attr_is_opening)
        self.assertFalse(cover._attr_is_closed)
        self.assertFalse(cover._attr_is_closing)

        # Wait for delay task to complete
        await asyncio.sleep(0.02)
        self.assertFalse(cover._attr_is_opening)
        self.assertFalse(cover._attr_is_closed)

    async def test_close_cover_triggers_relay_and_sets_closing(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator(trigger_result=True)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_pulse_duration=10)

        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)
        cover._attr_is_closed = False  # Start open
        await cover.async_close_cover()

        self.assertTrue(cover._attr_is_closing)
        self.assertFalse(cover._attr_is_opening)

        await asyncio.sleep(0.02)
        self.assertFalse(cover._attr_is_closing)
        self.assertTrue(cover._attr_is_closed)

    async def test_open_cover_failure(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator(trigger_result=False)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)
        await cover.async_open_cover()

        # State should not change on failure
        self.assertTrue(cover._attr_is_closed)
        self.assertFalse(cover._attr_is_opening)

    async def test_close_cover_failure(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator(trigger_result=False)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)
        cover._attr_is_closed = False
        await cover.async_close_cover()

        self.assertFalse(cover._attr_is_closed)
        self.assertFalse(cover._attr_is_closing)

    async def test_open_cancels_pending_state_task(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator(trigger_result=True)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_pulse_duration=5000)

        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)
        # First open creates a long-running task
        await cover.async_open_cover()
        first_task = cover._state_task
        self.assertIsNotNone(first_task)
        self.assertFalse(first_task.done())

        # Second open should cancel the first task
        cover._pulse_duration = 10
        await cover.async_open_cover()
        # Give event loop a tick for cancellation to propagate
        await asyncio.sleep(0)
        self.assertTrue(first_task.done())

        await asyncio.sleep(0.02)

    async def test_close_cancels_pending_state_task(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator(trigger_result=True)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_pulse_duration=5000)

        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)
        await cover.async_open_cover()
        first_task = cover._state_task
        self.assertFalse(first_task.done())

        cover._pulse_duration = 10
        await cover.async_close_cover()
        await asyncio.sleep(0)
        self.assertTrue(first_task.done())

        await asyncio.sleep(0.02)

    def test_cover_device_info(self) -> None:
        cover_module = self.cover_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        cover = cover_module.TwoNIntercomCover(coordinator, entry, relay_config)
        info = cover._attr_device_info
        self.assertEqual(info["entry_id"], "entry-1")
        self.assertEqual(info["name"], "Front Door")


if __name__ == "__main__":
    unittest.main()
