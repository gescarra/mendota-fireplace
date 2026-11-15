"""Config flow for Proflame 2."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CAPTURE,
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
from .protocol import ProflameError, decode_any
from .thermostat import MAX_LEVEL, Curve

SENSOR_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Fireplace"): str,
        vol.Required(CONF_REMOTE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="remote")
        ),
        vol.Required(CONF_CAPTURE): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        ),
        vol.Optional(CONF_TEMPERATURE_SENSOR): SENSOR_SELECTOR,
    }
)


class ProflameConfigFlow(ConfigFlow, domain=DOMAIN):
    """Identify the handheld remote to imitate from one captured code."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                remote = decode_any(user_input[CONF_CAPTURE]).remote
            except ProflameError:
                errors[CONF_CAPTURE] = "invalid_capture"
            else:
                await self.async_set_unique_id(remote.serial_hex)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_REMOTE_ENTITY: user_input[CONF_REMOTE_ENTITY],
                        CONF_REMOTE: remote.as_dict(),
                    },
                    options={
                        key: user_input[key]
                        for key in (CONF_TEMPERATURE_SENSOR,)
                        if user_input.get(key)
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ProflameOptionsFlow()


class ProflameOptionsFlow(OptionsFlow):
    """Resend interval, the room sensor, and the thermostat's flame curve."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_RESEND_MINUTES): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=60, step=1, unit_of_measurement="min"
                    )
                ),
                vol.Optional(CONF_TEMPERATURE_SENSOR): SENSOR_SELECTOR,
                vol.Required(CONF_SMART_SPAN): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=20, step=0.5, unit_of_measurement="°F"
                    )
                ),
                vol.Required(CONF_SMART_MIN_FLAME): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=MAX_LEVEL, step=1)
                ),
                vol.Required(CONF_HYSTERESIS): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=3, step=0.1, unit_of_measurement="°F"
                    )
                ),
            }
        )
        curve = Curve()
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_RESEND_MINUTES: DEFAULT_RESEND_MINUTES,
                    CONF_SMART_SPAN: curve.span,
                    CONF_SMART_MIN_FLAME: curve.min_flame,
                    CONF_HYSTERESIS: curve.hysteresis,
                }
                | self.config_entry.options,
            ),
        )
