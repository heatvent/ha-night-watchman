"""Night Watchman per-entry runtime state."""

from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util
from .const import (
    CONF_QUIET_MINUTES,
)
from .helpers import (
    _activity_entities,
    _home_mode_only_lights,
    _positive_int,
)
from .actions import (
    _IGNORED_ACTIVITY,
    _SELF_ACTION_GRACE,
    _activity_stamp,
    _alarm_matches_away_mode,
    _presence_is_on,
)


@dataclass
class RoundOutcome:
    """Result of one secure round."""

    summary: str
    left_open: list[str] = field(default_factory=list)
    lock_failed: list[str] = field(default_factory=list)

    @property
    def skipped(self) -> bool:
        return self.summary.startswith("Skipped:")

    @property
    def secure(self) -> bool:
        """True when doors were handled and no lock failed."""
        if self.skipped or self.left_open or self.lock_failed:
            return False
        return not self.summary.startswith("Round failed")


def _away_already_secured(hass: HomeAssistant, runtime: NightWatchmanRuntime, options: dict[str, Any]) -> bool:
    """True when this away pass already finished, or PS/alarm still show the house secured."""
    return (
        runtime.away_secured
        or _presence_is_on(hass, options)
        or _alarm_matches_away_mode(hass, options)
    )


def _should_disarm_alarm(runtime: NightWatchmanRuntime) -> bool:
    """Disarm only an alarm Night Watchman itself armed. The flag survives a restart."""
    return runtime.away_alarm_managed


class NightWatchmanRuntime:
    """Shared state for the switch, sensors, and the night rounds."""

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self.enabled = True
        self.suppress_activity = False
        self.away_secured = False
        self.away_alarm_managed = False
        self.started_at = dt_util.as_local(dt_util.utcnow())
        self.activity_times: dict[str, datetime] = {}
        self.self_actions: dict[str, datetime] = {}
        self.night_log: list[dict[str, str]] = []
        self.last_away_problem: str | None = None
        self._away_retry: Callable[[], None] | None = None
        self.on_enabled_change: Callable[[bool], Awaitable[None]] | None = None
        self.run_now: Callable[[], Awaitable[None]] | None = None
        self.save_state: Callable[[], Awaitable[None]] | None = None
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
        self.activity_times.pop(entity_id, None)
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
    when = dt_util.as_local(new_state.last_changed or dt_util.utcnow())
    acted = runtime.self_actions.get(new_state.entity_id)
    if acted is not None and when <= acted + _SELF_ACTION_GRACE:
        return
    runtime.activity_times[new_state.entity_id] = when
    runtime.record_activity(
        new_state.entity_id,
        new_state.name or new_state.entity_id,
        when,
    )


def _stamp_self_action(hass: HomeAssistant, runtime: NightWatchmanRuntime, entity_id: str, now: datetime) -> None:
    state = hass.states.get(entity_id)
    when = dt_util.as_local(state.last_changed) if state is not None and state.last_changed else now
    runtime.note_self_action(entity_id, when)


def _remaining_quiet_seconds(hass: HomeAssistant, options: dict[str, Any], runtime: NightWatchmanRuntime) -> int:
    """Seconds still needed before the house counts as quiet. Zero when it already is."""
    quiet_seconds = _positive_int(options.get(CONF_QUIET_MINUTES), 45) * 60
    now = dt_util.as_local(dt_util.utcnow())
    ignore = set(_home_mode_only_lights(options))
    latest: datetime | None = None
    for entity_id in _activity_entities(options):
        if entity_id in ignore:
            continue
        stamp = _activity_stamp(hass, entity_id, runtime)
        if stamp is not None and (latest is None or stamp > latest):
            latest = stamp
    if latest is None:
        return 0
    remaining = quiet_seconds - (now - latest).total_seconds()
    return max(0, int(remaining))
