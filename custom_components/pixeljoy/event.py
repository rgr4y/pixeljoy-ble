"""The panel's "LZ" 2.4 GHz remote as an HA event entity.

The remote only broadcasts BLE adverts (no connection), so presses arrive through HA's passive
scanning even when the panel is off. Every LZ remote in range fires here; ``remote_id`` tells them apart.
"""

from __future__ import annotations

from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_register_callback,
)
from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PixelJoyConfigEntry
from .entity import PixelJoyEntity
from pixeljoy import protocol as P


async def async_setup_entry(hass: HomeAssistant, entry: PixelJoyConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([PixelJoyRemote(entry.runtime_data)])


class PixelJoyRemote(PixelJoyEntity, EventEntity):
    _attr_translation_key = "remote"
    _attr_event_types = sorted(set(P.REMOTE_KEYS.values()) | {"unknown"})

    def __init__(self, panel) -> None:
        super().__init__(panel, "remote")
        self._last_seq: dict[str, int] = {}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_register_callback(
                self.hass,
                self._on_advert,
                BluetoothCallbackMatcher(
                    local_name=P.REMOTE_LOCAL_NAME, manufacturer_id=P.REMOTE_COMPANY_ID, connectable=False
                ),
                BluetoothScanningMode.PASSIVE,
            )
        )

    @callback
    def _on_advert(self, info: BluetoothServiceInfoBleak, _change: BluetoothChange) -> None:
        press = P.parse_remote_advert(info.name, info.manufacturer_data, info.address)
        if press is None or self._last_seq.get(press.remote_id) == press.seq:
            return  # not a key packet, or a repeat of the same transmission
        self._last_seq[press.remote_id] = press.seq
        action = press.action
        if action not in self._attr_event_types:  # unknown key code: still report it
            action = "unknown"
        self._trigger_event(action, {"key": press.key, "remote_id": press.remote_id, "seq": press.seq})
        self.async_write_ha_state()
