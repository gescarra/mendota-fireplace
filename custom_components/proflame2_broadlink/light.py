"""Accent light, 6 brightness levels."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry, ProflameEntity
from .protocol import MAX_LEVEL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ProflameLight(entry.runtime_data, "light")])


class ProflameLight(ProflameEntity, LightEntity):
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _last_level = MAX_LEVEL

    @property
    def is_on(self) -> bool:
        return self.controller.state.light > 0

    @property
    def brightness(self) -> int:
        return round(self.controller.state.light * 255 / MAX_LEVEL)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            self._last_level = max(1, math.ceil(brightness * MAX_LEVEL / 255))
        self.controller.async_set(light=self._last_level)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.controller.async_set(light=0)
