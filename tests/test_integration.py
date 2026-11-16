"""End to end: config flow, entities, and the code handed to the Broadlink."""

from datetime import timedelta
import json
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.proflame2_broadlink import protocol as p
from custom_components.proflame2_broadlink.const import DOMAIN

REAL_CAPTURE = json.loads(
    (Path(__file__).parent / "sample_capture.json").read_text()
)["b64"]
REMOTE = p.decode_any(REAL_CAPTURE).remote


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    return


async def _setup(hass: HomeAssistant) -> list:
    calls = async_mock_service(hass, "remote", "send_command")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Fireplace",
            "remote_entity": "remote.rm4_pro",
            "capture": "b64:" + REAL_CAPTURE,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["remote"]["serial"] == "123456"
    await hass.async_block_till_done()
    return calls


async def _sent(hass: HomeAssistant, calls: list) -> p.Decoded:
    before = len(calls)
    await hass.async_block_till_done()  # let state listeners schedule the send
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert len(calls) == before + 1  # one transmission for the whole burst
    call = calls[-1]
    assert call.data["entity_id"] == "remote.rm4_pro"
    return p.decode_any(call.data["command"][0])


async def test_entities_send_full_state(hass: HomeAssistant):
    calls = await _setup(hass)
    assert hass.states.get("switch.fireplace_power").state == "off"

    async def call(domain, service, entity, **data):
        await hass.services.async_call(
            domain, service, {"entity_id": entity, **data}, blocking=True
        )

    await call("switch", "turn_on", "switch.fireplace_power")
    await call("number", "set_value", "number.fireplace_flame", value=4)
    await call("fan", "set_percentage", "fan.fireplace_fan", percentage=50)
    await call("light", "turn_on", "light.fireplace_accent_light", brightness=255)
    await call("switch", "turn_on", "switch.fireplace_secondary_burner")
    await call("switch", "turn_on", "switch.fireplace_continuous_pilot")

    got = await _sent(hass, calls)
    assert got.remote == REMOTE
    assert got.state == p.State(
        power=True, flame=4, fan=3, light=6, front=True, pilot=True
    )
    assert hass.states.get("fan.fireplace_fan").attributes["percentage"] == 50
    assert hass.states.get("light.fireplace_accent_light").attributes[
        "brightness"
    ] == 255

    await call("switch", "turn_off", "switch.fireplace_power")
    got = await _sent(hass, calls)
    assert got.state.power is False and got.state.flame == 4


async def test_flow_rejects_bad_input(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Fireplace",
            "remote_entity": "remote.rm4_pro",
            "capture": "JgAEAAECAwQFBgcI",
        },
    )
    assert result["errors"] == {"capture": "invalid_capture"}


async def test_thermostat(hass: HomeAssistant):
    hass.config.units = US_CUSTOMARY_SYSTEM
    calls = await _setup(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    state = hass.states.get("climate.fireplace")
    assert state.state == "off"
    assert "preset_modes" not in state.attributes  # no sensor yet
    assert state.attributes.get("temperature") is None

    hass.states.async_set(
        "sensor.room", "22.0", {"unit_of_measurement": "°C", "device_class": "temperature"}
    )  # 71.6 °F
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "resend_minutes": 5,
            "temperature_sensor": "sensor.room",
            "smart_span": 6,
            "smart_min_flame": 1,
            "hysteresis": 0.5,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    climate = "climate.fireplace"
    assert hass.states.get(climate).state == "off"

    async def call(service, **data):
        await hass.services.async_call(
            "climate", service, {"entity_id": climate, **data}, blocking=True
        )

    def set_room(celsius):
        hass.states.async_set("sensor.room", str(celsius), {"unit_of_measurement": "°C"})

    # Heat: target above room -> thermostat bit, power bit clear, full flame.
    await call("set_temperature", temperature=75)
    await call("set_hvac_mode", hvac_mode="heat")
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.power, got.flame) == (True, False, 6)
    assert (got.set_point, got.room_temp) == (75, 720)
    assert hass.states.get(climate).attributes["hvac_action"] == "heating"

    # Room reaches target plus hysteresis -> flame off, still in thermostat mode.
    set_room(24.2)  # 75.56 °F
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.flame, got.room_temp) == (True, 0, 760)
    assert hass.states.get(climate).attributes["hvac_action"] == "idle"

    # Smart preset: both bits, flame follows the shortfall (75 - 71.6 -> level 4).
    set_room(22.0)
    await call("set_preset_mode", preset_mode="smart")
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.power, got.flame) == (True, True, 4)

    # Sensor lost -> stop heating.
    hass.states.async_set("sensor.room", "unavailable")
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.flame) == (True, 0)

    # The manual power switch takes over and is not confused by the smart bits.
    assert hass.states.get("switch.fireplace_power").state == "off"
    await call("set_hvac_mode", hvac_mode="off")
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.power) == (False, False)


async def test_sensor_chosen_at_setup(hass: HomeAssistant):
    async_mock_service(hass, "remote", "send_command")
    hass.states.async_set("sensor.room", "70", {"unit_of_measurement": "°F"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Fireplace",
            "remote_entity": "remote.rm4_pro",
            "capture": REAL_CAPTURE,
            "temperature_sensor": "sensor.room",
        },
    )
    assert result["options"] == {"temperature_sensor": "sensor.room"}
    await hass.async_block_till_done()
    state = hass.states.get("climate.fireplace")
    assert state.state == "off"
    assert state.attributes["preset_modes"] == ["manual", "thermostat", "smart"]


async def _climate(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "climate", service, {"entity_id": "climate.fireplace", **data}, blocking=True
    )


async def test_fireplace_entity_without_sensor(hass: HomeAssistant):
    """The controls HomeKit's Heater Cooler shows, all on one entity."""
    calls = await _setup(hass)
    state = hass.states.get("climate.fireplace")
    assert state.attributes["fan_modes"] == ["low", "middle", "medium", "high"]
    assert state.attributes["swing_modes"] == ["off", "on"]

    # On with no flame chosen yet: manual mode at a middle flame.
    await _climate(hass, "set_hvac_mode", hvac_mode="heat")
    got = (await _sent(hass, calls)).state
    assert (got.power, got.thermostat, got.flame) == (True, False, 3)
    state = hass.states.get("climate.fireplace")
    assert (state.state, state.attributes["fan_mode"]) == ("heat", "middle")
    assert hass.states.get("switch.fireplace_power").state == "on"

    await _climate(hass, "set_fan_mode", fan_mode="high")
    await _climate(hass, "set_swing_mode", swing_mode="on")
    got = (await _sent(hass, calls)).state
    assert (got.flame, got.front) == (6, True)
    assert hass.states.get("switch.fireplace_secondary_burner").state == "on"
    assert hass.states.get("climate.fireplace").attributes["swing_mode"] == "on"

    # A flame between two modes shows as the nearer, ties going up.
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.fireplace_flame", "value": 2},
        blocking=True,
    )
    await _sent(hass, calls)
    assert hass.states.get("climate.fireplace").attributes["fan_mode"] == "middle"

    await _climate(hass, "turn_off")
    got = (await _sent(hass, calls)).state
    assert (got.power, got.flame) == (False, 2)
    assert hass.states.get("climate.fireplace").state == "off"

    await _climate(hass, "turn_on")
    got = (await _sent(hass, calls)).state
    assert (got.power, got.flame) == (True, 2)  # resumes, no default


async def test_flame_and_temperature_pick_the_mode(hass: HomeAssistant):
    """HomeKit cannot choose presets, so flame and temperature do."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    hass.states.async_set("sensor.room", "70", {"unit_of_measurement": "°F"})
    calls = async_mock_service(hass, "remote", "send_command")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Fireplace",
            "remote_entity": "remote.rm4_pro",
            "capture": REAL_CAPTURE,
            "temperature_sensor": "sensor.room",
        },
    )
    await hass.async_block_till_done()

    def preset():
        return hass.states.get("climate.fireplace").attributes["preset_mode"]

    await _climate(hass, "set_preset_mode", preset_mode="smart")
    assert preset() == "smart"  # remembered while off
    await _climate(hass, "set_hvac_mode", hvac_mode="heat")
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.power) == (True, True)

    # Choosing a flame takes manual control.
    await _climate(hass, "set_fan_mode", fan_mode="low")
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.power, got.flame) == (False, True, 1)
    assert preset() == "manual"

    # Setting a temperature goes back to the last thermostat preset.
    await _climate(hass, "set_temperature", temperature=76)
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.power, got.set_point) == (True, True, 76)
    assert preset() == "smart"

    # Back to manual by preset: the manual flame comes back.
    await _climate(hass, "set_preset_mode", preset_mode="manual")
    got = (await _sent(hass, calls)).state
    assert (got.thermostat, got.power, got.flame) == (False, True, 1)
