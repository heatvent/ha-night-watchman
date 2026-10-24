"""Button that runs one Night Watchman round immediately."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import watchman_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the manual round button."""
    async_add_entities([NightWatchmanRunButton(hass.data[DOMAIN][entry.entry_id])])


class NightWatchmanRunButton(ButtonEntity):
    """Run locks and lights now, without waiting for the quiet period."""

    _attr_has_entity_name = True
    _attr_name = "Run round"
    _attr_icon = "mdi:shield-refresh"

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_run_round"
        self._attr_device_info = watchman_device(runtime.entry.entry_id)

    async def async_press(self) -> None:
        """Start one round if Night Watchman is enabled."""
        if self._runtime.run_now is not None:
            await self._runtime.run_now()
