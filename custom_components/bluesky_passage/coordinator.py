"""Feed polling, archive coordination, weather, and route comparison."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from statistics import median
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .archive import AsyncArchive
from .calculations import (
    cross_track_nm,
    destination_metrics,
    haversine_nm,
    parse_utc,
    route_deviation_analysis,
)
from .const import (
    CONF_LINK_NAME,
    CONF_NOTIFICATIONS_ENABLED,
    CONF_PREDICTWIND_URL,
    CONF_SPEED_UNIT,
    CONF_HEIGHT_UNIT,
    CONF_STALE_MINUTES,
    CONF_XWEATHER_CLIENT_ID,
    CONF_XWEATHER_CLIENT_SECRET,
    DEFAULT_NOTIFICATIONS_ENABLED,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POLL_OVERLAP,
    DEFAULT_STALE_MINUTES,
    DOMAIN,
    EVENT_DATA_UPDATED,
    GARMIN_MAP_URL,
    SOURCE_GARMIN,
    TIMER_INTERVAL,
    WEATHER_CACHE_TOLERANCE_MINUTES,
    VERSION,
)
from .feed import (
    FeedAuthenticationError,
    FeedConnectionError,
    FeedLinkError,
    GarminFeedClient,
)
from .database import route_context_hash
from .land import is_land, nearest_water_point
from .routing import (
    VesselProfile,
    optimize_sailing_route,
    route_weather_sample_requests,
    shortest_water_path,
)
from .weather import (
    WeatherAuthenticationError,
    WeatherError,
    WeatherLimitError,
    XweatherClient,
)

_LOGGER = logging.getLogger(__name__)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class BlueSkyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll a rolling Garmin window and archive only unseen records."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        archive: AsyncArchive,
        client: GarminFeedClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=DEFAULT_POLL_INTERVAL,
        )
        self.entry = entry
        self.archive = archive
        self.client = client
        self.source_available = True
        self.source_error: str | None = None
        self.consecutive_failures = 0
        self.last_poll_attempt_utc: str | None = None
        self.last_poll_success_utc: str | None = None
        self.last_ingestion: dict[str, Any] = {
            "seen": 0,
            "inserted": 0,
            "duplicated": 0,
            "rejected": 0,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        self.last_poll_attempt_utc = _utc(now)
        try:
            records = await self.client.async_fetch(
                start_utc=now - DEFAULT_POLL_OVERLAP,
                end_utc=now,
            )
        except FeedAuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (FeedConnectionError, FeedLinkError) as err:
            self.source_available = False
            self.source_error = str(err)
            self.consecutive_failures += 1
            if self.consecutive_failures == 1:
                _LOGGER.warning("Garmin MapShare poll failed: %s", err)
        else:
            recovered = self.consecutive_failures > 0
            self.source_available = True
            self.source_error = None
            self.consecutive_failures = 0
            self.last_poll_success_utc = self.last_poll_attempt_utc
            if recovered:
                _LOGGER.info("Garmin MapShare polling recovered")
            self.last_ingestion = await self.archive.async_ingest(
                records, SOURCE_GARMIN, live=True
            )
            self.last_ingestion["duplicated"] = (
                int(self.last_ingestion["seen"])
                - int(self.last_ingestion["inserted"])
            )
            self.last_ingestion["rejected"] = 0
            if self.last_ingestion["inserted"]:
                self.hass.bus.async_fire(
                    EVENT_DATA_UPDATED,
                    {
                        "entry_id": self.entry.entry_id,
                        "inserted": self.last_ingestion["inserted"],
                    },
                )
        return await self.archive.async_dashboard_state()


class BlueSkyRuntime:
    """Runtime facade shared by entities, WebSocket commands, and alerts."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        archive: AsyncArchive,
        coordinator: BlueSkyCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.archive = archive
        self.coordinator = coordinator
        self.notifications: Any = None
        self._cancel_timer: Any = None
        self._remove_coordinator_listener: Any = None
        self._last_time_state: tuple[bool, bool, str] | None = None
        self.weather_client = XweatherClient(
            hass,
            self._option(CONF_XWEATHER_CLIENT_ID),
            self._option(CONF_XWEATHER_CLIENT_SECRET),
        )
        self.last_weather_error: str | None = None

    def _option(self, key: str, default: Any = None) -> Any:
        return self.entry.options.get(key, self.entry.data.get(key, default))

    @property
    def latest(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("latest")

    @property
    def passage(self) -> None:
        """Compatibility property; v2.1 has no globally active passage."""
        return None

    @property
    def monitoring(self) -> bool:
        return self.latest is not None

    @property
    def stale_minutes(self) -> int:
        return int(self._option(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES))

    @property
    def notifications_enabled(self) -> bool:
        return bool(
            self._option(CONF_NOTIFICATIONS_ENABLED, DEFAULT_NOTIFICATIONS_ENABLED)
        )

    @property
    def report_age_minutes(self) -> float | None:
        if not self.latest or not self.latest.get("recorded_at_utc"):
            return None
        return max(
            0.0,
            (
                datetime.now(timezone.utc)
                - parse_utc(self.latest["recorded_at_utc"])
            ).total_seconds()
            / 60,
        )

    @property
    def is_stale(self) -> bool:
        age = self.report_age_minutes
        return age is None or age > self.stale_minutes

    @property
    def gps_problem(self) -> bool:
        return bool(self.latest and self.latest.get("valid_gps_fix") is False)

    @property
    def status(self) -> str:
        if self.latest and self.latest.get("in_emergency") is True:
            return "EMERGENCY"
        if not self.latest:
            return "Waiting for first Garmin report"
        if not self.coordinator.source_available:
            return "Source unavailable"
        if self.is_stale:
            return "Tracking stale"
        if self.gps_problem:
            return "GPS fix invalid"
        return "Tracking current"

    async def async_start(self) -> None:
        self._remove_coordinator_listener = self.coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        if self.notifications:
            await self.notifications.async_start()
        self._last_time_state = self._time_state()
        self._cancel_timer = async_track_time_interval(
            self.hass, self._async_timer, TIMER_INTERVAL
        )

    def _handle_coordinator_update(self) -> None:
        self._last_time_state = self._time_state()
        if self.notifications:
            self.hass.async_create_task(self.notifications.async_process())

    def _time_state(self) -> tuple[bool, bool, str]:
        return (self.is_stale, self.gps_problem, self.status)

    async def _async_timer(self, _now: datetime) -> None:
        state = self._time_state()
        if state != self._last_time_state:
            self._last_time_state = state
            self.coordinator.async_update_listeners()
        elif self.notifications:
            await self.notifications.async_process()

    async def async_stop(self) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        if self._remove_coordinator_listener:
            self._remove_coordinator_listener()
            self._remove_coordinator_listener = None
        if self.notifications:
            await self.notifications.async_stop()

    async def async_refresh_local(self) -> None:
        snapshot = await self.archive.async_dashboard_state()
        self.coordinator.async_set_updated_data(snapshot)
        self.hass.bus.async_fire(
            EVENT_DATA_UPDATED, {"entry_id": self.entry.entry_id, "inserted": 0}
        )

    async def async_state(self) -> dict[str, Any]:
        data = dict(
            self.coordinator.data or await self.archive.async_dashboard_state()
        )
        profile = await self.archive.async_get_vessel_profile()
        data["runtime"] = {
            "integration_version": VERSION,
            "status": self.status,
            "monitoring": self.monitoring,
            "is_stale": self.is_stale,
            "gps_problem": self.gps_problem,
            "report_age_minutes": round(self.report_age_minutes, 1)
            if self.report_age_minutes is not None
            else None,
            "stale_minutes": self.stale_minutes,
            "source_available": self.coordinator.source_available,
            "source_error": self.coordinator.source_error,
            "consecutive_failures": self.coordinator.consecutive_failures,
            "last_poll_attempt_utc": self.coordinator.last_poll_attempt_utc,
            "last_poll_success_utc": self.coordinator.last_poll_success_utc,
            "last_ingestion": self.coordinator.last_ingestion,
            "last_garmin_request": self.coordinator.client.last_request,
            "notifications_enabled": self.notifications_enabled,
            "units": {
                "speed": self._option(CONF_SPEED_UNIT, "kn"),
                "height": self._option(CONF_HEIGHT_UNIT, "m"),
            },
        }
        data["links"] = {
            "garmin_mapshare": GARMIN_MAP_URL.format(
                self.entry.data[CONF_LINK_NAME]
            ),
            "predictwind": str(self._option(CONF_PREDICTWIND_URL, "") or "").strip(),
        }
        data["passages"] = await self.archive.async_list_passages()
        data["destinations"] = await self.archive.async_list_destinations()
        data["imports"] = await self.archive.async_list_imports()
        data["backfill_jobs"] = await self.archive.async_list_backfill_jobs()
        data["vessel_profile"] = {
            **profile,
            "analysis": VesselProfile.from_mapping(profile["profile"]).completeness,
        }
        data["weather"] = {
            **data.get("weather", {}),
            "provider": "Xweather",
            "configured": self.weather_client.configured,
            "last_error": self.last_weather_error,
            "last_request": self.weather_client.last_request,
            "credential_values_exposed": False,
        }
        return data

    async def _range_bounds(
        self,
        range_name: str,
        start_utc: str | None,
        end_utc: str | None,
        passage_id: int | None,
        start_report_id: int | None,
        end_report_id: int | None,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        now = datetime.now(timezone.utc)
        ranges = {
            "24h": timedelta(hours=24),
            "1d": timedelta(days=1),
            "3d": timedelta(days=3),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
            "1y": timedelta(days=365),
        }
        passage = None
        if passage_id is not None:
            passage = await self.archive.async_passage_detail(int(passage_id))
        if start_report_id is not None or end_report_id is not None:
            if start_report_id is None or end_report_id is None:
                raise ValueError("Select both a first and last map report")
            first = await self.archive.async_point_by_id(int(start_report_id))
            last = await self.archive.async_point_by_id(int(end_report_id))
            if not first or not last:
                raise ValueError("A selected report is no longer in the archive")
            values = sorted([first["recorded_at_utc"], last["recorded_at_utc"]])
            return values[0], values[1], passage
        if range_name in ranges:
            return _utc(now - ranges[range_name]), None, passage
        if range_name == "passage":
            if not passage:
                raise ValueError("Select a passage first")
            return passage["started_at_utc"], passage.get("ended_at_utc"), passage
        if range_name == "all":
            return None, None, passage
        if range_name == "custom":
            if not start_utc or not end_utc:
                raise ValueError("Custom range needs both start and end")
            start = _utc(parse_utc(start_utc))
            end = _utc(parse_utc(end_utc))
            if start > end:
                raise ValueError("Custom range start must be before end")
            return start, end, passage
        raise ValueError("Unknown range")

    @staticmethod
    def _add_destination_series(
        points: list[dict[str, Any]],
        destination: dict[str, Any] | None,
        destination_versions: list[dict[str, Any]] | None = None,
    ) -> None:
        if not destination and not destination_versions:
            return
        versions = sorted(
            destination_versions or ([destination] if destination else []),
            key=lambda item: item.get("effective_at_utc", ""),
        )
        previous_by_track: dict[str, dict[str, Any]] = {}
        recent_rates: dict[str, list[float]] = {}
        for point in points:
            effective = [
                item
                for item in versions
                if not item.get("effective_at_utc")
                or item["effective_at_utc"] <= point["recorded_at_utc"]
            ]
            if not effective:
                point["destination_name"] = None
                point["destination_version_id"] = None
                point["destination_range_nm"] = None
                point["vmc_kn"] = None
                continue
            point_destination = effective[-1]
            if point.get("latitude") is None or point.get("longitude") is None:
                point["destination_range_nm"] = None
                point["vmc_kn"] = None
                continue
            track = str(point.get("display_track") or point.get("source") or "unknown")
            point_range = haversine_nm(
                float(point["latitude"]),
                float(point["longitude"]),
                float(point_destination["latitude"]),
                float(point_destination["longitude"]),
            )
            point["destination_name"] = point_destination.get("name")
            point["destination_version_id"] = point_destination.get("id")
            point["destination_range_nm"] = round(point_range, 3)
            point["vmc_kn"] = None
            previous = previous_by_track.get(track)
            if previous and previous.get("destination_version_id") == point.get(
                "destination_version_id"
            ):
                elapsed_hours = (
                    parse_utc(point["recorded_at_utc"])
                    - parse_utc(previous["recorded_at_utc"])
                ).total_seconds() / 3600
                if 0 < elapsed_hours <= 3:
                    rate = (
                        float(previous["destination_range_nm"]) - point_range
                    ) / elapsed_hours
                    rates = recent_rates.setdefault(track, [])
                    rates.append(rate)
                    del rates[:-3]
                    point["vmc_kn"] = round(median(rates), 2)
            previous_by_track[track] = point

    async def async_query_range(
        self,
        range_name: str,
        *,
        start_utc: str | None,
        end_utc: str | None,
        source: str | None,
        max_points: int,
        passage_id: int | None = None,
        start_report_id: int | None = None,
        end_report_id: int | None = None,
    ) -> dict[str, Any]:
        start_utc, end_utc, passage = await self._range_bounds(
            range_name,
            start_utc,
            end_utc,
            passage_id,
            start_report_id,
            end_report_id,
        )
        result = await self.archive.async_query_points(
            start_utc=start_utc,
            end_utc=end_utc,
            source=None if source in {None, "", "all"} else source,
            max_points=max_points,
        )
        destination = None
        if passage and passage.get("destination_versions"):
            latest_time = (
                result["points"][-1]["recorded_at_utc"]
                if result["points"]
                else end_utc or start_utc
            )
            applicable = [
                item
                for item in passage["destination_versions"]
                if latest_time is not None
                and item.get("effective_at_utc", "") <= latest_time
            ]
            if applicable:
                selected_destination = applicable[-1]
                destination = {
                    "name": selected_destination["name"],
                    "latitude": selected_destination["latitude"],
                    "longitude": selected_destination["longitude"],
                    "arrival_radius_nm": selected_destination.get(
                        "arrival_radius_nm"
                    ),
                }
            self._add_destination_series(
                result["points"],
                destination,
                passage.get("destination_versions"),
            )
        result["weather_samples"] = await self.archive.async_query_weather_samples(
            start_utc=start_utc, end_utc=end_utc
        )
        result["passage"] = passage
        result["destination"] = destination
        result["metrics"] = destination_metrics(result["points"], destination)
        result["range"] = {
            "name": range_name,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "source": source or "all",
            "selected_by_reports": start_report_id is not None,
        }
        return result

    @staticmethod
    def _representative_points(
        points: list[dict[str, Any]], limit: int = 12
    ) -> list[dict[str, Any]]:
        valid = [
            point
            for point in points
            if point.get("latitude") is not None and point.get("longitude") is not None
        ]
        if len(valid) <= limit:
            return valid
        return [
            valid[round(index * (len(valid) - 1) / (limit - 1))]
            for index in range(limit)
        ]

    async def _weather_for_requests(
        self, requests: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not self.weather_client.configured:
            raise ValueError("Add Xweather credentials in the integration Configure dialog")
        results: list[dict[str, Any] | None] = [None] * len(requests)
        missing: list[tuple[int, dict[str, Any]]] = []
        for index, request in enumerate(requests):
            cached = await self.archive.async_cached_weather_sample(
                latitude=request["latitude"],
                longitude=request["longitude"],
                valid_at_utc=request["valid_at_utc"],
                tolerance_minutes=WEATHER_CACHE_TOLERANCE_MINUTES,
            )
            if cached:
                context = {
                    key: request.get(key)
                    for key in (
                        "purpose",
                        "track_point_id",
                        "passage_id",
                        "route_candidate",
                        "candidate",
                        "coordinate_index",
                    )
                    if key in request
                }
                linked = {**cached, **context}
                if any(
                    linked.get(key) != cached.get(key)
                    for key in ("purpose", "track_point_id", "passage_id", "route_candidate")
                ):
                    await self.archive.async_save_weather_samples([linked])
                results[index] = linked
                continue
            missing.append((index, request))

        semaphore = asyncio.Semaphore(2)
        terminal_error: str | None = None

        async def fetch(
            index: int, request: dict[str, Any]
        ) -> tuple[int, dict[str, Any], bool]:
            nonlocal terminal_error
            try:
                async with semaphore:
                    if terminal_error:
                        raise WeatherLimitError(terminal_error)
                    sample = await self.weather_client.async_sample(
                        request["latitude"],
                        request["longitude"],
                        request["valid_at_utc"],
                    )
            except WeatherError as err:
                if isinstance(err, (WeatherAuthenticationError, WeatherLimitError)):
                    terminal_error = str(err)
                self.last_weather_error = str(err)
                return index, {
                    **request,
                    "provider": "xweather",
                    "quality_state": "unavailable",
                    "conditions_available": False,
                    "maritime_available": False,
                    "warnings": [str(err)],
                }, False
            value = sample.as_dict()
            metadata = {key: item for key, item in request.items() if key not in value}
            linked = {**value, **metadata}
            return index, linked, True

        fetched = await asyncio.gather(*(fetch(index, request) for index, request in missing))
        stored: list[dict[str, Any]] = []
        for index, value, successful in fetched:
            results[index] = value
            # Unavailable samples are deliberate gap markers. Caching them
            # prevents repeated failing provider calls and keeps charts from
            # drawing a false continuous line through unknown conditions.
            stored.append(value)
        if stored:
            await self.archive.async_save_weather_samples(stored)
        if fetched and all(successful for _index, _value, successful in fetched):
            self.last_weather_error = None
        return [item for item in results if item is not None]

    async def async_enrich_weather(
        self,
        *,
        range_name: str,
        start_utc: str | None,
        end_utc: str | None,
        passage_id: int | None,
        start_report_id: int | None,
        end_report_id: int | None,
    ) -> dict[str, Any]:
        result = await self.async_query_range(
            range_name,
            start_utc=start_utc,
            end_utc=end_utc,
            source=SOURCE_GARMIN,
            max_points=10_000,
            passage_id=passage_id,
            start_report_id=start_report_id,
            end_report_id=end_report_id,
        )
        requests = [
            {
                "latitude": point["latitude"],
                "longitude": point["longitude"],
                "valid_at_utc": point["recorded_at_utc"],
                "track_point_id": point["id"],
                "purpose": "track",
            }
            for point in self._representative_points(result["points"])
        ]
        if not requests:
            raise ValueError("The selected range has no valid Garmin positions")
        samples = await self._weather_for_requests(requests)
        warnings = sorted(
            {
                warning
                for item in samples
                for warning in item.get("warnings", [])
            }
        )
        return {
            "samples": samples,
            "requested": len(requests),
            "available": sum(
                bool(item.get("conditions_available") or item.get("maritime_available"))
                for item in samples
            ),
            "modeled": True,
            "warnings": warnings,
        }

    async def async_save_profile(
        self, profile_value: dict[str, Any], passage_id: int | None = None
    ) -> dict[str, Any]:
        profile = VesselProfile.from_mapping(profile_value)
        saved = await self.archive.async_save_vessel_profile(profile.as_dict())
        result: dict[str, Any] = {
            **saved,
            "analysis": profile.completeness,
            "route_recalculated": False,
        }
        if passage_id is not None:
            detail = await self.archive.async_passage_detail(passage_id)
            if detail.get("destination_name"):
                result["route"] = await self.async_plan_route(
                    passage_id, detail["started_at_utc"]
                )
                result["route_recalculated"] = True
        return result

    async def async_passage_detail(
        self, passage_id: int, *, include_analysis: bool = True
    ) -> dict[str, Any]:
        """Return passage detail; live comparison analytics are best-effort.

        Editing passage metadata must never be blocked by a route-analysis
        failure. Callers opening the edit form can explicitly skip the live
        analysis, while normal passage viewing still refreshes it.
        """
        if not include_analysis:
            # Admin View / edit is a metadata operation. Keep it independent of
            # route deserialization, coverage preview, weather, and deviation math.
            return await self.archive.async_passage_edit_detail(passage_id)

        detail = await self.archive.async_passage_detail(passage_id)
        route = detail.get("route")
        if include_analysis and route and route.get("context_status") == "current":
            summary = route.get("summary") or {}
            selected = summary.get("selected") or {}
            if selected.get("coordinates"):
                departure = (
                    summary.get("departure_at_utc")
                    or route.get("departure_at_utc")
                    or detail["started_at_utc"]
                )
                try:
                    summary["actual"] = await self._actual_route_analysis(
                        detail, selected, departure
                    )
                    summary.pop("actual_analysis_error", None)
                except Exception as err:  # analysis is supplemental; keep detail usable
                    _LOGGER.exception(
                        "BlueSky Passage actual-vs-modeled analysis failed for passage %s",
                        passage_id,
                    )
                    summary["actual_analysis_error"] = (
                        "Actual-vs-modeled analysis could not be refreshed. "
                        "Passage metadata and the saved route remain available; "
                        "see the Home Assistant log for the underlying error."
                    )
                route["summary"] = summary
        return detail

    async def _actual_route_analysis(
        self,
        detail: dict[str, Any],
        selected: dict[str, Any],
        departure_at_utc: str,
    ) -> dict[str, Any]:
        """Analyze the archived Garmin track against a saved modeled route."""
        actual_query = await self.archive.async_query_points(
            start_utc=_utc(parse_utc(departure_at_utc)),
            end_utc=detail.get("ended_at_utc"),
            source=SOURCE_GARMIN,
            max_points=100_000,
        )
        actual_points = [
            point
            for point in actual_query["points"]
            if point.get("latitude") is not None and point.get("longitude") is not None
        ]
        actual_distance = sum(
            haversine_nm(
                float(first["latitude"]),
                float(first["longitude"]),
                float(second["latitude"]),
                float(second["longitude"]),
            )
            for first, second in zip(actual_points, actual_points[1:])
        )
        elapsed_hours = None
        range_remaining = None
        progress_efficiency = None
        max_cross_track = None
        destination = None
        if detail.get("destination_latitude") is not None and detail.get("destination_longitude") is not None:
            destination = (
                float(detail["destination_latitude"]),
                float(detail["destination_longitude"]),
            )
        if actual_points and destination:
            range_remaining = haversine_nm(
                float(actual_points[-1]["latitude"]),
                float(actual_points[-1]["longitude"]),
                destination[0],
                destination[1],
            )
            start = (
                float(selected["coordinates"][0][1]),
                float(selected["coordinates"][0][0]),
            )
            direct_progress = haversine_nm(
                start[0],
                start[1],
                float(actual_points[-1]["latitude"]),
                float(actual_points[-1]["longitude"]),
            )
            if actual_distance > 0:
                progress_efficiency = direct_progress / actual_distance * 100
            deviations = [
                abs(
                    cross_track_nm(
                        start[0],
                        start[1],
                        destination[0],
                        destination[1],
                        float(point["latitude"]),
                        float(point["longitude"]),
                    )
                )
                for point in actual_points
            ]
            max_cross_track = max(deviations) if deviations else None
        if len(actual_points) >= 2:
            elapsed_hours = (
                parse_utc(actual_points[-1]["recorded_at_utc"])
                - parse_utc(actual_points[0]["recorded_at_utc"])
            ).total_seconds() / 3600
        comparison = route_deviation_analysis(
            actual_points,
            selected.get("coordinates") or [],
            route_waypoints=selected.get("waypoints"),
            modeled_total_hours=selected.get("estimated_hours"),
            departure_at_utc=departure_at_utc,
        )
        return {
            "state": "complete_range" if detail.get("ended_at_utc") else "to_date",
            "report_count": actual_query["total_matching"],
            "through_report_utc": actual_points[-1]["recorded_at_utc"] if actual_points else None,
            "recorded_distance_nm": round(actual_distance, 2),
            "elapsed_hours": round(elapsed_hours, 2) if elapsed_hours is not None else None,
            "range_remaining_nm": round(range_remaining, 2) if range_remaining is not None else None,
            "direct_progress_efficiency_percent": round(progress_efficiency, 1) if progress_efficiency is not None else None,
            "max_cross_track_from_direct_nm": round(max_cross_track, 2) if max_cross_track is not None else None,
            "modeled_comparison": comparison,
            "coverage_note": (
                "Actual metrics use archived Garmin reports in the passage range; gaps and reporting interval affect distance and deviation."
            ),
        }

    async def async_plan_route(
        self, passage_id: int, departure_at_utc: str | None = None
    ) -> dict[str, Any]:
        detail = await self.archive.async_passage_detail(passage_id)
        if not detail.get("destination_name"):
            raise ValueError("Add a destination before creating a route comparison")
        departure = departure_at_utc or max(
            detail["started_at_utc"],
            detail.get("destination_effective_at_utc") or detail["started_at_utc"],
        )
        if (
            _utc(parse_utc(departure)) == _utc(parse_utc(detail["started_at_utc"]))
            and
            detail.get("departure_latitude") is not None
            and detail.get("departure_longitude") is not None
        ):
            start = (
                float(detail["departure_latitude"]),
                float(detail["departure_longitude"]),
            )
        else:
            points = await self.archive.async_query_points(
                start_utc=_utc(parse_utc(departure)),
                end_utc=detail.get("ended_at_utc"),
                source=SOURCE_GARMIN,
                max_points=2,
            )
            if not points["points"]:
                raise ValueError("Enter departure coordinates or archive a report in this passage")
            point = points["points"][0]
            if point.get("latitude") is None or point.get("longitude") is None:
                raise ValueError("The passage has no report with a valid position")
            start = (float(point["latitude"]), float(point["longitude"]))
        requested_start = start
        requested_destination = (
            float(detail["destination_latitude"]),
            float(detail["destination_longitude"]),
        )
        destination = requested_destination
        endpoint_warnings: list[str] = []
        endpoint_adjustments: dict[str, Any] = {
            "departure": {
                "requested": [requested_start[1], requested_start[0]],
                "routed": [requested_start[1], requested_start[0]],
                "adjusted": False,
                "distance_nm": 0.0,
            },
            "destination": {
                "requested": [requested_destination[1], requested_destination[0]],
                "routed": [requested_destination[1], requested_destination[0]],
                "adjusted": False,
                "distance_nm": 0.0,
            },
        }

        # The bundled global mask is deliberately conservative and coarse. A
        # marina slip or narrow harbor channel can occupy a cell classified as
        # land. Resolve only the endpoint ambiguity to nearby modeled water;
        # every interior route segment still has to pass the hard land mask.
        if is_land(*start):
            resolved_start = nearest_water_point(*start, max_distance_nm=2.0)
            if resolved_start is None:
                raise ValueError(
                    "The departure coordinate is more than 2 nmi from modeled water. "
                    "Move the departure waypoint closer to navigable water."
                )
            start = (resolved_start["latitude"], resolved_start["longitude"])
            endpoint_adjustments["departure"] = {
                "requested": [requested_start[1], requested_start[0]],
                "routed": [start[1], start[0]],
                "adjusted": True,
                "distance_nm": round(resolved_start["distance_nm"], 2),
            }
            endpoint_warnings.append(
                "The departure fix fell in a coastal land-mask cell; the modeled "
                f"routing start was shifted {resolved_start['distance_nm']:.2f} nmi "
                "to the nearest modeled-water cell. The recorded Garmin track was not changed."
            )

        arrival_radius = float(detail.get("arrival_radius_nm") or 2.0)
        if is_land(*destination):
            # At least one coarse-cell width is allowed so a waterfront/city
            # destination does not become unusable solely because of mask
            # resolution. Larger configured arrival radii are honored up to a
            # bounded 10 nmi endpoint search.
            destination_limit = min(10.0, max(1.5, arrival_radius))
            resolved_destination = nearest_water_point(
                *destination, max_distance_nm=destination_limit
            )
            if resolved_destination is None:
                raise ValueError(
                    "The destination coordinate falls on modeled land and no modeled-water "
                    f"endpoint was found within {destination_limit:.1f} nmi. "
                    "Move the destination waypoint or increase its arrival radius."
                )
            destination = (
                resolved_destination["latitude"],
                resolved_destination["longitude"],
            )
            endpoint_adjustments["destination"] = {
                "requested": [requested_destination[1], requested_destination[0]],
                "routed": [destination[1], destination[0]],
                "adjusted": True,
                "distance_nm": round(resolved_destination["distance_nm"], 2),
            }
            endpoint_warnings.append(
                "The destination fell in a coastal land-mask cell; the modeled route "
                f"ends {resolved_destination['distance_nm']:.2f} nmi away at the nearest "
                "modeled-water cell. The saved destination and arrival radius were not changed."
            )

        profile_data = await self.archive.async_get_vessel_profile()
        profile = VesselProfile.from_mapping(profile_data["profile"])
        # v2.2: the direct geodesic is reference-only. Build a water-valid
        # geometric baseline first, then search many sailing headings through a
        # bounded Xweather field. Invalid land/no-go segments never receive a
        # score.
        baseline = shortest_water_path(start, destination)
        request_metadata = route_weather_sample_requests(baseline, departure, profile)
        request_metadata = [
            {
                **item,
                "purpose": "route",
                "passage_id": passage_id,
                "route_candidate": item["candidate"],
            }
            for item in request_metadata
        ]
        warnings: list[str] = []
        samples: list[dict[str, Any]] = []
        if self.weather_client.configured:
            fetched = await self._weather_for_requests(request_metadata)
            for sample in fetched:
                if sample.get("conditions_available") or sample.get("maritime_available"):
                    samples.append(sample)
                else:
                    warnings.extend(sample.get("warnings") or [])
        else:
            warnings.append(
                "Xweather is not configured; using a water-valid geometric reference without sailing-weather optimization"
            )
        summary = optimize_sailing_route(
            start, destination, departure, profile, baseline, samples
        )
        summary["endpoint_adjustments"] = endpoint_adjustments
        summary["endpoint_notes"] = endpoint_warnings
        summary["actual"] = await self._actual_route_analysis(
            detail, summary["selected"], departure
        )
        summary["warnings"] = sorted(set([*(summary.get("warnings") or []), *warnings]))
        summary["passage_id"] = passage_id
        summary["departure_at_utc"] = _utc(parse_utc(departure))
        summary["context_hash"] = route_context_hash(
            detail, profile_data.get("updated_at_utc")
        )
        selected = summary["selected"]
        digest = hashlib.sha256(
            json.dumps(
                {
                    "passage_id": passage_id,
                    "departure": summary["departure_at_utc"],
                    "profile": profile.as_dict(),
                    "result": summary,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return await self.archive.async_add_route_version(
            passage_id,
            "Sailing weather search" if summary["method"] == "xweather_sailing_search" else "Water-valid reference",
            summary["method"],
            digest,
            selected["coordinates"],
            summary=summary,
            departure_at_utc=summary["departure_at_utc"],
            weather_generated_at_utc=_utc(datetime.now(timezone.utc))
            if summary["weather_used"]
            else None,
        )

    async def async_create_backfill_preview(
        self, start_utc: str, end_utc: str
    ) -> dict[str, Any]:
        return await self.archive.async_create_backfill_job(
            phase="preview", start_utc=start_utc, end_utc=end_utc, chunk_days=7
        )

    async def async_create_backfill_commit(self, preview_job_id: int) -> dict[str, Any]:
        preview = await self.archive.async_get_backfill_job(preview_job_id)
        if preview["phase"] != "preview" or preview["status"] != "completed":
            raise ValueError("Complete the backfill preview first")
        digest = hashlib.sha256(
            f"garmin-backfill|{preview['start_utc']}|{preview['end_utc']}".encode()
        ).hexdigest()
        import_id = await self.archive.async_begin_import(
            SOURCE_GARMIN,
            f"Garmin backfill {preview['start_utc']} to {preview['end_utc']}",
            digest,
        )
        try:
            return await self.archive.async_create_backfill_job(
                phase="commit",
                start_utc=preview["start_utc"],
                end_utc=preview["end_utc"],
                chunk_days=7,
                preview_job_id=preview_job_id,
                import_id=import_id,
            )
        except Exception:
            await self.archive.async_finish_import(
                import_id, failed=True, notes="Backfill job could not be created"
            )
            raise

    async def async_backfill_step(self, job_id: int) -> dict[str, Any]:
        job = await self.archive.async_get_backfill_job(job_id)
        chunk = await self.archive.async_next_backfill_chunk(job_id)
        if chunk is None:
            return await self.archive.async_get_backfill_job(job_id)
        try:
            records = await self.coordinator.client.async_fetch(
                start_utc=parse_utc(chunk["start_utc"]),
                end_utc=parse_utc(chunk["end_utc"]),
            )
            preview = await self.archive.async_preview_records(records, SOURCE_GARMIN)
            inserted = int(preview["new"])
            if job["phase"] == "commit":
                result = await self.archive.async_ingest(
                    records,
                    SOURCE_GARMIN,
                    import_id=int(job["import_id"]),
                    live=False,
                )
                inserted = int(result["inserted"])
            updated = await self.archive.async_complete_backfill_chunk(
                job_id,
                int(chunk["id"]),
                returned=preview["returned"],
                inserted=inserted,
                duplicated=(
                    preview["returned"] - inserted
                    if job["phase"] == "commit"
                    else preview["duplicated"]
                ),
                rejected=0,
                first_recorded_at_utc=preview["first_recorded_at_utc"],
                last_recorded_at_utc=preview["last_recorded_at_utc"],
            )
            if updated["status"] == "completed" and job["phase"] == "commit":
                await self.archive.async_finish_import(int(job["import_id"]))
                await self.async_refresh_local()
            return updated
        except Exception as err:
            await self.archive.async_fail_backfill_chunk(
                job_id, int(chunk["id"]), str(err)
            )
            raise ValueError(f"Garmin backfill stopped: {err}") from err
