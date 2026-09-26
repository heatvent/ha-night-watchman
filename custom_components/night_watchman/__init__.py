"""Night Watchman rounds."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTIVITY_DOORS,
    CONF_ACTIVITY_ENTITIES,
    CONF_ACTIVITY_LIGHTS,
    CONF_ACTIVITY_MOTION,
    CONF_CONTACT_ENTITY,
    CONF_DOORS_TO_LOCK,
    CONF_END,
    CONF_INCLUDE_ACTIVITY_LIGHTS,
    CONF_INTERVAL,
    CONF_KEEP_ON_LIGHTS,
    CONF_LOCK_ENTITY,
    CONF_NOTIFY_SERVICE,
    CONF_QUIET_MINUTES,
    CONF_START,
    CONF_TURN_OFF_ENTITIES,
    LOCK_SLOTS,
    SUBENTRY_LOCK,
)

_LOGGER = logging.getLogger(__name__)

_DAY_MINUTES = 24 * 60
_NOTIFY_SERVICE = re.compile(r"[a-z0-9_]+")
_OPEN = frozenset({STATE_ON, "open"})
_CLOSED = frozenset({STATE_OFF, "closed"})
_TURN_OFF_DOMAINS = frozenset({"light", "switch"})
_ACTIVE_ROUNDS: set[str] = set()

type WatchmanConfigEntry = ConfigEntry


def _settings(entry: ConfigEntry) -> dict[str, Any]:
    merged = dict(entry.data)
    merged.update(entry.options)
    return merged


def _parse_time(value: Any) -> tuple[int, int] | None:
    try:
        if isinstance(value, dict):
            hour = int(value.get("hour", 0))
            minute = int(value.get("minute", 0))
        else:
            parts = str(value).split(":")
            hour = int(parts[0])
            minute = int(parts[1] if len(parts) > 1 else 0)
    except (TypeError, ValueError, IndexError):
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour, minute


def _minutes(value: Any) -> int | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    hour, minute = parsed
    return hour * 60 + minute


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _unique(entity_ids: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for entity_id in entity_ids:
        if entity_id not in seen:
            seen.add(entity_id)
            unique.append(entity_id)
    return unique


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def round_is_due(now_m: int, start: int, end: int, interval: int) -> bool:
    """True when this minute is inside the window and lands on the interval."""
    if interval <= 0:
        return False
    if end < start:
        in_window = now_m >= start or now_m <= end
        elapsed = (now_m - start) % _DAY_MINUTES
    else:
        in_window = start <= now_m <= end
        elapsed = now_m - start
    return in_window and elapsed % interval == 0


def _opt_in_lists_present(options: dict[str, Any]) -> bool:
    return any(key in options for key in (CONF_ACTIVITY_LIGHTS, CONF_ACTIVITY_MOTION, CONF_ACTIVITY_DOORS))


def _monitored_lights(options: dict[str, Any]) -> list[str]:
    """Only the lights the user chose. A new light is ignored until they add it."""
    if _opt_in_lists_present(options):
        return _unique(_as_list(options.get(CONF_ACTIVITY_LIGHTS)))
    return _unique(
        entity_id
        for entity_id in _as_list(options.get(CONF_ACTIVITY_ENTITIES))
        if entity_id.startswith("light.")
    )


def _activity_entities(options: dict[str, Any]) -> list[str]:
    if _opt_in_lists_present(options):
        entities: list[str] = []
        for key in (CONF_ACTIVITY_LIGHTS, CONF_ACTIVITY_MOTION, CONF_ACTIVITY_DOORS):
            entities.extend(_as_list(options.get(key)))
        return _unique(entities)
    return _unique(_as_list(options.get(CONF_ACTIVITY_ENTITIES)))


def _turn_off_entities(options: dict[str, Any]) -> list[str]:
    entities: list[str] = []
    if options.get(CONF_INCLUDE_ACTIVITY_LIGHTS, True):
        entities.extend(_monitored_lights(options))
    entities.extend(_as_list(options.get(CONF_TURN_OFF_ENTITIES)))
    keep = set(_as_list(options.get(CONF_KEEP_ON_LIGHTS)))
    return [
        entity_id
        for entity_id in _unique(entities)
        if entity_id not in keep and entity_id.split(".", 1)[0] in _TURN_OFF_DOMAINS
    ]


def _changed_recently(hass: HomeAssistant, entity_id: str, quiet: timedelta, now: datetime) -> bool:
    state = hass.states.get(entity_id)
    if state is None or state.last_changed is None:
        return False
    return dt_util.as_local(state.last_changed) > now - quiet


def _slot_value(options: dict[str, Any], key: str) -> str | None:
    """Read a lock slot. A saved section wins, including a cleared slot."""
    section = options.get(CONF_DOORS_TO_LOCK)
    if isinstance(section, dict) and key in section:
        value = section.get(key)
        return value if isinstance(value, str) and value else None
    value = options.get(key)
    return value if isinstance(value, str) and value else None


def _notify_service_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "phones_group"
    name = value.strip().removeprefix("notify.").strip()
    if not _NOTIFY_SERVICE.fullmatch(name):
        return None
    return name


def _is_overhead(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    name = state.name if state else ""
    return "overhead" in f"{entity_id} {name}".lower()


def _state_token(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return state.state.strip().casefold()


async def _call_entity(hass: HomeAssistant, domain: str, service: str, entity_id: str) -> bool:
    try:
        await hass.services.async_call(
            domain,
            service,
            {"entity_id": entity_id},
            blocking=True,
        )
    except Exception:
        _LOGGER.exception("Night Watchman could not %s %s", service, entity_id)
        return False
    return True


async def _run_round(hass: HomeAssistant, entry: ConfigEntry, now: datetime) -> None:
    options = _settings(entry)
    quiet = timedelta(minutes=_positive_int(options.get(CONF_QUIET_MINUTES), 45))
    if any(_changed_recently(hass, entity_id, quiet, now) for entity_id in _activity_entities(options)):
        _LOGGER.debug("Night Watchman skipped this round because the house is still active")
        return

    locked: list[str] = []
    left_open: list[str] = []
    seen_locks: set[str] = set()
    rules: list[tuple[str, str, str | None]] = []
    for slot in range(1, LOCK_SLOTS + 1):
        lock_entity = _slot_value(options, f"lock_entity_{slot}")
        contact = _slot_value(options, f"contact_entity_{slot}")
        if lock_entity and contact:
            rules.append((lock_entity, contact, None))
    for subentry in getattr(entry, "subentries", {}).values():
        contact = subentry.data.get(CONF_CONTACT_ENTITY)
        lock_entity = subentry.data.get(CONF_LOCK_ENTITY) if subentry.subentry_type == SUBENTRY_LOCK else None
        if isinstance(contact, str) and contact:
            rules.append((lock_entity if isinstance(lock_entity, str) else "", contact, subentry.title))

    for lock_entity, contact, title in rules:
        if lock_entity and lock_entity in seen_locks:
            continue
        contact_state = hass.states.get(contact)
        contact_name = contact_state.name if contact_state else contact
        lock_state = hass.states.get(lock_entity) if lock_entity else None
        name = title or (lock_state.name if lock_state else contact_name)
        token = _state_token(hass, contact)
        if token in _OPEN:
            left_open.append(name)
            if lock_entity:
                seen_locks.add(lock_entity)
            continue
        if not lock_entity or not lock_entity.startswith("lock.") or token not in _CLOSED:
            continue
        if lock_entity == contact or _is_overhead(hass, lock_entity):
            _LOGGER.warning("Night Watchman will not lock %s", lock_entity)
            seen_locks.add(lock_entity)
            continue
        if lock_state is None or lock_state.state.strip().casefold() != "unlocked":
            seen_locks.add(lock_entity)
            continue
        if await _call_entity(hass, "lock", "lock", lock_entity):
            locked.append(name)
        seen_locks.add(lock_entity)

    turned_off: list[str] = []
    for entity_id in _turn_off_entities(options):
        state = hass.states.get(entity_id)
        if state is None or state.state != STATE_ON:
            continue
        domain = entity_id.split(".", 1)[0]
        if await _call_entity(hass, domain, "turn_off", entity_id):
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

    service = _notify_service_name(options.get(CONF_NOTIFY_SERVICE))
    if service is None:
        _LOGGER.error("Night Watchman notify service is not a plain service name")
        return
    if not hass.services.has_service("notify", service):
        _LOGGER.error("Night Watchman notify service notify.%s is not available", service)
        return
    try:
        await hass.services.async_call(
            "notify",
            service,
            {"title": "Night watchman", "message": " ".join(parts)},
            blocking=False,
        )
    except Exception:
        _LOGGER.exception("Night Watchman could not notify %s", service)


async def async_setup_entry(hass: HomeAssistant, entry: WatchmanConfigEntry) -> bool:
    """Set up one Night Watchman entry."""

    async def _tick(now: datetime) -> None:
        if entry.entry_id in _ACTIVE_ROUNDS:
            _LOGGER.debug("Night Watchman is still finishing the previous round")
            return
        local = dt_util.as_local(now)
        options = _settings(entry)
        start = _minutes(options.get(CONF_START, "01:00:00"))
        end = _minutes(options.get(CONF_END, "05:00:00"))
        interval = _positive_int(options.get(CONF_INTERVAL), 60)
        if start is None or end is None:
            _LOGGER.error("Night Watchman has an invalid schedule")
            return
        if not round_is_due(local.hour * 60 + local.minute, start, end, interval):
            return
        _ACTIVE_ROUNDS.add(entry.entry_id)
        try:
            await _run_round(hass, entry, local)
        except Exception:
            _LOGGER.exception("Night Watchman round failed")
        finally:
            _ACTIVE_ROUNDS.discard(entry.entry_id)

    entry.async_on_unload(async_track_time_change(hass, _tick, second=10))
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _ACTIVE_ROUNDS.discard(entry.entry_id)
    return True
