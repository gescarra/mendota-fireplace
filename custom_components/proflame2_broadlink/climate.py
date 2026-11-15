"""Thermostat, available once a room temperature sensor is chosen in the options.

The handheld remote runs its own thermostat and just tells the fireplace what
flame to burn, so this entity does the same from a Home Assistant sensor.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry, ProflameEntity

PRESET_SMART = "smart"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.sensor:
        async_add_entities([ProflameClimate(entry.runtime_data, "thermostat")])


class ProflameClimate(ProflameEntity, ClimateEntity):
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = [PRESET_NONE, PRESET_SMART]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = 1
    _attr_min_temp = 50
    _attr_max_temp = 90
    _smart = False

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.controller.state.thermostat else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        state = self.controller.state
        if not state.thermostat:
            return HVACAction.OFF
        return HVACAction.HEATING if state.flame else HVACAction.IDLE

    @property
    def preset_mode(self) -> str:
        state = self.controller.state
        if state.thermostat:
            self._smart = state.power
        return PRESET_SMART if self._smart else PRESET_NONE

    @property
    def current_temperature(self) -> float | None:
        return self.controller.room_f

    @property
    def target_temperature(self) -> float:
        return self.controller.state.set_point

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.HEAT:
            # Thermostat bit alone is plain on/off heating; with the power bit
            # as well it is the remote's "smart" mode.
            self.controller.async_set(thermostat=True, power=self._smart)
        else:
            self.controller.async_set(thermostat=False, power=False)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self._smart = preset_mode == PRESET_SMART
        if self.controller.state.thermostat:
            self.controller.async_set(power=self._smart)
        else:
            self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self.controller.async_set(set_point=round(temperature))
