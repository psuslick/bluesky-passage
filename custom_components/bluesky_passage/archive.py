"""Async serialization wrapper around the SQLite archive."""

from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import Any, Callable, TypeVar

from homeassistant.core import HomeAssistant

from .database import SQLiteArchive

_T = TypeVar("_T")


class AsyncArchive:
    """Run blocking SQLite work in Home Assistant's executor, one job at a time."""

    def __init__(self, hass: HomeAssistant, path: str | Path) -> None:
        self._hass = hass
        self._database = SQLiteArchive(path)
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._database.path

    async def _call(self, function: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        async with self._lock:
            return await self._hass.async_add_executor_job(
                partial(function, *args, **kwargs)
            )

    async def async_initialize(self) -> None:
        await self._call(self._database.initialize)

    async def async_ingest(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.ingest_records, *args, **kwargs)

    async def async_latest_point(self) -> dict[str, Any] | None:
        return await self._call(self._database.latest_point)

    async def async_query_points(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.query_points, **kwargs)

    async def async_dashboard_state(self) -> dict[str, Any]:
        return await self._call(self._database.dashboard_state)

    async def async_list_passages(self) -> list[dict[str, Any]]:
        return await self._call(self._database.list_passages)

    async def async_list_destinations(self) -> list[dict[str, Any]]:
        return await self._call(self._database.list_destinations)

    async def async_start_passage(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.start_passage, *args, **kwargs)

    async def async_set_destination(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.set_destination, *args, **kwargs)

    async def async_end_passage(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.end_passage, *args, **kwargs)

    async def async_delete_passage(self, passage_id: int) -> None:
        await self._call(self._database.delete_passage, passage_id)

    async def async_begin_import(self, *args: Any) -> int:
        return await self._call(self._database.begin_import, *args)

    async def async_finish_import(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.finish_import, *args, **kwargs)

    async def async_rollback_import(self, import_id: int) -> dict[str, Any]:
        return await self._call(self._database.rollback_import, import_id)

    async def async_list_imports(self) -> list[dict[str, Any]]:
        return await self._call(self._database.list_imports)

    async def async_add_route_version(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.add_route_version, *args, **kwargs)

    async def async_current_route(self, passage_id: int) -> dict[str, Any] | None:
        return await self._call(self._database.current_route, passage_id)
