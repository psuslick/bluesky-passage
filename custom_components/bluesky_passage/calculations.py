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


def _local_xy_nm(
    latitude: float,
    longitude: float,
    origin_latitude: float,
    origin_longitude: float,
) -> tuple[float, float]:
    """Project a nearby WGS84 point into a local east/north plane in nautical miles."""
    reference_latitude = radians((latitude + origin_latitude) / 2.0)
    east = radians(longitude - origin_longitude) * EARTH_RADIUS_NM * cos(reference_latitude)
    north = radians(latitude - origin_latitude) * EARTH_RADIUS_NM
    return east, north


def _route_segments(
    coordinates: list[list[float]],
    waypoints: list[dict[str, Any]] | None,
    estimated_hours: float | None,
) -> tuple[list[dict[str, float]], float]:
    """Build cumulative-distance route segments with optional modeled timing."""
    valid = [
        (float(item[1]), float(item[0]))
        for item in coordinates
        if isinstance(item, (list, tuple)) and len(item) >= 2
    ]
    if len(valid) < 2:
        return [], 0.0
    lengths = [
        haversine_nm(first[0], first[1], second[0], second[1])
        for first, second in zip(valid, valid[1:])
    ]
    total = sum(lengths)
    waypoint_elapsed: list[float] | None = None
    if waypoints and len(waypoints) == len(valid):
        candidate: list[float] = []
        for item in waypoints:
            value = item.get("elapsed_hours")
            if value is None:
                candidate = []
                break
            try:
                candidate.append(float(value))
            except (TypeError, ValueError):
                candidate = []
                break
        if candidate and all(second >= first for first, second in zip(candidate, candidate[1:])):
            waypoint_elapsed = candidate
    if waypoint_elapsed is None:
        total_hours = float(estimated_hours or 0.0)
        cumulative = 0.0
        waypoint_elapsed = [0.0]
        for length in lengths:
            cumulative += length
            waypoint_elapsed.append(total_hours * cumulative / total if total > 0 else 0.0)

    segments: list[dict[str, float]] = []
    cumulative = 0.0
    for index, (first, second, length) in enumerate(zip(valid, valid[1:], lengths)):
        segments.append(
            {
                "index": float(index),
                "start_lat": first[0],
                "start_lon": first[1],
                "end_lat": second[0],
                "end_lon": second[1],
                "start_nm": cumulative,
                "end_nm": cumulative + length,
                "length_nm": length,
                "start_hours": waypoint_elapsed[index],
                "end_hours": waypoint_elapsed[index + 1],
            }
        )
        cumulative += length
    return segments, total


def _project_to_route_segment(
    point_latitude: float,
    point_longitude: float,
    segment: dict[str, float],
    minimum_progress_nm: float,
) -> dict[str, float] | None:
    """Project a point to one route segment, never before minimum route progress."""
    length = segment["length_nm"]
    if length <= 1e-9 or segment["end_nm"] + 1e-9 < minimum_progress_nm:
        return None
    ax = ay = 0.0
    bx, by = _local_xy_nm(
        segment["end_lat"],
        segment["end_lon"],
        segment["start_lat"],
        segment["start_lon"],
    )
    px, py = _local_xy_nm(
        point_latitude,
        point_longitude,
        segment["start_lat"],
        segment["start_lon"],
    )
    denominator = bx * bx + by * by
    if denominator <= 1e-12:
        return None
    t = (px * bx + py * by) / denominator
    lower_t = 0.0
    if minimum_progress_nm > segment["start_nm"]:
        lower_t = min(1.0, (minimum_progress_nm - segment["start_nm"]) / length)
    t = max(lower_t, min(1.0, t))
    qx, qy = bx * t, by * t
    distance = sqrt((px - qx) ** 2 + (py - qy) ** 2)
    cross = bx * py - by * px
    # Positive local cross product is left/port of the forward route vector.
    # The UI convention intentionally uses negative=port and positive=starboard.
    if abs(cross) < 1e-12 or distance < 1e-9:
        signed = 0.0
    else:
        signed = -distance if cross > 0 else distance
    progress = segment["start_nm"] + length * t
    elapsed = segment["start_hours"] + (segment["end_hours"] - segment["start_hours"]) * t
    latitude = segment["start_lat"] + (segment["end_lat"] - segment["start_lat"]) * t
    longitude = segment["start_lon"] + (segment["end_lon"] - segment["start_lon"]) * t
    return {
        "distance_nm": distance,
        "signed_deviation_nm": signed,
        "progress_nm": progress,
        "modeled_elapsed_hours": elapsed,
        "matched_latitude": latitude,
        "matched_longitude": longitude,
        "segment_index": segment["index"],
    }


def _decimate_deviation_samples(
    samples: list[dict[str, Any]], max_samples: int = 240
) -> list[dict[str, Any]]:
    """Bound UI payload while retaining endpoints and strong local extrema."""
    if len(samples) <= max_samples:
        return samples
    bucket_count = max(1, max_samples // 2)
    step = len(samples) / bucket_count
    selected: dict[int, dict[str, Any]] = {0: samples[0], len(samples) - 1: samples[-1]}
    for bucket in range(bucket_count):
        start = int(bucket * step)
        end = min(len(samples), max(start + 1, int((bucket + 1) * step)))
        group = samples[start:end]
        if not group:
            continue
        for item in (min(group, key=lambda row: row["signed_deviation_nm"]), max(group, key=lambda row: row["signed_deviation_nm"])):
            selected[int(item["_index"])] = item
    return [selected[index] for index in sorted(selected)]


def route_deviation_analysis(
    points: list[dict[str, Any]],
    route_coordinates: list[list[float]],
    *,
    route_waypoints: list[dict[str, Any]] | None = None,
    modeled_total_hours: float | None = None,
    departure_at_utc: str | datetime | None = None,
) -> dict[str, Any]:
    """Compare an observed track to a modeled route using monotonic route progress.

    The nearest match is constrained never to move backward along the modeled
    route. Signed deviation is negative to port and positive to starboard.
    """
    valid_points = [
        item
        for item in points
        if item.get("latitude") is not None
        and item.get("longitude") is not None
        and item.get("recorded_at_utc")
    ]
    segments, route_total = _route_segments(
        route_coordinates, route_waypoints, modeled_total_hours
    )
    if not valid_points or not segments or route_total <= 0:
        return {
            "available": False,
            "reason": "A current modeled route and archived Garmin positions are required.",
            "samples": [],
            "connectors": [],
        }

    departure = parse_utc(departure_at_utc) if departure_at_utc else parse_utc(valid_points[0]["recorded_at_utc"])
    prior_progress = 0.0
    actual_distance = 0.0
    previous: dict[str, Any] | None = None
    samples: list[dict[str, Any]] = []
    for index, point in enumerate(valid_points):
        if previous is not None:
            actual_distance += haversine_nm(
                float(previous["latitude"]),
                float(previous["longitude"]),
                float(point["latitude"]),
                float(point["longitude"]),
            )
        best: dict[str, float] | None = None
        for segment in segments:
            candidate = _project_to_route_segment(
                float(point["latitude"]),
                float(point["longitude"]),
                segment,
                prior_progress,
            )
            if candidate is None:
                continue
            if best is None or candidate["distance_nm"] < best["distance_nm"]:
                best = candidate
        if best is None:
            previous = point
            continue
        prior_progress = max(prior_progress, best["progress_nm"])
        actual_elapsed = max(
            0.0,
            (parse_utc(point["recorded_at_utc"]) - departure).total_seconds() / 3600.0,
        )
        efficiency = (
            best["progress_nm"] / actual_distance * 100.0
            if actual_distance > 0.05
            else None
        )
        sample = {
            "_index": index,
            "report_id": point.get("id"),
            "recorded_at_utc": point["recorded_at_utc"],
            "latitude": round(float(point["latitude"]), 6),
            "longitude": round(float(point["longitude"]), 6),
            "matched_latitude": round(best["matched_latitude"], 6),
            "matched_longitude": round(best["matched_longitude"], 6),
            "signed_deviation_nm": round(best["signed_deviation_nm"], 3),
            "absolute_deviation_nm": round(abs(best["signed_deviation_nm"]), 3),
            "modeled_progress_nm": round(best["progress_nm"], 3),
            "modeled_progress_percent": round(min(100.0, best["progress_nm"] / route_total * 100.0), 2),
            "actual_distance_nm": round(actual_distance, 3),
            "modeled_elapsed_hours": round(best["modeled_elapsed_hours"], 3),
            "actual_elapsed_hours": round(actual_elapsed, 3),
            "time_delta_hours": round(actual_elapsed - best["modeled_elapsed_hours"], 3),
            "distance_efficiency_percent": round(efficiency, 1) if efficiency is not None else None,
        }
        samples.append(sample)
        previous = point

    if not samples:
        return {
            "available": False,
            "reason": "No archived Garmin positions could be matched to the modeled route.",
            "samples": [],
            "connectors": [],
        }

    current = samples[-1]
    max_item = max(samples, key=lambda item: item["absolute_deviation_nm"])
    min_signed = min(item["signed_deviation_nm"] for item in samples)
    max_signed = max(item["signed_deviation_nm"] for item in samples)
    extra_distance = current["actual_distance_nm"] - current["modeled_progress_nm"]

    # Representative map connectors are distributed by modeled-route progress.
    connector_count = min(16, len(samples))
    connectors: list[dict[str, Any]] = []
    if connector_count:
        targets = [route_total * i / max(connector_count - 1, 1) for i in range(connector_count)]
        cursor = 0
        for target in targets:
            while cursor + 1 < len(samples) and abs(samples[cursor + 1]["modeled_progress_nm"] - target) <= abs(samples[cursor]["modeled_progress_nm"] - target):
                cursor += 1
            item = samples[cursor]
            if not connectors or item["report_id"] != connectors[-1]["report_id"]:
                connectors.append({
                    key: item[key]
                    for key in (
                        "report_id", "recorded_at_utc", "latitude", "longitude",
                        "matched_latitude", "matched_longitude", "signed_deviation_nm",
                        "modeled_progress_percent",
                    )
                })

    display_samples = _decimate_deviation_samples(samples)
    for item in display_samples:
        item.pop("_index", None)
    return {
        "available": True,
        "through_report_utc": current["recorded_at_utc"],
        "report_count": len(valid_points),
        "route_distance_nm": round(route_total, 2),
        "current_signed_deviation_nm": current["signed_deviation_nm"],
        "current_deviation_nm": current["absolute_deviation_nm"],
        "current_side": "port" if current["signed_deviation_nm"] < -0.01 else "starboard" if current["signed_deviation_nm"] > 0.01 else "on_route",
        "max_deviation_nm": max_item["absolute_deviation_nm"],
        "max_deviation_side": "port" if max_item["signed_deviation_nm"] < 0 else "starboard" if max_item["signed_deviation_nm"] > 0 else "on_route",
        "port_extent_nm": round(abs(min(0.0, min_signed)), 2),
        "starboard_extent_nm": round(max(0.0, max_signed), 2),
        "actual_distance_nm": current["actual_distance_nm"],
        "modeled_distance_to_progress_nm": current["modeled_progress_nm"],
        "extra_distance_nm": round(extra_distance, 2),
        "distance_efficiency_percent": current["distance_efficiency_percent"],
        "modeled_progress_percent": current["modeled_progress_percent"],
        "actual_elapsed_hours": current["actual_elapsed_hours"],
        "modeled_elapsed_to_progress_hours": current["modeled_elapsed_hours"],
        "time_delta_hours": current["time_delta_hours"],
        "samples": display_samples,
        "connectors": connectors,
        "coverage_note": (
            "Observed Garmin positions are projected monotonically onto the saved modeled sailing route. "
            "Negative deviation is port; positive deviation is starboard. Reporting gaps affect actual distance and extrema."
        ),
    }
