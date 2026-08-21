"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION


class BlueSkyEntity(CoordinatorEntity):
    """An entity backed by the archived Garmin snapshot."""

    _attr_has_entity_name = True

    def __init__(self, runtime, key: str) -> None:
        super().__init__(runtime.coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry.entry_id)},
            name="BlueSky Passage",
            manufacturer="BlueSky Passage",
            model="Archived Garmin MapShare tracker",
            sw_version=VERSION,
            configuration_url=f"https://share.garmin.com/{runtime.entry.data['link_name']}",
        )
