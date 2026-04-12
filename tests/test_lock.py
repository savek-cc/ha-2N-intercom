"""Unit tests for the 2N Intercom lock entity."""

from __future__ import annotations

import sys
import types
import unittest
from enum import Enum, IntFlag

from _stubs import (
    CONST_PATH,
    ENTITY_PATH,
    LOCK_PATH,
    ensure_package,
    load_module,
)


def _install_homeassistant_stubs() -> None:
    ensure_package("homeassistant")

    const_module = types.ModuleType("homeassistant.const")

    class Platform(Enum):
        LOCK = "lock"

    const_module.Platform = Platform
    sys.modules["homeassistant.const"] = const_module

    lock_module = types.ModuleType("homeassistant.components.lock")

    class LockEntity:
        def async_write_ha_state(self) -> None:
            return None

    class LockEntityFeature(IntFlag):
        OPEN = 1

    lock_module.LockEntity = LockEntity
    lock_module.LockEntityFeature = LockEntityFeature
    sys.modules["homeassistant.components.lock"] = lock_module
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


def load_lock_module():
    _install_homeassistant_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    const_module = load_module("custom_components.2n_intercom.const", CONST_PATH)
    coordinator_module = types.ModuleType("custom_components.2n_intercom.coordinator")
    coordinator_module.TwoNIntercomCoordinator = object
    coordinator_module.TwoNIntercomRuntimeData = object
    sys.modules["custom_components.2n_intercom.coordinator"] = coordinator_module
    load_module("custom_components.2n_intercom.entity", ENTITY_PATH)
    lock_module = load_module("custom_components.2n_intercom.lock", LOCK_PATH)
    return lock_module, const_module


class LockStatusDerivationTests(unittest.TestCase):
    """Tests for cached lock state derivation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.lock_module, cls.const_module = load_lock_module()

    def _make_entity(self, *, switch_status: dict[str, object], optimistic: bool) -> object:
        coordinator = types.SimpleNamespace(
            switch_status=switch_status,
            last_update_success=True,
            async_trigger_relay=lambda **kwargs: True,
            get_device_info=lambda entry_id, name: {"entry_id": entry_id, "name": name},
        )
        config_entry = types.SimpleNamespace(
            entry_id="entry-1",
            data={"name": "Front Door"},
            options={},
        )
        entity = self.lock_module.TwoNIntercomLock(coordinator, config_entry, None)
        entity._attr_is_locked = optimistic
        return entity

    def test_is_locked_prefers_cached_relay_one_activity_state(self) -> None:
        entity = self._make_entity(
            switch_status={
                "switches": [
                    {"switch": 1, "active": True, "locked": False, "held": False}
                ]
            },
            optimistic=True,
        )

        self.assertFalse(entity.is_locked)

    def test_is_locked_treats_inactive_relay_one_as_locked(self) -> None:
        entity = self._make_entity(
            switch_status={
                "switches": [
                    {"switch": 1, "active": False, "locked": False, "held": False}
                ]
            },
            optimistic=False,
        )

        self.assertTrue(entity.is_locked)

    def test_is_locked_falls_back_when_relay_one_cache_is_unusable(self) -> None:
        entity = self._make_entity(
            switch_status={
                "switches": [
                    {"switch": 2, "active": False, "locked": False, "held": False},
                    {"switch": 1, "active": False, "held": False},
                ]
            },
            optimistic=True,
        )

        self.assertTrue(entity.is_locked)

    def test_relay_one_present_true(self) -> None:
        coordinator = types.SimpleNamespace(
            switch_caps={"switches": [{"switch": 1}]},
            switch_status={"switches": []},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
        )
        entry = types.SimpleNamespace(entry_id="e1", data={}, options={})
        lock = self.lock_module.TwoNIntercomLock(coordinator, entry, None)
        self.assertTrue(lock._relay_one_present())

    def test_relay_one_present_false_when_no_relay(self) -> None:
        coordinator = types.SimpleNamespace(
            switch_caps={"switches": [{"switch": 2}]},
            switch_status={"switches": []},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
        )
        entry = types.SimpleNamespace(entry_id="e1", data={}, options={})
        lock = self.lock_module.TwoNIntercomLock(coordinator, entry, None)
        self.assertFalse(lock._relay_one_present())

    def test_relay_one_present_false_when_caps_empty(self) -> None:
        coordinator = types.SimpleNamespace(
            switch_caps={},
            switch_status={"switches": []},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
        )
        entry = types.SimpleNamespace(entry_id="e1", data={}, options={})
        lock = self.lock_module.TwoNIntercomLock(coordinator, entry, None)
        self.assertFalse(lock._relay_one_present())

    def test_cached_is_locked_when_held_true(self) -> None:
        entity = self._make_entity(
            switch_status={
                "switches": [{"switch": 1, "active": False, "held": True}]
            },
            optimistic=True,
        )
        self.assertFalse(entity.is_locked)

    def test_cached_is_locked_returns_none_on_missing_status(self) -> None:
        coordinator = types.SimpleNamespace(
            switch_status={},
            switch_caps={},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
        )
        entry = types.SimpleNamespace(entry_id="e1", data={}, options={})
        entity = self.lock_module.TwoNIntercomLock(coordinator, entry, None)
        entity._attr_is_locked = True
        # No switches in status, no relay in caps → falls back to optimistic
        self.assertTrue(entity.is_locked)

    def test_gate_door_type_sets_device_class(self) -> None:
        coordinator = types.SimpleNamespace(
            switch_status={"switches": []},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
        )
        entry = types.SimpleNamespace(entry_id="e1", data={}, options={})
        lock = self.lock_module.TwoNIntercomLock(coordinator, entry, "gate")
        self.assertEqual(lock._attr_device_class, "gate")

    def test_door_type_no_device_class(self) -> None:
        coordinator = types.SimpleNamespace(
            switch_status={"switches": []},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
        )
        entry = types.SimpleNamespace(entry_id="e1", data={}, options={})
        lock = self.lock_module.TwoNIntercomLock(coordinator, entry, "door")
        self.assertFalse(hasattr(lock, "_attr_device_class"))


class LockAsyncTests(unittest.IsolatedAsyncioTestCase):
    """Async tests for lock operations."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.lock_module, cls.const_module = load_lock_module()

    def _make_entity(self, relay_success: bool = True):
        async def fake_trigger(relay, duration):
            return relay_success

        coordinator = types.SimpleNamespace(
            switch_status={"switches": []},
            switch_caps={"switches": [{"switch": 1}]},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
            async_trigger_relay=fake_trigger,
        )
        entry = types.SimpleNamespace(entry_id="e1", data={}, options={})
        return self.lock_module.TwoNIntercomLock(coordinator, entry, None)

    async def test_async_lock_sets_locked(self) -> None:
        entity = self._make_entity()
        entity._attr_is_locked = False
        await entity.async_lock()
        self.assertTrue(entity._attr_is_locked)

    async def test_async_unlock_on_success(self) -> None:
        entity = self._make_entity(relay_success=True)
        await entity.async_unlock()
        self.assertFalse(entity._attr_is_locked)

    async def test_async_unlock_on_failure(self) -> None:
        entity = self._make_entity(relay_success=False)
        entity._attr_is_locked = True
        await entity.async_unlock()
        # Should stay locked on failure
        self.assertTrue(entity._attr_is_locked)

    async def test_async_open_delegates_to_unlock(self) -> None:
        entity = self._make_entity(relay_success=True)
        await entity.async_open()
        self.assertFalse(entity._attr_is_locked)

    async def test_setup_entry_creates_lock(self) -> None:
        entities = []

        def add_entities(ents, update_before_add):
            entities.extend(ents)

        coordinator = types.SimpleNamespace(
            switch_status={"switches": []},
            switch_caps={},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
        )
        runtime = types.SimpleNamespace(coordinator=coordinator)
        config_entry = types.SimpleNamespace(
            entry_id="e1",
            data={"door_type": "door"},
            options={},
            runtime_data=runtime,
        )
        await self.lock_module.async_setup_entry(None, config_entry, add_entities)
        self.assertEqual(len(entities), 1)

    async def test_setup_entry_derives_door_type_from_relays(self) -> None:
        entities = []

        def add_entities(ents, update_before_add):
            entities.extend(ents)

        coordinator = types.SimpleNamespace(
            switch_status={"switches": []},
            switch_caps={},
            last_update_success=True,
            get_device_info=lambda eid, n: {},
        )
        runtime = types.SimpleNamespace(coordinator=coordinator)
        config_entry = types.SimpleNamespace(
            entry_id="e1",
            data={"relays": [{"relay_device_type": "gate"}]},
            options={},
            runtime_data=runtime,
        )
        await self.lock_module.async_setup_entry(None, config_entry, add_entities)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]._attr_device_class, "gate")
