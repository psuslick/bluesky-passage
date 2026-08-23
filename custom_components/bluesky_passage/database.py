"""Dedicated SQLite archive for BlueSky Passage.

The archive is intentionally separate from Home Assistant Recorder. Recorder is
optimized for entity state history and retention; this database preserves the
source records, messages, provenance, passage metadata, and destination history
without an automatic purge.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .calculations import destination_metrics, haversine_nm, parse_utc
from .parser import TrackRecord, raw_json

SCHEMA_VERSION = 2
GAP_SECONDS = 90 * 60
IMPOSSIBLE_SPEED_KN = 80.0


def utc_now() -> str:
    """Return a normalized UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def route_context_hash(
    passage: dict[str, Any], profile_updated_at_utc: str | None
) -> str:
    """Fingerprint every saved input that can make a route comparison stale."""
    context = {
        "started_at_utc": passage.get("started_at_utc"),
        "ended_at_utc": passage.get("ended_at_utc"),
        "departure_latitude": passage.get("departure_latitude"),
        "departure_longitude": passage.get("departure_longitude"),
        "destination_version_id": passage.get("current_destination_version_id"),
        "profile_updated_at_utc": profile_updated_at_utc,
        # Routing semantics are part of the saved context. This intentionally
        # marks every pre-2.2 three-corridor result stale after upgrade.
        "routing_engine": "isochrone-water-v2",
    }
    return hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class SQLiteArchive:
    """Synchronous archive; Home Assistant calls it through its executor."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._last_integrity = "not_checked"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA wal_autocheckpoint = 1000")
        connection.execute("PRAGMA journal_size_limit = 8388608")
        return connection

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        """Add one backward-compatible column when upgrading an archive."""
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def initialize(self) -> None:
        """Create/migrate the archive and verify it opens cleanly."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA wal_autocheckpoint = 1000")
            connection.execute("PRAGMA journal_size_limit = 8388608")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL UNIQUE,
                    imported_at_utc TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','rolled_back','failed')),
                    rows_seen INTEGER NOT NULL DEFAULT 0,
                    rows_inserted INTEGER NOT NULL DEFAULT 0,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS track_points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_event_id TEXT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    device_imei TEXT,
                    device_name TEXT,
                    device_type TEXT,
                    recorded_at_utc TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    elevation_m REAL,
                    sog_kn REAL,
                    cog_true REAL,
                    valid_gps_fix INTEGER,
                    in_emergency INTEGER,
                    event_text TEXT,
                    message_text TEXT,
                    spatial_ref TEXT,
                    raw_json TEXT NOT NULL,
                    quality_flags_json TEXT NOT NULL DEFAULT '[]',
                    import_id INTEGER REFERENCES imports(id),
                    ingested_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_track_points_time
                    ON track_points(recorded_at_utc);
                CREATE INDEX IF NOT EXISTS idx_track_points_source_time
                    ON track_points(source, recorded_at_utc);
                CREATE INDEX IF NOT EXISTS idx_track_points_device_time
                    ON track_points(device_imei, recorded_at_utc);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_point_id INTEGER NOT NULL REFERENCES track_points(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('event','message')),
                    text TEXT NOT NULL,
                    UNIQUE(track_point_id, kind, text)
                );

                CREATE INDEX IF NOT EXISTS idx_events_time ON events(recorded_at_utc);

                CREATE TABLE IF NOT EXISTS destinations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    notes TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS passages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('planned','active','arrived','completed')),
                    started_at_utc TEXT,
                    arrived_at_utc TEXT,
                    ended_at_utc TEXT,
                    start_point_id INTEGER REFERENCES track_points(id),
                    current_destination_version_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_passage
                    ON passages((1)) WHERE status IN ('active','arrived');

                CREATE TABLE IF NOT EXISTS destination_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    passage_id INTEGER NOT NULL REFERENCES passages(id) ON DELETE CASCADE,
                    destination_id INTEGER REFERENCES destinations(id),
                    name TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    arrival_radius_nm REAL NOT NULL,
                    effective_at_utc TEXT NOT NULL,
                    notes TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_destination_versions_passage
                    ON destination_versions(passage_id, effective_at_utc);

                CREATE TABLE IF NOT EXISTS route_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    passage_id INTEGER REFERENCES passages(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    source TEXT NOT NULL,
                    imported_at_utc TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    route_json TEXT NOT NULL,
                    UNIQUE(passage_id, content_sha256)
                );

                CREATE INDEX IF NOT EXISTS idx_route_versions_passage
                    ON route_versions(passage_id, imported_at_utc);

                CREATE TABLE IF NOT EXISTS weather_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_key TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT 'track',
                    track_point_id INTEGER,
                    passage_id INTEGER,
                    route_candidate TEXT,
                    quality_state TEXT NOT NULL,
                    valid_at_utc TEXT NOT NULL,
                    requested_at_utc TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    wind_speed_kn REAL,
                    wind_gust_kn REAL,
                    wind_dir_deg REAL,
                    wave_height_m REAL,
                    wave_dir_deg REAL,
                    wave_period_s REAL,
                    current_speed_kn REAL,
                    current_dir_deg REAL,
                    pressure_hpa REAL,
                    sea_surface_temp_c REAL,
                    conditions_available INTEGER NOT NULL DEFAULT 0,
                    maritime_available INTEGER NOT NULL DEFAULT 0,
                    warnings_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE INDEX IF NOT EXISTS idx_weather_samples_time
                    ON weather_samples(valid_at_utc);
                CREATE INDEX IF NOT EXISTS idx_weather_samples_provider_time
                    ON weather_samples(provider, valid_at_utc);

                CREATE TABLE IF NOT EXISTS vessel_profiles (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    profile_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backfill_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phase TEXT NOT NULL CHECK(phase IN ('preview','commit')),
                    start_utc TEXT NOT NULL,
                    end_utc TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled')),
                    preview_job_id INTEGER REFERENCES backfill_jobs(id),
                    import_id INTEGER REFERENCES imports(id),
                    chunks_total INTEGER NOT NULL DEFAULT 0,
                    chunks_completed INTEGER NOT NULL DEFAULT 0,
                    records_returned INTEGER NOT NULL DEFAULT 0,
                    records_inserted INTEGER NOT NULL DEFAULT 0,
                    records_duplicated INTEGER NOT NULL DEFAULT 0,
                    records_rejected INTEGER NOT NULL DEFAULT 0,
                    first_recorded_at_utc TEXT,
                    last_recorded_at_utc TEXT,
                    error TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backfill_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL REFERENCES backfill_jobs(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    start_utc TEXT NOT NULL,
                    end_utc TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
                    records_returned INTEGER NOT NULL DEFAULT 0,
                    records_inserted INTEGER NOT NULL DEFAULT 0,
                    records_duplicated INTEGER NOT NULL DEFAULT 0,
                    records_rejected INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    UNIQUE(job_id, chunk_index)
                );

                CREATE INDEX IF NOT EXISTS idx_backfill_chunks_job_status
                    ON backfill_chunks(job_id, status, chunk_index);
                """
            )
            connection.execute("DROP INDEX IF EXISTS idx_one_open_passage")
            self._ensure_column(connection, "passages", "notes", "TEXT")
            self._ensure_column(connection, "passages", "departure_name", "TEXT")
            self._ensure_column(connection, "passages", "departure_latitude", "REAL")
            self._ensure_column(connection, "passages", "departure_longitude", "REAL")
            self._ensure_column(
                connection,
                "route_versions",
                "summary_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                connection, "route_versions", "departure_at_utc", "TEXT"
            )
            self._ensure_column(
                connection, "route_versions", "weather_generated_at_utc", "TEXT"
            )
            self._ensure_column(
                connection, "weather_samples", "purpose", "TEXT NOT NULL DEFAULT 'track'"
            )
            self._ensure_column(connection, "weather_samples", "track_point_id", "INTEGER")
            self._ensure_column(connection, "weather_samples", "passage_id", "INTEGER")
            self._ensure_column(connection, "weather_samples", "route_candidate", "TEXT")
            # v2.1 models passages as editable temporal annotations. Preserve
            # legacy rows and timestamps while retiring live operating states.
            connection.execute(
                "UPDATE passages SET status='planned' WHERE status IN ('active','arrived')"
            )
            # A power loss between completing the last committed backfill chunk
            # and closing its import batch must not leave a permanent
            # "running" batch. All imported rows are already transactional.
            connection.execute(
                "UPDATE imports SET status='completed',notes=COALESCE(notes,?) "
                "WHERE status='running' AND id IN ("
                "SELECT import_id FROM backfill_jobs "
                "WHERE phase='commit' AND status='completed' AND import_id IS NOT NULL)",
                ("Recovered completed backfill during archive startup",),
            )
            connection.execute(
                "INSERT INTO schema_info(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise sqlite3.DatabaseError(f"Archive quick_check failed: {check}")
            self._last_integrity = str(check)

    @staticmethod
    def _quality_flags(
        record: TrackRecord,
        previous: dict[str, Any] | None,
        latest_existing_time: str | None,
        live: bool,
    ) -> tuple[list[str], float | None, float | None]:
        flags: list[str] = []
        latitude, longitude = record.latitude, record.longitude
        if (
            latitude is None
            or longitude is None
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            flags.append("invalid_coordinates")
            latitude, longitude = None, None
        if record.valid_gps_fix is False:
            flags.append("invalid_gps_fix")
        if live and latest_existing_time and record.recorded_at_utc < latest_existing_time:
            flags.append("out_of_order")

        if previous and latitude is not None and longitude is not None:
            previous_time = parse_utc(previous["recorded_at_utc"])
            current_time = parse_utc(record.recorded_at_utc)
            elapsed_hours = (current_time - previous_time).total_seconds() / 3600
            if elapsed_hours > GAP_SECONDS / 3600:
                flags.append("long_gap")
            if elapsed_hours > 0:
                distance = haversine_nm(
                    float(previous["latitude"]),
                    float(previous["longitude"]),
                    latitude,
                    longitude,
                )
                if distance / elapsed_hours > IMPOSSIBLE_SPEED_KN:
                    flags.append("impossible_jump")
        return flags, latitude, longitude

    def ingest_records(
        self,
        records: Iterable[TrackRecord],
        source: str,
        *,
        import_id: int | None = None,
        live: bool = False,
    ) -> dict[str, Any]:
        """Insert unseen records atomically and return an ingestion summary."""
        ordered = sorted(records, key=lambda record: record.recorded_at_utc)
        inserted_ids: list[int] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if import_id is not None:
                import_row = connection.execute(
                    "SELECT source,status FROM imports WHERE id=?", (import_id,)
                ).fetchone()
                if not import_row or import_row["status"] != "running":
                    raise ValueError("Import is not running")
                if import_row["source"] != source:
                    raise ValueError("Import source does not match its registered batch")
            latest_row = connection.execute(
                "SELECT recorded_at_utc FROM track_points WHERE source=? "
                "ORDER BY recorded_at_utc DESC LIMIT 1",
                (source,),
            ).fetchone()
            latest_existing_time = latest_row[0] if latest_row else None
            previous = _dict(
                connection.execute(
                    "SELECT recorded_at_utc, latitude, longitude FROM track_points "
                    "WHERE source=? AND latitude IS NOT NULL AND longitude IS NOT NULL "
                    "ORDER BY recorded_at_utc DESC LIMIT 1",
                    (source,),
                ).fetchone()
            )
            ingested_at = utc_now()

            for record in ordered:
                flags, latitude, longitude = self._quality_flags(
                    record, previous, latest_existing_time, live
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO track_points(
                        source, source_event_id, dedupe_key, device_imei,
                        device_name, device_type, recorded_at_utc, latitude,
                        longitude, elevation_m, sog_kn, cog_true, valid_gps_fix,
                        in_emergency, event_text, message_text, spatial_ref,
                        raw_json, quality_flags_json, import_id, ingested_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        source,
                        record.source_event_id,
                        record.dedupe_key(source),
                        record.device_imei,
                        record.device_name,
                        record.device_type,
                        record.recorded_at_utc,
                        latitude,
                        longitude,
                        record.elevation_m,
                        record.sog_kn,
                        record.cog_true,
                        None if record.valid_gps_fix is None else int(record.valid_gps_fix),
                        None if record.in_emergency is None else int(record.in_emergency),
                        record.event_text,
                        record.message_text,
                        record.spatial_ref,
                        raw_json(record),
                        json.dumps(flags, separators=(",", ":")),
                        import_id,
                        ingested_at,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                point_id = int(cursor.lastrowid)
                inserted_ids.append(point_id)
                if record.event_text:
                    connection.execute(
                        "INSERT OR IGNORE INTO events(track_point_id,source,recorded_at_utc,kind,text) "
                        "VALUES(?,?,?,?,?)",
                        (point_id, source, record.recorded_at_utc, "event", record.event_text),
                    )
                if record.message_text:
                    connection.execute(
                        "INSERT OR IGNORE INTO events(track_point_id,source,recorded_at_utc,kind,text) "
                        "VALUES(?,?,?,?,?)",
                        (point_id, source, record.recorded_at_utc, "message", record.message_text),
                    )
                if latitude is not None and longitude is not None:
                    previous = {
                        "recorded_at_utc": record.recorded_at_utc,
                        "latitude": latitude,
                        "longitude": longitude,
                    }

            if import_id is not None:
                connection.execute(
                    "UPDATE imports SET rows_seen=rows_seen+?, rows_inserted=rows_inserted+? WHERE id=?",
                    (len(ordered), len(inserted_ids), import_id),
                )

            if inserted_ids and source == "garmin_mapshare":
                connection.execute(
                    """
                    UPDATE passages
                    SET start_point_id=(
                        SELECT id FROM track_points
                        WHERE source='garmin_mapshare'
                          AND recorded_at_utc >= passages.started_at_utc
                          AND latitude IS NOT NULL AND longitude IS NOT NULL
                        ORDER BY recorded_at_utc LIMIT 1
                    ), updated_at_utc=?
                    WHERE start_point_id IS NULL
                    """,
                    (ingested_at,),
                )
            connection.commit()

        return {
            "seen": len(ordered),
            "inserted": len(inserted_ids),
            "inserted_ids": inserted_ids,
        }

    def latest_point(self, source: str = "garmin_mapshare") -> dict[str, Any] | None:
        """Return the newest source point."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM track_points WHERE source=? "
                "ORDER BY recorded_at_utc DESC, id DESC LIMIT 1",
                (source,),
            ).fetchone()
            return self._normalize_point(row)

    def point_by_id(self, point_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            return self._normalize_point(
                connection.execute(
                    "SELECT * FROM track_points WHERE id=?", (point_id,)
                ).fetchone()
            )

    @staticmethod
    def _normalize_point(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        point = dict(row)
        point["valid_gps_fix"] = (
            None if point["valid_gps_fix"] is None else bool(point["valid_gps_fix"])
        )
        point["in_emergency"] = (
            None if point["in_emergency"] is None else bool(point["in_emergency"])
        )
        point["quality_flags"] = json.loads(point.pop("quality_flags_json") or "[]")
        point.pop("raw_json", None)
        return point

    def query_points(
        self,
        *,
        start_utc: str | None = None,
        end_utc: str | None = None,
        source: str | None = None,
        max_points: int = 4000,
    ) -> dict[str, Any]:
        """Return actual archive rows, decimated only when required for display."""
        canonical = source == "canonical"
        clauses: list[str] = []
        parameters: list[Any] = []
        if start_utc:
            clauses.append("recorded_at_utc>=?")
            parameters.append(start_utc)
        if end_utc:
            clauses.append("recorded_at_utc<=?")
            parameters.append(end_utc)
        if source and not canonical:
            clauses.append("source=?")
            parameters.append(source)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM track_points" + where + " ORDER BY recorded_at_utc,id",
                parameters,
            ).fetchall()

        points = [self._normalize_point(row) for row in rows]
        points = [point for point in points if point is not None]
        if canonical:
            priority = {"garmin_mapshare": 3, "predictwind_snapshot": 2, "gpx_import": 1}
            by_timestamp: dict[str, dict[str, Any]] = {}
            for point in points:
                key = point["recorded_at_utc"]
                existing = by_timestamp.get(key)
                if existing is None or priority.get(point["source"], 0) > priority.get(
                    existing["source"], 0
                ):
                    by_timestamp[key] = point
            points = [by_timestamp[key] for key in sorted(by_timestamp)]
        previous_by_source: dict[str, dict[str, Any]] = {}
        cumulative_by_source: dict[str, float] = {}
        daily_by_source: dict[tuple[str, str], float] = {}
        for point in points:
            point_source = str(point.get("source") or "unknown")
            track_key = "canonical" if canonical else point_source
            point["display_track"] = track_key
            previous = previous_by_source.get(track_key)
            point["minutes_from_prior"] = None
            point["distance_from_prior_nm"] = None
            point["break_before"] = False
            cumulative_by_source.setdefault(track_key, 0.0)
            if previous:
                seconds = (
                    parse_utc(point["recorded_at_utc"])
                    - parse_utc(previous["recorded_at_utc"])
                ).total_seconds()
                point["minutes_from_prior"] = round(seconds / 60, 1)
                if (
                    point.get("latitude") is not None
                    and point.get("longitude") is not None
                    and previous.get("latitude") is not None
                    and previous.get("longitude") is not None
                ):
                    segment_distance = haversine_nm(
                            float(previous["latitude"]),
                            float(previous["longitude"]),
                            float(point["latitude"]),
                            float(point["longitude"]),
                    )
                    point["distance_from_prior_nm"] = round(segment_distance, 3)
                    cumulative_by_source[track_key] += segment_distance
                    day = point["recorded_at_utc"][:10]
                    key = (track_key, day)
                    daily_by_source[key] = daily_by_source.get(key, 0.0) + segment_distance
                point["break_before"] = seconds > GAP_SECONDS
            point["cumulative_distance_nm"] = round(
                cumulative_by_source[track_key], 3
            )
            previous_by_source[track_key] = point

        total = len(points)
        display_points = self._decimate(points, max_points)
        return {
            "points": display_points,
            "total_matching": total,
            "returned": len(display_points),
            "decimated": len(display_points) < total,
            "daily_runs": [
                {"source": item_source, "date_utc": day, "distance_nm": round(distance, 2)}
                for (item_source, day), distance in sorted(daily_by_source.items())
            ],
        }

    @staticmethod
    def _decimate(points: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """Bound a display payload while preserving endpoints and local extrema.

        Each interior bucket contributes the lowest and highest speed values
        (falling back to its midpoint when speed is unavailable). This avoids
        the misleading smoothing caused by selecting only evenly spaced rows.
        """
        if len(points) <= limit:
            return points
        if limit <= 1:
            return [points[-1]]
        if limit == 2:
            return [points[0], points[-1]]
        bucket_count = max(1, (limit - 2) // 2)
        width = (len(points) - 2) / bucket_count
        indices = {0, len(points) - 1}
        for bucket in range(bucket_count):
            start = 1 + int(bucket * width)
            end = min(len(points) - 1, 1 + int((bucket + 1) * width))
            candidates = list(range(start, max(start + 1, end)))
            numeric = [
                index
                for index in candidates
                if isinstance(points[index].get("sog_kn"), (int, float))
            ]
            if numeric:
                indices.add(min(numeric, key=lambda index: points[index]["sog_kn"]))
                indices.add(max(numeric, key=lambda index: points[index]["sog_kn"]))
            elif candidates:
                indices.add(candidates[len(candidates) // 2])
        if len(indices) < limit:
            remaining = [index for index in range(1, len(points) - 1) if index not in indices]
            needed = limit - len(indices)
            if remaining and needed:
                indices.update(
                    remaining[
                        round(index * (len(remaining) - 1) / max(needed - 1, 1))
                    ]
                    for index in range(needed)
                )
        critical = {
            index
            for index, point in enumerate(points)
            if index in {0, len(points) - 1}
            or point.get("break_before")
            or point.get("in_emergency") is True
            or bool(
                {"invalid_gps_fix", "impossible_jump"}
                & set(point.get("quality_flags") or [])
            )
        }
        indices.update(critical)
        if len(indices) > limit:
            if len(critical) >= limit:
                ordered = sorted(critical)
                indices = {
                    ordered[round(index * (len(ordered) - 1) / (limit - 1))]
                    for index in range(limit)
                }
            else:
                remaining = sorted(indices - critical)
                needed = limit - len(critical)
                selected = {
                    remaining[
                        round(index * (len(remaining) - 1) / max(needed - 1, 1))
                    ]
                    for index in range(needed)
                } if remaining and needed else set()
                indices = critical | selected
        return [points[index] for index in sorted(indices)]

    def dashboard_state(self) -> dict[str, Any]:
        """Return lightweight tracking state independent of passage metadata."""
        with self._connect() as connection:
            latest = self._normalize_point(
                connection.execute(
                    "SELECT * FROM track_points WHERE source='garmin_mapshare' "
                    "ORDER BY recorded_at_utc DESC,id DESC LIMIT 1"
                ).fetchone()
            )
            first_time = connection.execute(
                "SELECT MIN(recorded_at_utc) FROM track_points"
            ).fetchone()[0]
            last_time = connection.execute(
                "SELECT MAX(recorded_at_utc) FROM track_points"
            ).fetchone()[0]
            counts = {
                row["source"]: row["count"]
                for row in connection.execute(
                    "SELECT source,COUNT(*) AS count FROM track_points GROUP BY source"
                ).fetchall()
            }
            latest_message = _dict(
                connection.execute(
                    "SELECT id,track_point_id,source,recorded_at_utc,kind,text "
                    "FROM events WHERE kind='message' ORDER BY recorded_at_utc DESC,id DESC LIMIT 1"
                ).fetchone()
            )
            latest_event = _dict(
                connection.execute(
                    "SELECT id,track_point_id,source,recorded_at_utc,kind,text "
                    "FROM events WHERE kind='event' ORDER BY recorded_at_utc DESC,id DESC LIMIT 1"
                ).fetchone()
            )
            weather_count = int(
                connection.execute("SELECT COUNT(*) FROM weather_samples").fetchone()[0]
            )
            profile_row = connection.execute(
                "SELECT profile_json,updated_at_utc FROM vessel_profiles WHERE id=1"
            ).fetchone()
            backfill = _dict(
                connection.execute(
                    "SELECT * FROM backfill_jobs ORDER BY created_at_utc DESC,id DESC LIMIT 1"
                ).fetchone()
            )
        file_bytes = sum(
            candidate.stat().st_size
            for candidate in (
                self.path,
                Path(str(self.path) + "-wal"),
                Path(str(self.path) + "-shm"),
            )
            if candidate.exists()
        )
        return {
            "latest": latest,
            "latest_message": latest_message,
            "latest_event": latest_event,
            "archive": {
                "counts_by_source": counts,
                "total_points": sum(counts.values()),
                "first_recorded_at_utc": first_time,
                "last_recorded_at_utc": last_time,
                "database_bytes": file_bytes,
                "schema_version": SCHEMA_VERSION,
                "integrity": self._last_integrity,
            },
            # Retained as null compatibility keys for v2.0 entities. Passage
            # analytics are requested explicitly by passage id in v2.1.
            "passage": None,
            "start_point": None,
            "destination": None,
            "route": None,
            "metrics": destination_metrics([], None),
            "weather": {"stored_samples": weather_count},
            "vessel_profile": {
                "configured": profile_row is not None,
                "updated_at_utc": profile_row["updated_at_utc"] if profile_row else None,
            },
            "latest_backfill": backfill,
        }

    def integrity_check(self) -> str:
        with self._connect() as connection:
            self._last_integrity = str(
                connection.execute("PRAGMA quick_check").fetchone()[0]
            )
            return self._last_integrity

    def list_passages(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*, dv.name AS destination_name, dv.latitude AS destination_latitude,
                       dv.longitude AS destination_longitude,
                       dv.arrival_radius_nm AS arrival_radius_nm,
                       dv.effective_at_utc AS destination_effective_at_utc,
                       (
                         SELECT COUNT(*) FROM track_points tp
                         WHERE tp.source='garmin_mapshare'
                           AND tp.recorded_at_utc>=p.started_at_utc
                           AND (p.ended_at_utc IS NULL OR tp.recorded_at_utc<=p.ended_at_utc)
                       ) AS report_count
                FROM passages p
                LEFT JOIN destination_versions dv ON dv.id=p.current_destination_version_id
                ORDER BY COALESCE(p.started_at_utc,p.created_at_utc) DESC
                """
            ).fetchall()
            result = [dict(row) for row in rows]
            for passage in result:
                passage["range_mode"] = (
                    "specific_time" if passage.get("ended_at_utc") else "open_ended"
                )
            return result

    def passage_detail(self, passage_id: int) -> dict[str, Any]:
        """Return one passage, destination history, coverage, and latest route."""
        passages = [item for item in self.list_passages() if item["id"] == passage_id]
        if not passages:
            raise ValueError("Passage not found")
        passage = passages[0]
        with self._connect() as connection:
            passage["destination_versions"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM destination_versions WHERE passage_id=? "
                    "ORDER BY effective_at_utc,id",
                    (passage_id,),
                ).fetchall()
            ]
            route = connection.execute(
                "SELECT * FROM route_versions WHERE passage_id=? "
                "ORDER BY imported_at_utc DESC,id DESC LIMIT 1",
                (passage_id,),
            ).fetchone()
            passage["route"] = self._normalize_route(route)
            profile_row = connection.execute(
                "SELECT updated_at_utc FROM vessel_profiles WHERE id=1"
            ).fetchone()
        if passage["route"]:
            stored_hash = passage["route"]["summary"].get("context_hash")
            expected_hash = route_context_hash(
                passage,
                profile_row["updated_at_utc"] if profile_row else None,
            )
            if stored_hash is None:
                passage["route"]["context_status"] = "unknown"
                passage["route"]["context_warning"] = (
                    "This comparison predates context validation; recalculate it "
                    "before using it for analysis."
                )
            elif stored_hash != expected_hash:
                passage["route"]["context_status"] = "stale"
                passage["route"]["context_warning"] = (
                    "The passage, destination, vessel profile, or routing engine changed after this "
                    "comparison was saved. Recalculate it to restore the map overlay."
                )
            else:
                passage["route"]["context_status"] = "current"
                passage["route"]["context_warning"] = None
        passage["coverage"] = self.preview_passage(
            passage_id=passage_id,
            name=passage["name"],
            start_utc=passage["started_at_utc"],
            end_utc=passage.get("ended_at_utc"),
            departure_name=passage.get("departure_name"),
            departure_latitude=passage.get("departure_latitude"),
            departure_longitude=passage.get("departure_longitude"),
            notes=passage.get("notes"),
            destination={
                "name": passage.get("destination_name"),
                "latitude": passage.get("destination_latitude"),
                "longitude": passage.get("destination_longitude"),
                "arrival_radius_nm": passage.get("arrival_radius_nm"),
                "effective_at_utc": passage.get("destination_effective_at_utc"),
            }
            if passage.get("destination_name")
            else None,
        )
        return passage

    @staticmethod
    def _normalize_route(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        route = dict(row)
        route["coordinates"] = json.loads(route.pop("route_json") or "[]")
        route["summary"] = json.loads(route.pop("summary_json", "{}") or "{}")
        return route

    def passage_points_and_metrics(
        self, passage_id: int, *, max_points: int = 4000
    ) -> dict[str, Any]:
        """Return one passage's archive slice and contextual metrics."""
        detail = self.passage_detail(passage_id)
        result = self.query_points(
            start_utc=detail["started_at_utc"],
            end_utc=detail.get("ended_at_utc"),
            source="garmin_mapshare",
            max_points=max_points,
        )
        destination = (
            {
                "name": detail.get("destination_name"),
                "latitude": detail.get("destination_latitude"),
                "longitude": detail.get("destination_longitude"),
                "arrival_radius_nm": detail.get("arrival_radius_nm"),
            }
            if detail.get("destination_name")
            else None
        )
        result["passage"] = detail
        result["destination"] = destination
        result["metrics"] = destination_metrics(result["points"], destination)
        return result

    def list_destinations(self) -> list[dict[str, Any]]:
        """Return saved destination entries, newest first."""
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM destinations ORDER BY updated_at_utc DESC,id DESC LIMIT 250"
                ).fetchall()
            ]

    @staticmethod
    def _validate_coordinates(latitude: float, longitude: float) -> None:
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("Destination coordinates are outside the valid range")

    def _add_destination_version(
        self,
        connection: sqlite3.Connection,
        passage_id: int,
        name: str,
        latitude: float,
        longitude: float,
        radius_nm: float,
        effective_at: str,
        notes: str | None,
    ) -> int:
        self._validate_coordinates(latitude, longitude)
        cursor = connection.execute(
            "INSERT INTO destinations(name,latitude,longitude,notes,created_at_utc,updated_at_utc) "
            "VALUES(?,?,?,?,?,?)",
            (name, latitude, longitude, notes, effective_at, effective_at),
        )
        destination_id = int(cursor.lastrowid)
        cursor = connection.execute(
            """
            INSERT INTO destination_versions(
                passage_id,destination_id,name,latitude,longitude,
                arrival_radius_nm,effective_at_utc,notes
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                passage_id,
                destination_id,
                name,
                latitude,
                longitude,
                radius_nm,
                effective_at,
                notes,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _passage_preview_token(payload: dict[str, Any]) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode()).hexdigest()

    def preview_passage(
        self,
        *,
        passage_id: int | None,
        name: str,
        start_utc: str,
        end_utc: str | None,
        departure_name: str | None = None,
        departure_latitude: float | None = None,
        departure_longitude: float | None = None,
        notes: str | None = None,
        destination: dict[str, Any] | None = None,
        clear_destination: bool = False,
    ) -> dict[str, Any]:
        """Preview retroactive effects without changing archive metadata."""
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Passage name is required")
        start = parse_utc(start_utc).isoformat().replace("+00:00", "Z")
        end = (
            parse_utc(end_utc).isoformat().replace("+00:00", "Z")
            if end_utc
            else None
        )
        if end and end <= start:
            raise ValueError("Passage end must be after its start")
        if (departure_latitude is None) != (departure_longitude is None):
            raise ValueError("Departure latitude and longitude must be entered together")
        if departure_latitude is not None and departure_longitude is not None:
            self._validate_coordinates(
                float(departure_latitude), float(departure_longitude)
            )
        normalized_destination: dict[str, Any] | None = None
        if destination:
            destination_name = str(destination.get("name") or "").strip()
            latitude = destination.get("latitude")
            longitude = destination.get("longitude")
            if not destination_name or latitude is None or longitude is None:
                raise ValueError("Destination name, latitude, and longitude are required together")
            self._validate_coordinates(float(latitude), float(longitude))
            radius = float(destination.get("arrival_radius_nm", 2.0))
            if not 0.1 <= radius <= 100:
                raise ValueError("Arrival radius must be between 0.1 and 100 nautical miles")
            effective = parse_utc(
                destination.get("effective_at_utc") or start
            ).isoformat().replace("+00:00", "Z")
            if effective < start or (end and effective > end):
                raise ValueError(
                    "Destination effective time must fall within the passage range"
                )
            normalized_destination = {
                "name": destination_name,
                "latitude": float(latitude),
                "longitude": float(longitude),
                "arrival_radius_nm": radius,
                "effective_at_utc": effective,
                "notes": str(destination.get("notes") or "").strip() or None,
            }
        payload = {
            "passage_id": int(passage_id) if passage_id is not None else None,
            "name": cleaned_name,
            "start_utc": start,
            "end_utc": end,
            "departure_name": str(departure_name or "").strip() or None,
            "departure_latitude": float(departure_latitude)
            if departure_latitude is not None
            else None,
            "departure_longitude": float(departure_longitude)
            if departure_longitude is not None
            else None,
            "notes": str(notes or "").strip() or None,
            "destination": normalized_destination,
            "clear_destination": bool(clear_destination),
        }
        clauses = ["source='garmin_mapshare'", "recorded_at_utc>=?"]
        parameters: list[Any] = [start]
        if end:
            clauses.append("recorded_at_utc<=?")
            parameters.append(end)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,recorded_at_utc,latitude,longitude FROM track_points WHERE "
                + " AND ".join(clauses)
                + " ORDER BY recorded_at_utc,id",
                parameters,
            ).fetchall()
            conflicts = connection.execute(
                """
                SELECT id,name,started_at_utc,ended_at_utc FROM passages
                WHERE (? IS NULL OR id<>?)
                  AND COALESCE(ended_at_utc,'9999-12-31T23:59:59Z')>=?
                  AND COALESCE(?, '9999-12-31T23:59:59Z')>=started_at_utc
                ORDER BY started_at_utc
                """,
                (passage_id, passage_id, start, end),
            ).fetchall()
            destination_versions_removed = (
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM destination_versions WHERE passage_id=?",
                        (passage_id,),
                    ).fetchone()[0]
                )
                if clear_destination and passage_id is not None
                else 0
            )
        gaps: list[dict[str, Any]] = []
        for previous, current in zip(rows, rows[1:]):
            seconds = (
                parse_utc(current["recorded_at_utc"])
                - parse_utc(previous["recorded_at_utc"])
            ).total_seconds()
            if seconds > GAP_SECONDS:
                gaps.append(
                    {
                        "start_utc": previous["recorded_at_utc"],
                        "end_utc": current["recorded_at_utc"],
                        "minutes": round(seconds / 60, 1),
                    }
                )
        return {
            "normalized": payload,
            "preview_token": self._passage_preview_token(payload),
            "report_count": len(rows),
            "first_report_utc": rows[0]["recorded_at_utc"] if rows else None,
            "last_report_utc": rows[-1]["recorded_at_utc"] if rows else None,
            "gap_count": len(gaps),
            "gaps": gaps[:25],
            "conflicts": [dict(row) for row in conflicts],
            "destination_versions_removed": destination_versions_removed,
            "raw_reports_unchanged": True,
        }

    def save_passage(
        self,
        *,
        passage_id: int | None,
        preview_token: str,
        name: str,
        start_utc: str,
        end_utc: str | None,
        departure_name: str | None = None,
        departure_latitude: float | None = None,
        departure_longitude: float | None = None,
        notes: str | None = None,
        destination: dict[str, Any] | None = None,
        clear_destination: bool = False,
    ) -> dict[str, Any]:
        """Create or edit a temporal annotation after an exact preview."""
        preview = self.preview_passage(
            passage_id=passage_id,
            name=name,
            start_utc=start_utc,
            end_utc=end_utc,
            departure_name=departure_name,
            departure_latitude=departure_latitude,
            departure_longitude=departure_longitude,
            notes=notes,
            destination=destination,
            clear_destination=clear_destination,
        )
        if preview_token != preview["preview_token"]:
            raise ValueError("Passage details changed; preview the archive coverage again")
        value = preview["normalized"]
        changed_at = utc_now()
        status = "completed" if value["end_utc"] else "planned"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if passage_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO passages(
                        name,status,started_at_utc,ended_at_utc,departure_name,
                        departure_latitude,departure_longitude,notes,
                        created_at_utc,updated_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        value["name"],
                        status,
                        value["start_utc"],
                        value["end_utc"],
                        value["departure_name"],
                        value["departure_latitude"],
                        value["departure_longitude"],
                        value["notes"],
                        changed_at,
                        changed_at,
                    ),
                )
                passage_id = int(cursor.lastrowid)
                existing_destination = None
            else:
                existing_destination = connection.execute(
                    "SELECT dv.* FROM passages p LEFT JOIN destination_versions dv "
                    "ON dv.id=p.current_destination_version_id WHERE p.id=?",
                    (passage_id,),
                ).fetchone()
                cursor = connection.execute(
                    """
                    UPDATE passages SET name=?,status=?,started_at_utc=?,ended_at_utc=?,
                        arrived_at_utc=NULL,departure_name=?,departure_latitude=?,
                        departure_longitude=?,notes=?,updated_at_utc=? WHERE id=?
                    """,
                    (
                        value["name"],
                        status,
                        value["start_utc"],
                        value["end_utc"],
                        value["departure_name"],
                        value["departure_latitude"],
                        value["departure_longitude"],
                        value["notes"],
                        changed_at,
                        passage_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Passage not found")
            if clear_destination:
                connection.execute(
                    "UPDATE passages SET current_destination_version_id=NULL WHERE id=?",
                    (passage_id,),
                )
                connection.execute(
                    "DELETE FROM destination_versions WHERE passage_id=?",
                    (passage_id,),
                )
            elif value["destination"]:
                target = value["destination"]
                has_existing_destination = bool(
                    existing_destination is not None
                    and existing_destination["id"] is not None
                )
                comparable = (
                    str(existing_destination["name"]),
                    float(existing_destination["latitude"]),
                    float(existing_destination["longitude"]),
                    float(existing_destination["arrival_radius_nm"]),
                    str(existing_destination["effective_at_utc"]),
                    existing_destination["notes"],
                ) if has_existing_destination else None
                proposed = (
                    target["name"],
                    target["latitude"],
                    target["longitude"],
                    target["arrival_radius_nm"],
                    target["effective_at_utc"],
                    target["notes"],
                )
                if comparable != proposed:
                    version_id = self._add_destination_version(
                        connection,
                        passage_id,
                        target["name"],
                        target["latitude"],
                        target["longitude"],
                        target["arrival_radius_nm"],
                        target["effective_at_utc"],
                        target["notes"],
                    )
                    connection.execute(
                        "UPDATE passages SET current_destination_version_id=? WHERE id=?",
                        (version_id, passage_id),
                    )
            connection.execute(
                """
                UPDATE passages SET start_point_id=(
                    SELECT id FROM track_points
                    WHERE source='garmin_mapshare' AND recorded_at_utc>=?
                      AND (? IS NULL OR recorded_at_utc<=?)
                      AND latitude IS NOT NULL AND longitude IS NOT NULL
                    ORDER BY recorded_at_utc,id LIMIT 1
                ) WHERE id=?
                """,
                (value["start_utc"], value["end_utc"], value["end_utc"], passage_id),
            )
            connection.commit()
        return self.passage_detail(int(passage_id))

    def delete_passage(self, passage_id: int) -> None:
        """Delete passage metadata without deleting global track records."""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM passages WHERE id=?", (passage_id,))
            if cursor.rowcount != 1:
                raise ValueError("Passage not found")

    def begin_import(self, source: str, filename: str, content_sha256: str) -> int:
        now = utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id,status FROM imports WHERE content_sha256=?", (content_sha256,)
            ).fetchone()
            if existing:
                if existing["status"] == "rolled_back":
                    connection.execute(
                        "UPDATE imports SET source=?,filename=?,imported_at_utc=?,"
                        "status='running',rows_seen=0,rows_inserted=0,notes=NULL WHERE id=?",
                        (source, filename, now, existing["id"]),
                    )
                    return int(existing["id"])
                raise ValueError(
                    f"This exact file is already registered as import {existing['id']} ({existing['status']})"
                )
            cursor = connection.execute(
                "INSERT INTO imports(source,filename,content_sha256,imported_at_utc,status) "
                "VALUES(?,?,?,?,'running')",
                (source, filename, content_sha256, now),
            )
            return int(cursor.lastrowid)

    def finish_import(self, import_id: int, *, failed: bool = False, notes: str | None = None) -> dict[str, Any]:
        status = "failed" if failed else "completed"
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE imports SET status=?,notes=? WHERE id=? AND status='running'",
                (status, notes, import_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Import is not running")
            return dict(connection.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone())

    def rollback_import(self, import_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            removed = connection.execute(
                "DELETE FROM track_points WHERE import_id=?", (import_id,)
            ).rowcount
            cursor = connection.execute(
                "UPDATE imports SET status='rolled_back',notes=? WHERE id=?",
                (f"Rolled back {removed} inserted records", import_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Import not found")
            connection.commit()
            result = dict(connection.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone())
            result["removed"] = removed
            return result

    def list_imports(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM imports ORDER BY imported_at_utc DESC"
                ).fetchall()
            ]

    def preview_records(
        self, records: Iterable[TrackRecord], source: str
    ) -> dict[str, Any]:
        """Count new and duplicate source records without writing them."""
        items = list(records)
        keys = [record.dedupe_key(source) for record in items]
        existing: set[str] = set()
        with self._connect() as connection:
            for start in range(0, len(keys), 500):
                batch = keys[start : start + 500]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                existing.update(
                    row[0]
                    for row in connection.execute(
                        f"SELECT dedupe_key FROM track_points WHERE dedupe_key IN ({placeholders})",
                        batch,
                    ).fetchall()
                )
        timestamps = sorted(record.recorded_at_utc for record in items)
        return {
            "returned": len(items),
            "new": sum(key not in existing for key in keys),
            "duplicated": sum(key in existing for key in keys),
            "first_recorded_at_utc": timestamps[0] if timestamps else None,
            "last_recorded_at_utc": timestamps[-1] if timestamps else None,
        }

    def create_backfill_job(
        self,
        *,
        phase: str,
        start_utc: str,
        end_utc: str,
        chunk_days: int = 7,
        preview_job_id: int | None = None,
        import_id: int | None = None,
    ) -> dict[str, Any]:
        """Persist a resumable, bounded Garmin backfill job."""
        if phase not in {"preview", "commit"}:
            raise ValueError("Backfill phase must be preview or commit")
        start = parse_utc(start_utc)
        end = parse_utc(end_utc)
        if end <= start:
            raise ValueError("Backfill end must be after its start")
        if end - start > timedelta(days=3650):
            raise ValueError("One backfill job is limited to ten years")
        if not 1 <= int(chunk_days) <= 31:
            raise ValueError("Backfill chunks must be between 1 and 31 days")
        normalized_start = start.isoformat().replace("+00:00", "Z")
        normalized_end = end.isoformat().replace("+00:00", "Z")
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if phase == "commit":
                preview = connection.execute(
                    "SELECT * FROM backfill_jobs WHERE id=? AND phase='preview' "
                    "AND status='completed'",
                    (preview_job_id,),
                ).fetchone()
                if not preview:
                    raise ValueError("Complete a matching preview before importing")
                if (
                    preview["start_utc"] != normalized_start
                    or preview["end_utc"] != normalized_end
                ):
                    raise ValueError("Backfill dates changed; run preview again")
                if import_id is None:
                    raise ValueError("Commit backfill needs an import batch")
            cursor = connection.execute(
                """
                INSERT INTO backfill_jobs(
                    phase,start_utc,end_utc,status,preview_job_id,import_id,
                    created_at_utc,updated_at_utc
                ) VALUES(?,?,?,'pending',?,?,?,?)
                """,
                (
                    phase,
                    normalized_start,
                    normalized_end,
                    preview_job_id,
                    import_id,
                    now,
                    now,
                ),
            )
            job_id = int(cursor.lastrowid)
            cursor_time = start
            chunk_index = 0
            delta = timedelta(days=int(chunk_days))
            while cursor_time < end:
                chunk_end = min(end, cursor_time + delta)
                connection.execute(
                    "INSERT INTO backfill_chunks(job_id,chunk_index,start_utc,end_utc,status) "
                    "VALUES(?,?,?,?,'pending')",
                    (
                        job_id,
                        chunk_index,
                        cursor_time.isoformat().replace("+00:00", "Z"),
                        chunk_end.isoformat().replace("+00:00", "Z"),
                    ),
                )
                cursor_time = chunk_end
                chunk_index += 1
            connection.execute(
                "UPDATE backfill_jobs SET chunks_total=? WHERE id=?",
                (chunk_index, job_id),
            )
            connection.commit()
        return self.get_backfill_job(job_id)

    def get_backfill_job(self, job_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            job = _dict(
                connection.execute(
                    "SELECT * FROM backfill_jobs WHERE id=?", (job_id,)
                ).fetchone()
            )
            if not job:
                raise ValueError("Backfill job not found")
            job["chunks"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM backfill_chunks WHERE job_id=? ORDER BY chunk_index",
                    (job_id,),
                ).fetchall()
            ]
            return job

    def list_backfill_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM backfill_jobs ORDER BY created_at_utc DESC,id DESC LIMIT ?",
                    (max(1, min(int(limit), 100)),),
                ).fetchall()
            ]

    def next_backfill_chunk(self, job_id: int) -> dict[str, Any] | None:
        """Claim the next chunk, resetting one interrupted running chunk."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT status FROM backfill_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not job:
                raise ValueError("Backfill job not found")
            if job["status"] in {"completed", "cancelled"}:
                return None
            connection.execute(
                "UPDATE backfill_chunks SET status='pending' "
                "WHERE job_id=? AND status='running'",
                (job_id,),
            )
            row = connection.execute(
                "SELECT * FROM backfill_chunks WHERE job_id=? AND status IN ('pending','failed') "
                "ORDER BY chunk_index LIMIT 1",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "UPDATE backfill_jobs SET status='completed',updated_at_utc=? WHERE id=?",
                    (utc_now(), job_id),
                )
                connection.commit()
                return None
            connection.execute(
                "UPDATE backfill_chunks SET status='running',error=NULL WHERE id=?",
                (row["id"],),
            )
            connection.execute(
                "UPDATE backfill_jobs SET status='running',error=NULL,updated_at_utc=? WHERE id=?",
                (utc_now(), job_id),
            )
            connection.commit()
            result = dict(row)
            result["status"] = "running"
            return result

    def complete_backfill_chunk(
        self,
        job_id: int,
        chunk_id: int,
        *,
        returned: int,
        inserted: int,
        duplicated: int,
        rejected: int,
        first_recorded_at_utc: str | None,
        last_recorded_at_utc: str | None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE backfill_chunks SET status='completed',records_returned=?,
                    records_inserted=?,records_duplicated=?,records_rejected=?,error=NULL
                WHERE id=? AND job_id=? AND status='running'
                """,
                (returned, inserted, duplicated, rejected, chunk_id, job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Backfill chunk is not running")
            remaining = int(
                connection.execute(
                    "SELECT COUNT(*) FROM backfill_chunks WHERE job_id=? AND status!='completed'",
                    (job_id,),
                ).fetchone()[0]
            )
            status = "completed" if remaining == 0 else "running"
            connection.execute(
                """
                UPDATE backfill_jobs SET status=?,chunks_completed=chunks_completed+1,
                    records_returned=records_returned+?,records_inserted=records_inserted+?,
                    records_duplicated=records_duplicated+?,records_rejected=records_rejected+?,
                    first_recorded_at_utc=CASE
                        WHEN first_recorded_at_utc IS NULL THEN ?
                        WHEN ? IS NULL THEN first_recorded_at_utc
                        ELSE MIN(first_recorded_at_utc,?) END,
                    last_recorded_at_utc=CASE
                        WHEN last_recorded_at_utc IS NULL THEN ?
                        WHEN ? IS NULL THEN last_recorded_at_utc
                        ELSE MAX(last_recorded_at_utc,?) END,
                    updated_at_utc=? WHERE id=?
                """,
                (
                    status,
                    returned,
                    inserted,
                    duplicated,
                    rejected,
                    first_recorded_at_utc,
                    first_recorded_at_utc,
                    first_recorded_at_utc,
                    last_recorded_at_utc,
                    last_recorded_at_utc,
                    last_recorded_at_utc,
                    utc_now(),
                    job_id,
                ),
            )
            connection.commit()
        return self.get_backfill_job(job_id)

    def fail_backfill_chunk(self, job_id: int, chunk_id: int, error: str) -> dict[str, Any]:
        message = str(error)[:1000]
        with self._connect() as connection:
            connection.execute(
                "UPDATE backfill_chunks SET status='failed',error=? WHERE id=? AND job_id=?",
                (message, chunk_id, job_id),
            )
            connection.execute(
                "UPDATE backfill_jobs SET status='failed',error=?,updated_at_utc=? WHERE id=?",
                (message, utc_now(), job_id),
            )
        return self.get_backfill_job(job_id)

    def cancel_backfill_job(self, job_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            job = connection.execute(
                "SELECT import_id FROM backfill_jobs WHERE id=?", (job_id,)
            ).fetchone()
            cursor = connection.execute(
                "UPDATE backfill_jobs SET status='cancelled',updated_at_utc=? "
                "WHERE id=? AND status NOT IN ('completed','cancelled')",
                (utc_now(), job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Backfill job is already finished or was not found")
            if job and job["import_id"] is not None:
                connection.execute(
                    "UPDATE imports SET status='failed',notes=? "
                    "WHERE id=? AND status='running'",
                    ("Backfill cancelled; inserted rows remain rollbackable", job["import_id"]),
                )
        return self.get_backfill_job(job_id)

    @staticmethod
    def _weather_key(sample: dict[str, Any]) -> str:
        value = "|".join(
            (
                str(sample.get("provider") or "unknown"),
                str(sample.get("purpose") or "track"),
                str(sample.get("track_point_id") or ""),
                str(sample.get("passage_id") or ""),
                str(sample.get("route_candidate") or ""),
                str(sample["valid_at_utc"]),
                f"{float(sample['latitude']):.4f}",
                f"{float(sample['longitude']):.4f}",
            )
        )
        return hashlib.sha256(value.encode()).hexdigest()

    def save_weather_samples(self, samples: Iterable[dict[str, Any]]) -> dict[str, int]:
        items = list(samples)
        inserted = 0
        requested = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for sample in items:
                cursor = connection.execute(
                    """
                    INSERT OR REPLACE INTO weather_samples(
                        sample_key,provider,purpose,track_point_id,passage_id,
                        route_candidate,quality_state,valid_at_utc,requested_at_utc,
                        latitude,longitude,wind_speed_kn,wind_gust_kn,wind_dir_deg,
                        wave_height_m,wave_dir_deg,wave_period_s,current_speed_kn,
                        current_dir_deg,pressure_hpa,sea_surface_temp_c,
                        conditions_available,maritime_available,warnings_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        self._weather_key(sample),
                        sample.get("provider") or "unknown",
                        sample.get("purpose") or "track",
                        sample.get("track_point_id"),
                        sample.get("passage_id"),
                        sample.get("route_candidate"),
                        sample.get("quality_state") or "modeled",
                        sample["valid_at_utc"],
                        requested,
                        float(sample["latitude"]),
                        float(sample["longitude"]),
                        sample.get("wind_speed_kn"),
                        sample.get("wind_gust_kn"),
                        sample.get("wind_dir_deg"),
                        sample.get("wave_height_m"),
                        sample.get("wave_dir_deg"),
                        sample.get("wave_period_s"),
                        sample.get("current_speed_kn"),
                        sample.get("current_dir_deg"),
                        sample.get("pressure_hpa"),
                        sample.get("sea_surface_temp_c"),
                        int(bool(sample.get("conditions_available"))),
                        int(bool(sample.get("maritime_available"))),
                        json.dumps(sample.get("warnings") or [], separators=(",", ":")),
                    ),
                )
                inserted += int(cursor.rowcount == 1)
            connection.commit()
        return {"seen": len(items), "stored": inserted}

    @staticmethod
    def _normalize_weather(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["conditions_available"] = bool(value["conditions_available"])
        value["maritime_available"] = bool(value["maritime_available"])
        value["warnings"] = json.loads(value.pop("warnings_json") or "[]")
        return value

    def query_weather_samples(
        self, *, start_utc: str | None = None, end_utc: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if start_utc:
            clauses.append("valid_at_utc>=?")
            parameters.append(start_utc)
        if end_utc:
            clauses.append("valid_at_utc<=?")
            parameters.append(end_utc)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM weather_samples" + (where + " AND purpose='track'" if where else " WHERE purpose='track'") + " ORDER BY valid_at_utc,id",
                parameters,
            ).fetchall()
            return [self._normalize_weather(row) for row in rows]

    def cached_weather_sample(
        self,
        *,
        latitude: float,
        longitude: float,
        valid_at_utc: str,
        tolerance_minutes: int = 90,
    ) -> dict[str, Any] | None:
        target = parse_utc(valid_at_utc)
        start = (target - timedelta(minutes=tolerance_minutes)).isoformat().replace(
            "+00:00", "Z"
        )
        end = (target + timedelta(minutes=tolerance_minutes)).isoformat().replace(
            "+00:00", "Z"
        )
        recent_model = target >= datetime.now(timezone.utc) - timedelta(hours=48)
        fresh_after = (
            (datetime.now(timezone.utc) - timedelta(hours=6))
            .isoformat()
            .replace("+00:00", "Z")
            if recent_model
            else None
        )
        failure_fresh_after = (
            (datetime.now(timezone.utc) - timedelta(hours=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM weather_samples WHERE valid_at_utc BETWEEN ? AND ? "
                "AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? "
                "AND (? IS NULL OR requested_at_utc>=?) "
                "AND (quality_state!='unavailable' OR requested_at_utc>=?)",
                (
                    start,
                    end,
                    latitude - 0.2,
                    latitude + 0.2,
                    longitude - 0.2,
                    longitude + 0.2,
                    fresh_after,
                    fresh_after,
                    failure_fresh_after,
                ),
            ).fetchall()
        if not rows:
            return None
        row = min(
            rows,
            key=lambda item: abs(
                (parse_utc(item["valid_at_utc"]) - target).total_seconds()
            )
            + abs(float(item["latitude"]) - latitude) * 3600
            + abs(float(item["longitude"]) - longitude) * 3600,
        )
        return self._normalize_weather(row)

    def get_vessel_profile(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_json,updated_at_utc FROM vessel_profiles WHERE id=1"
            ).fetchone()
        if not row:
            return {"profile": {}, "updated_at_utc": None}
        return {
            "profile": json.loads(row["profile_json"] or "{}"),
            "updated_at_utc": row["updated_at_utc"],
        }

    def save_vessel_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        serialized = json.dumps(profile, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO vessel_profiles(id,profile_json,updated_at_utc) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json," 
                "updated_at_utc=excluded.updated_at_utc",
                (serialized, now),
            )
        return {"profile": json.loads(serialized), "updated_at_utc": now}

    def add_route_version(
        self,
        passage_id: int,
        label: str,
        source: str,
        content_sha256: str,
        coordinates: list[list[float]],
        *,
        summary: dict[str, Any] | None = None,
        departure_at_utc: str | None = None,
        weather_generated_at_utc: str | None = None,
    ) -> dict[str, Any]:
        if len(coordinates) < 2:
            raise ValueError("A planned route needs at least two coordinates")
        normalized: list[list[float]] = []
        for coordinate in coordinates:
            if len(coordinate) < 2:
                raise ValueError("Each route coordinate needs longitude and latitude")
            longitude, latitude = float(coordinate[0]), float(coordinate[1])
            self._validate_coordinates(latitude, longitude)
            normalized.append([longitude, latitude])
        now = utc_now()
        with self._connect() as connection:
            if not connection.execute("SELECT 1 FROM passages WHERE id=?", (passage_id,)).fetchone():
                raise ValueError("Passage not found")
            try:
                cursor = connection.execute(
                    "INSERT INTO route_versions(passage_id,label,source,imported_at_utc,content_sha256,route_json," 
                    "summary_json,departure_at_utc,weather_generated_at_utc) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        passage_id,
                        label.strip(),
                        source,
                        now,
                        content_sha256,
                        json.dumps(normalized, separators=(",", ":")),
                        json.dumps(summary or {}, separators=(",", ":")),
                        departure_at_utc,
                        weather_generated_at_utc,
                    ),
                )
            except sqlite3.IntegrityError as err:
                existing = connection.execute(
                    "SELECT * FROM route_versions WHERE passage_id=? AND content_sha256=?",
                    (passage_id, content_sha256),
                ).fetchone()
                if existing:
                    result = self._normalize_route(existing)
                    result["unchanged"] = True
                    return result
                raise ValueError("The route version could not be stored") from err
            route_id = int(cursor.lastrowid)
            return self._normalize_route(
                connection.execute(
                    "SELECT * FROM route_versions WHERE id=?", (route_id,)
                ).fetchone()
            )

    def current_route(self, passage_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM route_versions WHERE passage_id=? "
                "ORDER BY imported_at_utc DESC,id DESC LIMIT 1",
                (passage_id,),
            ).fetchone()
            return self._normalize_route(row)
