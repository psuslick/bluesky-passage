"""Pure navigation calculations used by the archive and panel API.

These values are for supplementary situational awareness. They are not a
navigation solution and deliberately do not model currents, wind, hazards, or
a safe routed course.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import acos, asin, atan, atan2, cos, degrees, radians, sin, sqrt, tan
from statistics import median
from typing import Any, Iterable

EARTH_RADIUS_NM = 3440.065


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in nautical miles."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_NM * asin(min(1.0, sqrt(a)))


def initial_bearing_true(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the initial great-circle bearing in degrees true."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dlambda = radians(lon2 - lon1)
    y = sin(dlambda) * cos(phi2)
    x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dlambda)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


def cross_track_nm(
    start_lat: float,
    start_lon: float,
    destination_lat: float,
    destination_lon: float,
    point_lat: float,
    point_lon: float,
) -> float:
    """Return signed distance from the direct great-circle reference."""
    angular_distance = haversine_nm(
        start_lat, start_lon, point_lat, point_lon
    ) / EARTH_RADIUS_NM
    start_to_point = radians(
        initial_bearing_true(start_lat, start_lon, point_lat, point_lon)
    )
    start_to_destination = radians(
        initial_bearing_true(
            start_lat, start_lon, destination_lat, destination_lon
        )
    )
    return asin(
        sin(angular_distance) * sin(start_to_point - start_to_destination)
    ) * EARTH_RADIUS_NM


def _solar_event_utc(
    date_utc: datetime,
    latitude: float,
    longitude: float,
    *,
    rising: bool,
    zenith_degrees: float,
) -> datetime | None:
    """Approximate one solar event using the NOAA sunrise algorithm."""
    day = date_utc.timetuple().tm_yday
    longitude_hour = longitude / 15.0
    approximate = day + ((6 - longitude_hour) / 24 if rising else (18 - longitude_hour) / 24)
    mean_anomaly = 0.9856 * approximate - 3.289
    true_longitude = (
        mean_anomaly
        + 1.916 * sin(radians(mean_anomaly))
        + 0.020 * sin(radians(2 * mean_anomaly))
        + 282.634
    ) % 360
    right_ascension = degrees(atan(0.91764 * tan(radians(true_longitude)))) % 360
    right_ascension += (
        (true_longitude // 90) * 90 - (right_ascension // 90) * 90
    )
    right_ascension_hours = right_ascension / 15
    sin_declination = 0.39782 * sin(radians(true_longitude))
    cos_declination = cos(asin(sin_declination))
    denominator = cos_declination * cos(radians(latitude))
    if abs(denominator) < 1e-12:
        return None
    cosine_hour = (
        cos(radians(zenith_degrees))
        - sin_declination * sin(radians(latitude))
    ) / denominator
    if cosine_hour < -1 or cosine_hour > 1:
        return None
    hour_angle = 360 - degrees(acos(cosine_hour)) if rising else degrees(acos(cosine_hour))
    local_mean_time = (
        hour_angle / 15
        + right_ascension_hours
        - 0.06571 * approximate
        - 6.622
    )
    utc_hours = (local_mean_time - longitude_hour) % 24
    midnight = datetime(
        date_utc.year, date_utc.month, date_utc.day, tzinfo=timezone.utc
    )
    return midnight + timedelta(hours=utc_hours)


def daylight_at(
    moment_utc: datetime,
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    """Describe daylight/twilight at a destination for a UTC moment."""
    moment = parse_utc(moment_utc)
    events: list[tuple[str, datetime]] = []
    for offset in (-1, 0, 1):
        day = moment + timedelta(days=offset)
        for name, rising, zenith in (
            ("civil_dawn", True, 96.0),
            ("sunrise", True, 90.833),
            ("sunset", False, 90.833),
            ("civil_dusk", False, 96.0),
        ):
            event = _solar_event_utc(
                day,
                latitude,
                longitude,
                rising=rising,
                zenith_degrees=zenith,
            )
            if event:
                events.append((name, event))
    events.sort(key=lambda item: item[1])
    previous = next((item for item in reversed(events) if item[1] <= moment), None)
    next_event = next((item for item in events if item[1] > moment), None)
    previous_name = previous[0] if previous else None
    if previous_name in {"sunrise"}:
        state = "daylight"
    elif previous_name in {"civil_dawn", "sunset"}:
        state = "civil twilight"
    elif previous_name == "civil_dusk":
        state = "darkness"
    else:
        state = "unknown"
    nearby = {
        name: min(
            (event for event_name, event in events if event_name == name),
            key=lambda event: abs((event - moment).total_seconds()),
            default=None,
        )
        for name in ("civil_dawn", "sunrise", "sunset", "civil_dusk")
    }
    return {
        "state": state,
        "previous_event": previous_name,
        "next_event": next_event[0] if next_event else None,
        "next_event_utc": next_event[1].isoformat().replace("+00:00", "Z")
        if next_event
        else None,
        **{
            f"{name}_utc": event.isoformat().replace("+00:00", "Z")
            if event
            else None
            for name, event in nearby.items()
        },
    }


def cardinal(degrees_true: float | None) -> str | None:
    """Convert degrees true to a 16-point compass label."""
    if degrees_true is None:
        return None
    labels = (
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )
    return labels[int((degrees_true + 11.25) // 22.5) % 16]


def parse_utc(value: str | datetime) -> datetime:
    """Parse an ISO timestamp and always return an aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def path_distance_nm(points: Iterable[dict[str, Any]]) -> float:
    """Sum straight segments between recorded valid points.

    This is a lower-bound recorded-track distance; it cannot account for motion
    between reports.
    """
    total = 0.0
    previous: dict[str, Any] | None = None
    for point in points:
        if point.get("latitude") is None or point.get("longitude") is None:
            continue
        if previous is not None:
            total += haversine_nm(
                float(previous["latitude"]),
                float(previous["longitude"]),
                float(point["latitude"]),
                float(point["longitude"]),
            )
        previous = point
    return total


def closing_rate_kn(
    points: list[dict[str, Any]],
    destination_latitude: float,
    destination_longitude: float,
    window: timedelta = timedelta(hours=6),
) -> float | None:
    """Estimate closing rate using the median of consecutive range changes."""
    valid = [
        point
        for point in points
        if point.get("latitude") is not None
        and point.get("longitude") is not None
        and point.get("recorded_at_utc")
    ]
    if len(valid) < 2:
        return None
    newest = parse_utc(valid[-1]["recorded_at_utc"])
    cutoff = newest - window
    valid = [point for point in valid if parse_utc(point["recorded_at_utc"]) >= cutoff]
    if len(valid) < 2:
        return None

    rates: list[float] = []
    for previous, current in zip(valid, valid[1:]):
        previous_time = parse_utc(previous["recorded_at_utc"])
        current_time = parse_utc(current["recorded_at_utc"])
        hours = (current_time - previous_time).total_seconds() / 3600.0
        if hours <= 0 or hours > 3:
            continue
        previous_range = haversine_nm(
            float(previous["latitude"]),
            float(previous["longitude"]),
            destination_latitude,
            destination_longitude,
        )
        current_range = haversine_nm(
            float(current["latitude"]),
            float(current["longitude"]),
            destination_latitude,
            destination_longitude,
        )
        rates.append((previous_range - current_range) / hours)
    return median(rates) if rates else None


def destination_metrics(
    points: list[dict[str, Any]], destination: dict[str, Any] | None
) -> dict[str, Any]:
    """Calculate display metrics for the current destination."""
    result: dict[str, Any] = {
        "recorded_track_nm": round(path_distance_nm(points), 2),
        "range_nm": None,
        "bearing_true": None,
        "bearing_cardinal": None,
        "closing_rate_kn": None,
        "eta_utc": None,
        "eta_status": "No destination",
        "direct_reference_nm": None,
        "direct_progress_nm": None,
        "direct_progress_percent": None,
        "cross_track_nm": None,
        "cross_track_side": None,
        "daylight_at_eta": None,
    }
    valid = [
        point
        for point in points
        if point.get("latitude") is not None and point.get("longitude") is not None
    ]
    if not destination or not valid:
        if destination:
            result["eta_status"] = "No valid position"
        return result

    latest = valid[-1]
    destination_lat = float(destination["latitude"])
    destination_lon = float(destination["longitude"])
    distance = haversine_nm(
        float(latest["latitude"]),
        float(latest["longitude"]),
        destination_lat,
        destination_lon,
    )
    bearing = initial_bearing_true(
        float(latest["latitude"]),
        float(latest["longitude"]),
        destination_lat,
        destination_lon,
    )
    rate = closing_rate_kn(valid, destination_lat, destination_lon)
    result.update(
        {
            "range_nm": round(distance, 2),
            "bearing_true": round(bearing, 1),
            "bearing_cardinal": cardinal(bearing),
            "closing_rate_kn": round(rate, 2) if rate is not None else None,
        }
    )
    start = valid[0]
    direct_reference = haversine_nm(
        float(start["latitude"]),
        float(start["longitude"]),
        destination_lat,
        destination_lon,
    )
    signed_cross_track = cross_track_nm(
        float(start["latitude"]),
        float(start["longitude"]),
        destination_lat,
        destination_lon,
        float(latest["latitude"]),
        float(latest["longitude"]),
    )
    progress = direct_reference - distance
    result.update(
        {
            "direct_reference_nm": round(direct_reference, 2),
            "direct_progress_nm": round(progress, 2),
            "direct_progress_percent": round(progress / direct_reference * 100, 1)
            if direct_reference > 0
            else None,
            "cross_track_nm": round(abs(signed_cross_track), 2),
            "cross_track_side": "right" if signed_cross_track > 0 else "left"
            if signed_cross_track < 0
            else "on reference",
        }
    )
    if rate is None:
        result["eta_status"] = "Insufficient movement history"
    elif rate <= 0.2:
        result["eta_status"] = "Not currently closing"
    else:
        eta = parse_utc(latest["recorded_at_utc"]) + timedelta(hours=distance / rate)
        result["eta_utc"] = eta.isoformat().replace("+00:00", "Z")
        result["eta_status"] = "Estimated from recent closing rate"
        result["daylight_at_eta"] = daylight_at(
            eta, destination_lat, destination_lon
        )
    return result
