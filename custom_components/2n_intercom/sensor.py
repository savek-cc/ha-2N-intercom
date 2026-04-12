"""Sensor platform for 2N Intercom diagnostic status."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
try:
    from homeassistant.const import EntityCategory
except ImportError:  # pragma: no cover — test stub compat
    from homeassistant.helpers.entity import EntityCategory  # type: ignore[no-redef,assignment,attr-defined]
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TwoNIntercomCoordinator, TwoNIntercomRuntimeData
from .entity import TwoNIntercomEntity

if TYPE_CHECKING:
    from .coordinator import TwoNIntercomConfigEntry

# Sensors are pure consumers of the coordinator's cached payloads and never
# hit the device on async_update, so unlimited concurrency is correct per the
# HA quality-scale `parallel-updates` rule.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: TwoNIntercomConfigEntry,
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


class _TwoNIntercomDiagnosticSensor(TwoNIntercomEntity, SensorEntity):  # type: ignore[misc]
    """Base class for diagnostic sensors.

    Subclasses set ``_attr_translation_key`` (matched against
    ``entity.sensor.<key>.name`` in ``strings.json``) so the visible
    entity name is localised by HA, per the entity-translations rule.

    Disabled by default per the ``entity-disabled-by-default`` rule:
    diagnostic/technical telemetry should not clutter the default entity
    list. Users who need SIP registration or call-state data can enable
    them from the entity registry.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: TwoNIntercomConfigEntry,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_{unique_id_suffix}"


class TwoNIntercomSipRegistrationStatusSensor(_TwoNIntercomDiagnosticSensor):
    """Representation of the current SIP registration status."""

    _attr_translation_key = "sip_registration"

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: TwoNIntercomConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry, "sip_registration")

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
    def native_value(self) -> str:
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


class TwoNIntercomCallStateSensor(TwoNIntercomEntity, SensorEntity):  # type: ignore[misc]
    """Representation of the current call state.

    Promoted to a normal (non-diagnostic) entity because call state is
    core intercom functionality — users automate on ringing/active/idle
    transitions (e.g. "stream camera to TV when call is active").
    """

    _attr_translation_key = "call_state"

    def __init__(
        self,
        coordinator: TwoNIntercomCoordinator,
        config_entry: TwoNIntercomConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_call_state"

    @property
    def native_value(self) -> str:
        """Return the current call state."""
        call_state: str | None = self.coordinator.call_state
        if call_state:
            return call_state
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
