"""Unit tests for the binary sensor platform."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONST_PATH = REPO_ROOT / "custom_components" / "2n_intercom" / "const.py"
BINARY_SENSOR_PATH = REPO_ROOT / "custom_components" / "2n_intercom" / "binary_sensor.py"


def _ensure_package(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = module
    return module


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _install_homeassistant_stubs() -> None:
    _ensure_package("homeassistant")

    binary_sensor_module = types.ModuleType("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass:
        OCCUPANCY = "occupancy"

    class BinarySensorEntity:
        def __init__(self) -> None:
            self.hass = None

    binary_sensor_module.BinarySensorDeviceClass = BinarySensorDeviceClass
    binary_sensor_module.BinarySensorEntity = BinarySensorEntity
    sys.modules["homeassistant.components.binary_sensor"] = binary_sensor_module
    _ensure_package("homeassistant.components")

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
    _ensure_package("homeassistant.helpers")


def load_binary_sensor_module():
    _install_homeassistant_stubs()
    _ensure_package("custom_components")
    _ensure_package("custom_components.2n_intercom")
    _load_module("custom_components.2n_intercom.const", CONST_PATH)
    coordinator_module = types.ModuleType("custom_components.2n_intercom.coordinator")
    coordinator_module.TwoNIntercomCoordinator = object
    sys.modules["custom_components.2n_intercom.coordinator"] = coordinator_module
    return _load_module("custom_components.2n_intercom.binary_sensor", BINARY_SENSOR_PATH)


class FakeConfigEntry:
    def __init__(self, entry_id: str, data: dict[str, object]) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = {}


class FakeCoordinator:
    def __init__(
        self,
        *,
        io_caps: dict[str, object] | None = None,
        io_status: dict[str, object] | None = None,
        switch_caps: dict[str, object] | None = None,
        switch_status: dict[str, object] | None = None,
    ) -> None:
        self.io_caps = io_caps or {}
        self.io_status = io_status or {}
        self.switch_caps = switch_caps or {}
        self.switch_status = switch_status or {}
        self.last_update_success = True

    def get_device_info(self, entry_id, name):
        return {"entry_id": entry_id, "name": name}


class BinarySensorPlatformTests(unittest.IsolatedAsyncioTestCase):
    """Tests for binary sensor platform behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.binary_sensor_module = load_binary_sensor_module()

    async def test_setup_entry_adds_input_sensor_when_input1_is_present(self) -> None:
        binary_sensor_module = self.binary_sensor_module
        coordinator = FakeCoordinator(
            io_caps={
                "ports": [
                    {"port": "input1", "type": "input"},
                ]
            },
            io_status={
                "ports": [
                    {"port": "input1", "state": 1},
                ]
            },
        )
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})

        hass = types.SimpleNamespace(
            data={
                "2n_intercom": {
                    entry.entry_id: {
                        "coordinator": coordinator,
                    }
                }
            }
        )
        added: list[object] = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )

        self.assertEqual(
            [entity._attr_unique_id for entity in added],
            ["entry-1_doorbell", "entry-1_input1"],
        )

    async def test_setup_entry_keeps_only_doorbell_when_input1_is_missing(self) -> None:
        binary_sensor_module = self.binary_sensor_module
        coordinator = FakeCoordinator(
            io_caps={"ports": [{"port": "relay1", "type": "output"}]},
            io_status={"ports": [{"port": "relay1", "state": 0}]},
        )
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})

        hass = types.SimpleNamespace(
            data={
                "2n_intercom": {
                    entry.entry_id: {
                        "coordinator": coordinator,
                    }
                }
            }
        )
        added: list[object] = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )

        self.assertEqual(
            [entity._attr_unique_id for entity in added],
            ["entry-1_doorbell"],
        )

    async def test_setup_entry_adds_relay_sensor_when_switch1_is_present(self) -> None:
        binary_sensor_module = self.binary_sensor_module
        coordinator = FakeCoordinator(
            switch_caps={
                "switches": [
                    {"switch": 1, "enabled": True},
                ]
            },
            switch_status={
                "switches": [
                    {"switch": 1, "active": False},
                ]
            },
        )
        entry = FakeConfigEntry("entry-1", {"name": "Front Door"})

        hass = types.SimpleNamespace(
            data={
                "2n_intercom": {
                    entry.entry_id: {
                        "coordinator": coordinator,
                    }
                }
            }
        )
        added: list[object] = []

        await binary_sensor_module.async_setup_entry(
            hass,
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )

        self.assertEqual(
            [entity._attr_unique_id for entity in added],
            ["entry-1_doorbell", "entry-1_relay1_active"],
        )

    def test_input_sensor_uses_cached_io_status_payload(self) -> None:
        binary_sensor_module = self.binary_sensor_module
        coordinator = FakeCoordinator(
            io_caps={
                "ports": [
                    {"port": "input1", "type": "input"},
                ]
            },
            io_status={
                "ports": [
                    {"port": "input1", "state": 1},
                ]
            },
        )
        entity = binary_sensor_module.TwoNIntercomInput1Sensor(
            coordinator,
            FakeConfigEntry("entry-1", {"name": "Front Door"}),
        )

        self.assertTrue(entity.is_on)

    def test_relay_sensor_uses_cached_switch_status_payload(self) -> None:
        binary_sensor_module = self.binary_sensor_module
        coordinator = FakeCoordinator(
            switch_caps={
                "switches": [
                    {"switch": 1, "enabled": True},
                ]
            },
            switch_status={
                "switches": [
                    {"switch": 1, "active": True, "locked": False, "held": False},
                ]
            },
        )
        entity = binary_sensor_module.TwoNIntercomRelay1ActiveSensor(
            coordinator,
            FakeConfigEntry("entry-1", {"name": "Front Door"}),
        )

        self.assertTrue(entity.is_on)
        self.assertEqual(entity._attr_unique_id, "entry-1_relay1_active")
        self.assertEqual(entity._attr_entity_category, "diagnostic")


if __name__ == "__main__":
    unittest.main()
