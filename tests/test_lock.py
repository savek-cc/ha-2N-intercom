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
