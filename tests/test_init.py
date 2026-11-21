"""Entry setup and unload."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.night_watchman.const import (
    CONF_END,
    CONF_INTERVAL,
    CONF_NOTIFY_SERVICE,
    CONF_START,
    DOMAIN,
)

DATA = {
    CONF_START: "23:00",
    CONF_END: "05:00",
    CONF_INTERVAL: 30,
    CONF_NOTIFY_SERVICE: "phones_group",
}


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Night Watchman", data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not None
    assert hass.states.get("switch.night_watchman_enabled") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
