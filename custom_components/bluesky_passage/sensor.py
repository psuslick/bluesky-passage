"""Current and calculated BlueSky Passage sensors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .calculations import cardinal, parse_utc
from .const import DOMAIN
from .entity import BlueSkyEntity


@dataclass(frozen=True, kw_only=True)
class BlueSkySensorDescription(SensorEntityDescription):
    value_fn: Callable[[Any], Any]


def _latest(key: str):
    return lambda runtime: (runtime.latest or {}).get(key)


def _metrics(key: str):
    return lambda runtime: (runtime.coordinator.data or {}).get("metrics", {}).get(key)


SENSORS = (
    BlueSkySensorDescription(
        key="speed_over_ground",
        name="Speed over ground",
        icon="mdi:speedometer",
        native_unit_of_measurement="kn",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_latest("sog_kn"),
    ),
    BlueSkySensorDescription(
        key="course_over_ground",
        name="Course over ground true",
        icon="mdi:compass-outline",
        native_unit_of_measurement="°",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_latest("cog_true"),
    ),
    BlueSkySensorDescription(
        key="elevation",
        name="Elevation",
        icon="mdi:elevation-rise",
        native_unit_of_measurement="m",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_latest("elevation_m"),
    ),
    BlueSkySensorDescription(
        key="last_report",
        name="Last report",
        icon="mdi:clock-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda runtime: parse_utc(runtime.latest["recorded_at_utc"])
        if runtime.latest and runtime.latest.get("recorded_at_utc")
        else None,
    ),
    BlueSkySensorDescription(
        key="last_text",
        name="Last text",
        icon="mdi:message-text-outline",
        value_fn=lambda runtime: (runtime.coordinator.data or {}).get(
            "latest_message", {}
        ).get("text"),
    ),
    BlueSkySensorDescription(
        key="last_event",
        name="Last event",
        icon="mdi:message-badge-outline",
        value_fn=lambda runtime: (runtime.coordinator.data or {}).get(
            "latest_event", {}
        ).get("text"),
    ),
    BlueSkySensorDescription(
        key="tracking_status",
        name="Tracking status",
        icon="mdi:sail-boat",
        value_fn=lambda runtime: runtime.status,
    ),
    BlueSkySensorDescription(
        key="destination_range",
        name="Destination range",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement="nmi",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_metrics("range_nm"),
    ),
    BlueSkySensorDescription(
        key="closing_rate",
        name="Destination closing rate",
        icon="mdi:trending-up",
        native_unit_of_measurement="kn",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_metrics("closing_rate_kn"),
    ),
    BlueSkySensorDescription(
        key="estimated_arrival",
        name="Estimated arrival",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda runtime: parse_utc(
            (runtime.coordinator.data or {}).get("metrics", {})["eta_utc"]
        )
        if (runtime.coordinator.data or {}).get("metrics", {}).get("eta_utc")
        else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    runtime = hass.data[DOMAIN]["entries"][entry.entry_id]
    async_add_entities(
        [BlueSkySensor(runtime, description) for description in SENSORS]
    )


class BlueSkySensor(BlueSkyEntity, SensorEntity):
    """Generic current/metric sensor."""

    entity_description: BlueSkySensorDescription

    def __init__(self, runtime, description: BlueSkySensorDescription) -> None:
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        value = self.entity_description.value_fn(self.runtime)
        if isinstance(value, str) and len(value) > 255:
            return value[:252] + "..."
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key == "course_over_ground":
            return {"cardinal": cardinal(self.native_value)}
        if self.entity_description.key == "estimated_arrival":
            return {
                "method": (self.runtime.coordinator.data or {})
                .get("metrics", {})
                .get("eta_status")
            }
        return None
