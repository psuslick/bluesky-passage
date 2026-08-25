"""Supplementary persistent and optional mobile notifications."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .calculations import parse_utc
from .const import (
    CONF_MOBILE_NOTIFY_SERVICE,
    CONF_ZONE_NOTIFICATIONS_ENABLED,
    DEFAULT_ZONE_NOTIFICATIONS_ENABLED,
    GPS_PROBLEM_DELAY,
    NOTIFICATION_EMERGENCY,
    NOTIFICATION_GPS,
    NOTIFICATION_SOURCE,
    NOTIFICATION_STALE,
    NOTIFICATION_TEXT,
    NOTIFICATION_ZONE,
)


class NotificationManager:
    """Create one notification per condition and update it on recovery."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.hass = runtime.hass
        self._lock = asyncio.Lock()
        self._initialized = False
        self._emergency_initialized = False
        self._emergency = False
        self._stale = False
        self._gps_alerted = False
        self._gps_invalid_since: datetime | None = None
        self._last_message_id: int | None = None
        self._source_alerted = False
        self._cancel_zone_listener: Any = None

    async def async_start(self) -> None:
        """Subscribe to optional Home Assistant zone transitions."""
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "device_tracker",
            "bluesky_passage",
            f"{self.runtime.entry.entry_id}_inreach_position",
        )
        if entity_id:
            self._cancel_zone_listener = async_track_state_change_event(
                self.hass, [entity_id], self._handle_zone_change
            )

    async def async_stop(self) -> None:
        if self._cancel_zone_listener:
            self._cancel_zone_listener()
            self._cancel_zone_listener = None

    async def async_clear_routine(self) -> None:
        """Dismiss routine BlueSky notifications when the user disables alerts."""
        for notification_id in (
            NOTIFICATION_STALE,
            NOTIFICATION_GPS,
            NOTIFICATION_SOURCE,
            NOTIFICATION_TEXT,
            NOTIFICATION_ZONE,
        ):
            persistent_notification.async_dismiss(self.hass, notification_id)
        # Reset routine condition latches so re-enabling evaluates the current
        # state cleanly instead of emitting a misleading recovery message.
        self._initialized = False
        self._stale = False
        self._gps_alerted = False
        self._gps_invalid_since = None
        self._source_alerted = False
        self._last_message_id = self._newest_message_id()

    def _handle_zone_change(self, event) -> None:
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not old_state or not new_state or old_state.state == new_state.state:
            return
        if old_state.state in {"unknown", "unavailable"} or new_state.state in {
            "unknown",
            "unavailable",
        }:
            return
        enabled = bool(
            self.runtime.entry.options.get(
                CONF_ZONE_NOTIFICATIONS_ENABLED,
                DEFAULT_ZONE_NOTIFICATIONS_ENABLED,
            )
        )
        if enabled:
            self.hass.async_create_task(
                self._send(
                    NOTIFICATION_ZONE,
                    "BlueSky Passage zone changed",
                    f"The archived inReach tracker changed from {old_state.state} to {new_state.state}.",
                )
            )

    async def async_process(self) -> None:
        async with self._lock:
            latest = self.runtime.latest
            if not latest:
                return
            emergency = latest.get("in_emergency") is True

            # Emergency-state transitions remain active even when the user has
            # disabled routine alerts. Keep this latch independent so routine
            # state can be reset/re-evaluated without repeating an SOS alert.
            if not self._emergency_initialized:
                self._emergency = emergency
                self._emergency_initialized = True
                if emergency:
                    await self._send(
                        NOTIFICATION_EMERGENCY,
                        "🚨 BlueSky Passage emergency",
                        self._location_message(
                            "Garmin MapShare reports that the inReach is in emergency mode."
                        ),
                        force=True,
                    )
            else:
                if emergency and not self._emergency:
                    await self._send(
                        NOTIFICATION_EMERGENCY,
                        "🚨 BlueSky Passage emergency",
                        self._location_message(
                            "Garmin MapShare reports that the inReach is in emergency mode."
                        ),
                        force=True,
                    )
                elif not emergency and self._emergency:
                    await self._send(
                        NOTIFICATION_EMERGENCY,
                        "BlueSky Passage emergency cleared",
                        "Garmin MapShare no longer reports emergency mode. Confirm through Garmin's authoritative channels.",
                        force=True,
                    )
                self._emergency = emergency

            # Routine latches are deliberately not advanced while routine alerts
            # are disabled. Re-enabling therefore evaluates the current stale/GPS/
            # source state cleanly rather than assuming a suppressed alert fired.
            if not self.runtime.notifications_enabled:
                return

            stale = self.runtime.is_stale
            gps_problem = self.runtime.gps_problem
            if not self._initialized:
                self._stale = stale
                self._last_message_id = self._newest_message_id()
                self._initialized = True
                if stale:
                    age = self.runtime.report_age_minutes
                    await self._send(
                        NOTIFICATION_STALE,
                        "BlueSky Passage tracking is stale",
                        (
                            f"The latest report is about {age:.0f} minutes old; "
                            f"the configured threshold is {self.runtime.stale_minutes} minutes."
                            if age is not None
                            else "No Garmin report is archived yet."
                        ),
                    )
            else:
                if stale and not self._stale:
                    await self._send(
                        NOTIFICATION_STALE,
                        "BlueSky Passage tracking is stale",
                        f"The latest report is about {self.runtime.report_age_minutes:.0f} minutes old; the configured threshold is {self.runtime.stale_minutes} minutes.",
                    )
                elif not stale and self._stale:
                    await self._send(
                        NOTIFICATION_STALE,
                        "BlueSky Passage tracking restored",
                        "A report newer than the stale threshold is available.",
                    )
                self._stale = stale

            now = datetime.now(timezone.utc)
            if gps_problem:
                if self._gps_invalid_since is None:
                    timestamp = latest.get("recorded_at_utc")
                    self._gps_invalid_since = parse_utc(timestamp) if timestamp else now
                if (
                    not self._gps_alerted
                    and now - self._gps_invalid_since >= GPS_PROBLEM_DELAY
                ):
                    await self._send(
                        NOTIFICATION_GPS,
                        "BlueSky Passage GPS fix invalid",
                        "The current Garmin record explicitly reports an invalid GPS fix for at least 10 minutes.",
                    )
                    self._gps_alerted = True
            else:
                if self._gps_alerted:
                    await self._send(
                        NOTIFICATION_GPS,
                        "BlueSky Passage GPS fix restored",
                        "The current Garmin record reports a valid GPS fix.",
                    )
                self._gps_invalid_since = None
                self._gps_alerted = False

            message_id = self._newest_message_id()
            if (
                message_id is not None
                and self._last_message_id is not None
                and message_id != self._last_message_id
            ):
                event = (self.runtime.coordinator.data or {}).get("latest_message") or {}
                await self._send(
                    NOTIFICATION_TEXT,
                    "BlueSky Passage new inReach text",
                    str(event.get("text") or "A new text event is available in the passage panel."),
                )
            self._last_message_id = message_id

            source_problem = self.runtime.coordinator.consecutive_failures >= 3
            if source_problem and not self._source_alerted:
                await self._send(
                    NOTIFICATION_SOURCE,
                    "BlueSky Passage source unavailable",
                    "Three consecutive Garmin MapShare polls failed. Stored history remains available; check internet and Garmin service status.",
                )
                self._source_alerted = True
            elif not source_problem and self._source_alerted:
                await self._send(
                    NOTIFICATION_SOURCE,
                    "BlueSky Passage source restored",
                    "The Garmin MapShare poll succeeded again.",
                )
                self._source_alerted = False

    def _newest_message_id(self) -> int | None:
        event = (self.runtime.coordinator.data or {}).get("latest_message") or {}
        value = event.get("id")
        return int(value) if value is not None else None

    def _location_message(self, prefix: str) -> str:
        latest = self.runtime.latest or {}
        latitude, longitude = latest.get("latitude"), latest.get("longitude")
        location = (
            f" Last archived position: {latitude:.5f}, {longitude:.5f}."
            if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float))
            else " No valid archived position is available."
        )
        link_name = self.runtime.entry.data.get("link_name", "")
        link = f" [Open Garmin MapShare](https://share.garmin.com/{link_name})." if link_name else ""
        return (
            prefix
            + location
            + link
            + " Home Assistant is supplementary; use Garmin's normal emergency process."
        )

    async def _send(
        self,
        notification_id: str,
        title: str,
        message: str,
        *,
        force: bool = False,
    ) -> None:
        if not force and not self.runtime.notifications_enabled:
            return
        persistent_notification.async_create(
            self.hass,
            message,
            title=title,
            notification_id=notification_id,
        )
        service_name = str(
            self.runtime.entry.options.get(CONF_MOBILE_NOTIFY_SERVICE, "")
        ).strip()
        if service_name:
            domain, separator, service = service_name.partition(".")
            if separator and domain == "notify" and self.hass.services.has_service(domain, service):
                await self.hass.services.async_call(
                    domain,
                    service,
                    {"title": title, "message": message},
                    blocking=False,
                )

    async def async_test(self) -> None:
        await self._send(
            "bluesky_passage_test",
            "BlueSky Passage test",
            "The local persistent notification path is working. If a mobile notify action is configured, this test is sent there too.",
            force=True,
        )
