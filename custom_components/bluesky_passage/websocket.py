"""Authenticated panel API; every mutation and provider call is admin-only."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_QUERY_POINTS,
    DOMAIN,
    MAX_IMPORT_CHUNK,
    MAX_QUERY_POINTS,
    SOURCE_CSV,
    SOURCE_GARMIN,
    SOURCE_GPX,
    SOURCE_PREDICTWIND,
    SOURCE_RECORDER,
)
from .exporting import export_points
from .parser import records_from_mappings

_LOGGER = logging.getLogger(__name__)

RANGES = ["24h", "1d", "3d", "7d", "30d", "1y", "all", "custom", "passage"]
SOURCES = [
    "canonical",
    "all",
    SOURCE_GARMIN,
    SOURCE_PREDICTWIND,
    SOURCE_GPX,
    SOURCE_RECORDER,
    SOURCE_CSV,
]


def _runtime(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
):
    entries = hass.data.get(DOMAIN, {}).get("entries", {})
    entry_id = msg.get("entry_id")
    runtime = entries.get(entry_id) if entry_id else next(iter(entries.values()), None)
    if runtime is None:
        connection.send_error(
            msg["id"], "not_configured", "BlueSky Passage is not configured"
        )
    return runtime


def _error(connection, msg, err: Exception) -> None:
    connection.send_error(msg["id"], "invalid_request", str(err))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/state", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_state(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if runtime:
        connection.send_result(msg["id"], await runtime.async_state())


POINTS_FIELDS = {
    vol.Optional("entry_id"): str,
    vol.Optional("range", default="24h"): vol.In(RANGES),
    vol.Optional("start_utc"): str,
    vol.Optional("end_utc"): str,
    vol.Optional("source", default=SOURCE_GARMIN): vol.In(SOURCES),
    vol.Optional("passage_id"): vol.Coerce(int),
    vol.Optional("start_report_id"): vol.Coerce(int),
    vol.Optional("end_report_id"): vol.Coerce(int),
}


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/points",
        **POINTS_FIELDS,
        vol.Optional("max_points", default=DEFAULT_QUERY_POINTS): vol.All(
            vol.Coerce(int), vol.Range(min=100, max=MAX_QUERY_POINTS)
        ),
    }
)
@websocket_api.async_response
async def ws_points(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_query_range(
            msg["range"],
            start_utc=msg.get("start_utc"),
            end_utc=msg.get("end_utc"),
            source=msg["source"],
            max_points=msg["max_points"],
            passage_id=msg.get("passage_id"),
            start_report_id=msg.get("start_report_id"),
            end_report_id=msg.get("end_report_id"),
        )
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/refresh", vol.Optional("entry_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_refresh(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if runtime:
        await runtime.coordinator.async_request_refresh()
        if runtime.notifications:
            await runtime.notifications.async_process()
        connection.send_result(msg["id"], await runtime.async_state())


DESTINATION_SCHEMA = vol.Schema(
    {
        vol.Required("name"): vol.All(str, vol.Length(min=1, max=100)),
        vol.Required("latitude"): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
        vol.Required("longitude"): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
        vol.Optional("arrival_radius_nm", default=2.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=100)
        ),
        vol.Optional("effective_at_utc"): str,
        vol.Optional("notes"): vol.All(str, vol.Length(max=2000)),
    }
)

PASSAGE_FIELDS = {
    vol.Optional("entry_id"): str,
    vol.Optional("passage_id"): vol.Coerce(int),
    vol.Required("name"): vol.All(str, vol.Length(min=1, max=100)),
    vol.Required("start_utc"): str,
    vol.Optional("end_utc"): vol.Any(None, str),
    vol.Optional("departure_name"): vol.All(str, vol.Length(max=100)),
    vol.Optional("departure_latitude"): vol.Any(
        None, vol.All(vol.Coerce(float), vol.Range(min=-90, max=90))
    ),
    vol.Optional("departure_longitude"): vol.Any(
        None, vol.All(vol.Coerce(float), vol.Range(min=-180, max=180))
    ),
    vol.Optional("notes"): vol.All(str, vol.Length(max=5000)),
    vol.Optional("destination"): vol.Any(None, DESTINATION_SCHEMA),
    vol.Optional("clear_destination", default=False): bool,
}


def _passage_arguments(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "passage_id": msg.get("passage_id"),
        "name": msg["name"],
        "start_utc": msg["start_utc"],
        "end_utc": msg.get("end_utc") or None,
        "departure_name": msg.get("departure_name"),
        "departure_latitude": msg.get("departure_latitude"),
        "departure_longitude": msg.get("departure_longitude"),
        "notes": msg.get("notes"),
        "destination": msg.get("destination"),
        "clear_destination": msg.get("clear_destination", False),
    }


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/passage_preview", **PASSAGE_FIELDS}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_passage_preview(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.archive.async_preview_passage(
            **_passage_arguments(msg)
        )
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/passage_save",
        **PASSAGE_FIELDS,
        vol.Required("preview_token"): vol.Match(r"^[0-9a-f]{64}$"),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_passage_save(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.archive.async_save_passage(
            **_passage_arguments(msg),
            preview_token=msg["preview_token"],
        )
        await runtime.async_refresh_local()
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/passage_detail",
        vol.Optional("entry_id"): str,
        vol.Required("passage_id"): vol.Coerce(int),
        vol.Optional("include_analysis", default=True): bool,
    }
)
@websocket_api.async_response
async def ws_passage_detail(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_passage_detail(
            msg["passage_id"], include_analysis=msg["include_analysis"]
        )
    except ValueError as err:
        _error(connection, msg, err)
    except Exception:
        _LOGGER.exception(
            "BlueSky Passage could not load passage detail for passage %s",
            msg["passage_id"],
        )
        connection.send_error(
            msg["id"],
            "passage_detail_failed",
            "Passage details could not be loaded. Check the Home Assistant log for the underlying error.",
        )
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/passage_delete",
        vol.Optional("entry_id"): str,
        vol.Required("passage_id"): vol.Coerce(int),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_passage_delete(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        await runtime.archive.async_delete_passage(msg["passage_id"])
        await runtime.async_refresh_local()
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], {"deleted": msg["passage_id"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/profile_save",
        vol.Optional("entry_id"): str,
        vol.Required("profile"): dict,
        vol.Optional("passage_id"): vol.Coerce(int),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_profile_save(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_save_profile(
            msg["profile"], msg.get("passage_id")
        )
    except (TypeError, ValueError) as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/weather_enrich",
        **POINTS_FIELDS,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_weather_enrich(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_enrich_weather(
            range_name=msg["range"],
            start_utc=msg.get("start_utc"),
            end_utc=msg.get("end_utc"),
            passage_id=msg.get("passage_id"),
            start_report_id=msg.get("start_report_id"),
            end_report_id=msg.get("end_report_id"),
        )
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/route_plan",
        vol.Optional("entry_id"): str,
        vol.Required("passage_id"): vol.Coerce(int),
        vol.Optional("departure_at_utc"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_route_plan(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_plan_route(
            msg["passage_id"], msg.get("departure_at_utc")
        )
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/backfill_preview",
        vol.Optional("entry_id"): str,
        vol.Required("start_utc"): str,
        vol.Required("end_utc"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backfill_preview(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_create_backfill_preview(
            msg["start_utc"], msg["end_utc"]
        )
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/backfill_commit",
        vol.Optional("entry_id"): str,
        vol.Required("preview_job_id"): vol.Coerce(int),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backfill_commit(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_create_backfill_commit(msg["preview_job_id"])
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/backfill_step",
        vol.Optional("entry_id"): str,
        vol.Required("job_id"): vol.Coerce(int),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backfill_step(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_backfill_step(msg["job_id"])
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/backfill_cancel",
        vol.Optional("entry_id"): str,
        vol.Required("job_id"): vol.Coerce(int),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_backfill_cancel(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.archive.async_cancel_backfill_job(msg["job_id"])
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/integrity", vol.Optional("entry_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_integrity(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if runtime:
        result = await runtime.archive.async_integrity_check()
        await runtime.async_refresh_local()
        connection.send_result(msg["id"], {"integrity": result})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/test_notification", vol.Optional("entry_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_test_notification(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if runtime:
        await runtime.notifications.async_test()
        connection.send_result(msg["id"], {"sent": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_preview",
        vol.Optional("entry_id"): str,
        vol.Required("source"): vol.In(
            [SOURCE_PREDICTWIND, SOURCE_GPX, SOURCE_RECORDER, SOURCE_CSV]
        ),
        vol.Required("records"): vol.All(
            list, vol.Length(min=1, max=MAX_QUERY_POINTS)
        ),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import_preview(hass, connection, msg) -> None:
    """Preview normalized import rows without creating an import batch."""
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        records = records_from_mappings(msg["records"])
        result = await runtime.archive.async_preview_records(
            records, msg["source"]
        )
        result["rejected"] = len(msg["records"]) - len(records)
    except (TypeError, ValueError) as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_begin",
        vol.Optional("entry_id"): str,
        vol.Required("source"): vol.In(
            [SOURCE_PREDICTWIND, SOURCE_GPX, SOURCE_RECORDER, SOURCE_CSV]
        ),
        vol.Required("filename"): vol.All(str, vol.Length(min=1, max=255)),
        vol.Required("sha256"): vol.Match(r"^[0-9a-fA-F]{64}$"),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import_begin(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        import_id = await runtime.archive.async_begin_import(
            msg["source"], msg["filename"], msg["sha256"].lower()
        )
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], {"import_id": import_id})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_chunk",
        vol.Optional("entry_id"): str,
        vol.Required("import_id"): vol.Coerce(int),
        vol.Required("source"): vol.In(
            [SOURCE_PREDICTWIND, SOURCE_GPX, SOURCE_RECORDER, SOURCE_CSV]
        ),
        vol.Required("records"): vol.All(list, vol.Length(min=1, max=MAX_IMPORT_CHUNK)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import_chunk(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        records = records_from_mappings(msg["records"])
        result = await runtime.archive.async_ingest(
            records,
            msg["source"],
            import_id=msg["import_id"],
            live=False,
        )
        result["rejected"] = len(msg["records"]) - len(records)
    except (TypeError, ValueError) as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_finish",
        vol.Optional("entry_id"): str,
        vol.Required("import_id"): vol.Coerce(int),
        vol.Optional("failed", default=False): bool,
        vol.Optional("notes"): vol.All(str, vol.Length(max=1000)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import_finish(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.archive.async_finish_import(
            msg["import_id"], failed=msg["failed"], notes=msg.get("notes")
        )
        await runtime.async_refresh_local()
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_rollback",
        vol.Optional("entry_id"): str,
        vol.Required("import_id"): vol.Coerce(int),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import_rollback(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.archive.async_rollback_import(msg["import_id"])
        await runtime.async_refresh_local()
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/export",
        vol.Required("format"): vol.In(["csv", "geojson", "gpx"]),
        **POINTS_FIELDS,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_export(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.async_query_range(
            msg["range"],
            start_utc=msg.get("start_utc"),
            end_utc=msg.get("end_utc"),
            source=msg["source"],
            max_points=100_000,
            passage_id=msg.get("passage_id"),
            start_report_id=msg.get("start_report_id"),
            end_report_id=msg.get("end_report_id"),
        )
        suffix, mime_type, content = export_points(
            result["points"],
            msg["format"],
            weather_samples=result.get("weather_samples"),
        )
    except ValueError as err:
        _error(connection, msg, err)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        connection.send_result(
            msg["id"],
            {
                "filename": f"bluesky-passage-{stamp}.{suffix}",
                "mime_type": mime_type,
                "content": content,
                "returned": result["returned"],
                "total_matching": result["total_matching"],
                "decimated": result["decimated"],
            },
        )


COMMANDS = (
    ws_state,
    ws_points,
    ws_refresh,
    ws_passage_preview,
    ws_passage_save,
    ws_passage_detail,
    ws_passage_delete,
    ws_profile_save,
    ws_weather_enrich,
    ws_route_plan,
    ws_backfill_preview,
    ws_backfill_commit,
    ws_backfill_step,
    ws_backfill_cancel,
    ws_integrity,
    ws_test_notification,
    ws_import_preview,
    ws_import_begin,
    ws_import_chunk,
    ws_import_finish,
    ws_import_rollback,
    ws_export,
)


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register API commands once per Home Assistant start."""
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
