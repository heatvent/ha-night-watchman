"""Night Watchman rounds."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_NOT_HOME, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from .const import (
    AWAY_ALARM_MODES,
    CONF_ACTIVITY_DOORS,
    CONF_ACTIVITY_ENTITIES,
    CONF_ACTIVITY_LIGHTS,
    CONF_ACTIVITY_MOTION,
    CONF_AWAY_ALARM,
    CONF_AWAY_ALARM_MODE,
    CONF_AWAY_ENABLED,
    CONF_AWAY_PEOPLE,
    CONF_CONTACT_ENTITY,
    CONF_DOORS_TO_LOCK,
    CONF_END,
    CONF_INCLUDE_ACTIVITY_LIGHTS,
    CONF_INTERVAL,
    CONF_KEEP_ON_LIGHTS,
    CONF_LOCK_ENTITY,
    CONF_NOTIFY_SERVICE,
    CONF_PRESENCE_SIMULATION,
    CONF_QUIET_MINUTES,
    CONF_START,
    CONF_TURN_OFF_ENTITIES,
    DEFAULT_AWAY_ALARM_MODE,
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
_HOME_STATES = frozenset({STATE_HOME, "home"})
_AWAY_STATES = frozenset({STATE_NOT_HOME, "not_home", "away"})
_IGNORED_ACTIVITY = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN, "none", ""})
_ALARM_ARMED_PREFIX = "armed_"
_ALARM_ARM_SERVICES = {
    "away": "alarm_arm_away",
    "home": "alarm_arm_home",
    "night": "alarm_arm_night",
    "vacation": "alarm_arm_vacation",
}
_SELF_ACTION_GRACE = timedelta(seconds=5)
_STARTUP_GRACE = timedelta(seconds=30)
_ALARM_CONFIRM_ATTEMPTS = 12
_ALARM_CONFIRM_DELAY = 0.25
_LOCK_CONFIRM_ATTEMPTS = 20
_LOCK_CONFIRM_DELAY = 0.5
_ACTIVE_ROUNDS: set[str] = set()

type WatchmanConfigEntry = ConfigEntry


@dataclass
class RoundOutcome:
    """Result of one secure round."""

    summary: str
    left_open: list[str] = field(default_factory=list)

    @property
    def skipped(self) -> bool:
        return self.summary.startswith("Skipped:")


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


def _activity_entities(options: dict[str, Any], *, include_lights: bool = True) -> list[str]:
    if _opt_in_lists_present(options):
        entities: list[str] = []
        keys = (CONF_ACTIVITY_MOTION, CONF_ACTIVITY_DOORS)
        if include_lights:
            keys = (CONF_ACTIVITY_LIGHTS, CONF_ACTIVITY_MOTION, CONF_ACTIVITY_DOORS)
        for key in keys:
            entities.extend(_as_list(options.get(key)))
        return _unique(entities)
    entities = _unique(_as_list(options.get(CONF_ACTIVITY_ENTITIES)))
    if include_lights:
        return entities
    return [entity_id for entity_id in entities if not entity_id.startswith("light.")]


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


def _changed_recently(
    hass: HomeAssistant,
    entity_id: str,
    quiet: timedelta,
    now: datetime,
    runtime: NightWatchmanRuntime,
) -> bool:
    """True when this entity changed recently for a reason other than NW or startup."""
    state = hass.states.get(entity_id)
    if state is None or state.last_changed is None:
        return False
    changed = dt_util.as_local(state.last_changed)
    if runtime.started_at is not None and changed <= runtime.started_at + _STARTUP_GRACE:
        return False
    acted = runtime.self_actions.get(entity_id)
    if acted is not None and changed <= acted + _SELF_ACTION_GRACE:
        return False
    return changed > now - quiet


def _slot_value(options: dict[str, Any], key: str) -> str | None:
    """Read a lock slot. A saved section wins, including a cleared slot."""
    section = options.get(CONF_DOORS_TO_LOCK)
    if isinstance(section, dict) and key in section:
        value = section.get(key)
        return value if isinstance(value, str) and value else None
    value = options.get(key)
    return value if isinstance(value, str) and value else None


def _notify_service_names(value: Any) -> list[str] | None:
    """One service, or several separated by commas. Leave off the notify. prefix."""
    if not isinstance(value, str) or not value.strip():
        return ["phones_group"]
    names: list[str] = []
    for part in value.split(","):
        name = part.strip().removeprefix("notify.").strip()
        if not name:
            continue
        if not _NOTIFY_SERVICE.fullmatch(name):
            return None
        if name not in names:
            names.append(name)
    return names or None


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


async def _lock_and_confirm(hass: HomeAssistant, entity_id: str) -> bool:
    """Call lock.lock and wait until the entity reports locked."""
    if not await _call_entity(hass, "lock", "lock", entity_id):
        return False
    for _ in range(_LOCK_CONFIRM_ATTEMPTS):
        await asyncio.sleep(_LOCK_CONFIRM_DELAY)
        state = hass.states.get(entity_id)
        if state is not None and state.state.strip().casefold() == "locked":
            return True
    _LOGGER.error("Night Watchman lock %s did not report locked", entity_id)
    return False


def _entity_name(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    return state.name if state else entity_id


def _recent_activity_names(
    hass: HomeAssistant,
    entity_ids: list[str],
    quiet: timedelta,
    now: datetime,
    runtime: NightWatchmanRuntime,
) -> list[str]:
    names: list[str] = []
    for entity_id in entity_ids:
        if _changed_recently(hass, entity_id, quiet, now, runtime):
            names.append(_entity_name(hass, entity_id))
    return names


def _away_section(options: dict[str, Any]) -> dict[str, Any]:
    nested = options.get("when_everyone_away")
    return nested if isinstance(nested, dict) else {}


def _away_enabled(options: dict[str, Any]) -> bool:
    """Optional away path. Off by default so other home/away automations stay in charge."""
    section = _away_section(options)
    if CONF_AWAY_ENABLED in section:
        return bool(section.get(CONF_AWAY_ENABLED))
    return bool(options.get(CONF_AWAY_ENABLED, False))


def _away_people(options: dict[str, Any]) -> list[str]:
    section = _away_section(options)
    if CONF_AWAY_PEOPLE in section:
        return _unique(_as_list(section.get(CONF_AWAY_PEOPLE)))
    return _unique(_as_list(options.get(CONF_AWAY_PEOPLE)))


def _everyone_away(hass: HomeAssistant, people: list[str]) -> bool:
    """True when every watched person is away. Unknown people block the away path."""
    if not people:
        return False
    for entity_id in people:
        state = hass.states.get(entity_id)
        if state is None or state.state in _IGNORED_ACTIVITY:
            return False
        token = state.state.strip().casefold()
        if token in _HOME_STATES:
            return False
        if token not in _AWAY_STATES:
            return False
    return True


def _presence_switch(options: dict[str, Any]) -> str | None:
    section = _away_section(options)
    value = section.get(CONF_PRESENCE_SIMULATION)
    if not isinstance(value, str) or not value:
        value = options.get(CONF_PRESENCE_SIMULATION)
    if isinstance(value, str) and value.startswith("switch."):
        return value
    return None


def _presence_is_on(hass: HomeAssistant, options: dict[str, Any]) -> bool:
    switch_id = _presence_switch(options)
    if not switch_id:
        return False
    state = hass.states.get(switch_id)
    return state is not None and state.state == STATE_ON


async def _set_presence(hass: HomeAssistant, options: dict[str, Any], turn_on: bool) -> bool:
    switch_id = _presence_switch(options)
    if not switch_id:
        return False
    service = "turn_on" if turn_on else "turn_off"
    return await _call_entity(hass, "switch", service, switch_id)


def _away_alarm(options: dict[str, Any]) -> str | None:
    section = _away_section(options)
    value = section.get(CONF_AWAY_ALARM)
    if not isinstance(value, str) or not value:
        value = options.get(CONF_AWAY_ALARM)
    if isinstance(value, str) and value.startswith("alarm_control_panel."):
        return value
    return None


def _away_alarm_mode(options: dict[str, Any]) -> str:
    section = _away_section(options)
    value = section.get(CONF_AWAY_ALARM_MODE, options.get(CONF_AWAY_ALARM_MODE, DEFAULT_AWAY_ALARM_MODE))
    return value if value in AWAY_ALARM_MODES else DEFAULT_AWAY_ALARM_MODE


def _alarm_matches_away_mode(hass: HomeAssistant, options: dict[str, Any]) -> bool:
    """True when the selected alarm is armed in the configured away mode (not a night/home arm)."""
    alarm_id = _away_alarm(options)
    if not alarm_id:
        return False
    state = hass.states.get(alarm_id)
    if state is None:
        return False
    return state.state.strip().casefold() == f"armed_{_away_alarm_mode(options)}"


def _away_already_secured(hass: HomeAssistant, runtime: NightWatchmanRuntime, options: dict[str, Any]) -> bool:
    """True when this away pass already finished, or PS/alarm still show the house secured."""
    return (
        runtime.away_secured
        or _presence_is_on(hass, options)
        or _alarm_matches_away_mode(hass, options)
    )


def _should_disarm_alarm(hass: HomeAssistant, runtime: NightWatchmanRuntime, options: dict[str, Any]) -> bool:
    """Disarm only when NW armed it, or after restart when Away-mode arm is still active."""
    if runtime.away_alarm_managed:
        return True
    return _away_alarm_mode(options) == DEFAULT_AWAY_ALARM_MODE and _alarm_matches_away_mode(hass, options)


async def _set_alarm(hass: HomeAssistant, options: dict[str, Any], arm: bool) -> bool:
    """Arm or disarm the selected alarm. Confirms the resulting state."""
    alarm_id = _away_alarm(options)
    if not alarm_id:
        return False
    mode = _away_alarm_mode(options)
    expected = f"armed_{mode}" if arm else "disarmed"
    state = hass.states.get(alarm_id)
    if state is None or state.state in _IGNORED_ACTIVITY:
        _LOGGER.error("Night Watchman alarm %s is not available", alarm_id)
        return False
    token = state.state.strip().casefold()
    if token == expected:
        return True
    if arm:
        if token.startswith(_ALARM_ARMED_PREFIX) or token in {"arming", "pending"}:
            return False
        service = _ALARM_ARM_SERVICES[mode]
    else:
        service = "alarm_disarm"
    if not await _call_entity(hass, "alarm_control_panel", service, alarm_id):
        return False
    for _ in range(_ALARM_CONFIRM_ATTEMPTS):
        await asyncio.sleep(_ALARM_CONFIRM_DELAY)
        current = hass.states.get(alarm_id)
        if current is not None and current.state.strip().casefold() == expected:
            return True
    _LOGGER.error("Night Watchman alarm %s did not reach %s", alarm_id, expected)
    return False


async def _notify(hass: HomeAssistant, options: dict[str, Any], message: str) -> None:
    """Send a notice after every round to each configured notify service."""
    services = _notify_service_names(options.get(CONF_NOTIFY_SERVICE))
    if not services:
        _LOGGER.error("Night Watchman notify service is not a plain service name")
        return
    for service in services:
        if not hass.services.has_service("notify", service):
            _LOGGER.error("Night Watchman notify service notify.%s is not available", service)
            continue
        try:
            await hass.services.async_call(
                "notify",
                service,
                {"title": "Night watchman", "message": message},
                blocking=False,
            )
        except Exception:
            _LOGGER.exception("Night Watchman could not notify %s", service)


class NightWatchmanRuntime:
    """Shared state for the switch, sensors, and the night rounds."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self.enabled = True
        self.suppress_activity = False
        self.away_secured = False
        self.away_alarm_managed = False
        self.started_at = dt_util.as_local(dt_util.utcnow())
        self.self_actions: dict[str, datetime] = {}
        self._away_retry: Callable[[], None] | None = None
        self.on_enabled_change: Callable[[bool], Awaitable[None]] | None = None
        self.last_round: datetime | None = None
        self.last_round_summary: str | None = None
        self.last_activity: datetime | None = None
        self.last_activity_entity: str | None = None
        self.last_activity_name: str | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def away_wait_pending(self) -> bool:
        """True while the quiet timer for the away secure pass is running."""
        return self._away_retry is not None

    @callback
    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)

        @callback
        def _remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return _remove

    @callback
    def cancel_away_retry(self) -> None:
        if self._away_retry is not None:
            self._away_retry()
            self._away_retry = None

    @callback
    def note_self_action(self, entity_id: str, when: datetime | None = None) -> None:
        """Remember a change Night Watchman made so it does not trip the quiet gate."""
        acted = when or dt_util.as_local(dt_util.utcnow())
        self.self_actions[entity_id] = acted
        cutoff = acted - timedelta(hours=6)
        self.self_actions = {
            entity: stamp for entity, stamp in self.self_actions.items() if stamp >= cutoff
        }

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


def _stamp_self_action(hass: HomeAssistant, runtime: NightWatchmanRuntime, entity_id: str, now: datetime) -> None:
    state = hass.states.get(entity_id)
    when = dt_util.as_local(state.last_changed) if state is not None and state.last_changed else now
    runtime.note_self_action(entity_id, when)


async def _run_round(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: NightWatchmanRuntime,
    now: datetime,
    *,
    turn_off_lights: bool = True,
    check_light_activity: bool = True,
) -> RoundOutcome:
    """Run one round and always return a notice: skipped, all clear, or what changed."""
    options = _settings(entry)
    quiet = timedelta(minutes=_positive_int(options.get(CONF_QUIET_MINUTES), 45))
    activity = _activity_entities(options, include_lights=check_light_activity)
    recent = _recent_activity_names(hass, activity, quiet, now, runtime)
    if recent:
        summary = "Skipped: the house is still active. Recent activity: " + ", ".join(recent) + "."
        await _notify(hass, options, summary)
        return RoundOutcome(summary=summary)

    locked: list[str] = []
    lock_failed: list[str] = []
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
        lock_token = lock_state.state.strip().casefold() if lock_state is not None else None
        if token in _OPEN:
            left_open.append(name)
            if lock_entity:
                seen_locks.add(lock_entity)
            continue
        if not lock_entity or not lock_entity.startswith("lock."):
            continue
        if token not in _CLOSED:
            # Unlocked door with a missing/unknown contact must not look like All clear.
            if lock_token == "unlocked":
                lock_failed.append(f"{name} (door contact unavailable)")
                seen_locks.add(lock_entity)
            continue
        if lock_entity == contact or _is_overhead(hass, lock_entity):
            _LOGGER.warning("Night Watchman will not lock %s", lock_entity)
            seen_locks.add(lock_entity)
            continue
        if lock_token != "unlocked":
            seen_locks.add(lock_entity)
            continue
        if await _lock_and_confirm(hass, lock_entity):
            locked.append(name)
            _stamp_self_action(hass, runtime, lock_entity, now)
        else:
            lock_failed.append(name)
        seen_locks.add(lock_entity)

    turned_off: list[str] = []
    if turn_off_lights:
        for entity_id in _turn_off_entities(options):
            state = hass.states.get(entity_id)
            if state is None or state.state != STATE_ON:
                continue
            domain = entity_id.split(".", 1)[0]
            if await _call_entity(hass, domain, "turn_off", entity_id):
                turned_off.append(state.name or entity_id)
                _stamp_self_action(hass, runtime, entity_id, now)

    parts: list[str] = []
    if locked:
        parts.append("Locked " + ", ".join(locked) + ".")
    if lock_failed:
        parts.append("Lock failed: " + ", ".join(lock_failed) + ".")
    if turned_off:
        parts.append("Turned off " + ", ".join(turned_off) + ".")
    if left_open:
        parts.append("Still open: " + ", ".join(left_open) + ".")
    if not parts:
        summary = "All clear." if turn_off_lights else "All clear. Doors checked; lights left for presence simulation."
    else:
        summary = " ".join(parts)
        if not turn_off_lights:
            summary += " Lights left for presence simulation."
    await _notify(hass, options, summary)
    return RoundOutcome(summary=summary, left_open=left_open)


async def _execute_round(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: NightWatchmanRuntime,
    now: datetime,
    *,
    turn_off_lights: bool,
    check_light_activity: bool,
) -> RoundOutcome:
    if entry.entry_id in _ACTIVE_ROUNDS:
        return RoundOutcome(summary="Skipped: another round is still running.")
    options = _settings(entry)
    _ACTIVE_ROUNDS.add(entry.entry_id)
    runtime.suppress_activity = True
    try:
        outcome = await _run_round(
            hass,
            entry,
            runtime,
            now,
            turn_off_lights=turn_off_lights,
            check_light_activity=check_light_activity,
        )
    except Exception:
        _LOGGER.exception("Night Watchman round failed")
        summary = "Round failed. Check the Home Assistant log."
        await _notify(hass, options, summary)
        outcome = RoundOutcome(summary=summary)
    finally:
        runtime.suppress_activity = False
        _ACTIVE_ROUNDS.discard(entry.entry_id)
    runtime.record_round(now, outcome.summary)
    return outcome


async def async_setup_entry(hass: HomeAssistant, entry: WatchmanConfigEntry) -> bool:
    """Set up one Night Watchman entry."""
    runtime = NightWatchmanRuntime(entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    def _quiet_seconds(options: dict[str, Any]) -> int:
        return _positive_int(options.get(CONF_QUIET_MINUTES), 45) * 60

    def _schedule_away_secure(options: dict[str, Any]) -> None:
        if runtime._away_retry is not None:
            return
        runtime._away_retry = async_call_later(hass, _quiet_seconds(options), _secure_away)

    async def _secure_away(_now: datetime | None = None) -> None:
        """Everyone left: one quiet secure round, then optionally arm and start Presence Simulation."""
        runtime._away_retry = None
        if not runtime.enabled:
            return
        options = _settings(entry)
        if not _away_enabled(options):
            return
        people = _away_people(options)
        if not people or not _everyone_away(hass, people):
            return
        if _away_already_secured(hass, runtime, options):
            runtime.away_secured = True
            return
        local = dt_util.as_local(dt_util.utcnow())
        outcome = await _execute_round(
            hass,
            entry,
            runtime,
            local,
            turn_off_lights=True,
            check_light_activity=True,
        )
        if outcome.skipped:
            _schedule_away_secure(options)
            return
        runtime.away_secured = True
        follow_ups: list[str] = []
        if _away_alarm(options):
            if outcome.left_open:
                follow_ups.append("Alarm not armed: a door is still open.")
                await _notify(hass, options, "Alarm not armed: a door is still open.")
            elif await _set_alarm(hass, options, True):
                runtime.away_alarm_managed = True
                follow_ups.append("Alarm armed.")
                await _notify(hass, options, "Alarm armed.")
            else:
                follow_ups.append("Alarm arm failed.")
                await _notify(hass, options, "Alarm arm failed.")
        if _presence_switch(options) and await _set_presence(hass, options, True):
            follow_ups.append("Presence simulation started.")
            await _notify(hass, options, "Presence simulation started.")
        if follow_ups:
            runtime.record_round(local, outcome.summary + " " + " ".join(follow_ups))

    async def _schedule_away_if_needed() -> None:
        """Start the away quiet wait when enabled, everyone is away, and not yet secured."""
        if not runtime.enabled:
            return
        options = _settings(entry)
        if not _away_enabled(options):
            return
        people = _away_people(options)
        if not people or not _everyone_away(hass, people):
            return
        if _away_already_secured(hass, runtime, options):
            runtime.away_secured = True
            return
        _schedule_away_secure(options)

    async def _enabled_changed(enabled: bool) -> None:
        if not enabled:
            runtime.cancel_away_retry()
            return
        await _schedule_away_if_needed()

    runtime.on_enabled_change = _enabled_changed

    async def _people_changed(_event: Event) -> None:
        options = _settings(entry)
        if not _away_enabled(options):
            return
        people = _away_people(options)
        if not people:
            return
        if _everyone_away(hass, people):
            if not runtime.enabled:
                return
            if _away_already_secured(hass, runtime, options):
                runtime.away_secured = True
                return
            _schedule_away_secure(options)
            return

        runtime.cancel_away_retry()
        presence_on = _presence_is_on(hass, options)
        should_disarm = _should_disarm_alarm(hass, runtime, options)
        was_secured = runtime.away_secured or presence_on or should_disarm
        runtime.away_secured = False
        if not was_secured:
            return
        if should_disarm and _away_alarm(options):
            if await _set_alarm(hass, options, False):
                _LOGGER.info("Night Watchman disarmed the alarm because someone came home")
                await _notify(hass, options, "Alarm disarmed.")
            runtime.away_alarm_managed = False
        if _presence_switch(options) and await _set_presence(hass, options, False):
            _LOGGER.info("Night Watchman stopped presence simulation because someone came home")
            await _notify(hass, options, "Presence simulation stopped.")

    async def _tick(now: datetime) -> None:
        """Night schedule only. Away securing is triggered by people leaving, not by the clock."""
        if not runtime.enabled:
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

        # Leave the house alone while the away path owns it (secured, or quiet wait in progress).
        if (
            _away_enabled(options)
            and _away_people(options)
            and _everyone_away(hass, _away_people(options))
            and (_away_already_secured(hass, runtime, options) or runtime.away_wait_pending)
        ):
            _LOGGER.debug("Night Watchman skipped the night round because everyone is away")
            return

        await _execute_round(
            hass,
            entry,
            runtime,
            local,
            turn_off_lights=True,
            check_light_activity=True,
        )

    entry.async_on_unload(async_track_time_change(hass, _tick, second=10))
    watched = _activity_entities(_settings(entry))
    if watched:

        @callback
        def _activity(event: Event) -> None:
            _remember_activity(runtime, event)

        entry.async_on_unload(async_track_state_change_event(hass, watched, _activity))

    people = _away_people(_settings(entry))
    if people:
        entry.async_on_unload(async_track_state_change_event(hass, people, _people_changed))

    entry.async_on_unload(entry.add_update_listener(_reload))
    entry.async_on_unload(runtime.cancel_away_retry)
    await _schedule_away_if_needed()
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None:
        runtime.cancel_away_retry()
    _ACTIVE_ROUNDS.discard(entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
