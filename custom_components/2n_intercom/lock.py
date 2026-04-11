"""Support for 2N Intercom locks."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DOOR_TYPE,
    CONF_RELAY_DEVICE_TYPE,
    CONF_RELAYS,
    DEVICE_TYPE_GATE,
    DOMAIN,
    DOOR_TYPE_DOOR,
    DOOR_TYPE_GATE,
)
from .coordinator import TwoNIntercomCoordinator
from .entity import TwoNIntercomEntity

# Lock actions hit the device (relay trigger) so we serialise them per
# platform; reads come from the coordinator and don't count toward this limit.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up 2N Intercom lock platform."""
    coordinator: TwoNIntercomCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]

    door_type = config_entry.options.get(
        CONF_DOOR_TYPE, config_entry.data.get(CONF_DOOR_TYPE)
    )
    if door_type is None:
        relays = config_entry.data.get(CONF_RELAYS, [])
        door_type = (
            DOOR_TYPE_GATE
            if any(
                relay.get(CONF_RELAY_DEVICE_TYPE) == DEVICE_TYPE_GATE
                for relay in relays
            )
            else DOOR_TYPE_DOOR
        )

    async_add_entities(
        [TwoNIntercomLock(coordinator, config_entry, door_type)],
        True,
    )


class TwoNIntercomLock(TwoNIntercomEntity, LockEntity):
    """Representation of a 2N Intercom lock."""

    _attr_name = None
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
        door_type: str | None,
    ) -> None:
        """Initialize the lock."""
        super().__init__(coordinator, config_entry)

        self._door_type = door_type
        self._attr_unique_id = f"{config_entry.entry_id}_lock"
        self._attr_is_locked = True

        # Set device class based on door type for HomeKit
        # Gate -> DEVICE_CLASS_GATE for HomeKit garage door accessory
        # Door -> no device class (default door lock)
        if door_type == DOOR_TYPE_GATE:
            self._attr_device_class = "gate"

    def _relay_one_present(self) -> bool:
        """Return True when switch caps confirm that relay 1 exists.

        We only fall back to optimistic state when the device tells us
        relay 1 doesn't exist; a transient missing payload should keep
        the previous real state instead of bouncing to the optimistic
        ``_attr_is_locked`` flag.
        """
        switches = self.coordinator.switch_caps.get("switches")
        if not isinstance(switches, list):
            return False
        for switch in switches:
            if not isinstance(switch, dict):
                continue
            if switch.get("switch") == 1:
                return True
        return False

    def _cached_is_locked(self) -> bool | None:
        """Return the cached lock state for relay 1 when available."""
        switches = self.coordinator.switch_status.get("switches")
        if not isinstance(switches, list):
            return None

        for switch in switches:
            if not isinstance(switch, dict):
                continue
            if switch.get("switch") != 1:
                continue
            active = switch.get("active")
            held = switch.get("held")
            if isinstance(active, bool) or isinstance(held, bool):
                return not bool(active) and not bool(held)
            return None

        return None

    @property
    def is_locked(self) -> bool:
        """Return true if lock is locked."""
        cached_is_locked = self._cached_is_locked()
        if cached_is_locked is not None:
            return cached_is_locked
        # No real status available. If caps confirm relay 1 exists, the
        # missing status is transient — preserve the last optimistic value
        # rather than flapping. If caps say there is no relay 1, fall back
        # to the optimistic flag (legacy behavior for stub-only setups).
        if self._relay_one_present():
            return self._attr_is_locked
        return self._attr_is_locked

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock."""
        self._attr_is_locked = True
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock."""
        # Trigger relay 1 (default relay for legacy lock)
        success = await self.coordinator.async_trigger_relay(relay=1, duration=2000)

        if success:
            self._attr_is_locked = False
            self.async_write_ha_state()

    async def async_open(self, **kwargs: Any) -> None:
        """Open the door/gate."""
        # Same as unlock
        await self.async_unlock(**kwargs)
