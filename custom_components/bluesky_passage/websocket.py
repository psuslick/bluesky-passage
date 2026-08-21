"""Authenticated panel API; every mutation is admin-only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_QUERY_POINTS,
    DOMAIN,
    MAX_IMPORT_CHUNK,
    MAX_QUERY_POINTS,
    SOURCE_GARMIN,
    SOURCE_GPX,
    SOURCE_PREDICTWIND,
)
from .exporting import export_points
from .parser import records_from_mappings


def _runtime(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
):
    entries = hass.data.get(DOMAIN, {}).get("entries", {})
    entry_id = msg.get("entry_id")
    runtime = entries.get(entry_id) if entry_id else next(iter(entries.values()), None)
    if runtime is None:
        connection.send_error(msg["id"], "not_configured", "BlueSky Passage is not configured")
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/points",
        vol.Optional("entry_id"): str,
        vol.Optional("range", default="current_passage"): vol.In(
            ["current_passage", "1d", "3d", "7d", "30d", "1y", "all", "custom"]
        ),
        vol.Optional("start_utc"): str,
        vol.Optional("end_utc"): str,
        vol.Optional("source", default="all"): vol.In(
            ["canonical", "all", SOURCE_GARMIN, SOURCE_PREDICTWIND, SOURCE_GPX]
        ),
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
        vol.Optional("notes"): vol.All(str, vol.Length(max=1000)),
    }
)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/start_passage",
        vol.Optional("entry_id"): str,
        vol.Required("name"): vol.All(str, vol.Length(min=1, max=100)),
        vol.Optional("started_at_utc"): str,
        vol.Optional("destination"): DESTINATION_SCHEMA,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_start_passage(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        passage = await runtime.archive.async_start_passage(
            msg["name"].strip(),
            started_at_utc=msg.get("started_at_utc"),
            destination=msg.get("destination"),
        )
        await runtime.async_refresh_local()
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], passage)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_destination",
        vol.Optional("entry_id"): str,
        vol.Required("passage_id"): vol.Coerce(int),
        vol.Required("destination"): DESTINATION_SCHEMA,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_destination(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        destination = msg["destination"]
        passage = await runtime.archive.async_set_destination(
            msg["passage_id"],
            name=destination["name"],
            latitude=destination["latitude"],
            longitude=destination["longitude"],
            arrival_radius_nm=destination["arrival_radius_nm"],
            notes=destination.get("notes"),
        )
        await runtime.async_refresh_local()
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], passage)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/end_passage",
        vol.Optional("entry_id"): str,
        vol.Required("passage_id"): vol.Coerce(int),
        vol.Optional("ended_at_utc"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_end_passage(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        passage = await runtime.archive.async_end_passage(
            msg["passage_id"], msg.get("ended_at_utc")
        )
        await runtime.async_refresh_local()
    except ValueError as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], passage)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_passage",
        vol.Optional("entry_id"): str,
        vol.Required("passage_id"): vol.Coerce(int),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete_passage(hass, connection, msg) -> None:
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
        vol.Required("type"): f"{DOMAIN}/import_begin",
        vol.Optional("entry_id"): str,
        vol.Required("source"): vol.In([SOURCE_PREDICTWIND, SOURCE_GPX]),
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
        vol.Required("source"): vol.In([SOURCE_PREDICTWIND, SOURCE_GPX]),
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
        vol.Required("type"): f"{DOMAIN}/route_add",
        vol.Optional("entry_id"): str,
        vol.Required("passage_id"): vol.Coerce(int),
        vol.Required("label"): vol.All(str, vol.Length(min=1, max=100)),
        vol.Required("source"): vol.All(str, vol.Length(min=1, max=50)),
        vol.Required("sha256"): vol.Match(r"^[0-9a-fA-F]{64}$"),
        vol.Required("coordinates"): vol.All(list, vol.Length(min=2, max=100_000)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_route_add(hass, connection, msg) -> None:
    runtime = _runtime(hass, connection, msg)
    if not runtime:
        return
    try:
        result = await runtime.archive.async_add_route_version(
            msg["passage_id"],
            msg["label"],
            msg["source"],
            msg["sha256"].lower(),
            msg["coordinates"],
        )
        await runtime.async_refresh_local()
    except (TypeError, ValueError) as err:
        _error(connection, msg, err)
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/export",
        vol.Optional("entry_id"): str,
        vol.Required("format"): vol.In(["csv", "geojson", "gpx"]),
        vol.Optional("range", default="current_passage"): vol.In(
            ["current_passage", "1d", "3d", "7d", "30d", "1y", "all", "custom"]
        ),
        vol.Optional("start_utc"): str,
        vol.Optional("end_utc"): str,
        vol.Optional("source", default="all"): vol.In(
            ["canonical", "all", SOURCE_GARMIN, SOURCE_PREDICTWIND, SOURCE_GPX]
        ),
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
        )
        suffix, mime_type, content = export_points(result["points"], msg["format"])
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
    ws_start_passage,
    ws_set_destination,
    ws_end_passage,
    ws_delete_passage,
    ws_test_notification,
    ws_import_begin,
    ws_import_chunk,
    ws_import_finish,
    ws_import_rollback,
    ws_route_add,
    ws_export,
)


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register API commands once per Home Assistant start."""
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
