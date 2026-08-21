"""BlueSky Passage health and safety binary sensors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entity import BlueSkyEntity


@dataclass(frozen=True, kw_only=True)
class BlueSkyBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Any], bool | None]


BINARY_SENSORS = (
    BlueSkyBinaryDescription(
        key="valid_gps_fix",
        name="Valid GPS fix",
        icon="mdi:crosshairs-gps",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda runtime: (runtime.latest or {}).get("valid_gps_fix"),
    ),
    BlueSkyBinaryDescription(
        key="in_emergency",
        name="In emergency",
        icon="mdi:alert-octagon",
        device_class=BinarySensorDeviceClass.SAFETY,
        value_fn=lambda runtime: (runtime.latest or {}).get("in_emergency"),
    ),
    BlueSkyBinaryDescription(
        key="tracking_stale",
        name="Tracking stale",
        icon="mdi:timer-alert-outline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda runtime: runtime.is_stale,
    ),
    BlueSkyBinaryDescription(
        key="source_available",
        name="Garmin source available",
        icon="mdi:cloud-check-outline",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda runtime: runtime.coordinator.source_available,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    runtime = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities(
        [BlueSkyBinarySensor(runtime, description) for description in BINARY_SENSORS]
    )


class BlueSkyBinarySensor(BlueSkyEntity, BinarySensorEntity):
    entity_description: BlueSkyBinaryDescription

    def __init__(self, runtime, description: BlueSkyBinaryDescription) -> None:
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.runtime)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key == "tracking_stale":
            return {
                "threshold_minutes": self.runtime.stale_minutes,
                "report_age_minutes": round(self.runtime.report_age_minutes, 1)
                if self.runtime.report_age_minutes is not None
                else None,
            }
        if self.entity_description.key == "source_available":
            return {
                "last_success_utc": self.runtime.coordinator.last_poll_success_utc,
                "consecutive_failures": self.runtime.coordinator.consecutive_failures,
            }
        return None
