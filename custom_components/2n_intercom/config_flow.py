"""Config flow for 2N Intercom integration."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import logging
import json
from pathlib import Path

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .api import TwoNIntercomAPI
from .const import (
    CALLED_ID_ALL,
    CAMERA_MJPEG_FPS_MAX,
    CAMERA_MJPEG_FPS_MIN,
    CONF_CALLED_ID,
    CONF_DOOR_TYPE,
    CONF_ENABLE_CAMERA,
    CONF_ENABLE_DOORBELL,
    CONF_LIVE_VIEW_MODE,
    CONF_MJPEG_FPS,
    CONF_MJPEG_HEIGHT,
    CONF_MJPEG_WIDTH,
    CONF_PROTOCOL,
    CONF_RELAY_COUNT,
    CONF_RELAY_DEVICE_TYPE,
    CONF_RELAY_NAME,
    CONF_RELAY_NUMBER,
    CONF_RELAY_PULSE_DURATION,
    CONF_RELAYS,
    CONF_VERIFY_SSL,
    DEFAULT_CAMERA_MJPEG_FPS,
    DEFAULT_CAMERA_MJPEG_HEIGHT,
    DEFAULT_CAMERA_MJPEG_WIDTH,
    DEFAULT_ENABLE_CAMERA,
    DEFAULT_ENABLE_DOORBELL,
    DEFAULT_LIVE_VIEW_MODE,
    DEFAULT_PORT_HTTP,
    DEFAULT_PORT_HTTPS,
    DEFAULT_PROTOCOL,
    DEFAULT_PULSE_DURATION,
    DEFAULT_RELAY_COUNT,
    DEFAULT_VERIFY_SSL,
    DEVICE_TYPE_DOOR,
    DEVICE_TYPE_GATE,
    DOMAIN,
    DOOR_TYPE_DOOR,
    DOOR_TYPE_GATE,
    DOOR_TYPES,
    LIVE_VIEW_MODES,
    PROTOCOL_HTTP,
    PROTOCOL_HTTPS,
    PROTOCOLS,
)

_LOGGER = logging.getLogger(__name__)


def _all_calls_label(language: str) -> str:
    if language.startswith("cs"):
        return "Vsechny hovory"
    return "All calls"


def _read_integration_info(manifest_path: Path) -> tuple[str, str]:
    """Read integration name and version from the manifest."""
    with open(manifest_path, encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    return manifest.get("name", "2N Intercom"), manifest.get("version", "")


async def _async_get_called_peers(data: dict[str, Any]) -> list[str]:
    """Return list of called peers from directory."""
    api: TwoNIntercomAPI | None = None
    try:
        api = TwoNIntercomAPI(
            host=data[CONF_HOST],
            port=data.get(CONF_PORT, DEFAULT_PORT_HTTPS),
            username=data.get(CONF_USERNAME, ""),
            password=data.get(CONF_PASSWORD, ""),
            protocol=data.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
            verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
        directory = await api.async_get_directory()

        users: list[dict[str, Any]] = []
        if isinstance(directory, list):
            for entry in directory:
                if isinstance(entry, dict) and "users" in entry:
                    users.extend(entry.get("users") or [])
                elif isinstance(entry, dict):
                    users.append(entry)
        elif isinstance(directory, dict):
            if "users" in directory:
                users = directory.get("users", [])
            elif "result" in directory:
                result = directory.get("result")
                if isinstance(result, dict):
                    users = result.get("users", [])
                elif isinstance(result, list):
                    users = result

        peers: list[str] = []
        for user in users:
            for call_pos in user.get("callPos", []) or []:
                peer = call_pos.get("peer")
                if peer and peer not in peers:
                    peers.append(peer)

        return peers
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception(
            "Failed to load called peers from dir/query host=%s",
            data.get(CONF_HOST),
        )
        return []
    finally:
        if api is not None:
            await api.async_close()


class TwoNIntercomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for 2N Intercom."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._relays: list[dict[str, Any]] = []
        self._integration_name: str | None = None
        self._integration_version: str | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._reconfigure_entry: config_entries.ConfigEntry | None = None

    async def _ensure_integration_info(self) -> None:
        """Load and cache integration name/version."""
        if self._integration_name is not None and self._integration_version is not None:
            return

        manifest_path = Path(__file__).resolve().parent / "manifest.json"
        try:
            (
                self._integration_name,
                self._integration_version,
            ) = await self.hass.async_add_executor_job(
                _read_integration_info, manifest_path
            )
        except (OSError, json.JSONDecodeError):
            self._integration_name = "2N Intercom"
            self._integration_version = ""

    def _name_with_version(self, name: str) -> str:
        """Return name unchanged (version is not appended)."""
        return name

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - connection settings."""
        errors = {}
        api: TwoNIntercomAPI | None = None

        if user_input is not None:
            # Validate connection
            try:
                # Determine port based on protocol if not specified
                if CONF_PORT not in user_input:
                    user_input[CONF_PORT] = (
                        DEFAULT_PORT_HTTPS
                        if user_input.get(CONF_PROTOCOL) == PROTOCOL_HTTPS
                        else DEFAULT_PORT_HTTP
                    )

                api = TwoNIntercomAPI(
                    host=user_input[CONF_HOST],
                    port=user_input[CONF_PORT],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    protocol=user_input.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                    verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )

                # Test connection
                if not await api.async_test_connection():
                    _LOGGER.warning(
                        "Connection test failed host=%s port=%s protocol=%s verify_ssl=%s",
                        user_input.get(CONF_HOST),
                        user_input.get(CONF_PORT),
                        user_input.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                        user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    )
                    errors["base"] = "cannot_connect"
                else:
                    # Store data and move to device configuration
                    self._data = user_input

            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Connection test exception host=%s port=%s protocol=%s verify_ssl=%s",
                    user_input.get(CONF_HOST),
                    user_input.get(CONF_PORT),
                    user_input.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                    user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )
                errors["base"] = "cannot_connect"
            finally:
                if api is not None:
                    await api.async_close()

            if not errors:
                return await self.async_step_device()

        # Default port based on protocol
        default_protocol = (
            user_input.get(CONF_PROTOCOL)
            if user_input is not None
            else DEFAULT_PROTOCOL
        )
        default_port = (
            user_input.get(CONF_PORT)
            if user_input is not None and CONF_PORT in user_input
            else (
                DEFAULT_PORT_HTTPS
                if default_protocol == PROTOCOL_HTTPS
                else DEFAULT_PORT_HTTP
            )
        )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=user_input.get(CONF_HOST, "") if user_input else ""
                ): cv.string,
                vol.Required(CONF_PORT, default=default_port): cv.port,
                vol.Required(CONF_PROTOCOL, default=default_protocol): vol.In(
                    PROTOCOLS
                ),
                vol.Required(
                    CONF_USERNAME,
                    default=user_input.get(CONF_USERNAME, "") if user_input else "",
                ): cv.string,
                vol.Required(
                    CONF_PASSWORD,
                    default=user_input.get(CONF_PASSWORD, "") if user_input else "",
                ): cv.string,
                vol.Required(
                    CONF_VERIFY_SSL,
                    default=(
                        user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                        if user_input
                        else DEFAULT_VERIFY_SSL
                    ),
                ): cv.boolean,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle device configuration step."""
        errors = {}

        if user_input is not None:
            self._data.update(user_input)
            
            # If relays are configured, move to relay configuration
            relay_count = user_input.get(CONF_RELAY_COUNT, DEFAULT_RELAY_COUNT)
            if relay_count > 0:
                self._relays = []
                return await self.async_step_relay(relay_index=0)
            else:
                # No relays, create entry
                return await self._async_create_entry()

        await self._ensure_integration_info()
        default_name = self._integration_name or "2N Intercom"
        peers = await _async_get_called_peers(self._data)
        called_options = [
            {
                "label": _all_calls_label(self.hass.config.language),
                "value": CALLED_ID_ALL,
            }
        ] + [{"label": peer, "value": peer} for peer in peers]
        default_called = self._data.get(CONF_CALLED_ID) or CALLED_ID_ALL

        called_field = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=called_options,
                mode=selector.SelectSelectorMode.DROPDOWN,
                custom_value=True,
            )
        )

        data_schema = vol.Schema(
            {
                vol.Required("name", default=default_name): cv.string,
                vol.Required(
                    CONF_ENABLE_CAMERA, default=DEFAULT_ENABLE_CAMERA
                ): cv.boolean,
                vol.Required(
                    CONF_ENABLE_DOORBELL, default=DEFAULT_ENABLE_DOORBELL
                ): cv.boolean,
                vol.Required(
                    CONF_RELAY_COUNT, default=DEFAULT_RELAY_COUNT
                ): vol.In([0, 1, 2, 3, 4]),
                vol.Optional(
                    CONF_CALLED_ID, default=default_called
                ): called_field,
            }
        )

        return self.async_show_form(
            step_id="device",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_relay(
        self, user_input: dict[str, Any] | None = None, relay_index: int = 0
    ) -> FlowResult:
        """Handle relay configuration step."""
        errors = {}
        relay_count = self._data.get(CONF_RELAY_COUNT, DEFAULT_RELAY_COUNT)

        if user_input is not None:
            self._relays.append(user_input)
            
            # Check if we need to configure more relays
            if len(self._relays) < relay_count:
                return await self.async_step_relay(relay_index=len(self._relays))
            else:
                # All relays configured, create entry
                self._data[CONF_RELAYS] = self._relays
                return await self._async_create_entry()

        # relay_index is 0-based, but we show 1-based numbers to users
        relay_display_number = relay_index + 1
        
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_RELAY_NAME, default=f"Relay {relay_display_number}"
                ): cv.string,
                vol.Required(
                    CONF_RELAY_NUMBER, default=relay_display_number
                ): vol.In([1, 2, 3, 4]),
                vol.Required(
                    CONF_RELAY_DEVICE_TYPE, default=DEVICE_TYPE_DOOR
                ): vol.In([DEVICE_TYPE_DOOR, DEVICE_TYPE_GATE]),
                vol.Required(
                    CONF_RELAY_PULSE_DURATION, default=DEFAULT_PULSE_DURATION
                ): cv.positive_int,
            }
        )

        return self.async_show_form(
            step_id="relay",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"relay_number": str(relay_display_number)},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """Handle reauthentication when stored credentials stop working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is not None:
            self._data = {**self._reauth_entry.data, **self._reauth_entry.options}
        else:
            self._data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prompt the user to confirm or update credentials."""
        errors: dict[str, str] = {}
        existing = self._data
        api: TwoNIntercomAPI | None = None

        if user_input is not None:
            merged = {
                **existing,
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                api = TwoNIntercomAPI(
                    host=merged[CONF_HOST],
                    port=merged.get(CONF_PORT, DEFAULT_PORT_HTTPS),
                    username=merged[CONF_USERNAME],
                    password=merged[CONF_PASSWORD],
                    protocol=merged.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                    verify_ssl=merged.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )
                if not await api.async_test_connection():
                    errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Reauth connection test failed host=%s",
                    merged.get(CONF_HOST),
                )
                errors["base"] = "invalid_auth"
            finally:
                if api is not None:
                    await api.async_close()

            if not errors and self._reauth_entry is not None:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry, data=merged
                )
                await self.hass.config_entries.async_reload(
                    self._reauth_entry.entry_id
                )
                return self.async_abort(reason="reauth_successful")

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=existing.get(CONF_USERNAME, ""),
                ): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "host": str(existing.get(CONF_HOST, "")),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow the user to change host/port/credentials without dropping the entry."""
        self._reconfigure_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reconfigure_entry is not None:
            self._data = {
                **self._reconfigure_entry.data,
                **self._reconfigure_entry.options,
            }
        return await self._async_reconfigure_user_step(user_input)

    async def _async_reconfigure_user_step(
        self, user_input: dict[str, Any] | None
    ) -> FlowResult:
        """Reuse the user step shape for reconfigure with current values prefilled."""
        errors: dict[str, str] = {}
        api: TwoNIntercomAPI | None = None

        if user_input is not None:
            try:
                if CONF_PORT not in user_input:
                    user_input[CONF_PORT] = (
                        DEFAULT_PORT_HTTPS
                        if user_input.get(CONF_PROTOCOL) == PROTOCOL_HTTPS
                        else DEFAULT_PORT_HTTP
                    )
                api = TwoNIntercomAPI(
                    host=user_input[CONF_HOST],
                    port=user_input[CONF_PORT],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    protocol=user_input.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                    verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )
                if not await api.async_test_connection():
                    errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Reconfigure connection test failed host=%s",
                    user_input.get(CONF_HOST),
                )
                errors["base"] = "cannot_connect"
            finally:
                if api is not None:
                    await api.async_close()

            if not errors and self._reconfigure_entry is not None:
                merged = {**self._data, **user_input}
                self.hass.config_entries.async_update_entry(
                    self._reconfigure_entry, data=merged
                )
                await self.hass.config_entries.async_reload(
                    self._reconfigure_entry.entry_id
                )
                return self.async_abort(reason="reconfigure_successful")

        current = self._data
        default_protocol = current.get(CONF_PROTOCOL, DEFAULT_PROTOCOL)
        default_port = current.get(CONF_PORT) or (
            DEFAULT_PORT_HTTPS
            if default_protocol == PROTOCOL_HTTPS
            else DEFAULT_PORT_HTTP
        )
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=current.get(CONF_HOST, "")
                ): cv.string,
                vol.Required(CONF_PORT, default=default_port): cv.port,
                vol.Required(
                    CONF_PROTOCOL, default=default_protocol
                ): vol.In(PROTOCOLS),
                vol.Required(
                    CONF_USERNAME, default=current.get(CONF_USERNAME, "")
                ): cv.string,
                vol.Required(
                    CONF_PASSWORD, default=current.get(CONF_PASSWORD, "")
                ): cv.string,
                vol.Required(
                    CONF_VERIFY_SSL,
                    default=current.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): cv.boolean,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors,
        )

    async def _async_create_entry(self) -> FlowResult:
        """Create the config entry."""
        await self._ensure_integration_info()
        entry_name = self._data.get("name", self._integration_name or "2N Intercom")
        title = self._name_with_version(entry_name)

        await self.async_set_unique_id(
            f"{self._data[CONF_HOST]}_{entry_name}"
        )
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=title,
            data=self._data,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TwoNIntercomOptionsFlow:
        """Get the options flow for this handler."""
        return TwoNIntercomOptionsFlow(config_entry)


class TwoNIntercomOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for 2N Intercom."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow.

        HA 2024.12+ provides ``self.config_entry`` automatically; we accept
        the argument for backward compatibility with the existing
        ``async_get_options_flow`` callable but do not store it.
        """
        del config_entry  # provided by the framework as self.config_entry
        self._data: dict[str, Any] = {}
        self._relays: list[dict[str, Any]] = []

    def _merged_data(self) -> dict[str, Any]:
        """Return merged config data with options overriding defaults."""
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Start full options flow at connection settings."""
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle connection settings in options."""
        errors = {}
        current_data = self._merged_data()

        if user_input is not None:
            try:
                if not user_input.get(CONF_USERNAME):
                    user_input[CONF_USERNAME] = current_data.get(CONF_USERNAME, "")
                if not user_input.get(CONF_PASSWORD):
                    user_input[CONF_PASSWORD] = current_data.get(CONF_PASSWORD, "")

                if CONF_PORT not in user_input:
                    user_input[CONF_PORT] = (
                        DEFAULT_PORT_HTTPS
                        if user_input.get(CONF_PROTOCOL) == PROTOCOL_HTTPS
                        else DEFAULT_PORT_HTTP
                    )

                api = TwoNIntercomAPI(
                    host=user_input[CONF_HOST],
                    port=user_input[CONF_PORT],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    protocol=user_input.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                    verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )

                if not await api.async_test_connection():
                    _LOGGER.warning(
                        "Options connection test failed host=%s port=%s protocol=%s verify_ssl=%s",
                        user_input.get(CONF_HOST),
                        user_input.get(CONF_PORT),
                        user_input.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                        user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    )
                    errors["base"] = "cannot_connect"
                else:
                    await api.async_close()
                    self._data = user_input
                    return await self.async_step_device()

            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Options connection test exception host=%s port=%s protocol=%s verify_ssl=%s",
                    user_input.get(CONF_HOST),
                    user_input.get(CONF_PORT),
                    user_input.get(CONF_PROTOCOL, DEFAULT_PROTOCOL),
                    user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )
                errors["base"] = "cannot_connect"

        default_protocol = (
            user_input.get(CONF_PROTOCOL)
            if user_input is not None
            else current_data.get(CONF_PROTOCOL, DEFAULT_PROTOCOL)
        )
        default_port = current_data.get(CONF_PORT)
        if default_port is None:
            default_port = (
                DEFAULT_PORT_HTTPS
                if default_protocol == PROTOCOL_HTTPS
                else DEFAULT_PORT_HTTP
            )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=current_data.get(CONF_HOST, "")
                ): cv.string,
                vol.Required(CONF_PORT, default=default_port): cv.port,
                vol.Required(
                    CONF_PROTOCOL, default=default_protocol
                ): vol.In(PROTOCOLS),
                vol.Required(
                    CONF_USERNAME, default=current_data.get(CONF_USERNAME, "")
                ): cv.string,
                vol.Required(
                    CONF_PASSWORD, default=current_data.get(CONF_PASSWORD, "")
                ): cv.string,
                vol.Required(
                    CONF_VERIFY_SSL,
                    default=current_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): cv.boolean,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle device configuration step in options."""
        errors = {}
        current_data = self._merged_data()

        peers = await _async_get_called_peers(current_data)
        called_options = [
            {
                "label": _all_calls_label(self.hass.config.language),
                "value": CALLED_ID_ALL,
            }
        ] + [{"label": peer, "value": peer} for peer in peers]
        default_called = current_data.get(CONF_CALLED_ID) or CALLED_ID_ALL

        called_field = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=called_options,
                mode=selector.SelectSelectorMode.DROPDOWN,
                custom_value=True,
            )
        )

        relays = current_data.get(CONF_RELAYS, [])
        derived_door_type = DOOR_TYPE_GATE if any(
            relay.get(CONF_RELAY_DEVICE_TYPE) == DEVICE_TYPE_GATE
            for relay in relays
        ) else DOOR_TYPE_DOOR

        if user_input is not None:
            self._data.update(user_input)

            # If the camera is enabled, surface the camera transport options
            # step so the user can tune live-view mode and MJPEG resolution.
            # Disabling the camera skips it entirely — the defaults stay as a
            # no-op until the user re-enables.
            if user_input.get(CONF_ENABLE_CAMERA, DEFAULT_ENABLE_CAMERA):
                return await self.async_step_camera()

            return await self._async_after_camera_step()

        data_schema = vol.Schema(
            {
                vol.Required(
                    "name",
                    default=current_data.get("name", "2N Intercom"),
                ): cv.string,
                vol.Required(
                    CONF_ENABLE_CAMERA,
                    default=current_data.get(CONF_ENABLE_CAMERA, DEFAULT_ENABLE_CAMERA),
                ): cv.boolean,
                vol.Required(
                    CONF_ENABLE_DOORBELL,
                    default=current_data.get(
                        CONF_ENABLE_DOORBELL, DEFAULT_ENABLE_DOORBELL
                    ),
                ): cv.boolean,
                vol.Required(
                    CONF_RELAY_COUNT,
                    default=current_data.get(CONF_RELAY_COUNT, DEFAULT_RELAY_COUNT),
                ): vol.In([0, 1, 2, 3, 4]),
                vol.Required(
                    CONF_DOOR_TYPE,
                    default=current_data.get(CONF_DOOR_TYPE, derived_door_type),
                ): vol.In(DOOR_TYPES),
                vol.Optional(
                    CONF_CALLED_ID,
                    default=default_called,
                ): called_field,
            }
        )

        return self.async_show_form(
            step_id="device",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle camera transport options.

        Lets the user override how the integration talks to the 2N camera —
        live-view mode (auto/rtsp/mjpeg/jpeg-only) and MJPEG stream
        resolution/fps. The defaults match the device's high-res preset, so
        leaving every field untouched preserves the previous behavior. The
        coordinator picks the new values up via ``async_update_options`` →
        ``async_reload``, no manual restart required.
        """
        errors: dict[str, str] = {}
        current_data = self._merged_data()

        if user_input is not None:
            # Coerce NumberSelector outputs (which arrive as float) to ints
            # so the coordinator's ``isinstance(..., int)`` checks pass and
            # the values round-trip cleanly through entry.options storage.
            for int_key in (CONF_MJPEG_WIDTH, CONF_MJPEG_HEIGHT, CONF_MJPEG_FPS):
                if int_key in user_input and user_input[int_key] is not None:
                    user_input[int_key] = int(user_input[int_key])
            self._data.update(user_input)
            return await self._async_after_camera_step()

        live_view_field = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {"label": mode, "value": mode} for mode in LIVE_VIEW_MODES
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key=CONF_LIVE_VIEW_MODE,
            )
        )

        # NumberSelector with a sensible upper bound — 2N caps MJPEG output
        # at the device's max sensor resolution; we don't enforce that here
        # because capabilities are device-specific. The API still validates
        # against ``CameraCapabilities`` and falls back to the closest match.
        resolution_field = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=160,
                max=2592,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        )

        fps_field = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=CAMERA_MJPEG_FPS_MIN,
                max=CAMERA_MJPEG_FPS_MAX,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_LIVE_VIEW_MODE,
                    default=current_data.get(
                        CONF_LIVE_VIEW_MODE, DEFAULT_LIVE_VIEW_MODE
                    ),
                ): live_view_field,
                vol.Required(
                    CONF_MJPEG_WIDTH,
                    default=current_data.get(
                        CONF_MJPEG_WIDTH, DEFAULT_CAMERA_MJPEG_WIDTH
                    ),
                ): resolution_field,
                vol.Required(
                    CONF_MJPEG_HEIGHT,
                    default=current_data.get(
                        CONF_MJPEG_HEIGHT, DEFAULT_CAMERA_MJPEG_HEIGHT
                    ),
                ): resolution_field,
                vol.Required(
                    CONF_MJPEG_FPS,
                    default=current_data.get(
                        CONF_MJPEG_FPS, DEFAULT_CAMERA_MJPEG_FPS
                    ),
                ): fps_field,
            }
        )

        return self.async_show_form(
            step_id="camera",
            data_schema=data_schema,
            errors=errors,
        )

    async def _async_after_camera_step(self) -> FlowResult:
        """Continue the options flow after the (optional) camera step."""
        relay_count = self._data.get(CONF_RELAY_COUNT, DEFAULT_RELAY_COUNT)
        if relay_count > 0:
            self._relays = []
            return await self.async_step_relay(relay_index=0)

        self._data[CONF_RELAYS] = []
        return await self._async_create_entry()

    async def async_step_relay(
        self, user_input: dict[str, Any] | None = None, relay_index: int = 0
    ) -> FlowResult:
        """Handle relay configuration step in options."""
        errors = {}
        current_data = self._merged_data()
        relay_count = self._data.get(CONF_RELAY_COUNT, DEFAULT_RELAY_COUNT)
        existing_relays = current_data.get(CONF_RELAYS, [])

        if user_input is not None:
            self._relays.append(user_input)

            if len(self._relays) < relay_count:
                return await self.async_step_relay(relay_index=len(self._relays))

            self._data[CONF_RELAYS] = self._relays
            return await self._async_create_entry()

        relay_display_number = relay_index + 1
        default_relay = (
            existing_relays[relay_index]
            if relay_index < len(existing_relays)
            else {}
        )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_RELAY_NAME,
                    default=default_relay.get(
                        CONF_RELAY_NAME, f"Relay {relay_display_number}"
                    ),
                ): cv.string,
                vol.Required(
                    CONF_RELAY_NUMBER,
                    default=default_relay.get(
                        CONF_RELAY_NUMBER, relay_display_number
                    ),
                ): vol.In([1, 2, 3, 4]),
                vol.Required(
                    CONF_RELAY_DEVICE_TYPE,
                    default=default_relay.get(
                        CONF_RELAY_DEVICE_TYPE, DEVICE_TYPE_DOOR
                    ),
                ): vol.In([DEVICE_TYPE_DOOR, DEVICE_TYPE_GATE]),
                vol.Required(
                    CONF_RELAY_PULSE_DURATION,
                    default=default_relay.get(
                        CONF_RELAY_PULSE_DURATION, DEFAULT_PULSE_DURATION
                    ),
                ): cv.positive_int,
            }
        )

        return self.async_show_form(
            step_id="relay",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"relay_number": str(relay_display_number)},
        )

    async def _async_create_entry(self) -> FlowResult:
        """Create the options entry."""
        return self.async_create_entry(title="", data=self._data)
