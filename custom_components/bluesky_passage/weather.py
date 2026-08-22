"""On-demand Xweather conditions for analytics and route comparison.

Credentials remain in the Home Assistant config entry.  The frontend receives
only normalized weather values, never the client secret or upstream payload.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any, Iterable

import httpx
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from .calculations import parse_utc
from .const import PROVIDER_XWEATHER, WEATHER_SAMPLE_LIMIT, XWEATHER_API_URL

_LOGGER = logging.getLogger(__name__)


class WeatherError(Exception):
    """Base weather-provider exception safe to show in the panel."""


class WeatherAuthenticationError(WeatherError):
    """Xweather credentials were rejected or lack endpoint access."""


class WeatherLimitError(WeatherError):
    """The provider's account limit was reached."""


class WeatherConnectionError(WeatherError):
    """The provider could not be reached or returned unusable data."""


@dataclass(slots=True)
class WeatherSample:
    """One normalized model sample at a vessel position and valid time."""

    provider: str
    valid_at_utc: str
    latitude: float
    longitude: float
    wind_speed_kn: float | None = None
    wind_gust_kn: float | None = None
    wind_dir_deg: float | None = None
    wave_height_m: float | None = None
    wave_dir_deg: float | None = None
    wave_period_s: float | None = None
    current_speed_kn: float | None = None
    current_dir_deg: float | None = None
    pressure_hpa: float | None = None
    sea_surface_temp_c: float | None = None
    quality_state: str = "modeled"
    conditions_available: bool = False
    maritime_available: bool = False
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping."""
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        return value


def _finite(value: Any) -> float | None:
    """Return a finite number without converting missing data to zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_utc(value: str | datetime) -> str:
    return parse_utc(value).isoformat().replace("+00:00", "Z")


def _response_objects(payload: Any) -> list[dict[str, Any]]:
    """Extract Xweather response objects while rejecting error envelopes."""
    if not isinstance(payload, dict):
        raise WeatherConnectionError("Xweather returned an unexpected response")
    if payload.get("success") is False:
        error = payload.get("error") or {}
        code = str(error.get("code") if isinstance(error, dict) else error)
        description = str(
            error.get("description") if isinstance(error, dict) else ""
        ).strip()
        if code in {"invalid_client", "unauthorized_namespace", "insufficient_scope"}:
            raise WeatherAuthenticationError(
                description or "Xweather credentials or endpoint access were rejected"
            )
        if code in {"maxhits_daily", "maxhits_min"}:
            raise WeatherLimitError(
                description or "The Xweather request limit has been reached"
            )
        raise WeatherConnectionError(description or "Xweather rejected the request")
    response = payload.get("response")
    if isinstance(response, dict):
        return [response]
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    return []


def _closest_period(
    objects: Iterable[dict[str, Any]], target: datetime, *, max_delta_hours: float = 6
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return (object, period) closest to the requested valid time."""
    closest: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for item in objects:
        periods = item.get("periods")
        if not isinstance(periods, list):
            periods = [item]
        for period in periods:
            if not isinstance(period, dict):
                continue
            stamp = period.get("dateTimeISO") or period.get("validTimeISO")
            if stamp is None and period.get("timestamp") is not None:
                try:
                    stamp = datetime.fromtimestamp(
                        float(period["timestamp"]), timezone.utc
                    ).isoformat()
                except (TypeError, ValueError, OSError):
                    stamp = None
            if not stamp:
                continue
            try:
                delta = abs((parse_utc(str(stamp)) - target).total_seconds())
            except ValueError:
                continue
            if closest is None or delta < closest[0]:
                closest = (delta, item, period)
    if closest is None or closest[0] > max_delta_hours * 3600:
        return None
    return closest[1], closest[2]


def parse_xweather_sample(
    *,
    latitude: float,
    longitude: float,
    valid_at_utc: str | datetime,
    conditions_payload: Any | None,
    maritime_payload: Any | None,
    warnings: Iterable[str] = (),
) -> WeatherSample:
    """Normalize optional conditions and maritime response envelopes."""
    target = parse_utc(valid_at_utc)
    sample_warnings = list(warnings)
    sample = WeatherSample(
        provider=PROVIDER_XWEATHER,
        valid_at_utc=target.isoformat().replace("+00:00", "Z"),
        latitude=float(latitude),
        longitude=float(longitude),
        warnings=(),
    )
    if conditions_payload is not None:
        match = _closest_period(_response_objects(conditions_payload), target)
        if match:
            _item, period = match
            sample.conditions_available = True
            sample.wind_speed_kn = _finite(
                period.get("windSpeedKTS", period.get("windSpeedKnots"))
            )
            sample.wind_gust_kn = _finite(
                period.get("windGustKTS", period.get("windGustKnots"))
            )
            sample.wind_dir_deg = _finite(
                period.get("windDirDEG", period.get("windDirectionDEG"))
            )
            sample.pressure_hpa = _finite(
                period.get("pressureMB", period.get("pressureHPA"))
            )
        else:
            sample_warnings.append(
                "No wind model period was close enough to the requested time"
            )
    if maritime_payload is not None:
        match = _closest_period(_response_objects(maritime_payload), target)
        if match:
            _item, period = match
            sample.maritime_available = True
            sample.wave_height_m = _finite(period.get("significantWaveHeightM"))
            sample.wave_dir_deg = _finite(period.get("primaryWaveDirDEG"))
            sample.wave_period_s = _finite(period.get("primaryWavePeriod"))
            sample.current_speed_kn = _finite(period.get("seaCurrentSpeedKTS"))
            sample.current_dir_deg = _finite(period.get("seaCurrentDirDEG"))
            sample.sea_surface_temp_c = _finite(
                period.get("seaSurfaceTemperatureC")
            )
        else:
            sample_warnings.append(
                "No maritime model period was close enough to the requested time"
            )
    if not sample.conditions_available and not sample.maritime_available:
        sample.quality_state = "unavailable"
    sample.warnings = tuple(sample_warnings)
    return sample


class XweatherClient:
    """Small backend-only client for on-demand conditions and maritime data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client_id: str | None,
        client_secret: str | None,
    ) -> None:
        self._client = get_async_client(hass)
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.last_request: dict[str, Any] = {}

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _request(
        self,
        endpoint: str,
        latitude: float,
        longitude: float,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.configured:
            raise WeatherAuthenticationError("Add Xweather credentials in Data & settings")
        url = f"{XWEATHER_API_URL}/{endpoint}/{latitude:.5f},{longitude:.5f}"
        query = {
            **parameters,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        try:
            response = await self._client.get(url, params=query, timeout=45.0)
        except httpx.HTTPError as err:
            raise WeatherConnectionError("Xweather could not be reached") from err
        self.last_request = {
            "endpoint": endpoint,
            "http_status": response.status_code,
            "rate_limit_remaining": response.headers.get("X-RateLimit-Remaining"),
        }
        try:
            payload = response.json()
        except ValueError as err:
            raise WeatherConnectionError("Xweather returned invalid JSON") from err
        if response.status_code in {401, 403}:
            raise WeatherAuthenticationError("Xweather credentials or access were rejected")
        if response.status_code == 429:
            raise WeatherLimitError("The Xweather request limit has been reached")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            raise WeatherConnectionError(
                f"Xweather request failed with HTTP {response.status_code}"
            ) from err
        _response_objects(payload)
        return payload

    async def async_sample(
        self,
        latitude: float,
        longitude: float,
        valid_at_utc: str | datetime,
    ) -> WeatherSample:
        """Fetch one time/location sample, preserving partial endpoint success."""
        target = parse_utc(valid_at_utc)
        stamp = target.isoformat().replace("+00:00", "Z")
        maritime_start = (target - timedelta(hours=3)).isoformat().replace(
            "+00:00", "Z"
        )
        maritime_end = (target + timedelta(hours=3)).isoformat().replace(
            "+00:00", "Z"
        )
        now = datetime.now(timezone.utc)
        maritime_endpoint = (
            "maritime/archive" if target < now - timedelta(hours=48) else "maritime"
        )

        async def conditions() -> dict[str, Any]:
            return await self._request("conditions", latitude, longitude, {"for": stamp})

        async def maritime() -> dict[str, Any]:
            return await self._request(
                maritime_endpoint,
                latitude,
                longitude,
                {
                    "from": maritime_start,
                    "to": maritime_end,
                    "filter": "1hr",
                    "limit": 12,
                },
            )

        conditions_result, maritime_result = await asyncio.gather(
            conditions(), maritime(), return_exceptions=True
        )
        warnings: list[str] = []
        if isinstance(conditions_result, Exception):
            warnings.append(f"Wind unavailable: {conditions_result}")
            conditions_payload = None
        else:
            conditions_payload = conditions_result
        if isinstance(maritime_result, Exception):
            warnings.append(f"Marine conditions unavailable: {maritime_result}")
            maritime_payload = None
        else:
            maritime_payload = maritime_result
        if conditions_payload is None and maritime_payload is None:
            first = conditions_result if isinstance(conditions_result, Exception) else maritime_result
            if isinstance(first, WeatherError):
                raise first
            raise WeatherConnectionError("No Xweather model data was returned")
        return parse_xweather_sample(
            latitude=latitude,
            longitude=longitude,
            valid_at_utc=stamp,
            conditions_payload=conditions_payload,
            maritime_payload=maritime_payload,
            warnings=warnings,
        )

    async def async_sample_many(
        self,
        requests: Iterable[tuple[float, float, str | datetime]],
    ) -> list[WeatherSample]:
        """Fetch a deliberately small set with conservative concurrency."""
        items = list(requests)
        if len(items) > WEATHER_SAMPLE_LIMIT:
            raise ValueError(
                f"A single weather request is limited to {WEATHER_SAMPLE_LIMIT} samples"
            )
        semaphore = asyncio.Semaphore(2)

        async def fetch(item: tuple[float, float, str | datetime]) -> WeatherSample:
            async with semaphore:
                return await self.async_sample(item[0], item[1], item[2])

        results = await asyncio.gather(*(fetch(item) for item in items))
        _LOGGER.debug("Fetched %d normalized Xweather samples", len(results))
        return list(results)
