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


async def test_run_round_with_a_lock(hass: HomeAssistant) -> None:
    """Pressing Run round locks an unlocked door and records the result."""
    from pytest_homeassistant_custom_component.common import async_mock_service

    from custom_components.night_watchman.const import CONF_DOORS_TO_LOCK

    hass.states.async_set("lock.front_door", "unlocked")
    hass.states.async_set("binary_sensor.front_door", "off")
    locked = []

    async def _lock(call):
        locked.append(call.data["entity_id"])
        hass.states.async_set("lock.front_door", "locked")

    hass.services.async_register("lock", "lock", _lock)
    async_mock_service(hass, "notify", "phones_group")

    data = {**DATA, CONF_DOORS_TO_LOCK: {"lock_entity_1": "lock.front_door", "contact_entity_1": "binary_sensor.front_door"}}
    entry = MockConfigEntry(domain=DOMAIN, title="Night Watchman", data=data, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await entry.runtime_data.run_now()
    await hass.async_block_till_done()

    assert entry.runtime_data.last_round is not None
    assert entry.runtime_data.last_round_summary
    assert locked == ["lock.front_door"], entry.runtime_data.last_round_summary
    assert hass.states.get("lock.front_door").state == "locked"
