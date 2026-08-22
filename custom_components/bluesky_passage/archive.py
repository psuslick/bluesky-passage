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

    async def async_point_by_id(self, point_id: int) -> dict[str, Any] | None:
        return await self._call(self._database.point_by_id, point_id)

    async def async_query_points(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.query_points, **kwargs)

    async def async_dashboard_state(self) -> dict[str, Any]:
        return await self._call(self._database.dashboard_state)

    async def async_integrity_check(self) -> str:
        return await self._call(self._database.integrity_check)

    async def async_list_passages(self) -> list[dict[str, Any]]:
        return await self._call(self._database.list_passages)

    async def async_passage_detail(self, passage_id: int) -> dict[str, Any]:
        return await self._call(self._database.passage_detail, passage_id)

    async def async_passage_points_and_metrics(
        self, passage_id: int, *, max_points: int = 4000
    ) -> dict[str, Any]:
        return await self._call(
            self._database.passage_points_and_metrics,
            passage_id,
            max_points=max_points,
        )

    async def async_list_destinations(self) -> list[dict[str, Any]]:
        return await self._call(self._database.list_destinations)

    async def async_preview_passage(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.preview_passage, **kwargs)

    async def async_save_passage(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.save_passage, **kwargs)

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

    async def async_preview_records(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.preview_records, *args, **kwargs)

    async def async_create_backfill_job(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.create_backfill_job, **kwargs)

    async def async_get_backfill_job(self, job_id: int) -> dict[str, Any]:
        return await self._call(self._database.get_backfill_job, job_id)

    async def async_list_backfill_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._call(self._database.list_backfill_jobs, limit)

    async def async_next_backfill_chunk(self, job_id: int) -> dict[str, Any] | None:
        return await self._call(self._database.next_backfill_chunk, job_id)

    async def async_complete_backfill_chunk(
        self, job_id: int, chunk_id: int, **kwargs: Any
    ) -> dict[str, Any]:
        return await self._call(
            self._database.complete_backfill_chunk, job_id, chunk_id, **kwargs
        )

    async def async_fail_backfill_chunk(
        self, job_id: int, chunk_id: int, error: str
    ) -> dict[str, Any]:
        return await self._call(
            self._database.fail_backfill_chunk, job_id, chunk_id, error
        )

    async def async_cancel_backfill_job(self, job_id: int) -> dict[str, Any]:
        return await self._call(self._database.cancel_backfill_job, job_id)

    async def async_save_weather_samples(
        self, samples: list[dict[str, Any]]
    ) -> dict[str, int]:
        return await self._call(self._database.save_weather_samples, samples)

    async def async_query_weather_samples(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._call(self._database.query_weather_samples, **kwargs)

    async def async_cached_weather_sample(self, **kwargs: Any) -> dict[str, Any] | None:
        return await self._call(self._database.cached_weather_sample, **kwargs)

    async def async_get_vessel_profile(self) -> dict[str, Any]:
        return await self._call(self._database.get_vessel_profile)

    async def async_save_vessel_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        return await self._call(self._database.save_vessel_profile, profile)

    async def async_add_route_version(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._database.add_route_version, *args, **kwargs)

    async def async_current_route(self, passage_id: int) -> dict[str, Any] | None:
        return await self._call(self._database.current_route, passage_id)
