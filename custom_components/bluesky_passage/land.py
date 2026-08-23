"""Conservative global dry-land rejection for comparison routing.

The bundled bit mask is intentionally used as a *hard* invalidation test: a
comparison segment that intersects a land cell is not scored.  It is not a
nautical chart and does not model depths, reefs, bridge clearances, restricted
waters, traffic separation schemes, or local hazards.
"""

from __future__ import annotations

from functools import lru_cache
import gzip
import math
from pathlib import Path

from .calculations import haversine_nm

GRID_MINUTES = 1.25
DELTA_DEG = GRID_MINUTES / 60.0
NLONS = 17_280
NLATS = 8_640
MASK_PATH = Path(__file__).resolve().parent / "data" / "landmask_1_25min.bit.gz"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@lru_cache(maxsize=1)
def _mask_bytes() -> bytes:
    """Load the bit-packed mask once per Home Assistant process."""
    with gzip.open(MASK_PATH, "rb") as stream:
        data = stream.read()
    expected = (NLONS * NLATS + 7) // 8
    if len(data) != expected:
        raise RuntimeError(
            f"BlueSky Passage land mask has {len(data)} bytes; expected {expected}"
        )
    return data


def _cell(latitude: float, longitude: float) -> tuple[int, int]:
    lat = _clamp(float(latitude), -89.999999, 89.999999)
    lon = ((float(longitude) + 180.0) % 360.0) - 180.0
    row = int((lat + 90.0) / DELTA_DEG)
    col = int((lon + 180.0) / DELTA_DEG)
    return min(NLATS - 1, max(0, row)), min(NLONS - 1, max(0, col))


def is_land(latitude: float, longitude: float) -> bool:
    """Return whether the nearest 1.25-arc-minute mask cell is dry land."""
    row, col = _cell(latitude, longitude)
    index = row * NLONS + col
    byte = _mask_bytes()[index >> 3]
    return bool(byte & (1 << (7 - (index & 7))))


def nearest_water_point(
    latitude: float,
    longitude: float,
    *,
    max_distance_nm: float = 2.0,
) -> dict[str, float] | None:
    """Return the nearest modeled-water cell center within a bounded radius.

    The global mask is intentionally coarse (1.25 arc-minutes). Marina slips,
    narrow channels, and waterfront GPS fixes can therefore fall in a cell that
    is predominantly land even when the real position is navigable water. This
    helper is used only to resolve that *endpoint ambiguity*; route segments
    remain subject to the normal hard land-intersection test.
    """
    latitude = float(latitude)
    longitude = ((float(longitude) + 180.0) % 360.0) - 180.0
    max_distance_nm = max(0.0, float(max_distance_nm))
    if not is_land(latitude, longitude):
        return {
            "latitude": latitude,
            "longitude": longitude,
            "distance_nm": 0.0,
        }
    if max_distance_nm <= 0:
        return None

    row, col = _cell(latitude, longitude)
    north_south_nm = GRID_MINUTES
    east_west_nm = max(0.08, GRID_MINUTES * abs(math.cos(math.radians(latitude))))
    radius_cells = int(math.ceil(max_distance_nm / min(north_south_nm, east_west_nm))) + 2
    radius_cells = min(120, max(2, radius_cells))

    best: tuple[float, float, float] | None = None
    for row_offset in range(-radius_cells, radius_cells + 1):
        candidate_row = row + row_offset
        if candidate_row < 0 or candidate_row >= NLATS:
            continue
        candidate_latitude = -90.0 + (candidate_row + 0.5) * DELTA_DEG
        for col_offset in range(-radius_cells, radius_cells + 1):
            candidate_col = (col + col_offset) % NLONS
            candidate_longitude = -180.0 + (candidate_col + 0.5) * DELTA_DEG
            if is_land(candidate_latitude, candidate_longitude):
                continue
            distance = haversine_nm(
                latitude,
                longitude,
                candidate_latitude,
                candidate_longitude,
            )
            if distance > max_distance_nm + 1e-9:
                continue
            if best is None or distance < best[0]:
                best = (distance, candidate_latitude, candidate_longitude)

    if best is None:
        return None
    return {
        "latitude": best[1],
        "longitude": best[2],
        "distance_nm": best[0],
    }


def _great_circle_point(
    start: tuple[float, float], end: tuple[float, float], fraction: float
) -> tuple[float, float]:
    """Spherical interpolation between two latitude/longitude points."""
    if fraction <= 0:
        return start
    if fraction >= 1:
        return end
    lat1, lon1 = map(math.radians, start)
    lat2, lon2 = map(math.radians, end)
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    angular = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))
    if angular < 1e-12:
        return start
    sin_total = math.sin(angular)
    first = math.sin((1 - fraction) * angular) / sin_total
    second = math.sin(fraction * angular) / sin_total
    x = first * math.cos(lat1) * math.cos(lon1) + second * math.cos(lat2) * math.cos(lon2)
    y = first * math.cos(lat1) * math.sin(lon1) + second * math.cos(lat2) * math.sin(lon2)
    z = first * math.sin(lat1) + second * math.sin(lat2)
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    lon = math.degrees(math.atan2(y, x))
    return lat, ((lon + 540.0) % 360.0) - 180.0


def segment_is_water(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    sample_spacing_nm: float = 0.55,
) -> bool:
    """Reject a segment when any sampled cell intersects modeled dry land.

    Sampling at less than half the approximately 1.25-nm equatorial cell width
    makes it difficult for a segment to jump over a one-cell land barrier.
    """
    distance = haversine_nm(start[0], start[1], end[0], end[1])
    samples = max(1, int(math.ceil(distance / max(0.2, sample_spacing_nm))))
    for index in range(samples + 1):
        latitude, longitude = _great_circle_point(start, end, index / samples)
        if is_land(latitude, longitude):
            return False
    return True


def path_is_water(coordinates: list[list[float]] | list[tuple[float, float]]) -> bool:
    """Return True only if every lon/lat path segment stays in water cells."""
    if len(coordinates) < 2:
        return False
    points = [(float(item[1]), float(item[0])) for item in coordinates]
    return all(segment_is_water(first, second) for first, second in zip(points, points[1:]))


def mask_metadata() -> dict[str, object]:
    """Expose non-sensitive provenance for diagnostics and route summaries."""
    return {
        "grid_minutes": GRID_MINUTES,
        "grid_rows": NLATS,
        "grid_columns": NLONS,
        "source": "GSHHG-derived Basemap land/sea mask",
        "navigation_grade": False,
    }
