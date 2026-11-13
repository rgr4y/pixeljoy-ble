"""PixelJoy panel as a dimmable light, plus the content services (text, clock, image, GIF)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PixelJoyConfigEntry
from .const import (
    SERVICE_SHOW_CLOCK,
    SERVICE_SHOW_GIF,
    SERVICE_SHOW_IMAGE,
    SERVICE_SHOW_TEXT,
    SERVICE_SYNC_TIME,
)
from .entity import PixelJoyEntity
from .pixeljoy import protocol as P
from .pixeljoy.client import PixelJoy

_LOGGER = logging.getLogger(__name__)

RGB = vol.All(vol.Coerce(tuple), vol.ExactSequence((cv.byte, cv.byte, cv.byte)))


async def async_setup_entry(hass: HomeAssistant, entry: PixelJoyConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([PixelJoyLight(entry.runtime_data)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SHOW_TEXT,
        {
            vol.Required("text"): cv.string,
            vol.Optional("color", default=(255, 255, 255)): RGB,
            vol.Optional("speed", default=50): vol.All(vol.Coerce(int), vol.Range(0, 255)),
            vol.Optional("rainbow"): vol.All(vol.Coerce(int), vol.Range(0, 255)),
        },
        "async_show_text",
    )
    platform.async_register_entity_service(
        SERVICE_SHOW_CLOCK,
        {
            # 1-10 like the app; the firmware has no bounds check (style >= 10 corrupts RAM)
            vol.Optional("style", default=1): vol.All(vol.Coerce(int), vol.Range(1, len(P.CLOCK_STYLES))),
            vol.Optional("hour_24", default=True): cv.boolean,
            vol.Optional("show_date", default=False): cv.boolean,
            vol.Optional("color", default=(255, 255, 255)): RGB,
        },
        "async_show_clock",
    )
    platform.async_register_entity_service(
        SERVICE_SHOW_IMAGE,
        {
            vol.Required("path"): cv.string,
            vol.Optional("fit", default="contain"): vol.In(["stretch", "contain", "cover"]),
        },
        "async_show_image",
    )
    platform.async_register_entity_service(
        SERVICE_SHOW_GIF,
        {
            vol.Required("path"): cv.string,
            vol.Optional("speed", default=600): vol.All(vol.Coerce(int), vol.Range(0, 1050)),
        },
        "async_show_gif",
    )
    platform.async_register_entity_service(SERVICE_SYNC_TIME, {}, "async_sync_time")


class PixelJoyLight(PixelJoyEntity, LightEntity):
    """Power and brightness (the panel's 1-100 % maps onto HA's 1-255)."""

    _attr_name = None
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, panel) -> None:
        super().__init__(panel, "light")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.panel.add_listener(self._update))
        self._update()
        # Read the real state once; a panel that is off or out of range just stays "assumed".
        self.hass.async_create_background_task(self._initial_refresh(), f"pixeljoy refresh {self.panel.address}")

    async def _initial_refresh(self) -> None:
        try:
            await self.panel.refresh()
        except HomeAssistantError as err:
            _LOGGER.debug("initial refresh failed: %s", err)

    @callback
    def _update(self) -> None:
        props = self.panel.properties
        self._attr_assumed_state = props is None
        if props:
            self._attr_is_on = props.power
            self._attr_brightness = round(props.brightness * 255 / 100)
        if self.hass:
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        pct = max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)) if ATTR_BRIGHTNESS in kwargs else None

        async def go(c: PixelJoy) -> None:
            await c.power(True)
            if pct is not None:
                await c.brightness(pct)

        await self.panel.run(go)
        self._attr_is_on = True
        if pct is not None:
            self._attr_brightness = round(pct * 255 / 100)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.panel.run(lambda c: c.power(False))
        self._attr_is_on = False
        self.async_write_ha_state()

    # -- services ---------------------------------------------------------

    def _allowed_file(self, path: str) -> Path:
        if not self.hass.config.is_allowed_path(path):
            raise ServiceValidationError(f"{path} is not in allowlist_external_dirs")
        p = Path(path)
        if not p.is_file():
            raise ServiceValidationError(f"{path} does not exist")
        return p

    async def async_show_text(self, text: str, color, speed: int, rainbow: int | None = None) -> None:
        from .pixeljoy.render import render_glyphs

        props = self.panel.properties
        limit = 600 if props is None or props.support_600_txt else 100
        if len(text) > limit:
            raise ServiceValidationError(f"text is {len(text)} chars; this panel takes at most {limit}")
        glyphs = await self.hass.async_add_executor_job(render_glyphs, text, self.panel.model.height)
        style = P.TextStyle(color=tuple(color), rainbow=rainbow, speed=speed)
        await self.panel.run(lambda c: c.show_text(glyphs, style))

    async def async_show_clock(self, style: int, hour_24: bool, show_date: bool, color) -> None:
        await self.panel.run(lambda c: c.show_clock(style - 1, hour_24, show_date, tuple(color)))

    async def async_show_image(self, path: str, fit: str) -> None:
        from .pixeljoy.render import image_to_png

        p = await self.hass.async_add_executor_job(self._allowed_file, path)
        m = self.panel.model
        png = await self.hass.async_add_executor_job(image_to_png, str(p), m.width, m.height, fit)
        await self.panel.run(lambda c: c.show_png(png))

    async def async_show_gif(self, path: str, speed: int) -> None:
        p = await self.hass.async_add_executor_job(self._allowed_file, path)
        gif = await self.hass.async_add_executor_job(p.read_bytes)
        await self.panel.run(lambda c: c.show_gif(gif, speed))

    async def async_sync_time(self) -> None:
        await self.panel.run(lambda c: c.sync_time(str(self.hass.config.time_zone)))
