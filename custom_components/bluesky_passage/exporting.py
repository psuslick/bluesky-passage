"""Portable exports from authenticated point queries."""

from __future__ import annotations

import csv
from io import StringIO
import json
from typing import Any
from xml.sax.saxutils import escape


def export_points(
    points: list[dict[str, Any]],
    format_name: str,
    weather_samples: list[dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    """Return filename suffix, MIME type, and serialized content.

    CSV keeps source telemetry and any explicitly cached model sample on the
    same row while retaining separate source and valid-time fields.
    """
    if format_name == "csv":
        stream = StringIO()
        fields = (
            "id", "source", "source_event_id", "recorded_at_utc", "latitude",
            "longitude", "elevation_m", "sog_kn", "cog_true", "valid_gps_fix",
            "in_emergency", "event_text", "message_text", "quality_flags",
            "model_provider", "model_quality_state", "model_valid_at_utc",
            "model_requested_at_utc", "wind_speed_kn", "wind_gust_kn",
            "wind_dir_deg", "wave_height_m", "wave_dir_deg", "wave_period_s",
            "current_speed_kn", "current_dir_deg", "pressure_hpa",
            "sea_surface_temp_c", "model_warnings",
        )
        weather_by_point = {
            int(sample["track_point_id"]): sample
            for sample in weather_samples or []
            if sample.get("track_point_id") is not None
        }
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for point in points:
            row = dict(point)
            row["quality_flags"] = ";".join(row.get("quality_flags") or [])
            model = weather_by_point.get(int(point["id"])) if point.get("id") is not None else None
            if model:
                row.update(
                    {
                        "model_provider": model.get("provider"),
                        "model_quality_state": model.get("quality_state"),
                        "model_valid_at_utc": model.get("valid_at_utc"),
                        "model_requested_at_utc": model.get("requested_at_utc"),
                        "wind_speed_kn": model.get("wind_speed_kn"),
                        "wind_gust_kn": model.get("wind_gust_kn"),
                        "wind_dir_deg": model.get("wind_dir_deg"),
                        "wave_height_m": model.get("wave_height_m"),
                        "wave_dir_deg": model.get("wave_dir_deg"),
                        "wave_period_s": model.get("wave_period_s"),
                        "current_speed_kn": model.get("current_speed_kn"),
                        "current_dir_deg": model.get("current_dir_deg"),
                        "pressure_hpa": model.get("pressure_hpa"),
                        "sea_surface_temp_c": model.get("sea_surface_temp_c"),
                        "model_warnings": ";".join(model.get("warnings") or []),
                    }
                )
            writer.writerow(row)
        return "csv", "text/csv;charset=utf-8", stream.getvalue()
    if format_name == "geojson":
        features = []
        for point in points:
            if point.get("latitude") is None or point.get("longitude") is None:
                continue
            properties = {key: value for key, value in point.items() if key not in {"latitude", "longitude"}}
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [point["longitude"], point["latitude"]],
                    },
                    "properties": properties,
                }
            )
        return (
            "geojson",
            "application/geo+json",
            json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        )
    if format_name == "gpx":
        segments: list[str] = []
        for point in points:
            if point.get("latitude") is None or point.get("longitude") is None:
                continue
            elevation = f"<ele>{point['elevation_m']}</ele>" if point.get("elevation_m") is not None else ""
            extensions = (
                "<extensions>"
                f"<sog_kn>{point.get('sog_kn') if point.get('sog_kn') is not None else ''}</sog_kn>"
                f"<cog_true>{point.get('cog_true') if point.get('cog_true') is not None else ''}</cog_true>"
                f"<source>{escape(str(point.get('source') or ''))}</source>"
                "</extensions>"
            )
            segments.append(
                f"<trkpt lat=\"{point['latitude']}\" lon=\"{point['longitude']}\">"
                f"{elevation}<time>{escape(str(point['recorded_at_utc']))}</time>{extensions}</trkpt>"
            )
        content = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<gpx version="1.1" creator="BlueSky Passage" xmlns="http://www.topografix.com/GPX/1/1">'
            "<trk><name>BlueSky Passage archive export</name><trkseg>"
            + "".join(segments)
            + "</trkseg></trk></gpx>"
        )
        return "gpx", "application/gpx+xml", content
    raise ValueError("Unsupported export format")
