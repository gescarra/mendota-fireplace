"""Flame height, 0 (off) to 6."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry, ProflameEntity
from .protocol import MAX_LEVEL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ProflameFlame(entry.runtime_data, "flame")])


class ProflameFlame(ProflameEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_LEVEL
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> int:
        return self.controller.state.flame

    async def async_set_native_value(self, value: float) -> None:
        self.controller.async_set(flame=int(value))
