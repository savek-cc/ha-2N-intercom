"""DataUpdateCoordinator for 2N Intercom."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta, datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import TwoNIntercomAPI, TwoNAuthenticationError, TwoNConnectionError
from .const import CALLED_ID_ALL, DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

# Maximum number of retries before giving up
MAX_RETRIES = 5
# Maximum delay between retries (seconds)
MAX_BACKOFF_DELAY = 60
# Snapshot cache duration (seconds)
SNAPSHOT_CACHE_DURATION = 1
# Doorbell pulse duration (seconds)
DOORBELL_PULSE_DURATION = 1


@dataclass
class TwoNIntercomData:
    """Data structure for 2N Intercom coordinator."""

    call_status: dict[str, Any]
    last_ring_time: datetime | None
    caller_info: dict[str, Any] | None
    active_session_id: str | None
    available: bool


class TwoNIntercomCoordinator(DataUpdateCoordinator[TwoNIntercomData]):
    """Coordinator to manage data updates from 2N Intercom."""

    _RING_STATES = {"ringing", "alerting", "incoming", "ring"}

    def __init__(
        self,
        hass: HomeAssistant,
        api: TwoNIntercomAPI,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        called_id: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self._last_call_state: dict[str, Any] = {}
        self._ring_detected = False
        self._last_ring_time: datetime | None = None
        self._active_session_id: str | None = None
        # Note: _retry_count is safe from race conditions because Home Assistant's
        # DataUpdateCoordinator serializes update calls - only one _async_update_data
        # runs at a time
        self._retry_count = 0
        self._snapshot_cache: bytes | None = None
        self._snapshot_cache_time: datetime | None = None
        self._snapshot_cache_size: tuple[int | None, int | None] | None = None
        self._system_info: dict[str, Any] | None = None
        self._ring_filter_peer = self._normalize_peer(called_id)
        self._last_called_peer: str | None = None
        self._last_call_state_value: str = "idle"
        self._ring_pulse_until: datetime | None = None
        self._log_subscription_id: int | None = None
        self._log_listener_task: asyncio.Task | None = None

    @staticmethod
    def _normalize_peer(peer: str | None) -> str | None:
        if not peer or peer == CALLED_ID_ALL:
            return None
        normalized = peer.replace("sip:", "")
        if "@" in normalized:
            normalized = normalized.split("@", 1)[0]
        return normalized.strip() or None

    @staticmethod
    def _extract_called_peer(call_status: dict[str, Any]) -> str | None:
        sessions = call_status.get("sessions") or []
        if not sessions:
            return None
        calls = sessions[0].get("calls") or []
        if not calls:
            return None
        return calls[0].get("peer")

    @staticmethod
    def _extract_call_state(call_status: dict[str, Any]) -> str | None:
        state = call_status.get("state")
        if state:
            return state

        sessions = call_status.get("sessions") or []
        for session in sessions:
            session_state = session.get("state")
            if session_state:
                return session_state

            calls = session.get("calls") or []
            for call in calls:
                call_state = call.get("state") or call.get("status") or call.get("callState")
                if call_state:
                    return call_state

        return None

    @staticmethod
    def _extract_active_session_id(call_status: dict[str, Any]) -> str | None:
        """Return the active call session id, if available."""
        if not isinstance(call_status, dict):
            return None

        active_states = {"ringing", "alerting", "incoming", "active", "connected"}
        sessions = call_status.get("sessions") or []

        for session in sessions:
            if not isinstance(session, dict):
                continue

            candidate = session.get("session")
            if candidate is None:
                continue

            normalized_candidate = str(candidate).strip()
            if not normalized_candidate:
                continue

            state = str(session.get("state") or "").strip().lower()
            if state in active_states:
                return normalized_candidate

        state = str(call_status.get("state") or "").strip().lower()
        if state in active_states:
            session_id = call_status.get("session")
            if session_id is not None:
                normalized_session = str(session_id).strip()
                if normalized_session:
                    return normalized_session

        return None

    @staticmethod
    def _extract_first_nonempty_string(params: dict[str, Any], *keys: str) -> str | None:
        """Return the first non-empty string value for any of the provided keys."""
        for key in keys:
            raw_value = params.get(key)
            if raw_value is None:
                continue

            normalized_value = str(raw_value).strip()
            if normalized_value:
                return normalized_value

        return None

    def _process_log_event(self, event: dict[str, Any]) -> bool:
        """Apply a supported log event to coordinator state."""
        if not isinstance(event, dict):
            return False

        event_name = str(event.get("event") or "").strip()
        if event_name not in {"CallStateChanged", "CallSessionStateChanged"}:
            return False

        params = event.get("params") or {}
        if not isinstance(params, dict):
            return False

        raw_state = params.get("state") or params.get("status")
        if raw_state is None:
            return False

        state = str(raw_state).strip().lower()
        if not state:
            return False

        if event_name == "CallSessionStateChanged":
            session_id = self._extract_first_nonempty_string(
                params, "sessionNumber", "session"
            )
            raw_peer = self._extract_first_nonempty_string(
                params, "address", "peer"
            )
        else:
            session_id = self._extract_first_nonempty_string(
                params, "session", "sessionNumber"
            )
            raw_peer = self._extract_first_nonempty_string(
                params, "peer", "address"
            )

        if raw_peer is not None:
            self._last_called_peer = self._normalize_peer(raw_peer)

        direction = str(params.get("direction") or "").strip().lower()
        active_states = self._RING_STATES | {"active", "connected"}
        terminal_states = {
            "idle",
            "terminated",
            "ended",
            "finished",
            "closed",
            "hangup",
            "hungup",
            "disconnected",
        }

        if session_id is not None and state in active_states:
            self._active_session_id = session_id
        elif state in terminal_states and (
            session_id is None or self._active_session_id == session_id
        ):
            self._active_session_id = None

        ring_allowed = (
            self._ring_filter_peer is None
            or self._last_called_peer == self._ring_filter_peer
        )
        is_ringing = state in self._RING_STATES and direction != "outgoing"
        if is_ringing and ring_allowed:
            self._ring_detected = True
            self._last_ring_time = datetime.now()
            self._ring_pulse_until = (
                self._last_ring_time
                + timedelta(seconds=DOORBELL_PULSE_DURATION)
            )
        elif state not in self._RING_STATES:
            self._ring_detected = False
            self._ring_pulse_until = None

        self._last_call_state_value = state

        if self.data is not None:
            self.data = TwoNIntercomData(
                call_status=self.data.call_status,
                last_ring_time=self._last_ring_time,
                caller_info=self.data.caller_info,
                active_session_id=self._active_session_id,
                available=self.data.available,
            )

        return True

    async def _async_log_listener_loop(self) -> None:
        """Subscribe to call-related log events and apply them as they arrive."""
        subscription_id = await self.api.async_subscribe_log(
            ["CallStateChanged", "CallSessionStateChanged"]
        )
        if subscription_id is None:
            return

        self._log_subscription_id = subscription_id

        while True:
            events = await self.api.async_pull_log(subscription_id, timeout=1)
            updated = False
            for event in events:
                updated = self._process_log_event(event) or updated

            if updated and hasattr(self, "async_update_listeners"):
                self.async_update_listeners()

            await asyncio.sleep(0)

    async def async_start_log_listener(self) -> None:
        """Start the background log-listener task if it is not already running."""
        if self._log_listener_task is not None and not self._log_listener_task.done():
            return

        create_task = getattr(self.hass, "async_create_task", asyncio.create_task)
        self._log_listener_task = create_task(self._async_log_listener_loop())

    async def async_stop_log_listener(self) -> None:
        """Stop the background log-listener task and unsubscribe active channel."""
        subscription_id = self._log_subscription_id
        task = self._log_listener_task
        self._log_listener_task = None

        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if subscription_id is not None:
            try:
                await self.api.async_unsubscribe_log(subscription_id)
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.debug("Failed to unsubscribe log listener %s: %s", subscription_id, err)
            finally:
                if self._log_subscription_id == subscription_id:
                    self._log_subscription_id = None

    async def _async_update_data(self) -> TwoNIntercomData:
        """Fetch data from API."""
        try:
            if self._system_info is None:
                try:
                    self._system_info = await self.api.async_get_system_info()
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.debug("Failed to fetch system info: %s", err)
                    self._system_info = {}

            # Get current call status
            call_status = await self.api.async_get_call_status()
            
            # Reset retry count on successful update
            self._retry_count = 0
            
            # Detect ring events
            current_state = self._extract_call_state(call_status) or "idle"
            previous_state = self._last_call_state_value or "idle"
            called_peer_raw = self._extract_called_peer(call_status)
            active_session_id = self._extract_active_session_id(call_status)
            self._active_session_id = active_session_id
            self._last_called_peer = self._normalize_peer(called_peer_raw)
            ring_allowed = (
                self._ring_filter_peer is None
                or self._last_called_peer == self._ring_filter_peer
            )
            current_state_norm = str(current_state).lower()
            previous_state_norm = str(previous_state).lower()
            is_ringing = current_state_norm in self._RING_STATES
            was_ringing = previous_state_norm in self._RING_STATES
            
            # Ring detection: state changes to a ringing state
            if is_ringing and ring_allowed:
                if not was_ringing or not self._ring_detected:
                    self._ring_detected = True
                    self._last_ring_time = datetime.now()
                    self._ring_pulse_until = (
                        self._last_ring_time
                        + timedelta(seconds=DOORBELL_PULSE_DURATION)
                    )
                    _LOGGER.info("Doorbell ring detected")
            elif is_ringing and not ring_allowed:
                self._ring_detected = False
            elif not is_ringing:
                # Reset ring detection when call ends
                if self._ring_detected:
                    self._ring_detected = False
                    self._ring_pulse_until = None
            
            self._last_call_state = call_status
            self._last_call_state_value = current_state
            
            # Extract caller info
            caller_info = call_status.get("caller", {})
            
            return TwoNIntercomData(
                call_status=call_status,
                last_ring_time=self._last_ring_time,
                caller_info=caller_info if caller_info else None,
                active_session_id=active_session_id,
                available=True,
            )
            
        except TwoNAuthenticationError as err:
            # Authentication errors require user intervention
            _LOGGER.error("Authentication failed: %s", err)
            # Create persistent notification for user
            self.hass.components.persistent_notification.async_create(
                f"Authentication failed for 2N Intercom: {err}. "
                "Please check your credentials and reconfigure the integration.",
                title="2N Intercom Authentication Error",
                notification_id=f"{DOMAIN}_auth_error",
            )
            raise ConfigEntryNotReady(f"Authentication failed: {err}") from err
            
        except (TwoNConnectionError, ConnectionError, TimeoutError) as err:
            # Connection errors - use retry counter for tracking
            # Note: Home Assistant's DataUpdateCoordinator handles the actual retry timing
            # via the update_interval. We track retries here for logging and decision making.
            if self._retry_count < MAX_RETRIES:
                self._retry_count += 1
                # Calculate expected delay for informational logging
                expected_delay = min(2 ** self._retry_count, MAX_BACKOFF_DELAY)
                _LOGGER.warning(
                    "Connection error (retry %s/%s, coordinator will retry per update_interval ~%ss): %s",
                    self._retry_count,
                    MAX_RETRIES,
                    expected_delay,
                    err,
                )
                raise UpdateFailed(f"Connection error: {err}") from err
            else:
                # Max retries exceeded - require integration reload
                _LOGGER.error(
                    "Max retries (%s) exceeded for connection to device",
                    MAX_RETRIES,
                )
                raise ConfigEntryNotReady(
                    f"Failed to connect after {MAX_RETRIES} retries"
                ) from err
                
        except Exception as err:
            # Generic API errors - mark entities unavailable but keep trying
            _LOGGER.warning("API error: %s", err)
            raise UpdateFailed(f"Error communicating with device: {err}") from err

    @property
    def ring_active(self) -> bool:
        """Return if doorbell is currently ringing."""
        if self.data:
            if not self._ring_detected:
                return False
            if self._ring_pulse_until is None:
                return False
            return datetime.now() <= self._ring_pulse_until
        return False

    @property
    def last_ring_time(self) -> datetime | None:
        """Return last ring timestamp."""
        return self._last_ring_time

    @property
    def caller_info(self) -> dict[str, Any]:
        """Return caller information."""
        if self.data and self.data.caller_info:
            return self.data.caller_info
        return {}

    @property
    def called_peer(self) -> str | None:
        """Return the last called peer from call status."""
        return self._last_called_peer

    @property
    def call_state(self) -> str | None:
        """Return the last detected call state."""
        return self._last_call_state_value or None

    @property
    def active_session_id(self) -> str | None:
        """Return the last detected active call session id."""
        return self._active_session_id

    @property
    def system_info(self) -> dict[str, Any]:
        """Return cached system info."""
        return self._system_info or {}

    def get_device_info(self, entry_id: str, name: str) -> dict[str, Any]:
        """Build device info for entities."""
        system_info = self.system_info
        model = system_info.get("variant") or system_info.get("deviceName") or "IP Intercom"
        sw_version = system_info.get("swVersion") or "1.0.0"
        return {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "2N",
            "model": model,
            "sw_version": sw_version,
        }

    async def async_trigger_relay(
        self, relay: int, duration: int = 2000
    ) -> bool:
        """
        Trigger a relay.
        
        Args:
            relay: Relay number (1-4)
            duration: Pulse duration in milliseconds
            
        Returns:
            True if successful
        """
        try:
            success = await self.api.async_switch_control(
                relay=relay,
                action="trigger",
                duration=duration,
            )
            
            if success:
                _LOGGER.info("Relay %s triggered successfully", relay)
            else:
                _LOGGER.warning("Failed to trigger relay %s", relay)
                
            return success
            
        except Exception as err:
            _LOGGER.error("Error triggering relay %s: %s", relay, err)
            return False

    async def async_get_snapshot(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Get camera snapshot with caching to reduce API load."""
        try:
            # Check cache to avoid excessive API calls
            current_time = datetime.now()
            requested_size = (width, height)
            
            if (
                self._snapshot_cache is not None
                and self._snapshot_cache_time is not None
                and self._snapshot_cache_size == requested_size
                and (current_time - self._snapshot_cache_time).total_seconds() < SNAPSHOT_CACHE_DURATION
            ):
                _LOGGER.debug("Returning cached snapshot")
                return self._snapshot_cache
            
            # Fetch new snapshot
            snapshot = await self.api.async_get_snapshot(width=width, height=height)
            
            if snapshot:
                self._snapshot_cache = snapshot
                self._snapshot_cache_time = current_time
                self._snapshot_cache_size = requested_size
                _LOGGER.debug("Fetched new snapshot from API")
            
            return snapshot
            
        except Exception as err:
            _LOGGER.error("Error getting snapshot: %s", err)
            return None
