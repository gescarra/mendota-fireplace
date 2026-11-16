"""Proflame 2 fireplace control through a Broadlink RM Pro.

The fireplace never answers, so the state held here is what we last sent,
not what the fireplace is doing. Like the handheld remote, we send the whole
state on every change and again every few minutes.

The thermostat modes are also the remote's job: the fireplace only does what
the flame level in the last packet says. So when a thermostat mode is active
we watch a temperature sensor and pick the flame level ourselves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, Platform, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_REMOTE,
    CONF_REMOTE_ENTITY,
    CONF_HYSTERESIS,
    CONF_RESEND_MINUTES,
    CONF_SMART_MIN_FLAME,
    CONF_SMART_SPAN,
    CONF_TEMPERATURE_SENSOR,
    DEFAULT_RESEND_MINUTES,
    DOMAIN,
)
from .protocol import Remote, State, encode_broadlink_b64
from .thermostat import Curve

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CLIMATE,
    Platform.FAN,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SWITCH,
]

# Dragging a slider fires many changes; wait for it to settle before sending.
DEBOUNCE_SECONDS = 0.4

type ProflameConfigEntry = ConfigEntry[ProflameController]


class ProflameController:
    """Holds the assumed fireplace state and transmits it."""

    def __init__(self, hass: HomeAssistant, entry: ProflameConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.remote = Remote.from_dict(entry.data[CONF_REMOTE])
        self.state = State()
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._cancel_debounce: CALLBACK_TYPE | None = None
        self.sensor: str | None = entry.options.get(CONF_TEMPERATURE_SENSOR)
        self.room_f: float | None = None
        default = Curve()
        self.curve = Curve(
            span=entry.options.get(CONF_SMART_SPAN, default.span),
            min_flame=int(entry.options.get(CONF_SMART_MIN_FLAME, default.min_flame)),
            hysteresis=entry.options.get(CONF_HYSTERESIS, default.hysteresis),
        )
        self._manual_flame: int | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            manufacturer="SIT",
            model="Proflame 2",
            serial_number=self.remote.serial_hex,
        )

    async def async_load(self) -> None:
        if stored := await self._store.async_load():
            try:
                self.state = State(**stored)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignoring unreadable stored state: %s", stored)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _async_read_sensor(self, _event=None) -> None:
        self.room_f = None
        if (sensor := self.hass.states.get(self.sensor)) is not None:
            try:
                self.room_f = TemperatureConverter.convert(
                    float(sensor.state),
                    sensor.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
                    UnitOfTemperature.FAHRENHEIT,
                )
            except (ValueError, HomeAssistantError):
                pass  # unknown, unavailable, or not a temperature
        if _event is not None:
            self.async_set()

    def _run_thermostat(self, state: State) -> State:
        """Fill in the temperatures and, in a thermostat mode, the flame."""
        if self.room_f is not None:
            # The remote reports whole degrees, in tenths.
            tenths = round(self.room_f) * 10
            state = replace(state, room_temp=min(max(tenths, 0), 0xFFFF))
        if not state.thermostat:
            return state
        if self.room_f is None:
            return replace(state, flame=0)  # no reading: do not heat blind
        # Thermostat bit alone is plain on/off; with the power bit it is smart.
        flame = self.curve.flame(
            state.set_point - self.room_f, state.flame, smart=state.power
        )
        return replace(state, flame=flame)

    @callback
    def async_set(self, **changes) -> None:
        """Update the state now and transmit once changes stop arriving."""
        before = self.state
        state = replace(before, **changes)
        if state.thermostat and not before.thermostat:
            self._manual_flame = before.flame
        elif before.thermostat and not state.thermostat and "flame" not in changes:
            if self._manual_flame is not None:
                state = replace(state, flame=self._manual_flame)
        self.state = self._run_thermostat(state)
        if not changes and self.state == before:
            return  # a sensor update that changed nothing we send
        for listener in self._listeners:
            listener()
        self._store.async_delay_save(lambda: asdict(self.state), 1)
        if self._cancel_debounce:
            self._cancel_debounce()
        self._cancel_debounce = async_call_later(
            self.hass, DEBOUNCE_SECONDS, self._async_debounced
        )

    async def _async_debounced(self, _now) -> None:
        self._cancel_debounce = None
        try:
            await self.async_transmit()
        except HomeAssistantError as err:
            _LOGGER.error("Could not send fireplace command: %s", err)

    async def async_transmit(self, _now=None) -> None:
        """Send the current state through the Broadlink remote entity."""
        code = encode_broadlink_b64(self.remote, self.state)
        async with self._lock:
            await self.hass.services.async_call(
                "remote",
                "send_command",
                {
                    "entity_id": self.entry.data[CONF_REMOTE_ENTITY],
                    "command": [f"b64:{code}"],
                },
                blocking=True,
            )

    async def _async_resend(self, _now) -> None:
        try:
            await self.async_transmit()
        except HomeAssistantError as err:
            _LOGGER.warning("Periodic fireplace resend failed: %s", err)

    @callback
    def async_start(self) -> None:
        if self.sensor:
            self._async_read_sensor()
            self.entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, [self.sensor], self._async_read_sensor
                )
            )
        minutes = self.entry.options.get(CONF_RESEND_MINUTES, DEFAULT_RESEND_MINUTES)
        if minutes:
            self.entry.async_on_unload(
                async_track_time_interval(
                    self.hass, self._async_resend, timedelta(minutes=minutes)
                )
            )

    @callback
    def async_stop(self) -> None:
        if self._cancel_debounce:
            self._cancel_debounce()
            self._cancel_debounce = None


class ProflameEntity(Entity):
    """Base class for the fireplace's entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # The state is still assumed, but flagging it makes the frontend swap
    # toggles for a pair of lightning-bolt buttons. The controller resends
    # its state, so what HA shows is what the fireplace is told.
    _attr_assumed_state = False

    def __init__(self, controller: ProflameController, key: str) -> None:
        self.controller = controller
        self._attr_translation_key = key
        self._attr_unique_id = f"{controller.entry.entry_id}_{key}"
        self._attr_device_info = controller.device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.controller.async_add_listener(self.async_write_ha_state)
        )


async def async_setup_entry(hass: HomeAssistant, entry: ProflameConfigEntry) -> bool:
    controller = ProflameController(hass, entry)
    await controller.async_load()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ProflameConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ProflameConfigEntry) -> bool:
    entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
