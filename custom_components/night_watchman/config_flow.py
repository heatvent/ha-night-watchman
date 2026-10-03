"""Config flow for Night Watchman."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVITY_DOORS,
    CONF_ACTIVITY_LIGHTS,
    CONF_ACTIVITY_MOTION,
    CONF_AWAY_PEOPLE,
    CONF_DOORS_TO_LOCK,
    CONF_END,
    CONF_INCLUDE_ACTIVITY_LIGHTS,
    CONF_INTERVAL,
    CONF_KEEP_ON_LIGHTS,
    CONF_MONITOR_ALL,
    CONF_NOTIFY_SERVICE,
    CONF_PRESENCE_SIMULATION,
    CONF_QUIET_MINUTES,
    CONF_START,
    CONF_TURN_OFF_ENTITIES,
    DOMAIN,
    LOCK_SLOTS,
)

_NOTIFY_SERVICE = re.compile(r"[a-z0-9_]+")


def _slot_default(defaults: dict[str, Any], key: str) -> str | None:
    section = defaults.get(CONF_DOORS_TO_LOCK)
    if isinstance(section, dict):
        nested = section.get(key)
        if isinstance(nested, str) and nested:
            return nested
    value = defaults.get(key)
    return value if isinstance(value, str) and value else None


def _lock_slot_fields(defaults: dict[str, Any]) -> dict[Any, Any]:
    """Optional lock and contact pairs. A door is locked only when its contact is closed."""
    fields: dict[Any, Any] = {}
    for slot in range(1, LOCK_SLOTS + 1):
        lock_key = f"lock_entity_{slot}"
        contact_key = f"contact_entity_{slot}"
        lock_default = _slot_default(defaults, lock_key)
        contact_default = _slot_default(defaults, contact_key)
        lock_field = vol.Optional(lock_key, default=lock_default) if lock_default else vol.Optional(lock_key)
        contact_field = (
            vol.Optional(contact_key, default=contact_default) if contact_default else vol.Optional(contact_key)
        )
        fields[lock_field] = selector.EntitySelector(selector.EntitySelectorConfig(domain="lock"))
        fields[contact_field] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        )
    return fields


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
            vol.Optional(
                CONF_ACTIVITY_LIGHTS, default=defaults.get(CONF_ACTIVITY_LIGHTS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Optional(
                CONF_ACTIVITY_MOTION, default=defaults.get(CONF_ACTIVITY_MOTION, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="binary_sensor", device_class="motion", multiple=True
                )
            ),
            vol.Optional(
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
                CONF_KEEP_ON_LIGHTS, default=defaults.get(CONF_KEEP_ON_LIGHTS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Optional(
                CONF_TURN_OFF_ENTITIES, default=defaults.get(CONF_TURN_OFF_ENTITIES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["light", "switch"], multiple=True)
            ),
            vol.Required(CONF_DOORS_TO_LOCK): section(
                vol.Schema(_lock_slot_fields(defaults)),
                {"collapsed": False},
            ),
            vol.Required("when_everyone_away"): section(
                vol.Schema(_away_fields(defaults)),
                {"collapsed": False},
            ),
        }
    )


def _away_fields(defaults: dict[str, Any]) -> dict[Any, Any]:
    """People who must all be away, and the optional Presence Simulation switch."""
    presence_default = defaults.get(CONF_PRESENCE_SIMULATION)
    if isinstance(defaults.get("when_everyone_away"), dict):
        nested = defaults["when_everyone_away"]
        people_default = nested.get(CONF_AWAY_PEOPLE, defaults.get(CONF_AWAY_PEOPLE, []))
        nested_presence = nested.get(CONF_PRESENCE_SIMULATION)
        if isinstance(nested_presence, str) and nested_presence:
            presence_default = nested_presence
    else:
        people_default = defaults.get(CONF_AWAY_PEOPLE, [])
    presence_field = (
        vol.Optional(CONF_PRESENCE_SIMULATION, default=presence_default)
        if isinstance(presence_default, str) and presence_default
        else vol.Optional(CONF_PRESENCE_SIMULATION)
    )
    return {
        vol.Optional(CONF_AWAY_PEOPLE, default=people_default or []): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="person", multiple=True)
        ),
        presence_field: selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
    }


def _clean_notify(value: Any) -> str | None:
    """Accept one service or a comma-separated list. Store a cleaned list string."""
    if not isinstance(value, str):
        return None
    names: list[str] = []
    for part in value.split(","):
        name = part.strip().removeprefix("notify.").strip()
        if not name:
            continue
        if not _NOTIFY_SERVICE.fullmatch(name):
            return None
        if name not in names:
            names.append(name)
    return ", ".join(names) if names else None


def _normalize_submission(user_input: dict[str, Any]) -> dict[str, Any]:
    """Store lock pairs in one place and never fall back to monitoring every device."""
    data = dict(user_input)
    section = data.get(CONF_DOORS_TO_LOCK)
    if not isinstance(section, dict):
        section = {}
    stored_section: dict[str, str | None] = {}
    for slot in range(1, LOCK_SLOTS + 1):
        for key in (f"lock_entity_{slot}", f"contact_entity_{slot}"):
            value = section.get(key) or data.get(key)
            stored = value if isinstance(value, str) and value else None
            stored_section[key] = stored
            data[key] = stored
    away = data.get("when_everyone_away")
    if not isinstance(away, dict):
        away = {}
    people = away.get(CONF_AWAY_PEOPLE, data.get(CONF_AWAY_PEOPLE, []))
    if not isinstance(people, list):
        people = []
    presence = away.get(CONF_PRESENCE_SIMULATION) or data.get(CONF_PRESENCE_SIMULATION)
    presence = presence if isinstance(presence, str) and presence.startswith("switch.") else None
    data[CONF_AWAY_PEOPLE] = [item for item in people if isinstance(item, str) and item]
    data[CONF_PRESENCE_SIMULATION] = presence
    data["when_everyone_away"] = {
        CONF_AWAY_PEOPLE: data[CONF_AWAY_PEOPLE],
        CONF_PRESENCE_SIMULATION: presence,
    }
    service = _clean_notify(data.get(CONF_NOTIFY_SERVICE))
    data[CONF_NOTIFY_SERVICE] = service or "phones_group"
    data[CONF_DOORS_TO_LOCK] = stored_section
    data[CONF_MONITOR_ALL] = False
    return data


class NightWatchmanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Night Watchman config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if _clean_notify(user_input.get(CONF_NOTIFY_SERVICE)) is None:
                errors[CONF_NOTIFY_SERVICE] = "invalid_notify_service"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Night Watchman", data=_normalize_submission(user_input))
        return self.async_show_form(
            step_id="user",
            data_schema=_schedule_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return NightWatchmanOptionsFlow()

class NightWatchmanOptionsFlow(OptionsFlow):
    """Edit schedule, activity devices, and lights to turn off."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the main settings."""
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            if _clean_notify(user_input.get(CONF_NOTIFY_SERVICE)) is None:
                errors[CONF_NOTIFY_SERVICE] = "invalid_notify_service"
                current = {**current, **user_input}
            else:
                return self.async_create_entry(data=_normalize_submission(user_input))
        return self.async_show_form(
            step_id="init",
            data_schema=_schedule_schema(current),
            errors=errors,
        )
