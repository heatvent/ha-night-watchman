"""Shared device info for Night Watchman entities."""

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def watchman_device(entry_id: str) -> DeviceInfo:
    """Group the switch and sensors under one device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="Night Watchman",
        manufacturer="Night Watchman",
    )
