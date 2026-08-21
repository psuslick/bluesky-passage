"""Privacy-conscious Garmin MapShare feed client."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from .const import GARMIN_FEED_URL
from .parser import KmlParseError, TrackRecord, parse_kml

_LOGGER = logging.getLogger(__name__)


class FeedError(Exception):
    """Base feed exception."""


class FeedAuthenticationError(FeedError):
    """MapShare password is absent or incorrect."""


class FeedLinkError(FeedError):
    """MapShare link name is invalid."""


class FeedConnectionError(FeedError):
    """MapShare could not be reached or parsed."""


def normalize_link_name(value: str) -> str:
    """Accept either a Garmin share name or a full public URL."""
    cleaned = value.strip()
    if "://" in cleaned:
        parsed = urlparse(cleaned)
        cleaned = parsed.path.strip("/").split("/")[-1]
    cleaned = cleaned.strip("/")
    if not cleaned or any(character.isspace() for character in cleaned):
        raise FeedLinkError("Enter the MapShare name or full public link")
    return cleaned


class GarminFeedClient:
    """Fetch every record currently exposed by the MapShare KML feed."""

    def __init__(
        self, hass: HomeAssistant, link_name: str, link_password: str | None
    ) -> None:
        self._client = get_async_client(hass)
        self.link_name = normalize_link_name(link_name)
        self.link_password = link_password or None

    async def async_fetch(self) -> list[TrackRecord]:
        url = GARMIN_FEED_URL.format(self.link_name)
        auth = ("", self.link_password) if self.link_password else None
        try:
            response = await self._client.get(
                url,
                auth=auth,
                follow_redirects=True,
                timeout=60.0,
            )
        except httpx.HTTPError as err:
            raise FeedConnectionError("Garmin MapShare could not be reached") from err
        if response.status_code == 401:
            raise FeedAuthenticationError(
                "The MapShare feed needs a password or rejected the supplied password"
            )
        if response.status_code == 404 or (
            response.status_code == 200 and not response.content
        ):
            raise FeedLinkError("The MapShare link name did not return a feed")
        try:
            response.raise_for_status()
            records = parse_kml(response.content)
        except (httpx.HTTPStatusError, KmlParseError) as err:
            raise FeedConnectionError("Garmin returned an unusable MapShare feed") from err
        # Never log feed bodies, coordinates, or messages.
        _LOGGER.debug(
            "Garmin MapShare poll succeeded with %d timestamped records", len(records)
        )
        return records
