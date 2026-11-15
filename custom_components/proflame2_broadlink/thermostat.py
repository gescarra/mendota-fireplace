"""Flame level decisions for the thermostat modes. Pure functions, no HA."""

from __future__ import annotations

from dataclasses import dataclass
import math

MAX_LEVEL = 6


@dataclass(frozen=True)
class Curve:
    """How the thermostat turns a temperature shortfall into a flame level.

    All temperatures are in degrees Fahrenheit.
    """

    span: float = 6.0  # smart: shortfall at which the flame reaches maximum
    min_flame: int = 1  # smart: lowest flame level used while heating
    hysteresis: float = 0.5  # margin either side of a threshold before changing

    def _level(self, shortfall: float, smart: bool) -> int:
        if shortfall <= 0:
            return 0
        if not smart:
            return MAX_LEVEL
        level = math.ceil(shortfall * MAX_LEVEL / self.span)
        return min(MAX_LEVEL, max(self.min_flame, level))

    def flame(self, shortfall: float, current: int, smart: bool) -> int:
        """Pick the flame level, given target minus room and the level now.

        The current level is kept while it is still right for some temperature
        within the hysteresis margin, so a reading that hovers on a threshold
        does not make the flame hunt up and down.
        """
        low = self._level(shortfall - self.hysteresis, smart)
        high = self._level(shortfall + self.hysteresis, smart)
        if low <= current <= high:
            return current
        return self._level(shortfall, smart)
