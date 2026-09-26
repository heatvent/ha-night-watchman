"""Night Watchman rounds."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
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
    DOMAIN,
    LOCK_SLOTS,
    PLATFORMS,
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


class NightWatchmanRuntime:
    """Shared state for the switch, sensors, and the night rounds."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self.enabled = True
        self.suppress_activity = False
        self.last_round: datetime | None = None
        self.last_round_summary: str | None = None
        self.last_activity: datetime | None = None
        self.last_activity_entity: str | None = None
        self.last_activity_name: str | None = None
        self._listeners: list[Callable[[], None]] = []

    @callback
    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)

        @callback
        def _remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return _remove

    @callback
    def record_activity(self, entity_id: str, name: str, when: datetime) -> None:
        self.last_activity = when
        self.last_activity_entity = entity_id
        self.last_activity_name = name
        self._publish()

    @callback
    def record_round(self, when: datetime, summary: str) -> None:
        self.last_round = when
        self.last_round_summary = summary
        self._publish()

    @callback
    def _publish(self) -> None:
        for update in list(self._listeners):
            update()


_IGNORED_ACTIVITY = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN, "none", ""})


@callback
def _remember_activity(runtime: NightWatchmanRuntime, event: Event) -> None:
    """Remember a real change. Ignore startup noise and actions this round just took."""
    if runtime.suppress_activity:
        return
    old_state = event.data.get("old_state")
    new_state = event.data.get("new_state")
    if old_state is None or new_state is None or old_state.state == new_state.state:
        return
    if old_state.state in _IGNORED_ACTIVITY or new_state.state in _IGNORED_ACTIVITY:
        return
    runtime.record_activity(
        new_state.entity_id,
        new_state.name or new_state.entity_id,
        dt_util.as_local(new_state.last_changed or dt_util.utcnow()),
    )


async def _run_round(hass: HomeAssistant, entry: ConfigEntry, now: datetime) -> str | None:
    options = _settings(entry)
    quiet = timedelta(minutes=_positive_int(options.get(CONF_QUIET_MINUTES), 45))
    if any(_changed_recently(hass, entity_id, quiet, now) for entity_id in _activity_entities(options)):
        _LOGGER.debug("Night Watchman skipped this round because the house is still active")
        return None

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
        return "Nothing to do"

    summary = " ".join(parts)
    service = _notify_service_name(options.get(CONF_NOTIFY_SERVICE))
    if service is None:
        _LOGGER.error("Night Watchman notify service is not a plain service name")
        return summary
    if not hass.services.has_service("notify", service):
        _LOGGER.error("Night Watchman notify service notify.%s is not available", service)
        return summary
    try:
        await hass.services.async_call(
            "notify",
            service,
            {"title": "Night watchman", "message": summary},
            blocking=False,
        )
    except Exception:
        _LOGGER.exception("Night Watchman could not notify %s", service)
    return summary


async def async_setup_entry(hass: HomeAssistant, entry: WatchmanConfigEntry) -> bool:
    """Set up one Night Watchman entry."""
    runtime = NightWatchmanRuntime(entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
        if not runtime.enabled:
            _LOGGER.debug("Night Watchman is paused")
            return
        _ACTIVE_ROUNDS.add(entry.entry_id)
        runtime.suppress_activity = True
        try:
            summary = await _run_round(hass, entry, local)
        except Exception:
            _LOGGER.exception("Night Watchman round failed")
            summary = None
        finally:
            runtime.suppress_activity = False
            _ACTIVE_ROUNDS.discard(entry.entry_id)
        if summary is not None:
            runtime.record_round(local, summary)

    entry.async_on_unload(async_track_time_change(hass, _tick, second=10))
    watched = _activity_entities(_settings(entry))
    if watched:

        @callback
        def _activity(event: Event) -> None:
            _remember_activity(runtime, event)

        entry.async_on_unload(async_track_state_change_event(hass, watched, _activity))
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _ACTIVE_ROUNDS.discard(entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
