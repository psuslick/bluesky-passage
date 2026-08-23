"""Water-valid, sailing-aware comparison routing for BlueSky Passage.

Version 2.2 replaces the former three pre-shaped corridor scorer.  The direct
geodesic is now a reference only.  A route may be scored only when every
segment passes the bundled dry-land mask, and sailing headings inside the
configured/default no-go angle are rejected instead of being assigned an
artificially slow straight-line speed.

This remains a supplementary planning/analysis model, not a chart plotter.  It
does not know depths, reefs, bridge clearances, restricted areas, traffic,
COLREGS, local notices, or skipper judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import heapq
import math
from typing import Any, Iterable

from .calculations import haversine_nm, initial_bearing_true, parse_utc
from .land import is_land, mask_metadata, path_is_water, segment_is_water

EARTH_RADIUS_NM = 3440.065
ROUTING_ENGINE_VERSION = "isochrone-water-v3"


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


def _angle_difference(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _signed_angle(first: float, second: float) -> float:
    return ((first - second + 540.0) % 360.0) - 180.0


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


def path_distance(coordinates: Iterable[list[float] | tuple[float, float]]) -> float:
    total = 0.0
    previous: list[float] | tuple[float, float] | None = None
    for coordinate in coordinates:
        if previous is not None:
            total += haversine_nm(
                float(previous[1]),
                float(previous[0]),
                float(coordinate[1]),
                float(coordinate[0]),
            )
        previous = coordinate
    return total


def _xy_from_origin(
    origin: tuple[float, float], point: tuple[float, float]
) -> tuple[float, float]:
    distance = haversine_nm(origin[0], origin[1], point[0], point[1])
    bearing = math.radians(
        initial_bearing_true(origin[0], origin[1], point[0], point[1])
    )
    return distance * math.sin(bearing), distance * math.cos(bearing)


def _point_from_xy(origin: tuple[float, float], x_nm: float, y_nm: float) -> tuple[float, float]:
    distance = math.hypot(x_nm, y_nm)
    if distance < 1e-9:
        return origin
    bearing = (math.degrees(math.atan2(x_nm, y_nm)) + 360.0) % 360.0
    return destination_point(origin[0], origin[1], bearing, distance)


def _simplify_water_path(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    result = [points[0]]
    index = 0
    while index < len(points) - 1:
        candidate = len(points) - 1
        while candidate > index + 1:
            if segment_is_water(points[index], points[candidate]):
                break
            candidate -= 1
        result.append(points[candidate])
        index = candidate
    return result


def shortest_water_path(
    start: tuple[float, float], destination: tuple[float, float]
) -> list[list[float]]:
    """Find a conservative coarse shortest-water reference with A*.

    The grid is adaptive and exists only for route generation; the final path is
    revalidated against the higher-resolution bundled land mask.
    """
    if is_land(*start):
        raise ValueError(
            "The departure coordinate falls on the comparison land mask. Move the departure waypoint to navigable water."
        )
    if is_land(*destination):
        raise ValueError(
            "The destination coordinate falls on the comparison land mask. Move the destination waypoint to navigable water."
        )
    if segment_is_water(start, destination):
        return [[start[1], start[0]], [destination[1], destination[0]]]

    direct = haversine_nm(start[0], start[1], destination[0], destination[1])
    dest_x, dest_y = _xy_from_origin(start, destination)

    # Two attempts let a peninsula/cape escape a narrow first search envelope
    # without turning a regional route into an enormous global grid.
    for attempt in range(2):
        cell_nm = _clamp(direct / 32.0, 4.0, 22.0)
        margin = _clamp(direct * (0.28 if attempt == 0 else 0.60), 35.0, 350.0)
        xmin, xmax = min(0.0, dest_x) - margin, max(0.0, dest_x) + margin
        ymin, ymax = min(0.0, dest_y) - margin, max(0.0, dest_y) + margin
        nx = int(math.ceil((xmax - xmin) / cell_nm)) + 1
        ny = int(math.ceil((ymax - ymin) / cell_nm)) + 1
        if nx * ny > 45_000:
            scale = math.sqrt((nx * ny) / 45_000)
            cell_nm *= scale
            nx = int(math.ceil((xmax - xmin) / cell_nm)) + 1
            ny = int(math.ceil((ymax - ymin) / cell_nm)) + 1

        def point(index: tuple[int, int]) -> tuple[float, float]:
            ix, iy = index
            return _point_from_xy(start, xmin + ix * cell_nm, ymin + iy * cell_nm)

        def index_for(x: float, y: float) -> tuple[int, int]:
            return (
                int(round((x - xmin) / cell_nm)),
                int(round((y - ymin) / cell_nm)),
            )

        def nearest_water(x: float, y: float, endpoint: tuple[float, float]) -> tuple[int, int] | None:
            base_x, base_y = index_for(x, y)
            candidates: list[tuple[float, tuple[int, int]]] = []
            for radius in range(0, 5):
                for ix in range(max(0, base_x - radius), min(nx, base_x + radius + 1)):
                    for iy in range(max(0, base_y - radius), min(ny, base_y + radius + 1)):
                        if radius and max(abs(ix - base_x), abs(iy - base_y)) != radius:
                            continue
                        candidate = (ix, iy)
                        latlon = point(candidate)
                        if is_land(*latlon):
                            continue
                        if not segment_is_water(endpoint, latlon):
                            continue
                        candidates.append((haversine_nm(endpoint[0], endpoint[1], latlon[0], latlon[1]), candidate))
                if candidates:
                    return min(candidates, key=lambda item: item[0])[1]
            return None

        start_node = nearest_water(0.0, 0.0, start)
        goal_node = nearest_water(dest_x, dest_y, destination)
        if start_node is None or goal_node is None:
            continue

        queue: list[tuple[float, float, tuple[int, int]]] = [(0.0, 0.0, start_node)]
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        cost = {start_node: 0.0}
        closed: set[tuple[int, int]] = set()
        directions = (
            (-1, -1), (-1, 0), (-1, 1), (0, -1),
            (0, 1), (1, -1), (1, 0), (1, 1),
        )
        found = False
        while queue:
            _priority, current_cost, current = heapq.heappop(queue)
            if current in closed:
                continue
            closed.add(current)
            if current == goal_node:
                found = True
                break
            current_point = point(current)
            for dx, dy in directions:
                neighbor = (current[0] + dx, current[1] + dy)
                if not (0 <= neighbor[0] < nx and 0 <= neighbor[1] < ny):
                    continue
                if neighbor in closed:
                    continue
                neighbor_point = point(neighbor)
                if is_land(*neighbor_point) or not segment_is_water(
                    current_point, neighbor_point, sample_spacing_nm=0.8
                ):
                    continue
                step = haversine_nm(
                    current_point[0], current_point[1], neighbor_point[0], neighbor_point[1]
                )
                tentative = current_cost + step
                if tentative >= cost.get(neighbor, float("inf")):
                    continue
                cost[neighbor] = tentative
                came_from[neighbor] = current
                heuristic = haversine_nm(
                    neighbor_point[0], neighbor_point[1], destination[0], destination[1]
                )
                heapq.heappush(queue, (tentative + heuristic, tentative, neighbor))

        if not found:
            continue
        nodes = [goal_node]
        while nodes[-1] != start_node:
            nodes.append(came_from[nodes[-1]])
        nodes.reverse()
        points = [start, *(point(item) for item in nodes), destination]
        simplified = _simplify_water_path(points)
        coordinates = [[round(item[1], 6), round(item[0], 6)] for item in simplified]
        if path_is_water(coordinates):
            return coordinates

    raise ValueError(
        "No water-valid comparison corridor was found inside the routing search envelope. Choose water waypoints or attach a planned GPX route."
    )


@dataclass(slots=True)
class VesselProfile:
    """Partial vessel-performance profile with explicit fallbacks."""

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
    minimum_upwind_twa_deg: float | None = None
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
            minimum_upwind_twa_deg=_finite(data.get("minimum_upwind_twa_deg")),
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
            "minimum_upwind_twa_deg": self.minimum_upwind_twa_deg,
            "polar_table": [dict(item) for item in self.polar_table],
        }

    @property
    def is_motor_only(self) -> bool:
        value = (self.hull_configuration or "").lower()
        return any(word in value for word in ("motor", "power", "trawler")) and "sail" not in value

    @property
    def no_go_angle_deg(self) -> float:
        if self.is_motor_only:
            return 0.0
        if self.minimum_upwind_twa_deg is not None:
            return _clamp(self.minimum_upwind_twa_deg, 25.0, 70.0)
        return 40.0

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
                self.minimum_upwind_twa_deg,
            )
        )
        method = "polar" if self.polar_table else (
            "observed" if self.observed_cruise_speed_kn else (
                "hull estimate" if self.waterline_length_ft or self.length_overall_ft else "generic fallback"
            )
        )
        return {
            "populated_fields": populated,
            "speed_method": method,
            "no_go_angle_deg": round(self.no_go_angle_deg, 1),
        }


def _polar_speed(
    profile: VesselProfile, true_wind_angle: float, true_wind_speed: float
) -> float | None:
    if not profile.polar_table:
        return None
    # Smooth inverse-distance interpolation avoids the abrupt nearest-row jumps
    # used by the previous implementation while remaining tolerant of sparse JSON.
    ranked: list[tuple[float, dict[str, float]]] = []
    for row in profile.polar_table:
        distance = math.hypot(
            (row["twa_deg"] - true_wind_angle) / 20.0,
            (row["tws_kn"] - true_wind_speed) / 5.0,
        )
        if distance < 1e-9:
            return row["boat_speed_kn"]
        ranked.append((distance, row))
    ranked.sort(key=lambda item: item[0])
    nearest = ranked[:4]
    numerator = sum(item[1]["boat_speed_kn"] / item[0] ** 2 for item in nearest)
    denominator = sum(1.0 / item[0] ** 2 for item in nearest)
    return numerator / denominator if denominator else None


def _fallback_sail_speed(profile: VesselProfile, twa: float, tws: float) -> float:
    base = profile.base_speed_kn
    no_go = profile.no_go_angle_deg
    if twa < no_go:
        return 0.0
    if twa < 55:
        angle_factor = 0.62 + (twa - no_go) / max(1.0, 55 - no_go) * 0.12
    elif twa < 80:
        angle_factor = 0.74 + (twa - 55) / 25 * 0.20
    elif twa < 115:
        angle_factor = 0.94 + (twa - 80) / 35 * 0.06
    elif twa < 150:
        angle_factor = 1.0 - (twa - 115) / 35 * 0.10
    else:
        angle_factor = 0.90 - (twa - 150) / 30 * 0.10
    wind_factor = _clamp(0.55 + tws / 24.0, 0.50, 1.18)
    return base * angle_factor * wind_factor


def _vector(speed: float, bearing_deg: float) -> tuple[float, float]:
    radians = math.radians(bearing_deg)
    return speed * math.sin(radians), speed * math.cos(radians)


def _bearing_speed(east: float, north: float) -> tuple[float, float]:
    speed = math.hypot(east, north)
    bearing = (math.degrees(math.atan2(east, north)) + 360.0) % 360.0
    return bearing, speed


def performance_on_heading(
    profile: VesselProfile,
    heading_deg: float,
    weather: dict[str, Any] | None,
) -> dict[str, float] | None:
    """Resolve through-water performance plus current into COG/SOG.

    For sailing vessels, a heading inside the no-go angle is invalid and returns
    None.  This is the hard constraint that the v2.1 corridor scorer lacked.
    """
    weather = weather or {}
    wind_speed = _finite(weather.get("wind_speed_kn"))
    wind_dir = _finite(weather.get("wind_dir_deg"))
    wave_height = _finite(weather.get("wave_height_m"))
    wave_period = _finite(weather.get("wave_period_s"))
    current_speed = _finite(weather.get("current_speed_kn"))
    current_dir = _finite(weather.get("current_dir_deg"))

    through_water = profile.base_speed_kn
    twa = None
    if not profile.is_motor_only:
        # A sailing route cannot establish a no-go constraint without a wind
        # vector. Do not quietly fall back to arbitrary straight-line motion
        # when Xweather returned only marine data or a wind gap.
        if wind_speed is None or wind_dir is None:
            return None
        twa = _angle_difference(heading_deg, wind_dir)
        if twa < profile.no_go_angle_deg:
            return None
        polar = _polar_speed(profile, twa, wind_speed)
        through_water = polar if polar is not None else _fallback_sail_speed(profile, twa, wind_speed)
        if through_water <= 0:
            return None

    risk = 0.0
    speed_factor = 1.0
    if wind_speed is not None and wind_speed > 28:
        risk += (wind_speed - 28) / 6.0
        speed_factor *= _clamp(1.0 - (wind_speed - 32) * 0.018, 0.55, 1.0)
    if wave_height is not None:
        speed_factor *= _clamp(1.0 - max(0.0, wave_height - 1.0) * 0.07, 0.52, 1.0)
        risk += max(0.0, wave_height - 1.4) * 1.4
        if wave_period is not None and wave_height > 1.5 and wave_period < 7:
            risk += (7 - wave_period) * 0.25
        if profile.max_comfortable_wave_m and wave_height > profile.max_comfortable_wave_m:
            risk += 12.0 + (wave_height - profile.max_comfortable_wave_m) * 5.0
    through_water = _clamp(through_water * speed_factor, 0.5, 30.0)

    east, north = _vector(through_water, heading_deg)
    if current_speed is not None and current_dir is not None:
        current_east, current_north = _vector(max(0.0, current_speed), current_dir)
        east += current_east
        north += current_north
    cog, sog = _bearing_speed(east, north)
    if sog < 0.2:
        return None
    return {
        "heading_deg": heading_deg % 360.0,
        "through_water_kn": through_water,
        "cog_deg": cog,
        "sog_kn": sog,
        "risk": risk,
        "twa_deg": twa if twa is not None else -1.0,
    }


class WeatherField:
    """Sparse spatiotemporal interpolation over bounded Xweather samples."""

    def __init__(self, samples: Iterable[dict[str, Any]]) -> None:
        self.samples = [
            dict(item)
            for item in samples
            if item.get("conditions_available") or item.get("maritime_available")
        ]

    @property
    def available(self) -> bool:
        return bool(self.samples)

    @property
    def wind_available(self) -> bool:
        """Return True only when at least one usable wind vector exists."""
        return any(
            _finite(item.get("wind_speed_kn")) is not None
            and _finite(item.get("wind_dir_deg")) is not None
            for item in self.samples
        )

    @staticmethod
    def _weighted_direction(items: list[tuple[float, float]]) -> float | None:
        if not items:
            return None
        east = sum(weight * math.sin(math.radians(value)) for weight, value in items)
        north = sum(weight * math.cos(math.radians(value)) for weight, value in items)
        if abs(east) + abs(north) < 1e-9:
            return None
        return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0

    def at(
        self, latitude: float, longitude: float, valid_at_utc: str | datetime
    ) -> dict[str, Any] | None:
        if not self.samples:
            return None
        target = parse_utc(valid_at_utc)
        ranked: list[tuple[float, float, float, dict[str, Any]]] = []
        for sample in self.samples:
            distance = haversine_nm(
                latitude,
                longitude,
                float(sample["latitude"]),
                float(sample["longitude"]),
            )
            hours = abs((parse_utc(sample["valid_at_utc"]) - target).total_seconds()) / 3600
            metric = distance / 45.0 + hours / 6.0
            ranked.append((metric, distance, hours, sample))
        ranked.sort(key=lambda item: item[0])
        nearest = ranked[:6]
        # Do not pretend a very remote sample is local weather.
        if nearest[0][1] > 220 or nearest[0][2] > 30:
            return None
        weighted: list[tuple[float, dict[str, Any]]] = []
        for metric, _distance, _hours, sample in nearest:
            weighted.append((1.0 / max(0.12, metric) ** 2, sample))

        def scalar(key: str) -> float | None:
            values = [(weight, _finite(item.get(key))) for weight, item in weighted]
            present = [(weight, value) for weight, value in values if value is not None]
            if not present:
                return None
            return sum(weight * value for weight, value in present) / sum(weight for weight, _ in present)

        def direction(key: str) -> float | None:
            present = [
                (weight, value)
                for weight, item in weighted
                if (value := _finite(item.get(key))) is not None
            ]
            return self._weighted_direction(present)

        return {
            "wind_speed_kn": scalar("wind_speed_kn"),
            "wind_gust_kn": scalar("wind_gust_kn"),
            "wind_dir_deg": direction("wind_dir_deg"),
            "wave_height_m": scalar("wave_height_m"),
            "wave_dir_deg": direction("wave_dir_deg"),
            "wave_period_s": scalar("wave_period_s"),
            "current_speed_kn": scalar("current_speed_kn"),
            "current_dir_deg": direction("current_dir_deg"),
            "conditions_available": any(bool(item.get("conditions_available")) for _weight, item in weighted),
            "maritime_available": any(bool(item.get("maritime_available")) for _weight, item in weighted),
            "nearest_sample_distance_nm": round(nearest[0][1], 1),
            "nearest_sample_time_hours": round(nearest[0][2], 1),
        }


def _point_along_path(
    coordinates: list[list[float]], fraction: float
) -> tuple[tuple[float, float], float, float]:
    """Return (latlon, local bearing, cumulative distance) at path fraction."""
    if fraction <= 0:
        first, second = coordinates[0], coordinates[min(1, len(coordinates) - 1)]
        return (
            (float(first[1]), float(first[0])),
            initial_bearing_true(first[1], first[0], second[1], second[0]),
            0.0,
        )
    segment_lengths = [
        haversine_nm(first[1], first[0], second[1], second[0])
        for first, second in zip(coordinates, coordinates[1:])
    ]
    total = sum(segment_lengths)
    target = total * _clamp(fraction, 0.0, 1.0)
    cumulative = 0.0
    for index, length in enumerate(segment_lengths):
        if cumulative + length >= target or index == len(segment_lengths) - 1:
            ratio = 0.0 if length <= 0 else (target - cumulative) / length
            first, second = coordinates[index], coordinates[index + 1]
            bearing = initial_bearing_true(first[1], first[0], second[1], second[0])
            point = destination_point(first[1], first[0], bearing, length * ratio)
            return point, bearing, target
        cumulative += length
    last = coordinates[-1]
    return (float(last[1]), float(last[0])), 0.0, total


def route_weather_sample_requests(
    baseline_coordinates: list[list[float]],
    departure_at_utc: str | datetime,
    profile: VesselProfile,
) -> list[dict[str, Any]]:
    """Build a bounded 11-position weather lattice around the water corridor."""
    departure = parse_utc(departure_at_utc)
    total = path_distance(baseline_coordinates)
    corridor = _clamp(total * 0.12, 12.0, 60.0)
    result: list[dict[str, Any]] = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        center, bearing, distance = _point_along_path(baseline_coordinates, fraction)
        valid_at = departure + timedelta(hours=distance / max(profile.base_speed_kn, 0.8))
        sides = (0,) if fraction in {0.0, 1.0} else (-1, 0, 1)
        for side in sides:
            sample = center
            actual_offset = 0.0
            if side:
                for multiplier in (1.0, 0.5, 0.25):
                    candidate = destination_point(
                        center[0], center[1], bearing + side * 90.0, corridor * multiplier
                    )
                    if not is_land(*candidate) and segment_is_water(center, candidate):
                        sample = candidate
                        actual_offset = corridor * multiplier * side
                        break
            result.append(
                {
                    "candidate": f"field-{fraction:.2f}-{side:+d}",
                    "latitude": sample[0],
                    "longitude": sample[1],
                    "valid_at_utc": valid_at.isoformat().replace("+00:00", "Z"),
                    "route_fraction": fraction,
                    "lateral_offset_nm": round(actual_offset, 1),
                }
            )
    return result


@dataclass(slots=True)
class _RouteState:
    latitude: float
    longitude: float
    at_utc: datetime
    elapsed_hours: float
    distance_nm: float
    risk_total: float
    path: tuple[tuple[float, float], ...]
    path_elapsed_hours: tuple[float, ...]
    last_heading: float | None
    maneuvers: int
    weather_steps: int
    score: float = 0.0


def _candidate_headings(
    state: _RouteState,
    destination: tuple[float, float],
    weather: dict[str, Any] | None,
    profile: VesselProfile,
) -> list[float]:
    desired = initial_bearing_true(
        state.latitude, state.longitude, destination[0], destination[1]
    )
    values = [desired + offset for offset in (-95, -80, -65, -50, -35, -20, 0, 20, 35, 50, 65, 80, 95)]
    if state.last_heading is not None:
        values.extend((state.last_heading - 20, state.last_heading, state.last_heading + 20))
    wind_dir = _finite((weather or {}).get("wind_dir_deg"))
    if wind_dir is not None and not profile.is_motor_only:
        close = profile.no_go_angle_deg + 3.0
        values.extend((wind_dir - close, wind_dir + close))
    unique: list[float] = []
    for value in values:
        normalized = value % 360.0
        if all(_angle_difference(normalized, existing) > 2.0 for existing in unique):
            unique.append(normalized)
    return unique


def _major_maneuver(previous: float | None, current: float) -> int:
    return int(previous is not None and _angle_difference(previous, current) >= 65.0)


def _final_leg_performance(
    profile: VesselProfile,
    state: _RouteState,
    destination: tuple[float, float],
    weather: dict[str, Any] | None,
) -> dict[str, float] | None:
    """Find a sail-able heading whose ground vector can close the last leg.

    Current can rotate COG away from heading, so using the destination bearing
    itself as a heading is not sufficient. A bounded local heading search finds
    a through-water heading whose resulting COG points at the destination while
    still respecting the no-go constraint.
    """
    desired_cog = initial_bearing_true(
        state.latitude, state.longitude, destination[0], destination[1]
    )
    headings = [desired_cog + offset for offset in range(-70, 71, 5)]
    headings.extend(_candidate_headings(state, destination, weather, profile))
    best: tuple[float, float, dict[str, float]] | None = None
    seen: list[float] = []
    for heading in headings:
        normalized = heading % 360.0
        if any(_angle_difference(normalized, item) < 1.0 for item in seen):
            continue
        seen.append(normalized)
        performance = performance_on_heading(profile, normalized, weather)
        if performance is None:
            continue
        error = _angle_difference(performance["cog_deg"], desired_cog)
        if error > 4.0:
            continue
        rank = (error, -performance["sog_kn"], performance)
        if best is None or rank[:2] < best[:2]:
            best = rank
    return best[2] if best is not None else None


def _state_score(state: _RouteState, destination: tuple[float, float], base_speed: float) -> float:
    remaining = haversine_nm(
        state.latitude, state.longitude, destination[0], destination[1]
    )
    return (
        state.elapsed_hours
        + remaining / max(base_speed * 1.08, 1.0)
        + state.risk_total * 0.35
        + state.maneuvers * 0.035
    )


def _route_candidate_from_state(
    state: _RouteState,
    destination: tuple[float, float],
    departure: datetime,
    key: str,
    label: str,
) -> dict[str, Any]:
    path = list(state.path)
    if haversine_nm(path[-1][0], path[-1][1], destination[0], destination[1]) > 0.05:
        path.append(destination)
    coordinates = [[round(lon, 6), round(lat, 6)] for lat, lon in path]
    distance = path_distance(coordinates)
    return {
        "key": key,
        "label": label,
        "coordinates": coordinates,
        "waypoints": [
            {
                "longitude": round(point[1], 6),
                "latitude": round(point[0], 6),
                "elapsed_hours": round(elapsed, 3),
            }
            for point, elapsed in zip(state.path, state.path_elapsed_hours)
        ],
        "distance_nm": round(distance, 2),
        "estimated_hours": round(state.elapsed_hours, 2),
        "eta_utc": (departure + timedelta(hours=state.elapsed_hours)).isoformat().replace("+00:00", "Z"),
        "risk_score": round(state.risk_total / max(state.elapsed_hours, 1.0), 2),
        "weather_coverage_steps": state.weather_steps,
        "maneuvers": state.maneuvers,
        "land_valid": path_is_water(coordinates),
        "no_go_violations": 0,
        "score": round(state.score, 3),
    }


def optimize_sailing_route(
    start: tuple[float, float],
    destination: tuple[float, float],
    departure_at_utc: str | datetime,
    profile: VesselProfile,
    baseline_coordinates: list[list[float]],
    weather_samples: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Search many water-valid headings through a sparse time-varying weather field."""
    departure = parse_utc(departure_at_utc)
    field = WeatherField(weather_samples)
    direct_distance = haversine_nm(start[0], start[1], destination[0], destination[1])
    direct_valid = segment_is_water(start, destination)
    baseline_distance = path_distance(baseline_coordinates)
    reference = {
        "label": "Direct geodesic reference",
        "coordinates": [[round(start[1], 6), round(start[0], 6)], [round(destination[1], 6), round(destination[0], 6)]],
        "distance_nm": round(direct_distance, 2),
        "water_valid": direct_valid,
        "scored_candidate": False,
    }
    baseline = {
        "label": "Shortest water reference",
        "coordinates": baseline_coordinates,
        "distance_nm": round(baseline_distance, 2),
        "water_valid": path_is_water(baseline_coordinates),
        "scored_candidate": False,
    }

    if not field.available or (not profile.is_motor_only and not field.wind_available):
        hours = baseline_distance / max(profile.base_speed_kn, 0.8)
        selected = {
            "key": "water_reference",
            "label": "Shortest water reference",
            "coordinates": baseline_coordinates,
            "distance_nm": round(baseline_distance, 2),
            "estimated_hours": round(hours, 2),
            "eta_utc": (departure + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
            "risk_score": 0.0,
            "weather_coverage_steps": 0,
            "maneuvers": 0,
            "land_valid": True,
            "no_go_violations": None,
            "score": round(hours, 3),
        }
        return {
            "engine_version": ROUTING_ENGINE_VERSION,
            "method": "water_valid_reference",
            "selected": selected,
            "candidates": [selected],
            "reference": reference,
            "baseline": baseline,
            "profile": {**profile.completeness, "base_speed_kn": round(profile.base_speed_kn, 2)},
            "weather_used": False,
            "weather_sample_count": len(field.samples),
            "land_mask": mask_metadata(),
            "warnings": ([
                "No usable Xweather wind vector was available, so a sailing-aware route was not scored."
            ] if field.available and not profile.is_motor_only else []),
            "disclaimer": (
                "Water-valid geometric reference only; no weather sailing optimization was possible. "
                "The land mask is not a nautical chart and does not include depths, hazards, traffic, restrictions, or local notices."
            ),
        }

    estimated_hours = baseline_distance / max(profile.base_speed_kn, 0.8)
    step_hours = 2.5 if estimated_hours <= 72 else 3.5 if estimated_hours <= 160 else 5.0
    beam_width = 80
    max_iterations = min(160, max(30, int(math.ceil(estimated_hours / step_hours * 2.8)) + 18))
    arrival_nm = 0.35
    initial = _RouteState(
        start[0], start[1], departure, 0.0, 0.0, 0.0, (start,), (0.0,), None, 0, 0
    )
    initial.score = _state_score(initial, destination, profile.base_speed_kn)
    frontier = [initial]
    completed: list[_RouteState] = []
    first_completion_iteration: int | None = None

    # Prevent a search from wandering hundreds of miles away just to exploit a
    # sparse interpolated sample. This is intentionally much wider than the
    # weather lattice's lateral offsets.
    corridor_limit = max(85.0, direct_distance * 0.48)

    for iteration in range(max_iterations):
        expanded: list[_RouteState] = []
        for state in frontier:
            remaining = haversine_nm(
                state.latitude, state.longitude, destination[0], destination[1]
            )
            weather = field.at(state.latitude, state.longitude, state.at_utc)
            # Try to close the final leg only with a heading whose *resulting*
            # ground vector points at the destination. This prevents the last
            # mile from being appended across a no-go sector or against an
            # unaccounted current vector.
            if remaining <= max(arrival_nm, profile.base_speed_kn * step_hours * 1.35):
                final_perf = _final_leg_performance(
                    profile, state, destination, weather
                )
                if final_perf is not None and segment_is_water(
                    (state.latitude, state.longitude), destination
                ):
                    close_hours = remaining / max(final_perf["sog_kn"], 0.2)
                    if close_hours <= step_hours * 1.5:
                        maneuver = _major_maneuver(
                            state.last_heading, final_perf["heading_deg"]
                        )
                        elapsed = state.elapsed_hours + close_hours + maneuver * 0.03
                        final = _RouteState(
                            destination[0], destination[1],
                            departure + timedelta(hours=elapsed),
                            elapsed,
                            state.distance_nm + remaining,
                            state.risk_total + final_perf["risk"] * close_hours,
                            state.path + (destination,),
                            state.path_elapsed_hours + (elapsed,),
                            final_perf["heading_deg"],
                            state.maneuvers + maneuver,
                            state.weather_steps + int(weather is not None),
                        )
                        final.score = _state_score(
                            final, destination, profile.base_speed_kn
                        )
                        completed.append(final)
                        if first_completion_iteration is None:
                            first_completion_iteration = iteration
                        continue

            for heading in _candidate_headings(state, destination, weather, profile):
                performance = performance_on_heading(profile, heading, weather)
                if performance is None:
                    continue
                # Shrink the final step to avoid repeatedly overshooting a small
                # harbor/waypoint while preserving multi-hour offshore steps.
                dt = step_hours
                max_move = performance["sog_kn"] * dt
                if remaining < max_move * 1.25:
                    dt = _clamp(remaining / max(performance["sog_kn"], 0.4), 0.35, step_hours)
                distance = performance["sog_kn"] * dt
                next_point = destination_point(
                    state.latitude,
                    state.longitude,
                    performance["cog_deg"],
                    distance,
                )
                if is_land(*next_point) or not segment_is_water(
                    (state.latitude, state.longitude), next_point
                ):
                    continue
                next_remaining = haversine_nm(
                    next_point[0], next_point[1], destination[0], destination[1]
                )
                # Tacking may briefly open the range, but large backward moves
                # are dominated and are not useful in this bounded comparison.
                if next_remaining > remaining + max(12.0, distance * 0.72):
                    continue
                # Keep states inside a generous corridor around the water-valid
                # reference by comparing to its vertices.
                distance_to_baseline = min(
                    haversine_nm(next_point[0], next_point[1], item[1], item[0])
                    for item in baseline_coordinates
                )
                if distance_to_baseline > corridor_limit:
                    continue
                maneuver = _major_maneuver(state.last_heading, heading)
                risk_total = state.risk_total + performance["risk"] * dt
                elapsed = state.elapsed_hours + dt + maneuver * 0.03
                next_state = _RouteState(
                    next_point[0],
                    next_point[1],
                    departure + timedelta(hours=elapsed),
                    elapsed,
                    state.distance_nm + distance,
                    risk_total,
                    state.path + (next_point,),
                    state.path_elapsed_hours + (elapsed,),
                    heading,
                    state.maneuvers + maneuver,
                    state.weather_steps + int(weather is not None),
                )
                next_state.score = _state_score(next_state, destination, profile.base_speed_kn)
                expanded.append(next_state)

        if first_completion_iteration is not None and iteration >= first_completion_iteration + 3:
            break
        if not expanded:
            break

        # Spatial beam pruning preserves materially different tack/route options
        # without allowing combinatorial growth.
        bin_nm = max(4.0, profile.base_speed_kn * step_hours * 0.55)
        best_by_bin: dict[tuple[int, int], _RouteState] = {}
        for state in expanded:
            x, y = _xy_from_origin(start, (state.latitude, state.longitude))
            key = (int(round(x / bin_nm)), int(round(y / bin_nm)))
            prior = best_by_bin.get(key)
            if prior is None or state.score < prior.score:
                best_by_bin[key] = state
        frontier = sorted(best_by_bin.values(), key=lambda item: item.score)[:beam_width]

    if not completed:
        # Do not manufacture a weather route. Return the water-only reference
        # and clearly state that the sailing search could not close a route.
        hours = baseline_distance / max(profile.base_speed_kn, 0.8)
        selected = {
            "key": "water_reference",
            "label": "Shortest water reference",
            "coordinates": baseline_coordinates,
            "distance_nm": round(baseline_distance, 2),
            "estimated_hours": round(hours, 2),
            "eta_utc": (departure + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
            "risk_score": 0.0,
            "weather_coverage_steps": 0,
            "maneuvers": 0,
            "land_valid": True,
            "no_go_violations": None,
            "score": round(hours, 3),
        }
        return {
            "engine_version": ROUTING_ENGINE_VERSION,
            "method": "water_valid_reference",
            "selected": selected,
            "candidates": [selected],
            "reference": reference,
            "baseline": baseline,
            "profile": {**profile.completeness, "base_speed_kn": round(profile.base_speed_kn, 2)},
            "weather_used": True,
            "weather_sample_count": len(field.samples),
            "land_mask": mask_metadata(),
            "warnings": [
                "The sailing search could not close a complete route with the available modeled weather; the saved line is a water-valid reference, not an optimized sailing path."
            ],
            "disclaimer": (
                "Planning aid only. The land mask is not a nautical chart and the model does not include depths, reefs, traffic, restrictions, COLREGS, or local notices."
            ),
        }

    completed.sort(key=lambda item: item.score)
    # Keep alternatives that are not merely numerical duplicates of the winner.
    chosen_states: list[_RouteState] = []
    for state in completed:
        if not chosen_states:
            chosen_states.append(state)
        else:
            distance_delta = abs(state.distance_nm - chosen_states[0].distance_nm)
            time_delta = abs(state.elapsed_hours - chosen_states[0].elapsed_hours)
            maneuver_delta = state.maneuvers != chosen_states[0].maneuvers
            if distance_delta >= 1.0 or time_delta >= 0.5 or maneuver_delta:
                chosen_states.append(state)
        if len(chosen_states) >= 3:
            break
    candidates = [
        _route_candidate_from_state(
            state,
            destination,
            departure,
            f"optimized_{index + 1}",
            "Optimized sailing path" if index == 0 else f"Alternative {index + 1}",
        )
        for index, state in enumerate(chosen_states)
    ]
    candidates = [item for item in candidates if item["land_valid"]]
    if not candidates:
        raise ValueError("The routing search produced no land-valid result")
    selected = min(candidates, key=lambda item: item["score"])
    return {
        "engine_version": ROUTING_ENGINE_VERSION,
        "method": "xweather_sailing_search",
        "selected": selected,
        "candidates": candidates,
        "reference": reference,
        "baseline": baseline,
        "profile": {**profile.completeness, "base_speed_kn": round(profile.base_speed_kn, 2)},
        "weather_used": True,
        "weather_sample_count": len(field.samples),
        "land_mask": mask_metadata(),
        "disclaimer": (
            "Planning/analysis aid only—not a navigable route. Every scored segment passed the bundled dry-land mask and no-go headings were rejected, but the model does not include charted depths, reefs, bridge clearances, traffic, restricted areas, COLREGS, warnings, local notices, or skipper judgment."
        ),
    }


# Compatibility helpers retained for older tests/importers.  The direct line is
# now a reference only and score_routes delegates to the water-valid model.
def candidate_routes(
    start: tuple[float, float], destination: tuple[float, float], *, points: int = 9
) -> list[dict[str, Any]]:
    direct = [[start[1], start[0]], [destination[1], destination[0]]]
    return [{
        "key": "direct_reference",
        "label": "Direct geodesic reference",
        "coordinates": direct,
        "distance_nm": round(path_distance(direct), 2),
        "water_valid": segment_is_water(start, destination),
        "scored_candidate": False,
    }]


def route_sample_requests(
    routes: Iterable[dict[str, Any]], departure_at_utc: str | datetime, profile: VesselProfile
) -> list[dict[str, Any]]:
    routes = list(routes)
    if not routes:
        return []
    return route_weather_sample_requests(routes[0]["coordinates"], departure_at_utc, profile)


def score_routes(
    routes: Iterable[dict[str, Any]],
    profile: VesselProfile,
    weather_by_candidate: dict[str, list[dict[str, Any]]] | None,
    departure_at_utc: str | datetime,
) -> dict[str, Any]:
    routes = list(routes)
    if not routes:
        raise ValueError("No route reference was supplied")
    route = routes[0]
    start = (route["coordinates"][0][1], route["coordinates"][0][0])
    destination = (route["coordinates"][-1][1], route["coordinates"][-1][0])
    baseline = shortest_water_path(start, destination)
    samples = [item for group in (weather_by_candidate or {}).values() for item in group]
    return optimize_sailing_route(
        start, destination, departure_at_utc, profile, baseline, samples
    )
