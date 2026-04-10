"""Binary sensor platform for 2N Intercom."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TwoNIntercomCoordinator

_LOGGER = logging.getLogger(__name__)


def _port_exists(
    payload: dict[str, Any],
    port_name: str,
    *,
    port_type: str | None = None,
) -> bool:
    """Return True when the cached port payload contains the requested port."""
    ports = payload.get("ports") or []
    for port in ports:
        if not isinstance(port, dict):
            continue
        if port.get("port") != port_name:
            continue
        if port_type is not None and port.get("type") != port_type:
            continue
        return True
    return False


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up 2N Intercom binary sensor platform."""
    coordinator: TwoNIntercomCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    entities: list[BinarySensorEntity] = [TwoNIntercomDoorbell(coordinator, config_entry)]

    if _port_exists(coordinator.io_caps, "input1", port_type="input") and _port_exists(
        coordinator.io_status,
        "input1",
    ):
        entities.append(TwoNIntercomInput1Sensor(coordinator, config_entry))

    async_add_entities(
        entities,
        True,
    )


class TwoNIntercomDoorbell(CoordinatorEntity[TwoNIntercomCoordinator], BinarySensorEntity):
    """Representation of a 2N Intercom doorbell."""

    _attr_has_entity_name = True
    _attr_name = "Doorbell"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY  # Default, overridden in __init__

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the doorbell."""
        super().__init__(coordinator)
        
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_doorbell"
        
        # Prefer doorbell device class for HomeKit integration.
        # If the enum is missing (older HA), fall back to raw string.
        self._attr_device_class = getattr(
            BinarySensorDeviceClass,
            "DOORBELL",
            "doorbell",
        )

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information about this doorbell."""
        name = self._config_entry.options.get(
            "name",
            self._config_entry.data.get("name", "2N Intercom"),
        )
        return self.coordinator.get_device_info(self._config_entry.entry_id, name)

    @property
    def is_on(self) -> bool:
        """Return true if doorbell is ringing."""
        return self.coordinator.ring_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attributes: dict[str, Any] = {}
        
        if self.coordinator.last_ring_time:
            attributes["last_ring"] = self.coordinator.last_ring_time.isoformat()

        if self.coordinator.called_peer:
            attributes["called_peer"] = self.coordinator.called_peer
        
        caller_info = self.coordinator.caller_info
        if caller_info:
            if "name" in caller_info:
                attributes["caller_name"] = caller_info["name"]
            if "number" in caller_info:
                attributes["caller_number"] = caller_info["number"]
            if "button" in caller_info:
                attributes["button"] = caller_info["button"]
        
        # Add call status info if available
        if self.coordinator.data:
            call_status = self.coordinator.data.call_status
            call_state = self.coordinator.call_state
            if call_state:
                attributes["call_state"] = call_state
            if call_status and "direction" in call_status:
                attributes["call_direction"] = call_status["direction"]
        
        return attributes

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success


class TwoNIntercomInput1Sensor(
    CoordinatorEntity[TwoNIntercomCoordinator], BinarySensorEntity
):
    """Representation of the real IO input 1 state."""

    _attr_has_entity_name = True
    _attr_name = "Input 1"

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the input sensor."""
        super().__init__(coordinator)

        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_input1"

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information about this input sensor."""
        name = self._config_entry.options.get(
            "name",
            self._config_entry.data.get("name", "2N Intercom"),
        )
        return self.coordinator.get_device_info(self._config_entry.entry_id, name)

    @staticmethod
    def _is_port_on(io_status: dict[str, Any]) -> bool:
        ports = io_status.get("ports") or []
        for port in ports:
            if not isinstance(port, dict):
                continue
            if port.get("port") == "input1":
                return port.get("state") in (1, True, "1", "on", "true")
        return False

    @property
    def is_on(self) -> bool:
        """Return true if input 1 is active."""
        return self._is_port_on(self.coordinator.io_status)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success
