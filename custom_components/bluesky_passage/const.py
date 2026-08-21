"""Constants for BlueSky Passage."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "bluesky_passage"
VERSION = "2.0.1"
ENTRY_TITLE = "BlueSky Passage"

CONF_LINK_NAME = "link_name"
CONF_LINK_PASSWORD = "link_password"
CONF_PREDICTWIND_URL = "predictwind_url"
CONF_STALE_MINUTES = "stale_minutes"
CONF_NOTIFICATIONS_ENABLED = "notifications_enabled"
CONF_MOBILE_NOTIFY_SERVICE = "mobile_notify_service"
CONF_ZONE_NOTIFICATIONS_ENABLED = "zone_notifications_enabled"

DEFAULT_STALE_MINUTES = 60
DEFAULT_NOTIFICATIONS_ENABLED = True
DEFAULT_ZONE_NOTIFICATIONS_ENABLED = False
DEFAULT_POLL_INTERVAL = timedelta(minutes=10)
GPS_PROBLEM_DELAY = timedelta(minutes=10)
TIMER_INTERVAL = timedelta(minutes=1)

GARMIN_FEED_URL = "https://share.garmin.com/Feed/Share/{}"
GARMIN_MAP_URL = "https://share.garmin.com/{}"

ARCHIVE_DIRECTORY = "bluesky_passage"
ARCHIVE_FILENAME = "archive.sqlite3"

PANEL_URL_PATH = "bluesky-passage"
PANEL_TITLE = "BlueSky Passage"
PANEL_ICON = "mdi:sail-boat"
PANEL_COMPONENT = "bluesky-passage-panel"
PANEL_STATIC_URL = "/bluesky_passage_static/panel.js"

EVENT_DATA_UPDATED = "bluesky_passage_data_updated"

SOURCE_GARMIN = "garmin_mapshare"
SOURCE_PREDICTWIND = "predictwind_snapshot"
SOURCE_GPX = "gpx_import"

MAX_QUERY_POINTS = 10_000
DEFAULT_QUERY_POINTS = 4_000
MAX_IMPORT_CHUNK = 1_000

NOTIFICATION_EMERGENCY = "bluesky_passage_emergency"
NOTIFICATION_STALE = "bluesky_passage_stale"
NOTIFICATION_GPS = "bluesky_passage_gps"
NOTIFICATION_TEXT = "bluesky_passage_text"
NOTIFICATION_ARRIVAL = "bluesky_passage_arrival"
NOTIFICATION_SOURCE = "bluesky_passage_source"
NOTIFICATION_ZONE = "bluesky_passage_zone"
