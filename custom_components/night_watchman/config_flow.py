"""Config flow for Night Watchman."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVITY_DOORS,
    CONF_ACTIVITY_LIGHTS,
    CONF_ACTIVITY_MOTION,
    CONF_CONTACT_ENTITY,
    CONF_END,
    CONF_INCLUDE_ACTIVITY_LIGHTS,
    CONF_INTERVAL,
    CONF_LOCK_ENTITY,
    CONF_NOTIFY_SERVICE,
    CONF_QUIET_MINUTES,
    CONF_START,
    CONF_TURN_OFF_ENTITIES,
    DOMAIN,
    SUBENTRY_LOCK,
    SUBENTRY_REPORT,
)


def _schedule_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_START, default=defaults.get(CONF_START, "01:00:00")): selector.TimeSelector(),
            vol.Required(CONF_END, default=defaults.get(CONF_END, "05:00:00")): selector.TimeSelector(),
            vol.Required(CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, 60)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=15, max=180, step=15, mode="box", unit_of_measurement="min")
            ),
            vol.Required(
                CONF_QUIET_MINUTES, default=defaults.get(CONF_QUIET_MINUTES, 45)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=180, step=5, mode="box", unit_of_measurement="min")
            ),
            vol.Required(
                CONF_NOTIFY_SERVICE, default=defaults.get(CONF_NOTIFY_SERVICE, "phones_group")
            ): selector.TextSelector(),
            vol.Required(
                CONF_ACTIVITY_LIGHTS, default=defaults.get(CONF_ACTIVITY_LIGHTS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Required(
                CONF_ACTIVITY_MOTION, default=defaults.get(CONF_ACTIVITY_MOTION, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor", device_class="motion", multiple=True
                )
            ),
            vol.Required(
                CONF_ACTIVITY_DOORS, default=defaults.get(CONF_ACTIVITY_DOORS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor",
                    device_class=["door", "garage_door", "opening", "window"],
                    multiple=True,
                )
            ),
            vol.Required(
                CONF_INCLUDE_ACTIVITY_LIGHTS,
                default=defaults.get(CONF_INCLUDE_ACTIVITY_LIGHTS, True),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_TURN_OFF_ENTITIES, default=defaults.get(CONF_TURN_OFF_ENTITIES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
        }
    )


class NightWatchmanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Night Watchman config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the entry."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Night Watchman", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schedule_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return NightWatchmanOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Locks and report-only contacts are added from the integration page."""
        return {
            SUBENTRY_LOCK: LockRuleFlow,
            SUBENTRY_REPORT: ReportOpenFlow,
        }


class NightWatchmanOptionsFlow(OptionsFlow):
    """Edit schedule, activity devices, and lights to turn off."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the main settings."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schedule_schema(current))


def _lock_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_LOCK_ENTITY, default=defaults.get(CONF_LOCK_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="lock")
            ),
            vol.Required(
                CONF_CONTACT_ENTITY, default=defaults.get(CONF_CONTACT_ENTITY)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
        }
    )


class LockRuleFlow(ConfigSubentryFlow):
    """Add or edit a lock that requires a closed contact."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a lock rule."""
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_LOCK_ENTITY])
            title = state.name if state else user_input[CONF_LOCK_ENTITY]
            return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(step_id="user", data_schema=_lock_schema())

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit a lock rule."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_LOCK_ENTITY])
            title = state.name if state else user_input[CONF_LOCK_ENTITY]
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=title, data=user_input
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_lock_schema(dict(subentry.data)),
        )


class ReportOpenFlow(ConfigSubentryFlow):
    """Add or edit a contact that is only reported when open."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a report-only contact."""
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_CONTACT_ENTITY])
            title = state.name if state else user_input[CONF_CONTACT_ENTITY]
            return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONTACT_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="binary_sensor")
                    )
                }
            ),
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit a report-only contact."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_CONTACT_ENTITY])
            title = state.name if state else user_input[CONF_CONTACT_ENTITY]
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=title, data=user_input
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONTACT_ENTITY, default=subentry.data.get(CONF_CONTACT_ENTITY)
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))
                }
            ),
        )
