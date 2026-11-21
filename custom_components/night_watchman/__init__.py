"""Night Watchman rounds."""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from .const import (
    CONF_END,
    CONF_INTERVAL,
    CONF_START,
    DOMAIN,
    PLATFORMS,
)
from .helpers import (
    _activity_entities,
    _away_alarm,
    _away_enabled,
    _away_people,
    _clock_label,
    _last_due_minute,
    _minutes,
    _positive_int,
    _presence_switch,
    _settings,
    _window_start,
    round_is_due,
)
from .actions import (
    _everyone_away,
    _notify,
    _presence_is_on,
    _set_alarm,
    _set_presence,
)
from .runtime import (
    NightWatchmanRuntime,
    _away_already_secured,
    _remaining_quiet_seconds,
    _remember_activity,
    _should_disarm_alarm,
)
from .rounds import (
    _ACTIVE_ROUNDS,
    _execute_round,
)


_LOGGER = logging.getLogger(__name__)


_LOCK_RETRY_SECONDS = 15 * 60


_AWAY_QUIET_FLOOR_SECONDS = 60


type WatchmanConfigEntry = ConfigEntry["NightWatchmanRuntime"]


async def async_setup_entry(hass: HomeAssistant, entry: WatchmanConfigEntry) -> bool:
    """Set up one Night Watchman entry."""
    runtime = NightWatchmanRuntime(entry)
    entry.runtime_data = runtime
    store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
    stored = await store.async_load()
    if isinstance(stored, dict):
        runtime.away_alarm_managed = bool(stored.get("away_alarm_managed"))
        night_log = stored.get("night_log")
        if isinstance(night_log, list):
            runtime.night_log = [
                item
                for item in night_log
                if isinstance(item, dict)
                and isinstance(item.get("at"), str)
                and isinstance(item.get("summary"), str)
            ]

    async def _save_state() -> None:
        await store.async_save(
            {"away_alarm_managed": runtime.away_alarm_managed, "night_log": runtime.night_log}
        )

    runtime.save_state = _save_state

    def _schedule_away_secure(options: dict[str, Any], *, minimum: int = _AWAY_QUIET_FLOOR_SECONDS) -> None:
        if runtime._away_retry is not None:
            return
        delay = max(minimum, _remaining_quiet_seconds(hass, options, runtime))
        runtime._away_retry = async_call_later(hass, delay, _secure_away)

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
            enforce_quiet=True,
            lock_when_only_lights_active=False,
            notify_mode="none",
            for_away=True,
        )
        if not outcome.secure:
            if outcome.summary != runtime.last_away_problem:
                runtime.last_away_problem = outcome.summary
                await _notify(hass, options, outcome.summary)
            retry_floor = _AWAY_QUIET_FLOOR_SECONDS if outcome.skipped else _LOCK_RETRY_SECONDS
            _schedule_away_secure(options, minimum=retry_floor)
            return
        runtime.last_away_problem = None
        runtime.away_secured = True
        await _notify(hass, options, outcome.summary)
        follow_ups: list[str] = []
        if _away_alarm(options):
            if await _set_alarm(hass, options, True):
                runtime.away_alarm_managed = True
                await _save_state()
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
        runtime.last_away_problem = None
        presence_on = _presence_is_on(hass, options)
        should_disarm = _should_disarm_alarm(runtime)
        was_secured = runtime.away_secured or presence_on or should_disarm
        runtime.away_secured = False
        if not was_secured:
            return
        if should_disarm and _away_alarm(options):
            if await _set_alarm(hass, options, False):
                runtime.away_alarm_managed = False
                await _save_state()
                _LOGGER.info("Night Watchman disarmed the alarm because someone came home")
                await _notify(hass, options, "Alarm disarmed.")
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
        now_m = local.hour * 60 + local.minute
        if not round_is_due(now_m, start, end, interval):
            return
        last_due = _last_due_minute(start, end, interval)
        is_last = last_due is not None and now_m == last_due
        away_owns = (
            _away_enabled(options)
            and bool(_away_people(options))
            and _everyone_away(hass, _away_people(options))
            and (_away_already_secured(hass, runtime, options) or runtime.away_wait_pending)
        )
        if away_owns:
            _LOGGER.debug("Night Watchman skipped the night round because everyone is away")
            if is_last:
                await _send_night_summary(options, local, start, end, away=True)
            return

        outcome = await _execute_round(
            hass,
            entry,
            runtime,
            local,
            enforce_quiet=True,
            lock_when_only_lights_active=True,
            notify_mode="failures",
        )
        await _remember_night_round(local, outcome.summary, start, end)
        if is_last:
            await _send_night_summary(options, local, start, end, away=False)

    async def _remember_night_round(local: datetime, summary: str, start: int, end: int) -> None:
        window_start = _window_start(local, start, end)
        kept: list[dict[str, str]] = []
        for item in runtime.night_log:
            parsed = dt_util.parse_datetime(item["at"])
            if parsed is not None and dt_util.as_local(parsed) >= window_start:
                kept.append(item)
        kept.append({"at": dt_util.as_local(local).isoformat(), "summary": summary})
        runtime.night_log = kept[-12:]
        await _save_state()

    async def _send_night_summary(
        options: dict[str, Any], local: datetime, start: int, end: int, *, away: bool
    ) -> None:
        window_start = _window_start(local, start, end)
        lines: list[str] = []
        for item in runtime.night_log:
            parsed = dt_util.parse_datetime(item["at"])
            if parsed is None or dt_util.as_local(parsed) < window_start:
                continue
            lines.append(f"{_clock_label(parsed)}: {item['summary']}")
        if lines:
            message = "Night summary\n" + "\n".join(lines)
        elif away:
            message = "Night summary: no rounds ran. Everyone was away."
        else:
            message = "Night summary: no rounds ran."
        await _notify(hass, options, message)
        runtime.night_log = []
        await _save_state()

    async def _run_now() -> None:
        """Manual round. Skips the quiet gate so locks and lights are exercised now."""
        if not runtime.enabled:
            await _notify(hass, _settings(entry), "Skipped: Night Watchman is paused.")
            return
        await _execute_round(
            hass,
            entry,
            runtime,
            dt_util.as_local(dt_util.utcnow()),
            enforce_quiet=False,
            lock_when_only_lights_active=False,
            notify_mode="always",
        )

    runtime.run_now = _run_now

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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _schedule_away_if_needed()
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WatchmanConfigEntry) -> bool:
    """Unload a config entry."""
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        runtime.cancel_away_retry()
    _ACTIVE_ROUNDS.discard(entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
