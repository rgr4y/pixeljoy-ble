"""Config flow for PixelJoy panels (Bluetooth discovery or pick from the panels in range)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_MODEL

from .const import DOMAIN
from .pixeljoy import protocol as P


def _model(info: BluetoothServiceInfoBleak, models: list[P.Model]) -> P.Model | None:
    return P.match_model(info.name, info.service_uuids, models)


class PixelJoyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PixelJoy."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._found: dict[str, BluetoothServiceInfoBleak] = {}
        self._models: list[P.Model] = []

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """A panel was seen by an adapter or proxy."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._models = await self.hass.async_add_executor_job(P.load_models)
        if _model(discovery_info, self._models) is None:  # manifest matches on scan UUID only; confirm name too
            return self.async_abort(reason="not_supported")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._discovery is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery.name,
                data={CONF_ADDRESS: self._discovery.address, CONF_MODEL: _model(self._discovery, self._models).name},
            )
        self._set_confirm_only()
        return self.async_show_form(step_id="bluetooth_confirm", description_placeholders={"name": self._discovery.name})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick one of the panels currently in range."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            info = self._found[address]
            return self.async_create_entry(title=info.name, data={CONF_ADDRESS: address, CONF_MODEL: _model(info, self._models).name})

        self._models = await self.hass.async_add_executor_job(P.load_models)
        configured = self._async_current_ids(include_ignore=False)
        connectable = {i.address for i in async_discovered_service_info(self.hass, connectable=True)}
        seen = [i for i in async_discovered_service_info(self.hass, connectable=False) if _model(i, self._models)]
        for info in seen:
            if info.address in connectable and info.address not in configured:
                self._found[info.address] = info
        if not self._found:
            if seen and all(i.address in configured for i in seen):
                return self.async_abort(reason="all_configured")
            if seen:
                return self.async_abort(reason="no_connectable")
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In({a: f"{i.name} ({a})" for a, i in self._found.items()})}
            ),
        )
