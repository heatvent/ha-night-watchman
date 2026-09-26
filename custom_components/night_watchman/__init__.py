"""Night Watchman rounds."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNLOCKED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTIVITY_ENTITIES,
    CONF_CONTACT_ENTITY,
    CONF_END,
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

type WatchmanConfigEntry = ConfigEntry


def _settings(entry: ConfigEntry) -> dict[str, Any]:
    merged = dict(entry.data)
    merged.update(entry.options)
    return merged


def _parse_time(value: Any) -> tuple[int, int]:
    if isinstance(value, dict):
        return int(value.get("hour", 0)), int(value.get("minute", 0))
    text = str(value)
    parts = text.split(":")
    return int(parts[0]), int(parts[1] if len(parts) > 1 else 0)


def _minutes(value: Any) -> int:
    hour, minute = _parse_time(value)
    return hour * 60 + minute


def _changed_recently(hass: HomeAssistant, entity_id: str, quiet: timedelta, now: datetime) -> bool:
    state = hass.states.get(entity_id)
    if state is None or state.last_changed is None:
        return False
    return state.last_changed > now - quiet


async def _run_round(hass: HomeAssistant, entry: ConfigEntry, now: datetime) -> None:
    options = _settings(entry)
    quiet = timedelta(minutes=int(options.get(CONF_QUIET_MINUTES, 45)))
    activity = list(options.get(CONF_ACTIVITY_ENTITIES) or [])
    if any(_changed_recently(hass, entity_id, quiet, now) for entity_id in activity):
        return

    locked: list[str] = []
    left_open: list[str] = []
    for subentry in entry.subentries.values():
        contact = subentry.data.get(CONF_CONTACT_ENTITY)
        if not contact:
            continue
        contact_state = hass.states.get(contact)
        name = subentry.title
        if contact_state is not None and contact_state.state == STATE_ON:
            left_open.append(name)
            continue
        if subentry.subentry_type != SUBENTRY_LOCK:
            continue
        if contact_state is None or contact_state.state != STATE_OFF:
            continue
        lock_entity = subentry.data.get(CONF_LOCK_ENTITY)
        lock_state = hass.states.get(lock_entity) if lock_entity else None
        if lock_state is None or lock_state.state != STATE_UNLOCKED:
            continue
        await hass.services.async_call(
            "lock",
            "lock",
            {"entity_id": lock_entity},
            blocking=True,
        )
        locked.append(name)

    turned_off: list[str] = []
    for entity_id in options.get(CONF_TURN_OFF_ENTITIES) or []:
        state = hass.states.get(entity_id)
        if state is None or state.state != STATE_ON:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain != "light":
            continue
        await hass.services.async_call(
            domain,
            "turn_off",
            {"entity_id": entity_id},
            blocking=True,
        )
        turned_off.append(state.name or entity_id)

    parts: list[str] = []
    if locked:
        parts.append("Locked " + ", ".join(locked) + ".")
    if turned_off:
        parts.append("Turned off " + ", ".join(turned_off) + ".")
    if left_open:
        parts.append("Still open: " + ", ".join(left_open) + ".")
    if not parts:
        return

    service = str(options.get(CONF_NOTIFY_SERVICE) or "phones_group")
    await hass.services.async_call(
        "notify",
        service.removeprefix("notify."),
        {"title": "Night watchman", "message": " ".join(parts)},
        blocking=False,
    )


async def async_setup_entry(hass: HomeAssistant, entry: WatchmanConfigEntry) -> bool:
    """Set up one Night Watchman entry."""

    async def _tick(now: datetime) -> None:
        local = dt_util.as_local(now)
        options = _settings(entry)
        start = _minutes(options.get(CONF_START, "01:00:00"))
        end = _minutes(options.get(CONF_END, "05:00:00"))
        interval = max(int(options.get(CONF_INTERVAL, 60)), 1)
        now_m = local.hour * 60 + local.minute
        if end < start:
            in_window = now_m >= start or now_m <= end
        else:
            in_window = start <= now_m <= end
        if not in_window or (now_m - start) % interval != 0:
            return
        await _run_round(hass, entry, local)

    entry.async_on_unload(async_track_time_change(hass, _tick, second=10))
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
