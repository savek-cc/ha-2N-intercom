"""Unit tests for diagnostic sensors."""

from __future__ import annotations

import sys
import types
import unittest

from _stubs import (
    CONST_PATH,
    ENTITY_PATH,
    SENSOR_PATH,
    ensure_package,
    load_module,
)


def _install_homeassistant_stubs() -> None:
    ensure_package("homeassistant")

    sensor_module = types.ModuleType("homeassistant.components.sensor")

    class SensorEntity:
        def __init__(self) -> None:
            self.hass = None

    sensor_module.SensorEntity = SensorEntity
    sys.modules["homeassistant.components.sensor"] = sensor_module
    ensure_package("homeassistant.components")

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    sys.modules["homeassistant.config_entries"] = config_entries

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    sys.modules["homeassistant.core"] = core

    entity = types.ModuleType("homeassistant.helpers.entity")

    class EntityCategory:
        DIAGNOSTIC = "diagnostic"

    entity.EntityCategory = EntityCategory
    sys.modules["homeassistant.helpers.entity"] = entity

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

    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    ensure_package("homeassistant.helpers")


def load_sensor_module():
    _install_homeassistant_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    load_module("custom_components.2n_intercom.const", CONST_PATH)
    coordinator_module = types.ModuleType("custom_components.2n_intercom.coordinator")
    coordinator_module.TwoNIntercomCoordinator = object
    coordinator_module.TwoNIntercomRuntimeData = object
    sys.modules["custom_components.2n_intercom.coordinator"] = coordinator_module
    load_module("custom_components.2n_intercom.entity", ENTITY_PATH)
    return load_module("custom_components.2n_intercom.sensor", SENSOR_PATH)


class FakeConfigEntry:
    def __init__(self, entry_id: str, data: dict[str, object]) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = {}


class FakeCoordinator:
    def __init__(
        self,
        *,
        phone_status: dict[str, object] | None = None,
        call_state: str | None = None,
        active_session_id: str | None = None,
    ) -> None:
        self.phone_status = phone_status or {}
        self.call_state = call_state
        self.active_session_id = active_session_id
        self.last_update_success = True

    def get_device_info(self, entry_id, name):
        return {"entry_id": entry_id, "name": name}


class DiagnosticSensorTests(unittest.IsolatedAsyncioTestCase):
    """Tests for diagnostic sensor behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sensor_module = load_sensor_module()

    async def test_setup_entry_registers_both_sensor_entities(self) -> None:
        sensor_module = self.sensor_module
        coordinator = FakeCoordinator()
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})
        entry.runtime_data = types.SimpleNamespace(coordinator=coordinator)

        hass = types.SimpleNamespace(data={})
        added: list[object] = []

        await sensor_module.async_setup_entry(
            hass,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )

        self.assertEqual(
            [entity._attr_unique_id for entity in added],
            ["entry-1_sip_registration", "entry-1_call_state"],
        )

    def test_sip_registration_entity_has_translation_key(self) -> None:
        sensor_module = self.sensor_module
        coordinator = FakeCoordinator()
        entity = sensor_module.TwoNIntercomSipRegistrationStatusSensor(
            coordinator,
            FakeConfigEntry("entry-1", {"name": "Front Door"}),
        )

        self.assertEqual(entity._attr_translation_key, "sip_registration")
        self.assertTrue(entity._attr_has_entity_name)

    def test_call_state_entity_has_translation_key(self) -> None:
        sensor_module = self.sensor_module
        coordinator = FakeCoordinator()
        entity = sensor_module.TwoNIntercomCallStateSensor(
            coordinator,
            FakeConfigEntry("entry-1", {"name": "Front Door"}),
        )

        self.assertEqual(entity._attr_translation_key, "call_state")
        self.assertTrue(entity._attr_has_entity_name)

    def test_sip_registration_sensor_derives_registered_state(self) -> None:
        sensor_module = self.sensor_module
        coordinator = FakeCoordinator(
            phone_status={
                "accounts": [
                    {
                        "account": 1,
                        "registrationEnabled": True,
                        "registered": True,
                    },
                    {
                        "account": 2,
                        "registrationEnabled": True,
                        "registered": False,
                    },
                ]
            }
        )
        entity = sensor_module.TwoNIntercomSipRegistrationStatusSensor(
            coordinator,
            FakeConfigEntry("entry-1", {"name": "Front Door"}),
        )

        self.assertEqual(entity.state, "registered")
        self.assertEqual(entity.extra_state_attributes["registered_accounts"], 1)

    def test_call_state_sensor_uses_call_state_and_active_session(self) -> None:
        sensor_module = self.sensor_module
        coordinator = FakeCoordinator(
            call_state="connected",
            active_session_id="session-123",
        )
        entity = sensor_module.TwoNIntercomCallStateSensor(
            coordinator,
            FakeConfigEntry("entry-1", {"name": "Front Door"}),
        )

        self.assertEqual(entity.state, "connected")
        self.assertEqual(
            entity.extra_state_attributes["active_session_id"], "session-123"
        )


if __name__ == "__main__":
    unittest.main()
