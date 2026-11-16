"""The fireplace as one climate entity, shaped for HomeKit's Heater Cooler.

HomeKit Bridge makes one accessory per entity, so the controls that should
share one tile all live here: on/off, flame level (as fan modes), secondary
burner (as swing) and, once a room sensor is chosen, the thermostat.

HomeKit only understands four fan mode names and a swing toggle, which is why
the flame is offered in four steps here; the flame number entity still has
all six. With a sensor, the three ways of running are presets:

- manual: burn the chosen flame level;
- thermostat: full flame until the room reaches the target, then off;
- smart: the flame follows how far the room is below target.

HomeKit cannot pick a preset, so two actions switch between them: choosing a
flame level takes manual control, and setting a temperature hands control
back to the last thermostat preset used.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_MIDDLE,
    SWING_OFF,
    SWING_ON,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry, ProflameController, ProflameEntity

PRESET_MANUAL = "manual"
PRESET_THERMOSTAT = "thermostat"
PRESET_SMART = "smart"

# HomeKit maps these four names, in this order, onto its speed slider.
FLAME_BY_FAN_MODE = {FAN_LOW: 1, FAN_MIDDLE: 3, FAN_MEDIUM: 4, FAN_HIGH: 6}
# Turning on in manual mode with the flame at 0 would light nothing.
DEFAULT_FLAME = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    # The "thermostat" key keeps the unique ID this entity had before it
    # became the whole fireplace.
    async_add_entities([ProflameClimate(entry.runtime_data, "thermostat")])


def fan_mode_for_flame(flame: int) -> str | None:
    """The fan mode nearest a flame level; ties go to the higher one."""
    if not flame:
        return None
    return min(
        FLAME_BY_FAN_MODE,
        key=lambda mode: (abs(FLAME_BY_FAN_MODE[mode] - flame), -FLAME_BY_FAN_MODE[mode]),
    )


class ProflameClimate(ProflameEntity, ClimateEntity):
    _attr_name = None  # the device's main entity, named after the fireplace
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_fan_modes = list(FLAME_BY_FAN_MODE)
    _attr_swing_modes = [SWING_OFF, SWING_ON]
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = 1
    _attr_min_temp = 50
    _attr_max_temp = 90

    def __init__(self, controller: ProflameController, key: str) -> None:
        super().__init__(controller, key)
        self._attr_supported_features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if controller.sensor:
            self._attr_supported_features |= (
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.PRESET_MODE
            )
            self._attr_preset_modes = [PRESET_MANUAL, PRESET_THERMOSTAT, PRESET_SMART]
        # The mode to run in; kept while off so turning on resumes it.
        self._mode = self._running_mode() or PRESET_MANUAL
        self._last_thermostat = (
            self._mode if self._mode != PRESET_MANUAL else PRESET_THERMOSTAT
        )

    def _running_mode(self) -> str | None:
        state = self.controller.state
        if state.thermostat:
            # Thermostat bit alone is plain on/off; with the power bit it is smart.
            return PRESET_SMART if state.power else PRESET_THERMOSTAT
        return PRESET_MANUAL if state.power else None

    def _remember(self, mode: str) -> None:
        self._mode = mode
        if mode != PRESET_MANUAL:
            self._last_thermostat = mode

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.async_add_listener(self._async_update))

    @callback
    def _async_update(self) -> None:
        if (mode := self._running_mode()) is not None:
            self._remember(mode)
        self.async_write_ha_state()

    def _start(self, mode: str, **changes: Any) -> None:
        """Run the fireplace in a mode, with any other changes in one send."""
        if not self.controller.sensor:
            mode = PRESET_MANUAL  # no thermostat without a room reading
        self._remember(mode)
        if mode == PRESET_MANUAL:
            if "flame" not in changes and not self.controller.manual_flame:
                changes["flame"] = DEFAULT_FLAME
            self.controller.async_set(power=True, thermostat=False, **changes)
        else:
            self.controller.async_set(
                thermostat=True, power=mode == PRESET_SMART, **changes
            )

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._running_mode() else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if not self._running_mode():
            return HVACAction.OFF
        return HVACAction.HEATING if self.controller.state.flame else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        return self._mode if self.controller.sensor else None

    @property
    def fan_mode(self) -> str | None:
        return fan_mode_for_flame(self.controller.state.flame)

    @property
    def swing_mode(self) -> str:
        return SWING_ON if self.controller.state.front else SWING_OFF

    @property
    def current_temperature(self) -> float | None:
        return self.controller.room_f

    @property
    def target_temperature(self) -> float | None:
        return self.controller.state.set_point if self.controller.sensor else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.HEAT:
            if not self._running_mode():
                self._start(self._mode)
        else:
            self.controller.async_set(thermostat=False, power=False)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if self._running_mode():
            self._start(preset_mode)
        else:
            self._remember(preset_mode)
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        flame = FLAME_BY_FAN_MODE[fan_mode]
        if self._running_mode():
            self._start(PRESET_MANUAL, flame=flame)  # picking a flame takes over
        else:
            self._remember(PRESET_MANUAL)
            self.controller.async_set(flame=flame)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        self.controller.async_set(front=swing_mode == SWING_ON)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        set_point = round(temperature)
        if self._running_mode() == PRESET_MANUAL:
            # Setting a temperature hands control back to the thermostat.
            self._start(self._last_thermostat, set_point=set_point)
            return
        if self._mode == PRESET_MANUAL:
            self._remember(self._last_thermostat)
        self.controller.async_set(set_point=set_point)
