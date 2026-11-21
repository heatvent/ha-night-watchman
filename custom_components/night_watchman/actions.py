"""Night Watchman calls into Home Assistant: entities, locks, alarm, presence, notify."""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from homeassistant.const import STATE_HOME, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from .const import (
    CONF_NOTIFY_SERVICE,
)
from .helpers import (
    _away_alarm,
    _away_alarm_mode,
    _notify_service_names,
    _presence_switch,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import NightWatchmanRuntime


_LOGGER = logging.getLogger(__name__)


_HOME_STATES = frozenset({STATE_HOME, "home"})


_IGNORED_ACTIVITY = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN, "none", ""})


_SELF_ACTION_GRACE = timedelta(seconds=5)


_ALARM_ARMED_PREFIX = "armed_"


_ALARM_ARM_SERVICES = {
    "away": "alarm_arm_away",
    "home": "alarm_arm_home",
    "night": "alarm_arm_night",
    "vacation": "alarm_arm_vacation",
}


_ALARM_CONFIRM_ATTEMPTS = 12


_ALARM_CONFIRM_DELAY = 0.25


_LOCK_CONFIRM_ATTEMPTS = 20


_LOCK_CONFIRM_DELAY = 0.5


def _activity_stamp(
    hass: HomeAssistant, entity_id: str, runtime: NightWatchmanRuntime
) -> datetime | None:
    """When this entity last changed for real.

    Changes observed after startup come from state events. A last_changed written
    after startup without an event is an integration refresh and does not count.
    """
    recorded = runtime.activity_times.get(entity_id)
    if recorded is not None:
        acted = runtime.self_actions.get(entity_id)
        if acted is not None and recorded <= acted + _SELF_ACTION_GRACE:
            return None
        return recorded
    state = hass.states.get(entity_id)
    if state is None or state.last_changed is None or runtime.started_at is None:
        return None
    changed = dt_util.as_local(state.last_changed)
    if changed > runtime.started_at:
        return None
    return changed


def _changed_recently(
    hass: HomeAssistant,
    entity_id: str,
    quiet: timedelta,
    now: datetime,
    runtime: NightWatchmanRuntime,
) -> bool:
    """True when this entity changed recently for a reason other than Night Watchman."""
    changed = _activity_stamp(hass, entity_id, runtime)
    return changed is not None and changed > now - quiet


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


async def _wait_for_state(hass: HomeAssistant, entity_id: str, expected: frozenset[str]) -> bool:
    for _ in range(_LOCK_CONFIRM_ATTEMPTS):
        await asyncio.sleep(_LOCK_CONFIRM_DELAY)
        state = hass.states.get(entity_id)
        if state is not None and state.state.strip().casefold() in expected:
            return True
    return False


async def _lock_and_confirm(hass: HomeAssistant, entity_id: str, attempts: int = 2) -> bool:
    """Call lock.lock and wait until the entity reports locked. Retries once by default."""
    for _attempt in range(attempts):
        if not await _call_entity(hass, "lock", "lock", entity_id):
            continue
        if await _wait_for_state(hass, entity_id, frozenset({"locked"})):
            return True
    _LOGGER.error("Night Watchman lock %s did not report locked", entity_id)
    return False


async def _turn_off_and_confirm(hass: HomeAssistant, entity_id: str) -> bool:
    """Turn an entity off and wait until it no longer reports on."""
    domain = entity_id.split(".", 1)[0]
    if not await _call_entity(hass, domain, "turn_off", entity_id):
        return False
    if await _wait_for_state(hass, entity_id, frozenset({STATE_OFF, "off", "closed"})):
        return True
    state = hass.states.get(entity_id)
    if state is not None and state.state != STATE_ON:
        return True
    _LOGGER.error("Night Watchman %s stayed on", entity_id)
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


def _everyone_away(hass: HomeAssistant, people: list[str]) -> bool:
    """True when nobody watched is home. A named zone such as Work counts as away."""
    if not people:
        return False
    for entity_id in people:
        state = hass.states.get(entity_id)
        if state is None or state.state in _IGNORED_ACTIVITY:
            return False
        if state.state.strip().casefold() in _HOME_STATES:
            return False
    return True


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


def _alarm_matches_away_mode(hass: HomeAssistant, options: dict[str, Any]) -> bool:
    """True when the selected alarm is armed in the configured away mode (not a night/home arm)."""
    alarm_id = _away_alarm(options)
    if not alarm_id:
        return False
    state = hass.states.get(alarm_id)
    if state is None:
        return False
    return state.state.strip().casefold() == f"armed_{_away_alarm_mode(options)}"


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


def _notify_entity_id(hass: HomeAssistant, name: str) -> str | None:
    """Resolve a notify entity id such as email_group -> notify.email_group."""
    for candidate in (name if name.startswith("notify.") else f"notify.{name}", name):
        state = hass.states.get(candidate)
        if state is not None and state.domain == "notify":
            return state.entity_id
    return None


async def _notify(hass: HomeAssistant, options: dict[str, Any], message: str) -> None:
    """Send a notice after every round to each configured notify service or entity."""
    targets = _notify_service_names(options.get(CONF_NOTIFY_SERVICE))
    if not targets:
        _LOGGER.error("Night Watchman notify service is not a plain service name")
        return
    payload = {"title": "Night watchman", "message": message}
    for name in targets:
        try:
            if hass.services.has_service("notify", name):
                await hass.services.async_call("notify", name, payload, blocking=False)
                continue
            entity_id = _notify_entity_id(hass, name)
            if entity_id is not None and hass.services.has_service("notify", "send_message"):
                # Match script.send_notifications: entity notifiers get message only.
                await hass.services.async_call(
                    "notify",
                    "send_message",
                    {"message": f"Night watchman: {message}"},
                    target={"entity_id": entity_id},
                    blocking=False,
                )
                continue
            _LOGGER.error(
                "Night Watchman notify target %s is not an available notify service or entity",
                name,
            )
        except Exception:
            _LOGGER.exception("Night Watchman could not notify %s", name)
