"""Night Watchman option and schedule helpers (no Home Assistant state)."""

from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util
from .const import (
    AWAY_ALARM_MODES,
    CONF_ACTIVITY_DOORS,
    CONF_ACTIVITY_ENTITIES,
    CONF_ACTIVITY_LIGHTS,
    CONF_ACTIVITY_MOTION,
    CONF_ADDITIONAL_DEVICES,
    CONF_AWAY_ALARM,
    CONF_AWAY_ALARM_MODE,
    CONF_AWAY_ENABLED,
    CONF_AWAY_PEOPLE,
    CONF_DOORS_TO_LOCK,
    CONF_INCLUDE_ACTIVITY_LIGHTS,
    CONF_KEEP_ON_LIGHTS,
    CONF_LEAVE_ON_WHEN_AWAY,
    CONF_PRESENCE_SIMULATION,
    CONF_TURN_OFF_ADDITIONAL_DEVICES,
    CONF_TURN_OFF_AT_NIGHT,
    LEGACY_AWAY_KEEP_ON_LIGHTS,
    LEGACY_INCLUDE_HOME_MODE_ONLY_LIGHTS,
    LEGACY_INCLUDE_MONITORED_DEVICES,
    LEGACY_TURN_OFF_ENTITIES,
    DEFAULT_AWAY_ALARM_MODE,
)


_DAY_MINUTES = 24 * 60


_NOTIFY_SERVICE = re.compile(r"[a-z0-9_]+")


_TURN_OFF_DOMAINS = frozenset({"light", "switch"})


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


def _option_value(options: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Read the first present option key, including nested when_everyone_away."""
    for key in keys:
        if key in options:
            return options.get(key)
    section = options.get("when_everyone_away")
    if isinstance(section, dict):
        for key in keys:
            if key in section:
                return section.get(key)
    return default


def _home_mode_only_lights(options: dict[str, Any]) -> list[str]:
    """Lights turned off on night rounds only. Away securing leaves them alone."""
    value = _option_value(
        options, CONF_LEAVE_ON_WHEN_AWAY, LEGACY_AWAY_KEEP_ON_LIGHTS, default=[]
    )
    return _unique(_as_list(value))


def _monitored_devices(options: dict[str, Any]) -> list[str]:
    """Switches such as a coffee maker. Lights belong in the monitored light lists."""
    value = _option_value(
        options, CONF_ADDITIONAL_DEVICES, LEGACY_TURN_OFF_ENTITIES, default=[]
    )
    return [
        entity_id
        for entity_id in _unique(_as_list(value))
        if entity_id.startswith("switch.")
    ]


def _turn_off_entities(options: dict[str, Any], *, for_away: bool = False) -> list[str]:
    entities: list[str] = []
    if options.get(CONF_INCLUDE_ACTIVITY_LIGHTS, True):
        entities.extend(_monitored_lights(options))
    turn_off_at_night = _option_value(
        options,
        CONF_TURN_OFF_AT_NIGHT,
        LEGACY_INCLUDE_HOME_MODE_ONLY_LIGHTS,
        default=True,
    )
    if not for_away and turn_off_at_night:
        entities.extend(_home_mode_only_lights(options))
    turn_off_devices = _option_value(
        options,
        CONF_TURN_OFF_ADDITIONAL_DEVICES,
        LEGACY_INCLUDE_MONITORED_DEVICES,
        default=True,
    )
    if turn_off_devices:
        entities.extend(_monitored_devices(options))
        # Older installs may still list lights under the devices field.
        for entity_id in _as_list(
            _option_value(
                options, CONF_ADDITIONAL_DEVICES, LEGACY_TURN_OFF_ENTITIES, default=[]
            )
        ):
            if entity_id.startswith("light."):
                entities.append(entity_id)
    keep = set(_as_list(options.get(CONF_KEEP_ON_LIGHTS)))
    if for_away:
        keep.update(_home_mode_only_lights(options))
    return [
        entity_id
        for entity_id in _unique(entities)
        if entity_id not in keep and entity_id.split(".", 1)[0] in _TURN_OFF_DOMAINS
    ]


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


def _presence_switch(options: dict[str, Any]) -> str | None:
    section = _away_section(options)
    value = section.get(CONF_PRESENCE_SIMULATION)
    if not isinstance(value, str) or not value:
        value = options.get(CONF_PRESENCE_SIMULATION)
    if isinstance(value, str) and value.startswith("switch."):
        return value
    return None


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


def _needs_immediate_notice(summary: str) -> bool:
    """Night rounds speak up right away only when something needs attention."""
    markers = ("Lock failed", "Turn off failed", "Still open", "Round failed")
    return any(marker in summary for marker in markers)


def _last_due_minute(start: int, end: int, interval: int) -> int | None:
    """The last clock minute inside the window that lands on the interval."""
    if interval <= 0:
        return None
    span = (end - start) % _DAY_MINUTES if end < start else end - start
    last_elapsed = span - (span % interval)
    return (start + last_elapsed) % _DAY_MINUTES


def _clock_label(when: datetime) -> str:
    local = dt_util.as_local(when)
    hour = str(int(local.strftime("%I")))
    return f"{hour}:{local.strftime('%M %p')}"


def _window_start(local: datetime, start: int, end: int) -> datetime:
    start_today = local.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
    now_m = local.hour * 60 + local.minute
    if end < start and now_m <= end:
        return start_today - timedelta(days=1)
    return start_today
