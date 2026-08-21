"""Feed polling, archive coordination, and non-writing time state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from statistics import median
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .archive import AsyncArchive
from .calculations import haversine_nm, parse_utc
from .const import (
    CONF_LINK_NAME,
    CONF_LINK_PASSWORD,
    CONF_NOTIFICATIONS_ENABLED,
    CONF_PREDICTWIND_URL,
    CONF_STALE_MINUTES,
    DEFAULT_NOTIFICATIONS_ENABLED,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_STALE_MINUTES,
    DOMAIN,
    EVENT_DATA_UPDATED,
    GARMIN_MAP_URL,
    SOURCE_GARMIN,
    TIMER_INTERVAL,
)
from .feed import (
    FeedAuthenticationError,
    FeedConnectionError,
    FeedLinkError,
    GarminFeedClient,
)

_LOGGER = logging.getLogger(__name__)


class BlueSkyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll Garmin, store every unseen record, and expose a compact snapshot."""

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
        self.last_ingestion: dict[str, Any] = {"seen": 0, "inserted": 0}
        self.last_arrival: dict[str, Any] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        self.last_poll_attempt_utc = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        try:
            records = await self.client.async_fetch()
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
            self.last_arrival = self.last_ingestion.get("arrived_passage")
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

    @property
    def latest(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("latest")

    @property
    def passage(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("passage")

    @property
    def monitoring(self) -> bool:
        return bool(self.passage and self.passage.get("status") in {"active", "arrived"})

    @property
    def stale_minutes(self) -> int:
        return int(
            self.entry.options.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES)
        )

    @property
    def notifications_enabled(self) -> bool:
        return bool(
            self.entry.options.get(
                CONF_NOTIFICATIONS_ENABLED, DEFAULT_NOTIFICATIONS_ENABLED
            )
        )

    @property
    def report_age_minutes(self) -> float | None:
        latest = self.latest
        if not latest or not latest.get("recorded_at_utc"):
            return None
        return max(
            0.0,
            (
                datetime.now(timezone.utc) - parse_utc(latest["recorded_at_utc"])
            ).total_seconds()
            / 60,
        )

    @property
    def is_stale(self) -> bool:
        age = self.report_age_minutes
        return self.monitoring and (age is None or age > self.stale_minutes)

    @property
    def gps_problem(self) -> bool:
        latest = self.latest
        return bool(
            self.monitoring
            and latest
            and latest.get("valid_gps_fix") is False
        )

    @property
    def status(self) -> str:
        latest = self.latest
        if latest and latest.get("in_emergency") is True:
            return "EMERGENCY"
        if not self.monitoring:
            return "No active passage"
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
        # Re-evaluate time-based states in memory. Notify entities only when a
        # boolean/status boundary changes, avoiding minute-by-minute Recorder
        # churn while still crossing the stale threshold promptly.
        time_state = self._time_state()
        if time_state != self._last_time_state:
            self._last_time_state = time_state
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
        data = dict(self.coordinator.data or await self.archive.async_dashboard_state())
        passage_id = data.get("passage", {}).get("id") if data.get("passage") else None
        data["runtime"] = {
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
            "notifications_enabled": self.notifications_enabled,
        }
        data["links"] = {
            "garmin_mapshare": GARMIN_MAP_URL.format(
                self.entry.data[CONF_LINK_NAME]
            ),
            "predictwind": str(
                self.entry.options.get(
                    CONF_PREDICTWIND_URL,
                    self.entry.data.get(CONF_PREDICTWIND_URL, ""),
                )
                or ""
            ).strip(),
        }
        data["passages"] = await self.archive.async_list_passages()
        data["destinations"] = await self.archive.async_list_destinations()
        data["imports"] = await self.archive.async_list_imports()
        data["planned_route"] = (
            await self.archive.async_current_route(int(passage_id)) if passage_id else None
        )
        return data

    async def async_query_range(
        self,
        range_name: str,
        *,
        start_utc: str | None,
        end_utc: str | None,
        source: str | None,
        max_points: int,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        ranges = {
            "1d": timedelta(days=1),
            "3d": timedelta(days=3),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
            "1y": timedelta(days=365),
        }
        if range_name in ranges:
            start_utc = (now - ranges[range_name]).isoformat().replace("+00:00", "Z")
            end_utc = None
        elif range_name == "current_passage":
            start_utc = (
                self.passage.get("started_at_utc")
                if self.passage
                else (now - timedelta(days=7)).isoformat().replace("+00:00", "Z")
            )
            end_utc = None
        elif range_name == "all":
            start_utc = None
            end_utc = None
        elif range_name == "custom":
            if not start_utc or not end_utc:
                raise ValueError("Custom range needs both start and end")
            start_utc = parse_utc(start_utc).isoformat().replace("+00:00", "Z")
            end_utc = parse_utc(end_utc).isoformat().replace("+00:00", "Z")
            if start_utc > end_utc:
                raise ValueError("Custom range start must be before end")
        else:
            raise ValueError("Unknown range")
        result = await self.archive.async_query_points(
            start_utc=start_utc,
            end_utc=end_utc,
            source=None if source in {None, "", "all"} else source,
            max_points=max_points,
        )
        destination = (self.coordinator.data or {}).get("destination")
        if destination:
            previous_by_track: dict[str, dict[str, Any]] = {}
            recent_rates: dict[str, list[float]] = {}
            for point in result["points"]:
                if point.get("latitude") is None or point.get("longitude") is None:
                    point["destination_range_nm"] = None
                    point["vmc_kn"] = None
                    continue
                track = str(point.get("display_track") or point.get("source") or "unknown")
                point_range = haversine_nm(
                    float(point["latitude"]),
                    float(point["longitude"]),
                    float(destination["latitude"]),
                    float(destination["longitude"]),
                )
                point["destination_range_nm"] = round(point_range, 3)
                point["vmc_kn"] = None
                previous = previous_by_track.get(track)
                if previous:
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
        result["range"] = {
            "name": range_name,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "source": source or "all",
        }
        return result
