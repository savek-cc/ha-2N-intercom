"""Shared entity base for 2N Intercom platforms.

Centralises ``device_info``, ``available`` and the ``has_entity_name``
flag so the per-platform entities don't reimplement the same five lines
each. The camera entity does its own thing because it inherits from
``MjpegCamera`` and the multiple inheritance with ``CoordinatorEntity``
already pulls in everything we need (see ``camera.py``).
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TwoNIntercomCoordinator


class TwoNIntercomEntity(CoordinatorEntity[TwoNIntercomCoordinator]):  # type: ignore[misc]
    """Base entity that wires the config entry and shared device info."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialise the entity with its config entry."""
        super().__init__(coordinator)
        self._config_entry = config_entry

    @property
    def _entry_display_name(self) -> str:
        """Return the user-facing device name from the config entry."""
        name: str = self._config_entry.options.get(
            "name",
            self._config_entry.data.get("name", "2N Intercom"),
        )
        return name

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information for the integration device."""
        result: dict[str, Any] = self.coordinator.get_device_info(
            self._config_entry.entry_id, self._entry_display_name
        )
        return result

    @property
    def available(self) -> bool:
        """Return whether the entity is available."""
        available: bool = self.coordinator.last_update_success
        return available
