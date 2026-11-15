"""Fireplace on/off, pilot mode and secondary burner."""
# Power is the manual on/off; the climate entity owns the thermostat modes.

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry, ProflameController, ProflameEntity

# (state field, enabled by default)
SWITCHES = (("power", True), ("pilot", True), ("front", True))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        ProflameSwitch(entry.runtime_data, field, enabled)
        for field, enabled in SWITCHES
    )


class ProflameSwitch(ProflameEntity, SwitchEntity):
    def __init__(
        self, controller: ProflameController, field: str, enabled: bool
    ) -> None:
        super().__init__(controller, field)
        self._field = field
        self._attr_entity_registry_enabled_default = enabled

    @property
    def is_on(self) -> bool:
        state = self.controller.state
        if self._field == "power":
            # In the thermostat modes the power bit means something else.
            return state.power and not state.thermostat
        return getattr(state, self._field)

    def _set(self, on: bool) -> None:
        changes = {self._field: on}
        if self._field == "power":
            changes["thermostat"] = False  # manual control ends thermostat mode
        self.controller.async_set(**changes)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)
