"""Night Watchman secure rounds."""

from __future__ import annotations
import logging
from datetime import datetime, timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from .const import (
    CONF_CONTACT_ENTITY,
    CONF_LOCK_ENTITY,
    CONF_QUIET_MINUTES,
    LOCK_SLOTS,
    SUBENTRY_LOCK,
)
from .helpers import (
    _activity_entities,
    _home_mode_only_lights,
    _monitored_lights,
    _needs_immediate_notice,
    _positive_int,
    _settings,
    _slot_value,
    _turn_off_entities,
)
from .actions import (
    _is_overhead,
    _lock_and_confirm,
    _notify,
    _recent_activity_names,
    _state_token,
    _turn_off_and_confirm,
    _wait_for_state,
)
from .runtime import (
    NightWatchmanRuntime,
    RoundOutcome,
    _stamp_self_action,
)


_LOGGER = logging.getLogger(__name__)


_OPEN = frozenset({STATE_ON, "open"})


_CLOSED = frozenset({STATE_OFF, "closed"})


_ACTIVE_ROUNDS: set[str] = set()


async def _run_round(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: NightWatchmanRuntime,
    now: datetime,
    *,
    enforce_quiet: bool = True,
    lock_when_only_lights_active: bool = False,
    for_away: bool = False,
) -> RoundOutcome:
    """Run one round and return skipped, all clear, or what changed. The caller notifies."""
    options = _settings(entry)
    quiet = timedelta(minutes=_positive_int(options.get(CONF_QUIET_MINUTES), 45))
    # Keep-on-when-away lights and devices are turn-off lists only — never activity.
    home_only = set(_home_mode_only_lights(options))
    if enforce_quiet:
        occupied = _recent_activity_names(
            hass, _activity_entities(options, include_lights=False), quiet, now, runtime
        )
        monitored = [
            entity_id for entity_id in _monitored_lights(options) if entity_id not in home_only
        ]
        light_activity = _recent_activity_names(hass, monitored, quiet, now, runtime)
        if occupied or (light_activity and not lock_when_only_lights_active):
            recent = occupied or light_activity
            summary = "Skipped: the house is still active. Recent activity: " + ", ".join(recent) + "."
            return RoundOutcome(summary=summary)
    else:
        light_activity = []
    defer_lights = bool(enforce_quiet and light_activity and lock_when_only_lights_active)

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
            if lock_entity and lock_token != "locked":
                lock_failed.append(f"{name} (door contact unavailable)")
                seen_locks.add(lock_entity)
            continue
        if lock_entity == contact or _is_overhead(hass, lock_entity):
            _LOGGER.warning("Night Watchman will not lock %s", lock_entity)
            seen_locks.add(lock_entity)
            continue
        if lock_token == "locked":
            seen_locks.add(lock_entity)
            continue
        if lock_token == "locking":
            confirmed = await _wait_for_state(hass, lock_entity, frozenset({"locked"}))
            if not confirmed:
                confirmed = await _lock_and_confirm(hass, lock_entity, attempts=1)
        else:
            confirmed = await _lock_and_confirm(hass, lock_entity)
        if confirmed:
            locked.append(name)
            _stamp_self_action(hass, runtime, lock_entity, now)
        else:
            final = _state_token(hass, lock_entity) or lock_token
            detail = final if final and final not in {"unlocked", "locked"} else None
            lock_failed.append(f"{name} ({detail})" if detail else name)
        seen_locks.add(lock_entity)

    turned_off: list[str] = []
    turn_off_failed: list[str] = []
    left_on_away: list[str] = []
    if not defer_lights:
        for entity_id in _turn_off_entities(options, for_away=for_away):
            state = hass.states.get(entity_id)
            if state is None or state.state != STATE_ON:
                continue
            if await _turn_off_and_confirm(hass, entity_id):
                turned_off.append(state.name or entity_id)
                _stamp_self_action(hass, runtime, entity_id, now)
            else:
                turn_off_failed.append(state.name or entity_id)
        if for_away:
            for entity_id in home_only:
                state = hass.states.get(entity_id)
                if state is not None and state.state == STATE_ON:
                    left_on_away.append(state.name or entity_id)

    parts: list[str] = []
    if locked:
        parts.append("Locked " + ", ".join(locked) + ".")
    if lock_failed:
        parts.append("Lock failed: " + ", ".join(lock_failed) + ".")
    if turned_off:
        parts.append("Turned off " + ", ".join(turned_off) + ".")
    if turn_off_failed:
        parts.append("Turn off failed: " + ", ".join(turn_off_failed) + ".")
    if left_open:
        parts.append("Still open: " + ", ".join(left_open) + ".")
    if defer_lights:
        parts.append("Lights left on: recent activity: " + ", ".join(light_activity) + ".")
    if left_on_away:
        parts.append("Left on for away: " + ", ".join(left_on_away) + ".")
    if not parts:
        summary = "All clear."
    else:
        summary = " ".join(parts)
    return RoundOutcome(summary=summary, left_open=left_open, lock_failed=lock_failed)


async def _execute_round(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: NightWatchmanRuntime,
    now: datetime,
    *,
    enforce_quiet: bool,
    lock_when_only_lights_active: bool,
    notify_mode: str,
    for_away: bool = False,
) -> RoundOutcome:
    """notify_mode is always, failures, or none."""
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
            enforce_quiet=enforce_quiet,
            lock_when_only_lights_active=lock_when_only_lights_active,
            for_away=for_away,
        )
    except Exception:
        _LOGGER.exception("Night Watchman round failed")
        summary = "Round failed. Check the Home Assistant log."
        outcome = RoundOutcome(summary=summary)
    finally:
        runtime.suppress_activity = False
        _ACTIVE_ROUNDS.discard(entry.entry_id)
    runtime.record_round(now, outcome.summary)
    if notify_mode == "always" or (notify_mode == "failures" and _needs_immediate_notice(outcome.summary)):
        await _notify(hass, options, outcome.summary)
    return outcome
