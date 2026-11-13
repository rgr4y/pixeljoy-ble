"""Base entity: ties every PixelJoy entity to the panel's device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .panel import PixelJoyPanel


class PixelJoyEntity(Entity):
    _attr_has_entity_name = True

    def __init__(self, panel: PixelJoyPanel, key: str) -> None:
        self.panel = panel
        self._attr_unique_id = f"{panel.address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, panel.address)},
            connections={(CONNECTION_BLUETOOTH, panel.address)},
            name=panel.name,
            manufacturer="PixelJoy",
            model=f"{panel.model.pid} ({panel.model.width}x{panel.model.height})",
        )
