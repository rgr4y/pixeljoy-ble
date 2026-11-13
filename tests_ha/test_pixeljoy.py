"""Config flow, light and remote event entity against a real HA core (BLE mocked)."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_get_advertisement_callback
from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.const import CONF_ADDRESS, CONF_MODEL
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "pixeljoy"
PANEL = "AA:BB:CC:DD:EE:01"


def _info(name, address, mfr=None, uuids=(), connectable=True):
    return BluetoothServiceInfoBleak(
        name=name, address=address, rssi=-60, manufacturer_data=mfr or {}, service_data={},
        service_uuids=list(uuids), source="local", device=BLEDevice(address, name, {}),
        advertisement=None, connectable=connectable, time=time.monotonic(), tx_power=None,
    )


PANEL_INFO = _info("P0AF_A1B2", PANEL, uuids=["0000c0af-0000-1000-8000-00805f9b34fb"])


async def test_bluetooth_discovery_creates_entry(hass, mock_bluetooth):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=PANEL_INFO)
    assert result["type"] == "form" and result["step_id"] == "bluetooth_confirm"
    with patch("custom_components.pixeljoy.panel.PixelJoyPanel.refresh", AsyncMock(side_effect=HomeAssistantError("off"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_ADDRESS: PANEL, CONF_MODEL: "P0AF_XXX"}


async def test_discovery_rejects_wrong_name(hass, mock_bluetooth):
    info = _info("SomethingElse", PANEL, uuids=["0000c0af-0000-1000-8000-00805f9b34fb"])
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info)
    assert result["type"] == "abort" and result["reason"] == "not_supported"


@pytest.fixture
async def panel_entry(hass, mock_bluetooth):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PANEL, title="P0AF_A1B2",
                            data={CONF_ADDRESS: PANEL, CONF_MODEL: "P0AF_XXX"})
    entry.add_to_hass(hass)
    client = MagicMock()
    for m in ("power", "brightness", "show_clock", "show_text", "sync_time"):
        setattr(client, m, AsyncMock())

    async def run(self, fn):
        return await fn(client)

    with (
        patch("custom_components.pixeljoy.panel.PixelJoyPanel.refresh", AsyncMock(side_effect=HomeAssistantError("off"))),
        patch("custom_components.pixeljoy.panel.PixelJoyPanel.run", run),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield client


async def test_light_brightness_maps_to_percent(hass, panel_entry):
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.p0af_a1b2", "brightness": 128}, blocking=True)
    panel_entry.power.assert_awaited_with(True)
    panel_entry.brightness.assert_awaited_with(50)
    assert hass.states.get("light.p0af_a1b2").state == "on"
    await hass.services.async_call("light", "turn_off", {"entity_id": "light.p0af_a1b2"}, blocking=True)
    panel_entry.power.assert_awaited_with(False)


async def test_show_clock_service_is_1_based(hass, panel_entry):
    await hass.services.async_call(DOMAIN, "show_clock", {"entity_id": "light.p0af_a1b2", "style": 10}, blocking=True)
    panel_entry.show_clock.assert_awaited_with(9, True, False, (255, 255, 255))
    with pytest.raises(Exception):  # style 11 would be firmware style 10 (RAM corruption): rejected by schema
        await hass.services.async_call(DOMAIN, "show_clock", {"entity_id": "light.p0af_a1b2", "style": 11}, blocking=True)


async def test_remote_press_fires_event_once_per_seq(hass, panel_entry):
    inject = async_get_advertisement_callback(hass)
    remote = "11:22:33:44:55:66"

    def press(seq, key):
        inject(_info("LZ", remote, mfr={0x02CC: bytes([seq, 0x66, 0x55, key, 0, 0, 0xE8, 0x68])}, connectable=False))

    press(0x2E, 0x05)
    await hass.async_block_till_done()
    state = hass.states.get("event.p0af_a1b2_remote")
    assert state.attributes["event_type"] == "flip"
    assert state.attributes["remote_id"] == "66554433" and state.attributes["key"] == 5
    first = state.state

    press(0x2E, 0x05)  # same transmission again
    await hass.async_block_till_done()
    assert hass.states.get("event.p0af_a1b2_remote").state == first

    press(0x2F, 0x04)
    await hass.async_block_till_done()
    assert hass.states.get("event.p0af_a1b2_remote").attributes["event_type"] == "brightness_up"
