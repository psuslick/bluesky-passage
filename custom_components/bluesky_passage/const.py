"""Constants for BlueSky Passage."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "bluesky_passage"
VERSION = "2.2.2"
ENTRY_TITLE = "BlueSky Passage"

CONF_LINK_NAME = "link_name"
CONF_LINK_PASSWORD = "link_password"
CONF_PREDICTWIND_URL = "predictwind_url"
CONF_STALE_MINUTES = "stale_minutes"
CONF_NOTIFICATIONS_ENABLED = "notifications_enabled"
CONF_MOBILE_NOTIFY_SERVICE = "mobile_notify_service"
CONF_ZONE_NOTIFICATIONS_ENABLED = "zone_notifications_enabled"
CONF_XWEATHER_CLIENT_ID = "xweather_client_id"
CONF_XWEATHER_CLIENT_SECRET = "xweather_client_secret"
CONF_SPEED_UNIT = "speed_unit"
CONF_HEIGHT_UNIT = "height_unit"

DEFAULT_STALE_MINUTES = 60
DEFAULT_NOTIFICATIONS_ENABLED = True
DEFAULT_ZONE_NOTIFICATIONS_ENABLED = False
DEFAULT_POLL_INTERVAL = timedelta(minutes=10)
DEFAULT_POLL_OVERLAP = timedelta(hours=48)
GPS_PROBLEM_DELAY = timedelta(minutes=10)
TIMER_INTERVAL = timedelta(minutes=1)
BACKFILL_CHUNK = timedelta(days=7)
MAX_BACKFILL_DAYS = 3650
WEATHER_CACHE_TOLERANCE_MINUTES = 90
WEATHER_SAMPLE_LIMIT = 12

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
SOURCE_RECORDER = "ha_recorder"
SOURCE_CSV = "csv_import"

PROVIDER_XWEATHER = "xweather"
XWEATHER_API_URL = "https://data.api.xweather.com"

MAX_QUERY_POINTS = 10_000
DEFAULT_QUERY_POINTS = 4_000
MAX_IMPORT_CHUNK = 1_000

NOTIFICATION_EMERGENCY = "bluesky_passage_emergency"
NOTIFICATION_STALE = "bluesky_passage_stale"
NOTIFICATION_GPS = "bluesky_passage_gps"
NOTIFICATION_TEXT = "bluesky_passage_text"
NOTIFICATION_SOURCE = "bluesky_passage_source"
NOTIFICATION_ZONE = "bluesky_passage_zone"
