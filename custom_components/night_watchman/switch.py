"""Switch that pauses Night Watchman without removing it."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .entity import watchman_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the pause switch."""
    async_add_entities([NightWatchmanSwitch(hass.data[DOMAIN][entry.entry_id])])


class NightWatchmanSwitch(SwitchEntity, RestoreEntity):
    """Turn this off to skip rounds. The sensors keep updating."""

    _attr_has_entity_name = True
    _attr_name = "Enabled"
    _attr_icon = "mdi:shield-account"

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_enabled"
        self._attr_device_info = watchman_device(runtime.entry.entry_id)
        self._attr_is_on = True

    async def async_added_to_hass(self) -> None:
        """Restore whether rounds were paused before the restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._runtime.enabled = last.state == "on"
        self._attr_is_on = self._runtime.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Let the scheduled rounds run."""
        self._runtime.enabled = True
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Skip rounds until this is turned back on."""
        self._runtime.enabled = False
        self._attr_is_on = False
        self.async_write_ha_state()
