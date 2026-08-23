"""Garmin MapShare KML and portable import parsing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

try:
    import defusedxml.ElementTree as ET
except ImportError:  # Standalone bundle tests; Home Assistant ships defusedxml.
    import xml.etree.ElementTree as ET


class KmlParseError(ValueError):
    """Raised when a feed contains no usable records."""


@dataclass(slots=True)
class TrackRecord:
    """One immutable source record."""

    recorded_at_utc: str
    latitude: float | None
    longitude: float | None
    source_event_id: str | None = None
    device_imei: str | None = None
    device_name: str | None = None
    device_type: str | None = None
    elevation_m: float | None = None
    sog_kn: float | None = None
    cog_true: float | None = None
    valid_gps_fix: bool | None = None
    in_emergency: bool | None = None
    event_text: str | None = None
    message_text: str | None = None
    spatial_ref: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping."""
        return asdict(self)

    def dedupe_key(self, source: str) -> str:
        """Build a stable source-scoped key."""
        if self.source_event_id:
            identity = f"{self.device_imei or ''}|{self.source_event_id}"
        else:
            identity = "|".join(
                (
                    self.device_imei or "",
                    self.recorded_at_utc,
                    str(self.latitude),
                    str(self.longitude),
                    self.event_text or "",
                    self.message_text or "",
                )
            )
        return hashlib.sha256(f"{source}|{identity}".encode()).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _number(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group()) if match else None


def _boolean(value: Any) -> bool | None:
    text = (_text(value) or "").lower()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    return None


def _timestamp(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    parsed: datetime | None = None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in (
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _speed_knots(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    unit = (_text(value) or "").lower()
    if "km/h" in unit or "kph" in unit:
        return number / 1.852
    if "mph" in unit:
        return number / 1.150779448
    if "m/s" in unit:
        return number * 1.943844492
    if "ft/s" in unit:
        return number * 0.592483801
    return number


def _elevation_m(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    unit = (_text(value) or "").lower()
    return number * 0.3048 if "ft" in unit or "feet" in unit else number


def _record_from_values(values: dict[str, Any]) -> TrackRecord | None:
    timestamp = _timestamp(
        values.get("Time UTC")
        or values.get("timestamp")
        or values.get("time")
        or values.get("recorded_at_utc")
        or values.get("t")
    )
    if timestamp is None and isinstance(values.get("t"), (int, float)):
        raw_epoch = float(values["t"])
        if raw_epoch > 10_000_000_000:
            raw_epoch /= 1000
        timestamp = datetime.fromtimestamp(raw_epoch, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    if timestamp is None:
        return None

    def first_present(*keys: str) -> Any:
        return next((values[key] for key in keys if key in values and values[key] is not None), None)

    latitude = _number(first_present("Latitude", "latitude", "lat"))
    longitude = _number(first_present("Longitude", "longitude", "lon", "lng"))
    position = values.get("p")
    if (latitude is None or longitude is None) and isinstance(position, (list, tuple)) and len(position) >= 2:
        first, second = _number(position[0]), _number(position[1])
        if first is not None and second is not None:
            # PredictWind has historically used [latitude, longitude].
            latitude, longitude = first, second
    elif (latitude is None or longitude is None) and isinstance(position, dict):
        if latitude is None:
            latitude = _number(
                position.get("latitude")
                if "latitude" in position
                else position.get("lat")
            )
        if longitude is None:
            longitude = _number(
                position.get("longitude")
                if "longitude" in position
                else position.get("lon", position.get("lng"))
            )
    elif (latitude is None or longitude is None) and isinstance(position, str):
        components = [component.strip() for component in position.split(",")]
        if len(components) >= 2:
            latitude, longitude = _number(components[0]), _number(components[1])

    return TrackRecord(
        recorded_at_utc=timestamp,
        latitude=latitude,
        longitude=longitude,
        source_event_id=_text(first_present("Id", "id", "source_event_id")),
        device_imei=_text(first_present("IMEI", "imei")),
        device_name=_text(
            first_present("Map Display Name", "Name", "device_name")
        ),
        device_type=_text(first_present("Device Type", "device_type")),
        elevation_m=_elevation_m(first_present("Elevation", "elevation")),
        sog_kn=_speed_knots(
            first_present("Velocity", "velocity", "sog_kn", "bsp")
        ),
        cog_true=_number(
            first_present("Course", "course", "cog_true", "bearing")
        ),
        valid_gps_fix=_boolean(first_present("Valid GPS Fix", "valid_gps_fix")),
        in_emergency=_boolean(first_present("In Emergency", "in_emergency")),
        event_text=_text(first_present("Event", "event_text", "event")),
        message_text=_text(
            first_present("Text", "Message", "message_text")
        ),
        spatial_ref=_text(first_present("SpatialRefSystem", "spatial_ref")),
        raw=dict(values),
    )


def parse_kml(body: str | bytes, *, allow_empty: bool = False) -> list[TrackRecord]:
    """Parse every ExtendedData record in a Garmin KML feed.

    A syntactically valid KML document may legitimately contain no timestamped
    records for a bounded historical interval. ``allow_empty`` distinguishes
    that case from malformed/non-KML responses without weakening live-feed
    validation.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as err:
        raise KmlParseError("The Garmin response was not valid KML") from err

    if _local_name(root.tag).lower() != "kml":
        raise KmlParseError("The Garmin response was XML but not a KML document")

    records: list[TrackRecord] = []
    for element in root.iter():
        if _local_name(element.tag) != "ExtendedData":
            continue
        values: dict[str, Any] = {}
        for child in element.iter():
            child_name = _local_name(child.tag)
            if child_name not in {"Data", "SimpleData"}:
                continue
            key = child.attrib.get("name")
            if not key:
                continue
            if child_name == "Data":
                value = next(
                    (
                        node.text
                        for node in child
                        if _local_name(node.tag) == "value"
                    ),
                    child.text,
                )
            else:
                value = child.text
            values[key] = value
        record = _record_from_values(values)
        if record is not None:
            records.append(record)

    if not records and not allow_empty:
        raise KmlParseError("The Garmin feed contained no timestamped records")
    records.sort(key=lambda record: record.recorded_at_utc)
    return records


def records_from_mappings(mappings: list[dict[str, Any]]) -> list[TrackRecord]:
    """Normalize imported JSON/GeoJSON/PredictWind mappings."""
    records: list[TrackRecord] = []
    for mapping in mappings:
        values: dict[str, Any]
        if mapping.get("type") == "Feature":
            values = dict(mapping.get("properties") or {})
            geometry = mapping.get("geometry") or {}
            coordinates = geometry.get("coordinates") or []
            if geometry.get("type") == "Point" and len(coordinates) >= 2:
                values.setdefault("longitude", coordinates[0])
                values.setdefault("latitude", coordinates[1])
        else:
            values = dict(mapping)
        record = _record_from_values(values)
        if record is not None:
            records.append(record)
    records.sort(key=lambda record: record.recorded_at_utc)
    return records


def raw_json(record: TrackRecord) -> str:
    """Serialize source values deterministically."""
    return json.dumps(record.raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
