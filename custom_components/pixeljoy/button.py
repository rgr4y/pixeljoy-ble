"""One-press controls: clock, carousel and the remote's six car signs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PixelJoyConfigEntry
from .const import CLOCK_DEFAULTS, CONF_CLOCK_24H, CONF_CLOCK_COLOR, CONF_CLOCK_DATE, CONF_CLOCK_STYLE
from .entity import PixelJoyEntity
from pixeljoy.client import PixelJoy

# The remote's car signs are GIFs in the material pack (rem_0..rem_5), which the app cannot reach over BLE.
# Extract them once with tools/material_extract.py and copy rem_*.gif here; the buttons upload and play them.
SIGNS_DIR = ("pixeljoy", "signs")
SIGNS = ("sign_left", "sign_no_high_beam", "sign_right", "sign_warning", "sign_baby", "sign_no_horn")
SIGN_GIF_SPEED = 600  # interval 450 ms, what the firmware uses for the remote presets


@dataclass(frozen=True, kw_only=True)
class PixelJoyButtonDescription(ButtonEntityDescription):
    press: Callable[[PixelJoyConfigEntry, PixelJoy], Awaitable[object]] | None = None
    sign: int | None = None  # rem_<n>.gif


BUTTONS = (
    PixelJoyButtonDescription(key="clock", translation_key="clock", press=lambda entry, c: _show_clock(entry, c)),
    PixelJoyButtonDescription(key="carousel", translation_key="carousel", press=lambda entry, c: c.resume_carousel()),
    *(PixelJoyButtonDescription(key=name, translation_key=name, sign=n) for n, name in enumerate(SIGNS)),
)


def _show_clock(entry: PixelJoyConfigEntry, c: PixelJoy) -> Awaitable[None]:
    o = {**CLOCK_DEFAULTS, **entry.options}
    return c.show_clock(int(o[CONF_CLOCK_STYLE]) - 1, o[CONF_CLOCK_24H], o[CONF_CLOCK_DATE], tuple(o[CONF_CLOCK_COLOR]))


async def async_setup_entry(hass: HomeAssistant, entry: PixelJoyConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(PixelJoyButton(entry, d) for d in BUTTONS)


class PixelJoyButton(PixelJoyEntity, ButtonEntity):
    entity_description: PixelJoyButtonDescription

    def __init__(self, entry: PixelJoyConfigEntry, description: PixelJoyButtonDescription) -> None:
        super().__init__(entry.runtime_data, description.key)
        self.entity_description = description
        self._entry = entry

    async def async_press(self) -> None:
        d = self.entity_description
        if d.sign is None:
            await self.panel.run(lambda c: d.press(self._entry, c))
            return
        path = Path(self.hass.config.path(*SIGNS_DIR, f"rem_{d.sign}.gif"))
        gif = await self.hass.async_add_executor_job(lambda: path.read_bytes() if path.is_file() else None)
        if gif is None:
            raise ServiceValidationError(
                f"{path} is missing; extract it from the material pack with tools/material_extract.py"
            )
        await self.panel.run(lambda c: c.show_gif(gif, SIGN_GIF_SPEED))
