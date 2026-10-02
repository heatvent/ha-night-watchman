"""Feedback sensors for the last round and the last activity."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import watchman_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the feedback sensors."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LastRoundSensor(runtime),
            LastActivitySensor(runtime),
            LastActiveDeviceSensor(runtime),
        ]
    )


def _restored_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return dt_util.as_local(value)
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is not None:
            return dt_util.as_local(parsed)
    return None


class LastRoundSensor(RestoreSensor):
    """When the last scheduled round was checked, including skipped and all clear."""

    _attr_has_entity_name = True
    _attr_name = "Last round"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check"

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_last_round"
        self._attr_device_info = watchman_device(runtime.entry.entry_id)

    @property
    def native_value(self) -> datetime | None:
        return self._runtime.last_round

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        if not self._runtime.last_round_summary:
            return {}
        return {"result": self._runtime.last_round_summary}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._runtime.add_listener(self.async_write_ha_state))
        stored = await self.async_get_last_sensor_data()
        if stored is not None:
            when = _restored_time(stored.native_value)
            if when is not None:
                self._runtime.last_round = when
                last = await self.async_get_last_state()
                if last is not None and isinstance(last.attributes.get("result"), str):
                    self._runtime.last_round_summary = last.attributes["result"]
        self.async_write_ha_state()


class LastActivitySensor(RestoreSensor):
    """When a monitored device last changed. The card shows how long ago that was."""

    _attr_has_entity_name = True
    _attr_name = "Last activity"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:history"

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_last_activity"
        self._attr_device_info = watchman_device(runtime.entry.entry_id)

    @property
    def native_value(self) -> datetime | None:
        return self._runtime.last_activity

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        if not self._runtime.last_activity_entity:
            return {}
        return {
            "entity_id": self._runtime.last_activity_entity,
            "device": self._runtime.last_activity_name or self._runtime.last_activity_entity,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._runtime.add_listener(self.async_write_ha_state))
        stored = await self.async_get_last_sensor_data()
        if stored is not None:
            when = _restored_time(stored.native_value)
            if when is not None:
                self._runtime.last_activity = when
        self.async_write_ha_state()


class LastActiveDeviceSensor(SensorEntity, RestoreEntity):
    """Name of the monitored device that changed most recently."""

    _attr_has_entity_name = True
    _attr_name = "Last active device"
    _attr_icon = "mdi:motion-sensor"

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_last_active_device"
        self._attr_device_info = watchman_device(runtime.entry.entry_id)

    @property
    def native_value(self) -> str | None:
        return self._runtime.last_activity_name

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        if not self._runtime.last_activity_entity:
            return {}
        return {"entity_id": self._runtime.last_activity_entity}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._runtime.add_listener(self.async_write_ha_state))
        last = await self.async_get_last_state()
        if last is not None and last.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            if self._runtime.last_activity_name is None:
                self._runtime.last_activity_name = last.state
            entity_id = last.attributes.get("entity_id")
            if isinstance(entity_id, str) and self._runtime.last_activity_entity is None:
                self._runtime.last_activity_entity = entity_id
        self.async_write_ha_state()
