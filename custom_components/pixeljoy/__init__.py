"""PixelJoy LED panel integration (BLE).

Protocol, firmware notes and the CLI live in the parent repo; the `pixeljoy` library is installed
from PyPI (`pixeljoy-ble`, pinned in manifest.json).
"""

from __future__ import annotations

from homeassistant.components.bluetooth import async_rediscover_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_MODEL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .panel import PixelJoyPanel
from pixeljoy import protocol as P

PLATFORMS = [Platform.BUTTON, Platform.EVENT, Platform.LIGHT]

PixelJoyConfigEntry = ConfigEntry[PixelJoyPanel]


async def async_setup_entry(hass: HomeAssistant, entry: PixelJoyConfigEntry) -> bool:
    models = await hass.async_add_executor_job(P.load_models)
    model = next((m for m in models if m.name == entry.data[CONF_MODEL]), None)
    if model is None:
        raise ConfigEntryError(f"unknown PixelJoy model {entry.data[CONF_MODEL]}")
    entry.runtime_data = PixelJoyPanel(hass, entry.data[CONF_ADDRESS], model, entry.title)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PixelJoyConfigEntry) -> bool:
    if ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.disconnect()
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: PixelJoyConfigEntry) -> None:
    """Let the panel show up under Discovered again without restarting HA."""
    async_rediscover_address(hass, entry.data[CONF_ADDRESS])
