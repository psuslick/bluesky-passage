"""Pure Garmin date-filter helpers used by runtime and regression tests."""

from __future__ import annotations

from datetime import datetime, timezone


def garmin_timestamp(value: str | datetime) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def garmin_date_params(
    start_utc: str | datetime | None,
    end_utc: str | datetime | None,
) -> dict[str, str]:
    """Build Garmin's optional inclusive d1/d2 KML query parameters."""
    params: dict[str, str] = {}
    if start_utc is not None:
        params["d1"] = garmin_timestamp(start_utc)
    if end_utc is not None:
        params["d2"] = garmin_timestamp(end_utc)
    if "d1" in params and "d2" in params and params["d1"] > params["d2"]:
        raise ValueError("Garmin request start must be before its end")
    return params
