"""Unit tests for the 2N Intercom config flow.

Covers the user, reauth, and reconfigure entry points. The point is to lock
in the contracts that broke (or got added) during the HA 2026.4+ remediation:

* the **user** step still validates connection and moves to the device step
* a failed connection on the user step shows ``cannot_connect`` and stays on
  the form
* **reauth** pulls the existing entry data, rejects bad creds, accepts good
  ones, updates the entry, and aborts with ``reauth_successful``
* **reconfigure** uses the existing entry, validates the new credentials,
  updates the entry, and aborts with ``reconfigure_successful``
"""

from __future__ import annotations

import sys
import types
import unittest

from _stubs import (
    API_PATH,
    CONFIG_FLOW_PATH,
    CONST_PATH,
    ensure_package,
    install_api_stubs,
    load_module,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FormResult(dict):
    """Marker dict so tests can assert on the kind of flow result returned."""


def _install_voluptuous_stub() -> None:
    voluptuous = types.ModuleType("voluptuous")

    class Schema:
        def __init__(self, schema):
            self.schema = schema

    class Required:
        def __init__(self, key, default=None):
            self.key = key
            self.default = default

        def __hash__(self) -> int:
            return hash(("required", self.key))

        def __eq__(self, other) -> bool:
            return isinstance(other, Required) and other.key == self.key

    class Optional(Required):
        def __hash__(self) -> int:
            return hash(("optional", self.key))

        def __eq__(self, other) -> bool:
            return isinstance(other, Optional) and other.key == self.key

    class In:
        def __init__(self, options):
            self.options = options

    voluptuous.Schema = Schema
    voluptuous.Required = Required
    voluptuous.Optional = Optional
    voluptuous.In = In
    sys.modules["voluptuous"] = voluptuous


def _install_homeassistant_stubs() -> None:
    ensure_package("homeassistant")

    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PASSWORD = "password"
    const.CONF_PORT = "port"
    const.CONF_USERNAME = "username"
    sys.modules["homeassistant.const"] = const

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object

    def callback(func):
        return func

    core.callback = callback
    sys.modules["homeassistant.core"] = core

    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    sys.modules["homeassistant.data_entry_flow"] = data_entry_flow

    config_entries = types.ModuleType("homeassistant.config_entries")

    class _BaseFlow:
        def __init__(self) -> None:
            self.context: dict[str, object] = {}
            self.hass: object | None = None
            self._unique_id: str | None = None

        async def async_set_unique_id(self, unique_id):
            self._unique_id = unique_id
            return None

        def _abort_if_unique_id_configured(self):
            return None

        def async_show_form(
            self,
            *,
            step_id,
            data_schema=None,
            errors=None,
            description_placeholders=None,
        ):
            return _FormResult(
                type="form",
                step_id=step_id,
                data_schema=data_schema,
                errors=errors or {},
                description_placeholders=description_placeholders or {},
            )

        def async_abort(self, *, reason):
            return _FormResult(type="abort", reason=reason)

        def async_create_entry(self, *, title, data):
            return _FormResult(type="create_entry", title=title, data=data)

    class ConfigFlow(_BaseFlow):
        def __init_subclass__(cls, **kwargs):
            kwargs.pop("domain", None)
            super().__init_subclass__(**kwargs)

    class OptionsFlow(_BaseFlow):
        pass

    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow
    config_entries.ConfigEntry = object
    sys.modules["homeassistant.config_entries"] = config_entries

    helpers = ensure_package("homeassistant.helpers")
    selector_module = types.ModuleType("homeassistant.helpers.selector")

    class SelectSelector:
        def __init__(self, config):
            self.config = config

    class SelectSelectorConfig:
        def __init__(self, *, options, mode, custom_value=False):
            self.options = options
            self.mode = mode
            self.custom_value = custom_value

    class SelectSelectorMode:
        DROPDOWN = "dropdown"

    selector_module.SelectSelector = SelectSelector
    selector_module.SelectSelectorConfig = SelectSelectorConfig
    selector_module.SelectSelectorMode = SelectSelectorMode
    sys.modules["homeassistant.helpers.selector"] = selector_module

    cv_module = types.ModuleType("homeassistant.helpers.config_validation")
    cv_module.string = str
    cv_module.boolean = bool
    cv_module.port = int
    cv_module.positive_int = int
    sys.modules["homeassistant.helpers.config_validation"] = cv_module
    helpers.selector = selector_module
    helpers.config_validation = cv_module


def load_config_flow_module():
    install_api_stubs()
    _install_voluptuous_stub()
    _install_homeassistant_stubs()
    ensure_package("custom_components")
    ensure_package("custom_components.2n_intercom")
    load_module("custom_components.2n_intercom.const", CONST_PATH)
    load_module("custom_components.2n_intercom.api", API_PATH)
    return load_module("custom_components.2n_intercom.config_flow", CONFIG_FLOW_PATH)


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------


class FakeAPI:
    """In-memory replacement for ``TwoNIntercomAPI``.

    The flow only calls ``async_test_connection``, ``async_get_directory``,
    and ``async_close`` during these tests, so we keep the surface tiny.
    """

    instances: list["FakeAPI"] = []

    def __init__(
        self,
        *,
        host,
        port,
        username,
        password,
        protocol,
        verify_ssl,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.protocol = protocol
        self.verify_ssl = verify_ssl
        self.closed = False
        FakeAPI.instances.append(self)

    async def async_test_connection(self) -> bool:
        return FakeAPI.connection_result

    async def async_get_directory(self):
        return []

    async def async_close(self) -> None:
        self.closed = True


FakeAPI.connection_result = True


class FakeConfigEntry:
    def __init__(self, entry_id, data, options=None) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = options or {}


class FakeConfigEntries:
    def __init__(self, entries) -> None:
        self._entries = {entry.entry_id: entry for entry in entries}
        self.update_calls: list[tuple[str, dict]] = []
        self.reload_calls: list[str] = []

    def async_get_entry(self, entry_id):
        return self._entries.get(entry_id)

    def async_update_entry(self, entry, *, data=None, **_kwargs):
        if data is not None:
            entry.data = data
        self.update_calls.append((entry.entry_id, dict(entry.data)))

    async def async_reload(self, entry_id):
        self.reload_calls.append(entry_id)


class FakeHass:
    def __init__(self, entries) -> None:
        self.config_entries = FakeConfigEntries(entries)
        self.config = types.SimpleNamespace(language="en")

    async def async_add_executor_job(self, func, *args):
        return func(*args)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    """Lock in user / reauth / reconfigure flow contracts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config_flow_module = load_config_flow_module()
        # Patch the loaded module's TwoNIntercomAPI symbol so the flow uses
        # our fake instead of the real (network-touching) implementation.
        cls.config_flow_module.TwoNIntercomAPI = FakeAPI

    def setUp(self) -> None:
        FakeAPI.instances = []
        FakeAPI.connection_result = True

    def _make_flow(self, hass: FakeHass | None = None):
        flow = self.config_flow_module.TwoNIntercomConfigFlow()
        flow.hass = hass or FakeHass([])
        return flow

    async def test_user_step_happy_path_advances_to_device(self) -> None:
        flow = self._make_flow()
        result = await flow.async_step_user(
            {
                "host": "192.168.2.20",
                "port": 443,
                "protocol": "https",
                "username": "homeassistant",
                "password": "secret",
                "verify_ssl": False,
            }
        )
        # Successful auth advances to the device step (form, not error).
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "device")
        self.assertEqual(result["errors"], {})
        # User step creates one API for the connection test; the device
        # step then creates another to fetch the directory peers. The
        # invariant we care about is that *every* API the flow opens is
        # also closed — no resource leaks regardless of step count.
        self.assertGreaterEqual(len(FakeAPI.instances), 1)
        self.assertTrue(all(instance.closed for instance in FakeAPI.instances))

    async def test_user_step_failed_connection_keeps_form_with_error(self) -> None:
        FakeAPI.connection_result = False
        flow = self._make_flow()
        result = await flow.async_step_user(
            {
                "host": "192.168.2.20",
                "port": 443,
                "protocol": "https",
                "username": "homeassistant",
                "password": "wrong",
                "verify_ssl": False,
            }
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")
        self.assertEqual(result["errors"], {"base": "cannot_connect"})

    async def test_reauth_confirm_with_valid_credentials_updates_and_aborts(
        self,
    ) -> None:
        entry = FakeConfigEntry(
            "entry-1",
            {
                "host": "192.168.2.20",
                "port": 443,
                "protocol": "https",
                "username": "homeassistant",
                "password": "old-secret",
                "verify_ssl": False,
            },
        )
        hass = FakeHass([entry])
        flow = self._make_flow(hass)
        flow.context = {"entry_id": entry.entry_id}

        # async_step_reauth pulls the existing entry data, then forwards
        # to the confirmation step which renders the form.
        first = await flow.async_step_reauth(entry.data)
        self.assertEqual(first["type"], "form")
        self.assertEqual(first["step_id"], "reauth_confirm")
        # The flow exposes the host as a description placeholder so the user
        # knows which device they're updating credentials for.
        self.assertEqual(
            first["description_placeholders"].get("host"), "192.168.2.20"
        )

        # Submit valid creds → entry is updated, reload kicked off, aborted
        # with reauth_successful.
        result = await flow.async_step_reauth_confirm(
            {"username": "homeassistant", "password": "new-secret"}
        )
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "reauth_successful")
        self.assertEqual(
            hass.config_entries.update_calls,
            [
                (
                    "entry-1",
                    {
                        "host": "192.168.2.20",
                        "port": 443,
                        "protocol": "https",
                        "username": "homeassistant",
                        "password": "new-secret",
                        "verify_ssl": False,
                    },
                )
            ],
        )
        self.assertEqual(hass.config_entries.reload_calls, ["entry-1"])

    async def test_reauth_confirm_with_bad_credentials_shows_invalid_auth(
        self,
    ) -> None:
        FakeAPI.connection_result = False
        entry = FakeConfigEntry(
            "entry-1",
            {
                "host": "192.168.2.20",
                "port": 443,
                "protocol": "https",
                "username": "homeassistant",
                "password": "old",
                "verify_ssl": False,
            },
        )
        hass = FakeHass([entry])
        flow = self._make_flow(hass)
        flow.context = {"entry_id": entry.entry_id}

        await flow.async_step_reauth(entry.data)
        result = await flow.async_step_reauth_confirm(
            {"username": "homeassistant", "password": "still-wrong"}
        )
        # No update, no reload, form re-rendered with invalid_auth.
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertEqual(hass.config_entries.update_calls, [])
        self.assertEqual(hass.config_entries.reload_calls, [])

    async def test_reconfigure_with_valid_credentials_updates_and_aborts(
        self,
    ) -> None:
        entry = FakeConfigEntry(
            "entry-1",
            {
                "host": "192.168.2.20",
                "port": 443,
                "protocol": "https",
                "username": "homeassistant",
                "password": "secret",
                "verify_ssl": False,
            },
        )
        hass = FakeHass([entry])
        flow = self._make_flow(hass)
        flow.context = {"entry_id": entry.entry_id}

        # First call (no input) renders the form prefilled with entry data.
        first = await flow.async_step_reconfigure(None)
        self.assertEqual(first["type"], "form")
        self.assertEqual(first["step_id"], "reconfigure")

        # Submit a changed host; flow validates and updates the entry.
        result = await flow.async_step_reconfigure(
            {
                "host": "192.168.2.21",
                "port": 443,
                "protocol": "https",
                "username": "homeassistant",
                "password": "secret",
                "verify_ssl": False,
            }
        )
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(len(hass.config_entries.update_calls), 1)
        updated_entry_id, updated_data = hass.config_entries.update_calls[0]
        self.assertEqual(updated_entry_id, "entry-1")
        self.assertEqual(updated_data["host"], "192.168.2.21")
        self.assertEqual(hass.config_entries.reload_calls, ["entry-1"])

    async def test_reconfigure_with_failed_connection_does_not_update(
        self,
    ) -> None:
        FakeAPI.connection_result = False
        entry = FakeConfigEntry(
            "entry-1",
            {
                "host": "192.168.2.20",
                "port": 443,
                "protocol": "https",
                "username": "homeassistant",
                "password": "secret",
                "verify_ssl": False,
            },
        )
        hass = FakeHass([entry])
        flow = self._make_flow(hass)
        flow.context = {"entry_id": entry.entry_id}

        await flow.async_step_reconfigure(None)
        result = await flow.async_step_reconfigure(
            {
                "host": "192.168.2.99",
                "port": 443,
                "protocol": "https",
                "username": "homeassistant",
                "password": "secret",
                "verify_ssl": False,
            }
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        self.assertEqual(hass.config_entries.update_calls, [])
        self.assertEqual(hass.config_entries.reload_calls, [])


if __name__ == "__main__":
    unittest.main()
