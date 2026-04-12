"""Unit tests for the switch platform."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest

from _stubs import (
    CONST_PATH,
    ENTITY_PATH,
    SWITCH_PATH,
    ensure_package,
    load_module,
)


def _install_homeassistant_stubs() -> None:
    ensure_package("homeassistant")

    switch_module = types.ModuleType("homeassistant.components.switch")

    class SwitchEntity:
        def __init__(self) -> None:
            self.hass = None

        def async_write_ha_state(self) -> None:
            return None

    switch_module.SwitchEntity = SwitchEntity
    sys.modules["homeassistant.components.switch"] = switch_module
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


def load_switch_module():
    _install_homeassistant_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    load_module("custom_components.2n_intercom.const", CONST_PATH)
    coordinator_module = types.ModuleType("custom_components.2n_intercom.coordinator")
    coordinator_module.TwoNIntercomCoordinator = object
    coordinator_module.TwoNIntercomRuntimeData = object
    sys.modules["custom_components.2n_intercom.coordinator"] = coordinator_module
    load_module("custom_components.2n_intercom.entity", ENTITY_PATH)
    return load_module("custom_components.2n_intercom.switch", SWITCH_PATH)


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


class SwitchPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Tests for switch platform behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.switch_module = load_switch_module()

    def _make_relay_config(self, **overrides):
        cfg = {
            "relay_number": 1,
            "relay_name": "Front Door",
            "relay_device_type": "door",
            "relay_pulse_duration": 2000,
        }
        cfg.update(overrides)
        return cfg

    async def test_setup_entry_creates_switch_for_door_relay(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()
        entry.options = {"relays": [relay_config]}
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        added: list[object] = []
        await switch_module.async_setup_entry(
            None,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]._attr_unique_id, "entry-1_switch_1")

    async def test_setup_entry_skips_gate_type_relay(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_device_type="gate")
        entry.options = {"relays": [relay_config]}
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        added: list[object] = []
        await switch_module.async_setup_entry(
            None,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
        self.assertEqual(len(added), 0)

    async def test_setup_entry_no_relays(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        entry.options = {}
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        added: list[object] = []
        await switch_module.async_setup_entry(
            None,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
        self.assertEqual(len(added), 0)

    async def test_setup_entry_reads_relays_from_data_fallback(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        relay_config = self._make_relay_config()
        entry = FakeConfigEntry("entry-1", {"name": "Door", "relays": [relay_config]})
        entry.options = {}
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        added: list[object] = []
        await switch_module.async_setup_entry(
            None,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
        self.assertEqual(len(added), 1)

    def test_switch_initial_state(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)

        self.assertFalse(switch.is_on)
        self.assertEqual(switch._attr_name, "Front Door")
        self.assertEqual(switch._attr_unique_id, "entry-1_switch_1")
        self.assertEqual(switch._pulse_duration, 2000)

    def test_switch_uses_default_pulse_duration(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = {
            "relay_number": 2,
            "relay_name": "Side Door",
            "relay_device_type": "door",
        }
        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)
        self.assertEqual(switch._pulse_duration, 2000)

    async def test_turn_on_triggers_relay_and_sets_on(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator(trigger_result=True)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_pulse_duration=10)

        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)
        await switch.async_turn_on()

        self.assertEqual(len(coordinator.trigger_calls), 1)
        self.assertEqual(coordinator.trigger_calls[0]["relay"], 1)
        self.assertTrue(switch.is_on)

        # Wait for auto turn-off
        await asyncio.sleep(0.02)
        self.assertFalse(switch.is_on)

    async def test_turn_on_failure(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator(trigger_result=False)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)
        await switch.async_turn_on()

        self.assertFalse(switch.is_on)

    async def test_turn_off_sets_off(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)
        switch._attr_is_on = True
        await switch.async_turn_off()

        self.assertFalse(switch.is_on)

    async def test_turn_on_cancels_pending_off_task(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator(trigger_result=True)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_pulse_duration=5000)

        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)
        await switch.async_turn_on()
        first_task = switch._turning_off_task
        self.assertIsNotNone(first_task)
        self.assertFalse(first_task.done())

        # Second turn_on cancels the first auto-off task
        switch._pulse_duration = 10
        await switch.async_turn_on()
        await asyncio.sleep(0)
        self.assertTrue(first_task.done())

        await asyncio.sleep(0.02)

    async def test_turn_off_cancels_pending_off_task(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator(trigger_result=True)
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config(relay_pulse_duration=5000)

        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)
        await switch.async_turn_on()
        first_task = switch._turning_off_task

        await switch.async_turn_off()
        await asyncio.sleep(0)
        self.assertTrue(first_task.done())
        self.assertFalse(switch.is_on)

    def test_switch_device_info(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)
        info = switch.device_info
        self.assertEqual(info["entry_id"], "entry-1")
        self.assertEqual(info["name"], "Front Door")

    def test_switch_available(self) -> None:
        switch_module = self.switch_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        relay_config = self._make_relay_config()

        switch = switch_module.TwoNIntercomSwitch(coordinator, entry, relay_config)
        self.assertTrue(switch.available)
        coordinator.last_update_success = False
        self.assertFalse(switch.available)


if __name__ == "__main__":
    unittest.main()
