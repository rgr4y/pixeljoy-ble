"""One PixelJoy panel: a lazily opened BLE session shared by its entities."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import IDLE_DISCONNECT_S
from .pixeljoy import protocol as P
from .pixeljoy.client import PixelJoy

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


class _HAPixelJoy(PixelJoy):
    """PixelJoy client whose link comes from HA's Bluetooth stack (adapters, proxies, retries)."""

    def __init__(self, ble_device, model: P.Model, tz_name: str, on_disconnect: Callable[[], None]) -> None:
        super().__init__(ble_device, model, auto_time_sync=True, tz_name=tz_name)
        self._ble_device = ble_device
        self._on_disconnect = on_disconnect

    async def _open(self) -> None:
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self._ble_device.name or self._ble_device.address,
            disconnected_callback=lambda _c: self._on_disconnect(),
            max_attempts=3,
        )


class PixelJoyPanel:
    """Serialises commands, connects on demand and disconnects when idle."""

    def __init__(self, hass: HomeAssistant, address: str, model: P.Model, name: str) -> None:
        self.hass = hass
        self.address = address
        self.model = model
        self.name = name
        self.properties: P.Properties | None = None
        self.firmware: str | None = None
        self._client: _HAPixelJoy | None = None
        self._lock = asyncio.Lock()
        self._idle: asyncio.TimerHandle | None = None
        self._listeners: list[Callable[[], None]] = []

    @callback
    def add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``cb`` whenever properties change; returns the remover."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    async def run(self, fn: Callable[[PixelJoy], Awaitable[T]]) -> T:
        """Run ``fn(client)`` with a connected, handshaken client."""
        async with self._lock:
            if self._idle:
                self._idle.cancel()
            try:
                client = await self._ensure_connected()
                return await fn(client)
            except Exception as err:
                await self._drop()
                raise HomeAssistantError(f"{self.name}: {err}") from err
            finally:
                self._idle = self.hass.loop.call_later(
                    IDLE_DISCONNECT_S, lambda: self.hass.async_create_task(self.disconnect())
                )

    async def _ensure_connected(self) -> PixelJoy:
        if self._client and self._client._client.is_connected:
            return self._client
        ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device is None:
            raise HomeAssistantError(f"{self.name} ({self.address}) is not in range of any Bluetooth adapter")
        client = _HAPixelJoy(ble_device, self.model, str(self.hass.config.time_zone), self._on_disconnect)
        await client.connect()  # GATT, notify, handshake, READ_PROPERTY, time sync
        self._client = client
        self.properties = client.properties
        if self.firmware is None:
            self.firmware = await client.firmware_version()
        self._notify()
        return client

    @callback
    def _on_disconnect(self) -> None:
        _LOGGER.debug("%s disconnected", self.name)
        self._client = None

    async def _drop(self) -> None:
        client, self._client = self._client, None
        if client:
            try:
                await asyncio.wait_for(client._client.disconnect(), 5)
            except Exception:  # noqa: BLE001 - already failing; just make sure the link is gone
                pass

    async def disconnect(self) -> None:
        async with self._lock:
            await self._drop()

    async def refresh(self) -> None:
        """Re-read properties (power, brightness, mode)."""
        async def read(c: PixelJoy) -> None:
            self.properties = await c.read_properties()
        await self.run(read)
        self._notify()
