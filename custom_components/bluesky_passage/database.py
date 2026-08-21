"""Dedicated SQLite archive for BlueSky Passage.

The archive is intentionally separate from Home Assistant Recorder. Recorder is
optimized for entity state history and retention; this database preserves the
source records, messages, provenance, passage metadata, and destination history
without an automatic purge.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .calculations import destination_metrics, haversine_nm, parse_utc
from .parser import TrackRecord, raw_json

SCHEMA_VERSION = 1
GAP_SECONDS = 90 * 60
IMPOSSIBLE_SPEED_KN = 80.0


def utc_now() -> str:
    """Return a normalized UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        return connection

    def initialize(self) -> None:
        """Create/migrate the archive and verify it opens cleanly."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
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
                """
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
        arrived_passage: dict[str, Any] | None = None
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
                    WHERE status IN ('active','arrived') AND start_point_id IS NULL
                    """,
                    (ingested_at,),
                )
                arrived_passage = self._check_arrival(connection, ingested_at)
            connection.commit()

        return {
            "seen": len(ordered),
            "inserted": len(inserted_ids),
            "inserted_ids": inserted_ids,
            "arrived_passage": arrived_passage,
        }

    def _check_arrival(
        self, connection: sqlite3.Connection, changed_at: str
    ) -> dict[str, Any] | None:
        passage = connection.execute(
            """
            SELECT p.id, p.name, dv.name AS destination_name, dv.latitude,
                   dv.longitude, dv.arrival_radius_nm
            FROM passages p
            JOIN destination_versions dv ON dv.id=p.current_destination_version_id
            WHERE p.status='active' LIMIT 1
            """
        ).fetchone()
        point = connection.execute(
            "SELECT latitude,longitude,recorded_at_utc FROM track_points "
            "WHERE source='garmin_mapshare' AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "ORDER BY recorded_at_utc DESC LIMIT 1"
        ).fetchone()
        if not passage or not point:
            return None
        distance = haversine_nm(
            float(point["latitude"]),
            float(point["longitude"]),
            float(passage["latitude"]),
            float(passage["longitude"]),
        )
        if distance > float(passage["arrival_radius_nm"]):
            return None
        connection.execute(
            "UPDATE passages SET status='arrived', arrived_at_utc=?, updated_at_utc=? WHERE id=?",
            (point["recorded_at_utc"], changed_at, passage["id"]),
        )
        result = dict(passage)
        result["range_nm"] = round(distance, 2)
        result["arrived_at_utc"] = point["recorded_at_utc"]
        return result

    def latest_point(self, source: str = "garmin_mapshare") -> dict[str, Any] | None:
        """Return the newest source point."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM track_points WHERE source=? "
                "ORDER BY recorded_at_utc DESC, id DESC LIMIT 1",
                (source,),
            ).fetchone()
            return self._normalize_point(row)

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
        if len(points) <= limit:
            return points
        if limit <= 1:
            return [points[-1]]
        indices = {
            round(index * (len(points) - 1) / (limit - 1)) for index in range(limit)
        }
        return [points[index] for index in sorted(indices)]

    def dashboard_state(self) -> dict[str, Any]:
        """Return lightweight state and current-passage metrics."""
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
            passage = _dict(
                connection.execute(
                    "SELECT * FROM passages WHERE status IN ('active','arrived') LIMIT 1"
                ).fetchone()
            )
            destination = None
            start_point = None
            passage_points: list[dict[str, Any]] = []
            if passage:
                if passage.get("start_point_id"):
                    start_point = self._normalize_point(
                        connection.execute(
                            "SELECT * FROM track_points WHERE id=?",
                            (passage["start_point_id"],),
                        ).fetchone()
                    )
                destination = _dict(
                    connection.execute(
                        "SELECT * FROM destination_versions WHERE id=?",
                        (passage["current_destination_version_id"],),
                    ).fetchone()
                ) if passage.get("current_destination_version_id") else None
                passage_points = [
                    self._normalize_point(row)
                    for row in connection.execute(
                        "SELECT * FROM track_points WHERE source='garmin_mapshare' "
                        "AND recorded_at_utc>=? ORDER BY recorded_at_utc,id",
                        (passage["started_at_utc"],),
                    ).fetchall()
                ]
                passage_points = [point for point in passage_points if point is not None]
            route = None
            if passage:
                route = _dict(
                    connection.execute(
                        "SELECT id,label,source,imported_at_utc FROM route_versions "
                        "WHERE passage_id=? ORDER BY imported_at_utc DESC LIMIT 1",
                        (passage["id"],),
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
            "passage": passage,
            "start_point": start_point,
            "destination": destination,
            "route": route,
            "metrics": destination_metrics(passage_points, destination),
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
                       dv.arrival_radius_nm AS arrival_radius_nm
                FROM passages p
                LEFT JOIN destination_versions dv ON dv.id=p.current_destination_version_id
                ORDER BY COALESCE(p.started_at_utc,p.created_at_utc) DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

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

    def start_passage(
        self,
        name: str,
        *,
        started_at_utc: str | None = None,
        destination: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        changed_at = utc_now()
        started_at = started_at_utc or changed_at
        parse_utc(started_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM passages WHERE status IN ('active','arrived') LIMIT 1"
            ).fetchone():
                raise ValueError("End the current passage before starting another")
            cursor = connection.execute(
                "INSERT INTO passages(name,status,started_at_utc,created_at_utc,updated_at_utc) "
                "VALUES(?,'active',?,?,?)",
                (name.strip(), started_at, changed_at, changed_at),
            )
            passage_id = int(cursor.lastrowid)
            if destination:
                version_id = self._add_destination_version(
                    connection,
                    passage_id,
                    str(destination["name"]).strip(),
                    float(destination["latitude"]),
                    float(destination["longitude"]),
                    float(destination.get("arrival_radius_nm", 2.0)),
                    changed_at,
                    destination.get("notes"),
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
                      AND latitude IS NOT NULL AND longitude IS NOT NULL
                    ORDER BY recorded_at_utc,id LIMIT 1
                ) WHERE id=?
                """,
                (started_at, passage_id),
            )
            connection.commit()
        return next(item for item in self.list_passages() if item["id"] == passage_id)

    def set_destination(
        self,
        passage_id: int,
        *,
        name: str,
        latitude: float,
        longitude: float,
        arrival_radius_nm: float,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if not 0.1 <= arrival_radius_nm <= 100:
            raise ValueError("Arrival radius must be between 0.1 and 100 nautical miles")
        changed_at = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            passage = connection.execute(
                "SELECT id FROM passages WHERE id=? AND status IN ('active','arrived')",
                (passage_id,),
            ).fetchone()
            if not passage:
                raise ValueError("The passage is not active")
            version_id = self._add_destination_version(
                connection,
                passage_id,
                name.strip(),
                latitude,
                longitude,
                arrival_radius_nm,
                changed_at,
                notes,
            )
            connection.execute(
                "UPDATE passages SET current_destination_version_id=?, status='active', "
                "arrived_at_utc=NULL, updated_at_utc=? WHERE id=?",
                (version_id, changed_at, passage_id),
            )
            connection.commit()
        return next(item for item in self.list_passages() if item["id"] == passage_id)

    def end_passage(self, passage_id: int, ended_at_utc: str | None = None) -> dict[str, Any]:
        ended_at = ended_at_utc or utc_now()
        parse_utc(ended_at)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE passages SET status='completed',ended_at_utc=?,updated_at_utc=? "
                "WHERE id=? AND status IN ('active','arrived')",
                (ended_at, utc_now(), passage_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("The passage is not active")
        return next(item for item in self.list_passages() if item["id"] == passage_id)

    def delete_passage(self, passage_id: int) -> None:
        """Delete passage metadata without deleting global track records."""
        with self._connect() as connection:
            status = connection.execute(
                "SELECT status FROM passages WHERE id=?", (passage_id,)
            ).fetchone()
            if not status:
                raise ValueError("Passage not found")
            if status["status"] in {"active", "arrived"}:
                raise ValueError("End the passage before deleting it")
            connection.execute("DELETE FROM passages WHERE id=?", (passage_id,))

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

    def add_route_version(
        self,
        passage_id: int,
        label: str,
        source: str,
        content_sha256: str,
        coordinates: list[list[float]],
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
                    "INSERT INTO route_versions(passage_id,label,source,imported_at_utc,content_sha256,route_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        passage_id,
                        label.strip(),
                        source,
                        now,
                        content_sha256,
                        json.dumps(normalized, separators=(",", ":")),
                    ),
                )
            except sqlite3.IntegrityError as err:
                raise ValueError(
                    "This exact planned route is already attached to the passage"
                ) from err
            route_id = int(cursor.lastrowid)
            return dict(
                connection.execute(
                    "SELECT id,passage_id,label,source,imported_at_utc,route_json "
                    "FROM route_versions WHERE id=?",
                    (route_id,),
                ).fetchone()
            )

    def current_route(self, passage_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,passage_id,label,source,imported_at_utc,route_json "
                "FROM route_versions WHERE passage_id=? ORDER BY imported_at_utc DESC LIMIT 1",
                (passage_id,),
            ).fetchone()
            result = _dict(row)
            if result:
                result["coordinates"] = json.loads(result.pop("route_json"))
            return result
