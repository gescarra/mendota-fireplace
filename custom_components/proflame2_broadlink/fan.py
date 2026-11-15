"""Blower fan, 6 speeds."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from . import ProflameConfigEntry, ProflameEntity
from .protocol import MAX_LEVEL

RANGE = (1, MAX_LEVEL)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ProflameFan(entry.runtime_data, "fan")])


class ProflameFan(ProflameEntity, FanEntity):
    _attr_speed_count = MAX_LEVEL
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _last_level = MAX_LEVEL

    @property
    def percentage(self) -> int:
        level = self.controller.state.fan
        return ranged_value_to_percentage(RANGE, level) if level else 0

    async def async_set_percentage(self, percentage: int) -> None:
        level = math.ceil(percentage_to_ranged_value(RANGE, percentage))
        if level:
            self._last_level = level
        self.controller.async_set(fan=level)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if percentage is None:
            self.controller.async_set(fan=self._last_level)
        else:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.controller.async_set(fan=0)
