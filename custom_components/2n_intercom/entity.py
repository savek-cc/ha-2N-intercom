"""Shared entity base for 2N Intercom platforms.

Centralises ``device_info``, ``available`` and the ``has_entity_name``
flag so the per-platform entities don't reimplement the same five lines
each. The camera entity does its own thing because it inherits from
``MjpegCamera`` and the multiple inheritance with ``CoordinatorEntity``
already pulls in everything we need (see ``camera.py``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TwoNIntercomCoordinator

if TYPE_CHECKING:
    from .coordinator import TwoNIntercomConfigEntry


class TwoNIntercomEntity(CoordinatorEntity[TwoNIntercomCoordinator]):  # type: ignore[misc]
    """Base entity that wires the config entry and shared device info."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: TwoNIntercomConfigEntry,
    ) -> None:
        """Initialise the entity with its config entry."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        name: str = config_entry.options.get(
            "name",
            config_entry.data.get("name", "2N Intercom"),
        )
        self._attr_device_info = coordinator.get_device_info(
            config_entry.entry_id, name
        )
