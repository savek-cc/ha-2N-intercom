"""Sensor platform for 2N Intercom diagnostic status."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TwoNIntercomCoordinator, TwoNIntercomRuntimeData
from .entity import TwoNIntercomEntity

# Sensors are pure consumers of the coordinator's cached payloads and never
# hit the device on async_update, so unlimited concurrency is correct per the
# HA quality-scale `parallel-updates` rule.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up 2N Intercom sensor platform."""
    runtime: TwoNIntercomRuntimeData = config_entry.runtime_data
    coordinator: TwoNIntercomCoordinator = runtime.coordinator

    async_add_entities(
        [
            TwoNIntercomSipRegistrationStatusSensor(coordinator, config_entry),
            TwoNIntercomCallStateSensor(coordinator, config_entry),
        ],
        True,
    )


class _TwoNIntercomDiagnosticSensor(TwoNIntercomEntity, SensorEntity):
    """Base class for diagnostic sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
        name: str,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_name = name
        self._attr_unique_id = f"{config_entry.entry_id}_{unique_id_suffix}"


class TwoNIntercomSipRegistrationStatusSensor(_TwoNIntercomDiagnosticSensor):
    """Representation of the current SIP registration status."""

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            "SIP registration",
            "sip_registration",
        )

    @staticmethod
    def _derive_state(phone_status: dict[str, Any]) -> str:
        accounts = phone_status.get("accounts") or []
        if not accounts:
            return "unknown"

        registration_enabled_accounts = [
            account
            for account in accounts
            if isinstance(account, dict) and account.get("registrationEnabled")
        ]
        if not registration_enabled_accounts:
            return "disabled"

        if any(
            bool(account.get("registered"))
            for account in registration_enabled_accounts
        ):
            return "registered"

        return "unregistered"

    @property
    def state(self) -> str:
        """Return the registration status."""
        return self._derive_state(self.coordinator.phone_status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return concise registration details."""
        accounts = self.coordinator.phone_status.get("accounts") or []
        registration_enabled_accounts = [
            account
            for account in accounts
            if isinstance(account, dict) and account.get("registrationEnabled")
        ]
        registered_accounts = [
            account
            for account in registration_enabled_accounts
            if account.get("registered")
        ]
        return {
            "accounts": len(accounts),
            "registration_enabled_accounts": len(registration_enabled_accounts),
            "registered_accounts": len(registered_accounts),
        }


class TwoNIntercomCallStateSensor(_TwoNIntercomDiagnosticSensor):
    """Representation of the current call state."""

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry, "Call state", "call_state")

    @property
    def state(self) -> str:
        """Return the current call state."""
        if self.coordinator.call_state:
            return self.coordinator.call_state
        if self.coordinator.active_session_id:
            return "active"
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return concise call details."""
        attributes: dict[str, Any] = {}
        if self.coordinator.active_session_id:
            attributes["active_session_id"] = self.coordinator.active_session_id
        return attributes
