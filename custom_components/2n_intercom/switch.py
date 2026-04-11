"""Switch platform for 2N Intercom."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_RELAY_DEVICE_TYPE,
    CONF_RELAY_NAME,
    CONF_RELAY_NUMBER,
    CONF_RELAY_PULSE_DURATION,
    CONF_RELAYS,
    DEFAULT_PULSE_DURATION,
    DEVICE_TYPE_DOOR,
    DOMAIN,
)
from .coordinator import TwoNIntercomCoordinator
from .entity import TwoNIntercomEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up 2N Intercom switch platform."""
    coordinator: TwoNIntercomCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    
    relays = {**config_entry.data, **config_entry.options}.get(CONF_RELAYS, [])
    
    # Create switch entities for door-type relays
    switches = []
    for relay_config in relays:
        if relay_config.get(CONF_RELAY_DEVICE_TYPE) == DEVICE_TYPE_DOOR:
            switches.append(
                TwoNIntercomSwitch(coordinator, config_entry, relay_config)
            )
    
    if switches:
        async_add_entities(switches, True)


class TwoNIntercomSwitch(TwoNIntercomEntity, SwitchEntity):
    """Representation of a 2N Intercom switch (for doors)."""

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
        relay_config: dict[str, Any],
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, config_entry)

        self._relay_config = relay_config
        self._relay_number = relay_config[CONF_RELAY_NUMBER]
        self._relay_name = relay_config[CONF_RELAY_NAME]
        self._pulse_duration = relay_config.get(
            CONF_RELAY_PULSE_DURATION, DEFAULT_PULSE_DURATION
        )

        self._attr_name = self._relay_name
        self._attr_unique_id = f"{config_entry.entry_id}_switch_{self._relay_number}"
        self._attr_is_on = False
        self._turning_off_task: asyncio.Task | None = None

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (trigger relay)."""
        # Cancel any pending turn off task
        if self._turning_off_task and not self._turning_off_task.done():
            self._turning_off_task.cancel()
        
        # Trigger relay
        success = await self.coordinator.async_trigger_relay(
            relay=self._relay_number,
            duration=self._pulse_duration,
        )
        
        if success:
            self._attr_is_on = True
            self.async_write_ha_state()
            
            # Schedule automatic turn off after pulse duration
            self._turning_off_task = asyncio.create_task(
                self._async_turn_off_after_delay()
            )
        else:
            _LOGGER.error("Failed to trigger relay %s", self._relay_number)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (no actual action, just update state)."""
        # Cancel any pending turn off task
        if self._turning_off_task and not self._turning_off_task.done():
            self._turning_off_task.cancel()
        
        self._attr_is_on = False
        self.async_write_ha_state()

    async def _async_turn_off_after_delay(self) -> None:
        """Turn off the switch after the pulse duration."""
        try:
            # Wait for pulse duration (convert milliseconds to seconds)
            await asyncio.sleep(self._pulse_duration / 1000)
            self._attr_is_on = False
            self.async_write_ha_state()
        except asyncio.CancelledError:
            # Task was cancelled, do nothing
            pass
