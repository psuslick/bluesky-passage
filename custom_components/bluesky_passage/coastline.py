"""High-resolution route geography for BlueSky Passage.

The v2.5 routing engine fails closed unless it can obtain NOAA ENC polygon
coverage for the requested route corridor.  The old bundled 1.25 arc-minute
mask is retained elsewhere for legacy display/tests only; it is not allowed to
certify a scored route.

NOAA ENC Direct to GIS is a planning/data service and is not a certified
navigation product.  BlueSky uses it only as a hard land-intersection screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .calculations import haversine_nm

ENC_ROOT = "https://encdirect.noaa.gov/arcgis/rest/services/encdirect"
ENC_BANDS = (
    ("berthing", "enc_berthing"),
    ("harbour", "enc_harbour"),
    ("approach", "enc_approach"),
    ("coastal", "enc_coastal"),
    ("general", "enc_general"),
)
CACHE_TTL = timedelta(hours=6)
MAX_FEATURES_PER_LAYER = 6000
PAGE_SIZE = 1000


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bbox(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    values = list(points)
    return (
        min(point[1] for point in values),
        min(point[0] for point in values),
        max(point[1] for point in values),
        max(point[0] for point in values),
    )


def _bbox_intersects(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    return not (
        first[2] < second[0]
        or first[0] > second[2]
        or first[3] < second[1]
        or first[1] > second[3]
    )


def _ring_bbox(ring: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    return (
        min(point[0] for point in ring),
        min(point[1] for point in ring),
        max(point[0] for point in ring),
        max(point[1] for point in ring),
    )


def _point_on_segment(point: tuple[float, float], first: tuple[float, float], second: tuple[float, float], eps: float = 1e-9) -> bool:
    px, py = point
    ax, ay = first
    bx, by = second
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > eps:
        return False
    return (
        min(ax, bx) - eps <= px <= max(ax, bx) + eps
        and min(ay, by) - eps <= py <= max(ay, by) + eps
    )


def _orientation(first: tuple[float, float], second: tuple[float, float], third: tuple[float, float]) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])


def _segments_intersect(
    first_a: tuple[float, float],
    first_b: tuple[float, float],
    second_a: tuple[float, float],
    second_b: tuple[float, float],
) -> bool:
    o1 = _orientation(first_a, first_b, second_a)
    o2 = _orientation(first_a, first_b, second_b)
    o3 = _orientation(second_a, second_b, first_a)
    o4 = _orientation(second_a, second_b, first_b)
    eps = 1e-12
    if ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and (
        (o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)
    ):
        return True
    return any(
        (
            abs(value) <= eps
            and _point_on_segment(point, segment_a, segment_b)
        )
        for value, point, segment_a, segment_b in (
            (o1, second_a, first_a, first_b),
            (o2, second_b, first_a, first_b),
            (o3, first_a, second_a, second_b),
            (o4, first_b, second_a, second_b),
        )
    )


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        if _point_on_segment(point, previous, current):
            return True
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            intersect_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < intersect_x:
                inside = not inside
        previous = current
    return inside


@dataclass(slots=True)
class Polygon:
    outer: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]]
    bbox: tuple[float, float, float, float]
    source: str
    properties: dict[str, Any] = field(default_factory=dict)

    def contains(self, latitude: float, longitude: float) -> bool:
        point = (longitude, latitude)
        if not (self.bbox[0] <= longitude <= self.bbox[2] and self.bbox[1] <= latitude <= self.bbox[3]):
            return False
        if not _point_in_ring(point, self.outer):
            return False
        return not any(_point_in_ring(point, ring) for ring in self.holes)

    def segment_intersects_boundary(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> bool:
        segment_bbox = (
            min(start[1], end[1]),
            min(start[0], end[0]),
            max(start[1], end[1]),
            max(start[0], end[0]),
        )
        if not _bbox_intersects(self.bbox, segment_bbox):
            return False
        first = (start[1], start[0])
        second = (end[1], end[0])
        for ring in (self.outer, *self.holes):
            if len(ring) < 2:
                continue
            previous = ring[-1]
            for current in ring:
                if _segments_intersect(first, second, previous, current):
                    return True
                previous = current
        return False


def _geojson_polygons(payload: dict[str, Any], source: str) -> list[Polygon]:
    result: list[Polygon] = []
    for feature in payload.get("features") or []:
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        coordinates = geometry.get("coordinates") or []
        polygon_sets = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
        for polygon_coordinates in polygon_sets:
            rings: list[list[tuple[float, float]]] = []
            for raw_ring in polygon_coordinates or []:
                ring: list[tuple[float, float]] = []
                for item in raw_ring or []:
                    if not isinstance(item, (list, tuple)) or len(item) < 2:
                        continue
                    longitude = _finite(item[0])
                    latitude = _finite(item[1])
                    if latitude is None or longitude is None:
                        continue
                    ring.append((longitude, latitude))
                if len(ring) >= 3:
                    rings.append(ring)
            if not rings:
                continue
            result.append(Polygon(rings[0], rings[1:], _ring_bbox(rings[0]), source, dict(feature.get("properties") or {})))
    return result


@dataclass(slots=True)
class EncConstraint:
    """Prepared route constraint backed by NOAA ENC polygons."""

    land: list[Polygon]
    coverage: list[Polygon]
    bands: tuple[str, ...]
    query_bbox: tuple[float, float, float, float]
    fetched_at_utc: str
    depth: list[Polygon] = field(default_factory=list)
    unsurveyed: list[Polygon] = field(default_factory=list)
    minimum_depth_m: float | None = None

    def is_land(self, latitude: float, longitude: float) -> bool:
        return any(polygon.contains(latitude, longitude) for polygon in self.land)

    def is_covered(self, latitude: float, longitude: float) -> bool:
        return any(polygon.contains(latitude, longitude) for polygon in self.coverage)

    @staticmethod
    def _depth_value(polygon: Polygon) -> float | None:
        props = {str(k).lower(): v for k, v in (polygon.properties or {}).items()}
        values = []
        for key in ("drval1", "depth_min", "mindepth", "minimum_depth"):
            if key in props:
                value = _finite(props.get(key))
                if value is not None:
                    values.append(value)
        if values:
            return min(values)
        for key in ("drval2", "depth_max", "maxdepth", "maximum_depth"):
            if key in props:
                value = _finite(props.get(key))
                if value is not None:
                    return value
        return None

    def depth_at(self, latitude: float, longitude: float) -> float | None:
        values = [
            value
            for polygon in self.depth
            if polygon.contains(latitude, longitude)
            if (value := self._depth_value(polygon)) is not None
        ]
        return min(values) if values else None

    def is_unsurveyed(self, latitude: float, longitude: float) -> bool:
        return any(polygon.contains(latitude, longitude) for polygon in self.unsurveyed)

    def point_is_safe(self, latitude: float, longitude: float) -> bool:
        if self.is_land(latitude, longitude) or not self.is_covered(latitude, longitude):
            return False
        if self.is_unsurveyed(latitude, longitude):
            return False
        if self.minimum_depth_m is not None:
            depth = self.depth_at(latitude, longitude)
            if depth is None or depth < self.minimum_depth_m:
                return False
        return True

    def segment_is_water(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        sample_spacing_nm: float = 0.25,
    ) -> bool:
        if self.is_land(*start) or self.is_land(*end):
            return False
        # Boundary intersection is the critical test that the former raster mask
        # could not perform for thin barrier islands.
        if any(polygon.segment_intersects_boundary(start, end) for polygon in self.land):
            return False
        distance = haversine_nm(start[0], start[1], end[0], end[1])
        samples = max(1, int(math.ceil(distance / max(0.1, sample_spacing_nm))))
        for index in range(samples + 1):
            ratio = index / samples
            lat = start[0] + (end[0] - start[0]) * ratio
            lon = start[1] + (end[1] - start[1]) * ratio
            if self.is_land(lat, lon):
                return False
        return True

    def segment_is_safe(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        sample_spacing_nm: float = 0.20,
    ) -> bool:
        if not self.segment_is_water(start, end, sample_spacing_nm=sample_spacing_nm):
            return False
        distance = haversine_nm(start[0], start[1], end[0], end[1])
        samples = max(1, int(math.ceil(distance / max(0.1, sample_spacing_nm))))
        for index in range(samples + 1):
            ratio = index / samples
            lat = start[0] + (end[0] - start[0]) * ratio
            lon = start[1] + (end[1] - start[1]) * ratio
            if not self.point_is_safe(lat, lon):
                return False
        return True

    def path_is_safe(self, coordinates: Iterable[list[float] | tuple[float, float]]) -> bool:
        values = list(coordinates)
        if len(values) < 2:
            return False
        return all(
            self.segment_is_safe(
                (float(first[1]), float(first[0])),
                (float(second[1]), float(second[0])),
            )
            for first, second in zip(values, values[1:])
        )

    def path_is_water(self, coordinates: Iterable[list[float] | tuple[float, float]]) -> bool:
        values = list(coordinates)
        if len(values) < 2:
            return False
        for first, second in zip(values, values[1:]):
            start = (float(first[1]), float(first[0]))
            end = (float(second[1]), float(second[0]))
            if not self.segment_is_water(start, end):
                return False
        return True

    def coverage_for_path(
        self, coordinates: Iterable[list[float] | tuple[float, float]], *, spacing_nm: float = 2.0
    ) -> tuple[bool, float]:
        values = list(coordinates)
        if not values:
            return False, 0.0
        checked = 0
        covered = 0
        for first, second in zip(values, values[1:]):
            start = (float(first[1]), float(first[0]))
            end = (float(second[1]), float(second[0]))
            distance = haversine_nm(start[0], start[1], end[0], end[1])
            samples = max(1, int(math.ceil(distance / max(0.5, spacing_nm))))
            for index in range(samples):
                ratio = index / samples
                lat = start[0] + (end[0] - start[0]) * ratio
                lon = start[1] + (end[1] - start[1]) * ratio
                checked += 1
                covered += int(self.is_covered(lat, lon))
        last = values[-1]
        checked += 1
        covered += int(self.is_covered(float(last[1]), float(last[0])))
        percent = 100.0 * covered / checked if checked else 0.0
        return covered == checked, round(percent, 1)

    def nearest_water_point(
        self, latitude: float, longitude: float, *, max_distance_nm: float
    ) -> dict[str, float] | None:
        if not self.is_land(latitude, longitude):
            return {"latitude": latitude, "longitude": longitude, "distance_nm": 0.0}
        # Concentric 0.1 nmi radial search. Endpoint adjustment is only for
        # model geometry; the user's saved pin is never modified.
        radial_step = 0.1
        radius = radial_step
        while radius <= max_distance_nm + 1e-9:
            headings = max(24, int(math.ceil(2 * math.pi * radius / radial_step)))
            for index in range(headings):
                bearing = 360.0 * index / headings
                angle = radius / 3440.065
                lat1 = math.radians(latitude)
                lon1 = math.radians(longitude)
                brg = math.radians(bearing)
                lat2 = math.asin(math.sin(lat1) * math.cos(angle) + math.cos(lat1) * math.sin(angle) * math.cos(brg))
                lon2 = lon1 + math.atan2(
                    math.sin(brg) * math.sin(angle) * math.cos(lat1),
                    math.cos(angle) - math.sin(lat1) * math.sin(lat2),
                )
                candidate = (math.degrees(lat2), ((math.degrees(lon2) + 540) % 360) - 180)
                if not self.is_land(*candidate) and self.is_covered(*candidate):
                    return {
                        "latitude": candidate[0],
                        "longitude": candidate[1],
                        "distance_nm": radius,
                    }
            radius += radial_step
        return None

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": "NOAA ENC Direct to GIS",
            "mode": "vector_land_polygons",
            "bands": list(self.bands),
            "land_polygons": len(self.land),
            "coverage_polygons": len(self.coverage),
            "depth_polygons": len(self.depth),
            "unsurveyed_polygons": len(self.unsurveyed),
            "minimum_depth_m": self.minimum_depth_m,
            "query_bbox": [round(value, 5) for value in self.query_bbox],
            "fetched_at_utc": self.fetched_at_utc,
            "certified_navigation": False,
        }


class EncGeometryError(RuntimeError):
    """ENC geometry could not be prepared safely."""


class EncGeometryClient:
    """Fetch and cache NOAA ENC polygon geometry for one route corridor."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.session = async_get_clientsession(hass)
        self._metadata: dict[str, dict[str, Any]] = {}
        self._cache: dict[str, tuple[datetime, EncConstraint]] = {}
        self.last_error: str | None = None
        self.last_request: dict[str, Any] | None = None

    async def _json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self.session.get(url, params=params, timeout=25) as response:
            if response.status >= 400:
                raise EncGeometryError(f"NOAA ENC returned HTTP {response.status}")
            payload = await response.json(content_type=None)
        if isinstance(payload, dict) and payload.get("error"):
            message = (payload.get("error") or {}).get("message") or "NOAA ENC query failed"
            raise EncGeometryError(str(message))
        if not isinstance(payload, dict):
            raise EncGeometryError("NOAA ENC returned an unexpected response")
        return payload

    async def _band_metadata(self, service: str) -> dict[str, Any]:
        if service in self._metadata:
            return self._metadata[service]
        payload = await self._json(f"{ENC_ROOT}/{service}/MapServer", {"f": "json"})
        layers = payload.get("layers") or []
        land = next((item for item in layers if str(item.get("name") or "").endswith(".Land_Area") and str(item.get("geometryType") or "").endswith("Polygon")), None)
        coverage = next((item for item in layers if str(item.get("name") or "").endswith(".Coverage_area") and str(item.get("geometryType") or "").endswith("Polygon")), None)
        depth = next((item for item in layers if str(item.get("name") or "").endswith(".Depth_Area") and str(item.get("geometryType") or "").endswith("Polygon")), None)
        unsurveyed = next((item for item in layers if str(item.get("name") or "").endswith(".Unsurveyed_Area") and str(item.get("geometryType") or "").endswith("Polygon")), None)
        if not land or not coverage:
            raise EncGeometryError(f"NOAA ENC {service} is missing land/coverage layers")
        result = {"land": int(land["id"]), "coverage": int(coverage["id"]), "depth": int(depth["id"]) if depth else None, "unsurveyed": int(unsurveyed["id"]) if unsurveyed else None}
        self._metadata[service] = result
        return result

    async def _query_layer(
        self,
        service: str,
        layer_id: int,
        bbox: tuple[float, float, float, float],
    ) -> dict[str, Any]:
        features: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self._json(
                f"{ENC_ROOT}/{service}/MapServer/{layer_id}/query",
                {
                    "f": "geojson",
                    "where": "1=1",
                    "geometry": ",".join(f"{value:.7f}" for value in bbox),
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": "4326",
                    "outSR": "4326",
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "*",
                    "returnGeometry": "true",
                    "geometryPrecision": "6",
                    "resultOffset": str(offset),
                    "resultRecordCount": str(PAGE_SIZE),
                },
            )
            page = payload.get("features") or []
            features.extend(page)
            if len(features) > MAX_FEATURES_PER_LAYER:
                raise EncGeometryError("NOAA ENC corridor returned too many polygon features; use closer routing gates")
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)
        return {"type": "FeatureCollection", "features": features}

    @staticmethod
    def _query_bbox(
        start: tuple[float, float], destination: tuple[float, float]
    ) -> tuple[float, float, float, float]:
        direct = haversine_nm(start[0], start[1], destination[0], destination[1])
        margin_nm = max(75.0, min(260.0, direct * 0.70))
        mean_lat = (start[0] + destination[0]) / 2.0
        lat_margin = margin_nm / 60.0
        lon_margin = margin_nm / max(18.0, 60.0 * math.cos(math.radians(mean_lat)))
        west = min(start[1], destination[1]) - lon_margin
        east = max(start[1], destination[1]) + lon_margin
        south = min(start[0], destination[0]) - lat_margin
        north = max(start[0], destination[0]) + lat_margin
        if west < -180 or east > 180:
            raise EncGeometryError("Dateline-crossing passages are not supported by the NOAA ENC routing provider yet")
        return (max(-180.0, west), max(-89.0, south), min(180.0, east), min(89.0, north))

    async def async_prepare(
        self, start: tuple[float, float], destination: tuple[float, float], *, minimum_depth_m: float | None = None
    ) -> EncConstraint:
        bbox = self._query_bbox(start, destination)
        cache_key = ":".join(f"{round(value, 3):.3f}" for value in bbox) + f":depth={round(minimum_depth_m or 0.0,2)}"
        now = datetime.now(timezone.utc)
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] <= CACHE_TTL:
            return cached[1]

        land: list[Polygon] = []
        coverage: list[Polygon] = []
        depth: list[Polygon] = []
        unsurveyed: list[Polygon] = []
        successful_bands: list[str] = []
        errors: list[str] = []
        for label, service in ENC_BANDS:
            try:
                metadata = await self._band_metadata(service)
                tasks = [
                    self._query_layer(service, metadata["land"], bbox),
                    self._query_layer(service, metadata["coverage"], bbox),
                ]
                if minimum_depth_m is not None and metadata.get("depth") is not None:
                    tasks.append(self._query_layer(service, metadata["depth"], bbox))
                if metadata.get("unsurveyed") is not None:
                    tasks.append(self._query_layer(service, metadata["unsurveyed"], bbox))
                payloads = await asyncio.gather(*tasks)
                land_payload, coverage_payload = payloads[0], payloads[1]
                cursor = 2
                depth_payload = payloads[cursor] if minimum_depth_m is not None and metadata.get("depth") is not None else {"features": []}
                cursor += int(minimum_depth_m is not None and metadata.get("depth") is not None)
                unsurveyed_payload = payloads[cursor] if metadata.get("unsurveyed") is not None else {"features": []}
            except Exception as err:  # fail only after trying all bands
                errors.append(f"{label}: {err}")
                continue
            band_land = _geojson_polygons(land_payload, label)
            band_coverage = _geojson_polygons(coverage_payload, label)
            band_depth = _geojson_polygons(depth_payload, label)
            band_unsurveyed = _geojson_polygons(unsurveyed_payload, label)
            if band_coverage:
                successful_bands.append(label)
                land.extend(band_land)
                coverage.extend(band_coverage)
                depth.extend(band_depth)
                unsurveyed.extend(band_unsurveyed)

        self.last_request = {
            "provider": "NOAA ENC Direct to GIS",
            "bbox": [round(value, 5) for value in bbox],
            "bands": successful_bands,
            "land_polygons": len(land),
            "coverage_polygons": len(coverage),
            "depth_polygons": len(depth),
            "unsurveyed_polygons": len(unsurveyed),
            "minimum_depth_m": minimum_depth_m,
            "at_utc": now.isoformat().replace("+00:00", "Z"),
        }
        if not coverage:
            self.last_error = "; ".join(errors) or "No NOAA ENC coverage intersects the requested corridor"
            raise EncGeometryError(
                "High-resolution NOAA ENC coverage could not be established for this passage. "
                "BlueSky will not generate or score an ideal route without a validated coastline source."
            )
        if not land:
            self.last_error = "NOAA ENC returned coverage but no land polygons in the routing corridor"
            raise EncGeometryError(
                "NOAA ENC coverage was found but land geometry could not be loaded; route generation stopped closed."
            )
        if minimum_depth_m is not None and not depth:
            self.last_error = "NOAA ENC returned no usable depth-area polygons for the requested corridor"
            raise EncGeometryError(
                "Depth-aware routing was requested from the vessel draft, but NOAA ENC depth-area geometry could not be established for this corridor."
            )
        constraint = EncConstraint(
            land=land,
            coverage=coverage,
            bands=tuple(successful_bands),
            query_bbox=bbox,
            fetched_at_utc=now.isoformat().replace("+00:00", "Z"),
            depth=depth,
            unsurveyed=unsurveyed,
            minimum_depth_m=minimum_depth_m,
        )
        # Both endpoints must be within some ENC coverage. Coastal endpoint
        # adjustment may resolve land classification, but lack of coverage is
        # never silently accepted.
        if not constraint.is_covered(*start) or not constraint.is_covered(*destination):
            self.last_error = "The requested routing endpoints are not both inside NOAA ENC coverage"
            raise EncGeometryError(
                "The departure and destination are not both inside usable NOAA ENC coverage. "
                "Set offshore routing gates inside supported chart coverage; no route was generated."
            )
        self.last_error = None
        self._cache[cache_key] = (now, constraint)
        return constraint


def strict_validate_route(
    constraint: EncConstraint,
    coordinates: list[list[float]],
) -> dict[str, Any]:
    """Independently validate a completed route before it can be saved."""
    if len(coordinates) < 2:
        return {"valid": False, "reason": "route has fewer than two coordinates", "coverage_percent": 0.0}
    coverage_ok, coverage_percent = constraint.coverage_for_path(coordinates, spacing_nm=1.0)
    if not coverage_ok:
        return {
            "valid": False,
            "reason": "route leaves NOAA ENC coverage",
            "coverage_percent": coverage_percent,
        }
    for index, (first, second) in enumerate(zip(coordinates, coordinates[1:])):
        start = (float(first[1]), float(first[0]))
        end = (float(second[1]), float(second[0]))
        check = constraint.segment_is_safe if hasattr(constraint, "segment_is_safe") else constraint.segment_is_water
        if not check(start, end, sample_spacing_nm=0.1):
            return {
                "valid": False,
                "reason": f"segment {index + 1} intersects ENC land geometry",
                "coverage_percent": coverage_percent,
            }
    return {
        "valid": True,
        "reason": None,
        "coverage_percent": coverage_percent,
        "provider": "NOAA ENC Direct to GIS",
    }
