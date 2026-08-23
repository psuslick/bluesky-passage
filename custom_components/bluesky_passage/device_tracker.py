"""Current archived inReach position."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entity import BlueSkyEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    runtime = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities([BlueSkyTracker(runtime)])


class BlueSkyTracker(BlueSkyEntity, TrackerEntity):
    """The latest Garmin point, retained locally during source outages."""

    _attr_name = "inReach position"
    _attr_icon = "mdi:satellite-variant"

    def __init__(self, runtime) -> None:
        super().__init__(runtime, "inreach_position")

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return (self.runtime.latest or {}).get("latitude")

    @property
    def longitude(self) -> float | None:
        return (self.runtime.latest or {}).get("longitude")

    @property
    def location_accuracy(self) -> int:
        return 0

    @property
    def available(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def extra_state_attributes(self):
        latest = self.runtime.latest or {}
        return {
            "recorded_at_utc": latest.get("recorded_at_utc"),
            "valid_gps_fix": latest.get("valid_gps_fix"),
            "source": latest.get("source"),
            "source_event_id": latest.get("source_event_id"),
        }
