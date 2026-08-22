"""Dependency-free comparison-route generation for BlueSky Passage.

The output is deliberately described as a route estimate, not a navigable
course.  It does not contain charted hazards, land avoidance, COLREGS, traffic,
or skipper judgment.  Xweather model samples may improve the comparison score
but do not turn the result into a navigation system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any, Iterable

from .calculations import haversine_nm, initial_bearing_true, parse_utc

EARTH_RADIUS_NM = 3440.065


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _longitude_delta(start: float, end: float) -> float:
    return ((end - start + 540.0) % 360.0) - 180.0


def destination_point(
    latitude: float, longitude: float, bearing_deg: float, distance_nm: float
) -> tuple[float, float]:
    """Return a geodesic destination for a true bearing and distance."""
    angular = distance_nm / EARTH_RADIUS_NM
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), ((math.degrees(lon2) + 540.0) % 360.0) - 180.0


def _interpolate(
    start: tuple[float, float], end: tuple[float, float], fraction: float
) -> tuple[float, float]:
    """Antimeridian-aware interpolation adequate for comparison corridors."""
    latitude = start[0] + (end[0] - start[0]) * fraction
    longitude = start[1] + _longitude_delta(start[1], end[1]) * fraction
    return latitude, ((longitude + 540.0) % 360.0) - 180.0


def candidate_routes(
    start: tuple[float, float],
    destination: tuple[float, float],
    *,
    points: int = 9,
) -> list[dict[str, Any]]:
    """Create direct and two conservative offshore comparison corridors."""
    if points < 3:
        raise ValueError("A route estimate needs at least three points")
    distance = haversine_nm(start[0], start[1], destination[0], destination[1])
    direct_bearing = initial_bearing_true(
        start[0], start[1], destination[0], destination[1]
    )
    offset_nm = _clamp(distance * 0.075, 3.0, 60.0)
    routes: list[dict[str, Any]] = []
    for key, label, side in (
        ("direct", "Direct reference", 0),
        ("port", "Port comparison", -1),
        ("starboard", "Starboard comparison", 1),
    ):
        coordinates: list[list[float]] = []
        for index in range(points):
            fraction = index / (points - 1)
            latitude, longitude = _interpolate(start, destination, fraction)
            if side and 0 < index < points - 1:
                lateral = math.sin(math.pi * fraction) * offset_nm
                latitude, longitude = destination_point(
                    latitude,
                    longitude,
                    direct_bearing + side * 90.0,
                    lateral,
                )
            coordinates.append([round(longitude, 6), round(latitude, 6)])
        routes.append(
            {
                "key": key,
                "label": label,
                "coordinates": coordinates,
                "distance_nm": round(path_distance(coordinates), 2),
            }
        )
    return routes


def path_distance(coordinates: Iterable[list[float]]) -> float:
    total = 0.0
    previous: list[float] | None = None
    for coordinate in coordinates:
        if previous is not None:
            total += haversine_nm(
                previous[1], previous[0], coordinate[1], coordinate[0]
            )
        previous = coordinate
    return total


@dataclass(slots=True)
class VesselProfile:
    """Partial performance profile with safe, explicit fallbacks."""

    vessel_name: str | None = None
    hull_configuration: str | None = None
    length_overall_ft: float | None = None
    waterline_length_ft: float | None = None
    beam_ft: float | None = None
    draft_ft: float | None = None
    displacement_lb: float | None = None
    sail_area_sqft: float | None = None
    engine_cruise_speed_kn: float | None = None
    observed_cruise_speed_kn: float | None = None
    max_comfortable_wave_m: float | None = None
    polar_table: tuple[dict[str, float], ...] = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "VesselProfile":
        data = value or {}
        polar: list[dict[str, float]] = []
        for item in data.get("polar_table") or []:
            if not isinstance(item, dict):
                continue
            twa = _finite(item.get("twa_deg"))
            tws = _finite(item.get("tws_kn"))
            speed = _finite(item.get("boat_speed_kn"))
            if twa is not None and tws is not None and speed is not None and speed > 0:
                polar.append(
                    {
                        "twa_deg": _clamp(abs(twa), 0, 180),
                        "tws_kn": max(0, tws),
                        "boat_speed_kn": speed,
                    }
                )
        text = lambda key: str(data.get(key) or "").strip() or None
        return cls(
            vessel_name=text("vessel_name"),
            hull_configuration=text("hull_configuration"),
            length_overall_ft=_finite(data.get("length_overall_ft")),
            waterline_length_ft=_finite(data.get("waterline_length_ft")),
            beam_ft=_finite(data.get("beam_ft")),
            draft_ft=_finite(data.get("draft_ft")),
            displacement_lb=_finite(data.get("displacement_lb")),
            sail_area_sqft=_finite(data.get("sail_area_sqft")),
            engine_cruise_speed_kn=_finite(data.get("engine_cruise_speed_kn")),
            observed_cruise_speed_kn=_finite(data.get("observed_cruise_speed_kn")),
            max_comfortable_wave_m=_finite(data.get("max_comfortable_wave_m")),
            polar_table=tuple(polar),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "vessel_name": self.vessel_name,
            "hull_configuration": self.hull_configuration,
            "length_overall_ft": self.length_overall_ft,
            "waterline_length_ft": self.waterline_length_ft,
            "beam_ft": self.beam_ft,
            "draft_ft": self.draft_ft,
            "displacement_lb": self.displacement_lb,
            "sail_area_sqft": self.sail_area_sqft,
            "engine_cruise_speed_kn": self.engine_cruise_speed_kn,
            "observed_cruise_speed_kn": self.observed_cruise_speed_kn,
            "max_comfortable_wave_m": self.max_comfortable_wave_m,
            "polar_table": [dict(item) for item in self.polar_table],
        }

    @property
    def base_speed_kn(self) -> float:
        if self.observed_cruise_speed_kn and self.observed_cruise_speed_kn > 0:
            return self.observed_cruise_speed_kn
        if self.engine_cruise_speed_kn and self.engine_cruise_speed_kn > 0:
            return self.engine_cruise_speed_kn
        waterline = self.waterline_length_ft or self.length_overall_ft
        if waterline and waterline > 0:
            return _clamp(1.34 * math.sqrt(waterline), 3.0, 12.0)
        return 5.5

    @property
    def completeness(self) -> dict[str, Any]:
        populated = sum(
            value is not None
            for value in (
                self.hull_configuration,
                self.length_overall_ft,
                self.waterline_length_ft,
                self.beam_ft,
                self.draft_ft,
                self.displacement_lb,
                self.sail_area_sqft,
                self.engine_cruise_speed_kn,
                self.observed_cruise_speed_kn,
                self.max_comfortable_wave_m,
            )
        )
        method = "polar" if self.polar_table else (
            "observed" if self.observed_cruise_speed_kn else (
                "hull estimate" if self.waterline_length_ft or self.length_overall_ft else "generic fallback"
            )
        )
        return {"populated_fields": populated, "speed_method": method}


def _angle_difference(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _polar_speed(
    profile: VesselProfile, true_wind_angle: float, true_wind_speed: float
) -> float | None:
    if not profile.polar_table:
        return None
    best = min(
        profile.polar_table,
        key=lambda row: abs(row["twa_deg"] - true_wind_angle)
        + abs(row["tws_kn"] - true_wind_speed) * 2,
    )
    return best["boat_speed_kn"]


def estimated_speed_kn(
    profile: VesselProfile,
    course_deg: float,
    weather: dict[str, Any] | None,
) -> tuple[float, float]:
    """Return estimated speed made good and a non-navigational risk score."""
    base = profile.base_speed_kn
    if not weather:
        return base, 0.0
    wind_speed = _finite(weather.get("wind_speed_kn"))
    wind_dir = _finite(weather.get("wind_dir_deg"))
    wave_height = _finite(weather.get("wave_height_m"))
    current_speed = _finite(weather.get("current_speed_kn"))
    current_dir = _finite(weather.get("current_dir_deg"))
    motor = "motor" in (profile.hull_configuration or "").lower()
    speed = base
    risk = 0.0
    if wind_speed is not None and wind_dir is not None and not motor:
        twa = _angle_difference(course_deg, wind_dir)
        polar = _polar_speed(profile, twa, wind_speed)
        if polar is not None:
            speed = polar
        else:
            angle_factor = (
                0.38 if twa < 35 else 0.68 if twa < 60 else 1.0 if twa < 125 else 0.86
            )
            wind_factor = _clamp(wind_speed / 12.0, 0.35, 1.15)
            speed = base * angle_factor * wind_factor
        if wind_speed > 30:
            risk += (wind_speed - 30) / 10
    if wave_height is not None:
        speed *= _clamp(1.0 - max(0.0, wave_height - 1.2) * 0.09, 0.45, 1.0)
        risk += max(0.0, wave_height - 1.5)
        if profile.max_comfortable_wave_m and wave_height > profile.max_comfortable_wave_m:
            risk += 10 + (wave_height - profile.max_comfortable_wave_m) * 4
    if current_speed is not None and current_dir is not None:
        speed += current_speed * math.cos(math.radians(_angle_difference(course_deg, current_dir)))
    return _clamp(speed, 0.8, 30.0), risk


def route_sample_requests(
    routes: Iterable[dict[str, Any]],
    departure_at_utc: str | datetime,
    profile: VesselProfile,
) -> list[dict[str, Any]]:
    """Choose four time/location samples for each of three candidate routes."""
    departure = parse_utc(departure_at_utc)
    result: list[dict[str, Any]] = []
    for route in routes:
        coordinates = route["coordinates"]
        indices = sorted({0, round((len(coordinates) - 1) / 3), round(2 * (len(coordinates) - 1) / 3), len(coordinates) - 1})
        cumulative = 0.0
        previous = coordinates[0]
        distances = [0.0]
        for coordinate in coordinates[1:]:
            cumulative += haversine_nm(previous[1], previous[0], coordinate[1], coordinate[0])
            distances.append(cumulative)
            previous = coordinate
        for index in indices:
            coordinate = coordinates[index]
            valid_at = departure + timedelta(hours=distances[index] / profile.base_speed_kn)
            result.append(
                {
                    "candidate": route["key"],
                    "coordinate_index": index,
                    "latitude": coordinate[1],
                    "longitude": coordinate[0],
                    "valid_at_utc": valid_at.isoformat().replace("+00:00", "Z"),
                }
            )
    return result


def score_routes(
    routes: Iterable[dict[str, Any]],
    profile: VesselProfile,
    weather_by_candidate: dict[str, list[dict[str, Any]]] | None,
    departure_at_utc: str | datetime,
) -> dict[str, Any]:
    """Score comparison corridors and return the best labeled estimate."""
    departure = parse_utc(departure_at_utc)
    candidates: list[dict[str, Any]] = []
    weather_by_candidate = weather_by_candidate or {}
    for route in routes:
        samples = weather_by_candidate.get(route["key"], [])
        elapsed_hours = 0.0
        risk = 0.0
        coverage = 0
        coordinates = route["coordinates"]
        for index in range(1, len(coordinates)):
            previous = coordinates[index - 1]
            current = coordinates[index]
            segment = haversine_nm(previous[1], previous[0], current[1], current[0])
            course = initial_bearing_true(previous[1], previous[0], current[1], current[0])
            fraction = (index - 0.5) / (len(coordinates) - 1)
            sample = None
            if samples:
                sample = min(
                    samples,
                    key=lambda item: abs(
                        float(item.get("route_fraction", 0.0)) - fraction
                    ),
                )
                if sample.get("conditions_available") or sample.get("maritime_available"):
                    coverage += 1
            speed, segment_risk = estimated_speed_kn(profile, course, sample)
            elapsed_hours += segment / speed
            risk += segment_risk * max(segment, 1.0)
        risk = risk / max(route["distance_nm"], 1.0)
        # Favor useful time savings but strongly demote explicit comfort-limit breaches.
        score = elapsed_hours + risk * 2.0
        candidates.append(
            {
                **route,
                "estimated_hours": round(elapsed_hours, 2),
                "eta_utc": (departure + timedelta(hours=elapsed_hours))
                .isoformat()
                .replace("+00:00", "Z"),
                "risk_score": round(risk, 2),
                "weather_coverage_segments": coverage,
                "score": round(score, 3),
            }
        )
    if not candidates:
        raise ValueError("No route candidates were generated")
    has_weather = any(weather_by_candidate.values())
    selected = min(candidates, key=lambda item: item["score"]) if has_weather else candidates[0]
    return {
        "method": "xweather_comparison" if has_weather else "great_circle_reference",
        "selected": selected,
        "candidates": candidates,
        "profile": {
            **profile.completeness,
            "base_speed_kn": round(profile.base_speed_kn, 2),
        },
        "weather_used": has_weather,
        "disclaimer": (
            "Comparison only—not a navigable route. It does not account for charted "
            "hazards, land, traffic, COLREGS, local notices, or skipper judgment."
        ),
    }
