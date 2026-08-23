"""Core archive/parser regression tests runnable with the Python standard library."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from _loader import (
    calculations,
    database,
    exporting,
    garmin_dates,
    land,
    migration,
    parser,
    routing,
    weather,
)


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><ExtendedData>
    <Data name="Id"><value>1001</value></Data>
    <Data name="Time UTC"><value>8/20/2026 8:00:00 PM</value></Data>
    <Data name="Map Display Name"><value>Example Vessel</value></Data>
    <Data name="IMEI"><value>111222333</value></Data>
    <Data name="Latitude"><value>18.000000</value></Data>
    <Data name="Longitude"><value>-61.000000</value></Data>
    <Data name="Elevation"><value>10 ft from MSL</value></Data>
    <Data name="Velocity"><value>9.26 km/h</value></Data>
    <Data name="Course"><value>90.0 ° True</value></Data>
    <Data name="Valid GPS Fix"><value>True</value></Data>
    <Data name="In Emergency"><value>False</value></Data>
    <Data name="Event"><value>Tracking message received.</value></Data>
  </ExtendedData></Placemark>
  <Placemark><ExtendedData>
    <Data name="Id"><value>1002</value></Data>
    <Data name="Time UTC"><value>8/20/2026 8:10:00 PM</value></Data>
    <Data name="IMEI"><value>111222333</value></Data>
    <Data name="Latitude"><value>18.010000</value></Data>
    <Data name="Longitude"><value>-60.990000</value></Data>
    <Data name="Velocity"><value>5 kn</value></Data>
    <Data name="Course"><value>45 True</value></Data>
    <Data name="Valid GPS Fix"><value>True</value></Data>
    <Data name="In Emergency"><value>False</value></Data>
    <Data name="Event"><value>Text message received.</value></Data>
    <Data name="Text"><value>All well</value></Data>
  </ExtendedData></Placemark>
</Document></kml>"""


class MigrationTests(unittest.TestCase):
    def test_exact_legacy_generated_title_is_cleaned(self):
        self.assertEqual(
            "BlueSky Passage",
            migration.migrated_entry_title(
                "BlueSky Passage (ExampleShare)", "ExampleShare"
            ),
        )

    def test_customized_and_already_generic_titles_are_preserved(self):
        self.assertEqual(
            "Family Passage",
            migration.migrated_entry_title("Family Passage", "ExampleShare"),
        )
        self.assertEqual(
            "BlueSky Passage",
            migration.migrated_entry_title("BlueSky Passage", "ExampleShare"),
        )
        self.assertEqual(
            "BlueSky Passage ()",
            migration.migrated_entry_title("BlueSky Passage ()", None),
        )


class ParserTests(unittest.TestCase):
    def test_kml_keeps_every_record_and_converts_units(self):
        records = parser.parse_kml(KML)
        self.assertEqual(2, len(records))
        self.assertEqual("1001", records[0].source_event_id)
        self.assertAlmostEqual(5.0, records[0].sog_kn, places=2)
        self.assertAlmostEqual(3.048, records[0].elevation_m, places=3)
        self.assertEqual("All well", records[1].message_text)
        self.assertEqual("2026-08-20T20:00:00Z", records[0].recorded_at_utc)

    def test_malformed_or_untimestamped_kml_fails_closed(self):
        with self.assertRaises(parser.KmlParseError):
            parser.parse_kml("<kml><broken>")
        with self.assertRaises(parser.KmlParseError):
            parser.parse_kml("<kml><Document /></kml>")

    def test_bounded_history_can_accept_valid_empty_kml(self):
        self.assertEqual([], parser.parse_kml("<kml><Document /></kml>", allow_empty=True))
        with self.assertRaises(parser.KmlParseError):
            parser.parse_kml("<html><body /></html>", allow_empty=True)

    def test_predictwind_shape_normalizes(self):
        records = parser.records_from_mappings(
            [{"t": 1787256000, "p": [18.1, -61.2], "bsp": 6.2, "bearing": 275}]
        )
        self.assertEqual(1, len(records))
        self.assertEqual(18.1, records[0].latitude)
        self.assertEqual(-61.2, records[0].longitude)
        self.assertEqual(6.2, records[0].sog_kn)

    def test_zero_values_and_false_flags_are_preserved(self):
        records = parser.records_from_mappings(
            [{"timestamp": "2026-08-20T20:00:00Z", "lat": 0, "lon": 0, "sog_kn": 0, "valid_gps_fix": False}]
        )
        self.assertEqual(0, records[0].latitude)
        self.assertEqual(0, records[0].longitude)
        self.assertEqual(0, records[0].sog_kn)
        self.assertFalse(records[0].valid_gps_fix)


class CalculationTests(unittest.TestCase):
    def test_geodesic_and_cardinal(self):
        distance = calculations.haversine_nm(0, 0, 0, 1)
        self.assertAlmostEqual(60.04, distance, places=1)
        self.assertAlmostEqual(90.0, calculations.initial_bearing_true(0, 0, 0, 1), places=1)
        self.assertEqual("W", calculations.cardinal(270))

    def test_eta_requires_positive_closing_rate(self):
        points = [
            {"recorded_at_utc": "2026-08-20T20:00:00Z", "latitude": 18.0, "longitude": -62.0},
            {"recorded_at_utc": "2026-08-20T21:00:00Z", "latitude": 18.0, "longitude": -61.9},
        ]
        result = calculations.destination_metrics(
            points, {"latitude": 18.0, "longitude": -61.0}
        )
        self.assertGreater(result["closing_rate_kn"], 0)
        self.assertIsNotNone(result["eta_utc"])
        self.assertIn(
            result["daylight_at_eta"]["state"],
            {"daylight", "civil twilight", "darkness"},
        )

    def test_cross_track_is_zero_on_direct_reference(self):
        value = calculations.cross_track_nm(0, 0, 0, 10, 0, 5)
        self.assertAlmostEqual(0, value, places=5)


class RouteDeviationTests(unittest.TestCase):
    def test_signed_deviation_uses_port_left_and_starboard_right(self):
        route = [[-76.0, 35.0], [-75.0, 35.0]]
        points = [
            {"id": 1, "recorded_at_utc": "2026-08-23T12:00:00Z", "latitude": 35.0, "longitude": -76.0},
            {"id": 2, "recorded_at_utc": "2026-08-23T13:00:00Z", "latitude": 35.1, "longitude": -75.7},
        ]
        result = calculations.route_deviation_analysis(points, route, modeled_total_hours=10, departure_at_utc="2026-08-23T12:00:00Z")
        self.assertTrue(result["available"])
        self.assertEqual("port", result["current_side"])
        self.assertLess(result["current_signed_deviation_nm"], 0)

        points[-1]["latitude"] = 34.9
        result = calculations.route_deviation_analysis(points, route, modeled_total_hours=10, departure_at_utc="2026-08-23T12:00:00Z")
        self.assertEqual("starboard", result["current_side"])
        self.assertGreater(result["current_signed_deviation_nm"], 0)

    def test_route_progress_never_moves_backward(self):
        route = [[-76.0, 35.0], [-75.5, 35.0], [-75.0, 35.0]]
        points = [
            {"id": 1, "recorded_at_utc": "2026-08-23T12:00:00Z", "latitude": 35.0, "longitude": -75.7},
            {"id": 2, "recorded_at_utc": "2026-08-23T13:00:00Z", "latitude": 35.0, "longitude": -75.9},
            {"id": 3, "recorded_at_utc": "2026-08-23T14:00:00Z", "latitude": 35.0, "longitude": -75.4},
        ]
        result = calculations.route_deviation_analysis(points, route, modeled_total_hours=12, departure_at_utc="2026-08-23T12:00:00Z")
        progress = [item["modeled_progress_nm"] for item in result["samples"]]
        self.assertEqual(progress, sorted(progress))

    def test_waypoint_timing_interpolates_modeled_elapsed(self):
        route = [[-76.0, 35.0], [-75.5, 35.0], [-75.0, 35.0]]
        waypoints = [
            {"longitude": -76.0, "latitude": 35.0, "elapsed_hours": 0.0},
            {"longitude": -75.5, "latitude": 35.0, "elapsed_hours": 4.0},
            {"longitude": -75.0, "latitude": 35.0, "elapsed_hours": 10.0},
        ]
        points = [
            {"id": 1, "recorded_at_utc": "2026-08-23T12:00:00Z", "latitude": 35.0, "longitude": -76.0},
            {"id": 2, "recorded_at_utc": "2026-08-23T17:00:00Z", "latitude": 35.0, "longitude": -75.5},
        ]
        result = calculations.route_deviation_analysis(points, route, route_waypoints=waypoints, modeled_total_hours=10, departure_at_utc="2026-08-23T12:00:00Z")
        self.assertAlmostEqual(4.0, result["modeled_elapsed_to_progress_hours"], places=1)
        self.assertAlmostEqual(1.0, result["time_delta_hours"], places=1)


class GarminDateTests(unittest.TestCase):
    def test_bounded_parameters_are_utc_and_ordered(self):
        result = garmin_dates.garmin_date_params(
            "2026-08-01T00:00:00-04:00", "2026-08-08T04:00:00Z"
        )
        self.assertEqual("2026-08-01T04:00:00Z", result["d1"])
        self.assertEqual("2026-08-08T04:00:00Z", result["d2"])

    def test_reversed_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            garmin_dates.garmin_date_params(
                "2026-08-02T00:00:00Z", "2026-08-01T00:00:00Z"
            )


class LandEndpointTests(unittest.TestCase):
    def test_coastal_land_cell_can_resolve_to_nearby_modeled_water(self):
        # A coarse shoreline cell near Hampton is intentionally classified as
        # land even though navigable water is within about a nautical mile.
        point = (37.0, -76.3)
        self.assertTrue(land.is_land(*point))
        resolved = land.nearest_water_point(*point, max_distance_nm=2.0)
        self.assertIsNotNone(resolved)
        self.assertLess(resolved["distance_nm"], 2.0)
        self.assertFalse(land.is_land(resolved["latitude"], resolved["longitude"]))

    def test_inland_coordinate_does_not_silently_snap_to_coast(self):
        self.assertIsNone(
            land.nearest_water_point(37.5407, -77.4360, max_distance_nm=2.0)
        )


class RoutingTests(unittest.TestCase):
    def test_no_weather_is_explicit_water_valid_reference(self):
        profile = routing.VesselProfile.from_mapping({})
        routes = routing.candidate_routes((18.0, -62.0), (17.2, -62.0))
        result = routing.score_routes(routes, profile, {}, "2026-08-20T20:00:00Z")
        self.assertEqual("water_valid_reference", result["method"])
        self.assertFalse(result["weather_used"])
        self.assertEqual("water_reference", result["selected"]["key"])
        self.assertTrue(result["selected"]["land_valid"])
        self.assertFalse(result["reference"]["scored_candidate"])
        self.assertIn("not a nautical chart", result["disclaimer"])

    def test_direct_hampton_to_beaufort_is_rejected_and_water_path_detours(self):
        start = (36.99, -76.30)
        destination = (34.68, -76.68)
        self.assertFalse(land.segment_is_water(start, destination))
        path = routing.shortest_water_path(start, destination)
        self.assertTrue(land.path_is_water(path))
        self.assertGreater(routing.path_distance(path), calculations.haversine_nm(*start, *destination) + 20)

    def test_no_go_heading_is_not_assigned_a_slow_straight_speed(self):
        profile = routing.VesselProfile.from_mapping({
            "hull_configuration": "monohull sailboat",
            "observed_cruise_speed_kn": 6.0,
            "minimum_upwind_twa_deg": 40,
        })
        weather_value = {"wind_speed_kn": 14.0, "wind_dir_deg": 180.0}
        self.assertIsNone(routing.performance_on_heading(profile, 180.0, weather_value))
        self.assertIsNotNone(routing.performance_on_heading(profile, 135.0, weather_value))

    def test_sailing_search_requires_a_wind_vector(self):
        start = (18.0, -62.0)
        destination = (17.2, -62.0)
        profile = routing.VesselProfile.from_mapping({
            "hull_configuration": "monohull sailboat",
            "observed_cruise_speed_kn": 6.0,
        })
        baseline = routing.shortest_water_path(start, destination)
        requests = routing.route_weather_sample_requests(
            baseline, "2026-08-20T20:00:00Z", profile
        )
        marine_only = [{
            **item,
            "conditions_available": False,
            "maritime_available": True,
            "wave_height_m": 0.7,
            "current_speed_kn": 0.5,
            "current_dir_deg": 180.0,
        } for item in requests]
        result = routing.optimize_sailing_route(
            start, destination, "2026-08-20T20:00:00Z", profile, baseline, marine_only
        )
        self.assertEqual("water_valid_reference", result["method"])
        self.assertTrue(result["warnings"])
        self.assertFalse(result["reference"]["scored_candidate"])

    def test_weather_search_tacks_and_stays_off_land(self):
        start = (18.0, -62.0)
        destination = (17.2, -62.0)
        profile = routing.VesselProfile.from_mapping({
            "hull_configuration": "monohull sailboat",
            "observed_cruise_speed_kn": 6.0,
            "minimum_upwind_twa_deg": 40,
        })
        baseline = routing.shortest_water_path(start, destination)
        requests = routing.route_weather_sample_requests(
            baseline, "2026-08-20T20:00:00Z", profile
        )
        samples = [{
            **item,
            "conditions_available": True,
            "maritime_available": True,
            "wind_speed_kn": 14.0,
            "wind_dir_deg": 180.0,
            "wave_height_m": 0.5,
            "current_speed_kn": 0.0,
            "current_dir_deg": 0.0,
        } for item in requests]
        result = routing.optimize_sailing_route(
            start, destination, "2026-08-20T20:00:00Z", profile, baseline, samples
        )
        self.assertEqual("xweather_sailing_search", result["method"])
        self.assertTrue(result["selected"]["land_valid"])
        self.assertEqual(0, result["selected"]["no_go_violations"])
        self.assertGreater(result["selected"]["distance_nm"], result["reference"]["distance_nm"])
        self.assertEqual(len(result["selected"]["coordinates"]), len(result["selected"]["waypoints"]))
        self.assertEqual(0.0, result["selected"]["waypoints"][0]["elapsed_hours"])
        self.assertAlmostEqual(result["selected"]["estimated_hours"], result["selected"]["waypoints"][-1]["elapsed_hours"], places=2)

    def test_partial_profile_has_a_bounded_fallback_speed(self):
        profile = routing.VesselProfile.from_mapping({"waterline_length_ft": 36})
        self.assertGreater(profile.base_speed_kn, 3)
        self.assertLess(profile.base_speed_kn, 12)
        self.assertEqual("hull estimate", profile.completeness["speed_method"])


class WeatherTests(unittest.TestCase):
    def test_model_period_must_be_close_to_the_requested_time(self):
        target = "2026-08-20T20:00:00Z"
        near = {
            "success": True,
            "response": [{
                "periods": [{
                    "dateTimeISO": "2026-08-20T21:00:00Z",
                    "windSpeedKTS": 12.0,
                }]
            }],
        }
        sample = weather.parse_xweather_sample(
            latitude=18.0,
            longitude=-61.0,
            valid_at_utc=target,
            conditions_payload=near,
            maritime_payload=None,
        )
        self.assertTrue(sample.conditions_available)
        self.assertEqual(12.0, sample.wind_speed_kn)
        self.assertEqual("modeled", sample.quality_state)

        far = {
            "success": True,
            "response": [{
                "periods": [{
                    "dateTimeISO": "2026-08-21T08:00:00Z",
                    "windSpeedKTS": 99.0,
                }]
            }],
        }
        missing = weather.parse_xweather_sample(
            latitude=18.0,
            longitude=-61.0,
            valid_at_utc=target,
            conditions_payload=far,
            maritime_payload=None,
        )
        self.assertFalse(missing.conditions_available)
        self.assertIsNone(missing.wind_speed_kn)
        self.assertEqual("unavailable", missing.quality_state)
        self.assertTrue(missing.warnings)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "archive.sqlite3"
        self.archive = database.SQLiteArchive(self.path)
        self.archive.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_is_idempotent_and_integrity_is_ok(self):
        self.archive.initialize()
        self.assertEqual("ok", self.archive.integrity_check())

    def test_v2_archive_migrates_without_losing_rows_or_routes(self):
        legacy_path = Path(self.temp.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript(
                """
                CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO schema_info VALUES('schema_version','1');
                CREATE TABLE track_points (
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
                    import_id INTEGER,
                    ingested_at_utc TEXT NOT NULL
                );
                CREATE TABLE passages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('planned','active','arrived','completed')),
                    started_at_utc TEXT,
                    arrived_at_utc TEXT,
                    ended_at_utc TEXT,
                    start_point_id INTEGER,
                    current_destination_version_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE UNIQUE INDEX idx_one_open_passage
                    ON passages((1)) WHERE status IN ('active','arrived');
                CREATE TABLE route_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    passage_id INTEGER,
                    label TEXT NOT NULL,
                    source TEXT NOT NULL,
                    imported_at_utc TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    route_json TEXT NOT NULL,
                    UNIQUE(passage_id, content_sha256)
                );
                INSERT INTO track_points(
                    source,source_event_id,dedupe_key,recorded_at_utc,
                    latitude,longitude,sog_kn,valid_gps_fix,in_emergency,
                    raw_json,quality_flags_json,ingested_at_utc
                ) VALUES(
                    'garmin_mapshare','legacy-1','legacy-dedupe',
                    '2026-08-20T20:00:00Z',18.0,-61.0,5.0,1,0,
                    '{}','[]','2026-08-20T20:01:00Z'
                );
                INSERT INTO passages(
                    name,status,started_at_utc,start_point_id,created_at_utc,updated_at_utc
                ) VALUES(
                    'Legacy passage','active','2026-08-20T20:00:00Z',1,
                    '2026-08-20T20:00:00Z','2026-08-20T20:00:00Z'
                );
                INSERT INTO route_versions(
                    passage_id,label,source,imported_at_utc,content_sha256,route_json
                ) VALUES(
                    1,'Legacy route','gpx_import','2026-08-20T20:00:00Z',
                    'legacy-route','[[-61.0,18.0],[-60.9,18.1]]'
                );
                """
            )
        migrated = database.SQLiteArchive(legacy_path)
        migrated.initialize()
        self.assertEqual(1, migrated.dashboard_state()["archive"]["total_points"])
        self.assertEqual("planned", migrated.passage_detail(1)["status"])
        self.assertEqual(2, len(migrated.current_route(1)["coordinates"]))
        with sqlite3.connect(legacy_path) as connection:
            self.assertEqual(
                "2",
                connection.execute(
                    "SELECT value FROM schema_info WHERE key='schema_version'"
                ).fetchone()[0],
            )
            passage_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(passages)")
            }
            route_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(route_versions)")
            }
            indexes = {
                row[1] for row in connection.execute("PRAGMA index_list(passages)")
            }
        self.assertTrue(
            {"notes", "departure_latitude", "departure_longitude"}
            <= passage_columns
        )
        self.assertIn("summary_json", route_columns)
        self.assertNotIn("idx_one_open_passage", indexes)
        self.assertEqual("ok", migrated.integrity_check())

    def test_ingest_is_atomic_and_deduplicates(self):
        records = parser.parse_kml(KML)
        first = self.archive.ingest_records(records, "garmin_mapshare", live=True)
        second = self.archive.ingest_records(records, "garmin_mapshare", live=True)
        self.assertEqual(2, first["inserted"])
        self.assertEqual(0, second["inserted"])
        state = self.archive.dashboard_state()
        self.assertEqual(2, state["archive"]["total_points"])
        self.assertEqual("All well", state["latest_message"]["text"])

    def test_delayed_report_is_kept_once_and_marked_out_of_order(self):
        records = parser.parse_kml(KML)
        self.archive.ingest_records([records[1]], "garmin_mapshare", live=True)
        result = self.archive.ingest_records(
            [records[0], records[1]], "garmin_mapshare", live=True
        )
        self.assertEqual(1, result["inserted"])
        points = self.archive.query_points(source="garmin_mapshare")["points"]
        self.assertEqual(2, len(points))
        self.assertIn("out_of_order", points[0]["quality_flags"])

    def test_point_detail_stays_attached_to_its_record(self):
        self.archive.ingest_records(parser.parse_kml(KML), "garmin_mapshare")
        points = self.archive.query_points()["points"]
        self.assertIsNone(points[0]["message_text"])
        self.assertEqual("All well", points[1]["message_text"])

    def test_combined_track_prefers_garmin_on_exact_timestamp(self):
        garmin = parser.TrackRecord(
            recorded_at_utc="2026-08-20T20:00:00Z",
            latitude=18.0,
            longitude=-61.0,
            source_event_id="garmin-1",
        )
        predictwind = parser.TrackRecord(
            recorded_at_utc="2026-08-20T20:00:00Z",
            latitude=19.0,
            longitude=-62.0,
            source_event_id="pw-1",
        )
        self.archive.ingest_records([predictwind], "predictwind_snapshot")
        self.archive.ingest_records([garmin], "garmin_mapshare")
        result = self.archive.query_points(source="canonical")
        self.assertEqual(1, result["total_matching"])
        self.assertEqual("garmin_mapshare", result["points"][0]["source"])
        self.assertEqual("canonical", result["points"][0]["display_track"])

    def test_passage_is_a_previewed_annotation_and_never_a_live_state(self):
        self.archive.ingest_records(parser.parse_kml(KML), "garmin_mapshare")
        preview = self.archive.preview_passage(
            passage_id=None,
            name="Test passage",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
            destination={
                "name": "Test harbor",
                "latitude": 18.01,
                "longitude": -60.99,
                "arrival_radius_nm": 0.2,
            },
        )
        self.assertEqual(2, preview["report_count"])
        passage = self.archive.save_passage(
            passage_id=None,
            preview_token=preview["preview_token"],
            name="Test passage",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
            destination={
                "name": "Test harbor",
                "latitude": 18.01,
                "longitude": -60.99,
                "arrival_radius_nm": 0.2,
            },
        )
        self.assertEqual("planned", passage["status"])
        self.assertEqual("open_ended", passage["range_mode"])
        self.assertIsNone(self.archive.dashboard_state()["passage"])
        self.assertEqual(2, self.archive.dashboard_state()["archive"]["total_points"])

    def test_passage_save_rejects_changed_details_after_preview(self):
        preview = self.archive.preview_passage(
            passage_id=None,
            name="Original",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
        )
        with self.assertRaises(ValueError):
            self.archive.save_passage(
                passage_id=None,
                preview_token=preview["preview_token"],
                name="Changed",
                start_utc="2026-08-20T19:00:00Z",
                end_utc=None,
            )

    def test_passage_can_be_edited_after_creation_without_a_destination(self):
        first_preview = self.archive.preview_passage(
            passage_id=None,
            name="Range",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
        )
        created = self.archive.save_passage(
            passage_id=None,
            preview_token=first_preview["preview_token"],
            name="Range",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
        )
        edit_preview = self.archive.preview_passage(
            passage_id=created["id"],
            name="Renamed",
            start_utc="2026-08-20T19:00:00Z",
            end_utc="2026-08-20T22:00:00Z",
        )
        edited = self.archive.save_passage(
            passage_id=created["id"],
            preview_token=edit_preview["preview_token"],
            name="Renamed",
            start_utc="2026-08-20T19:00:00Z",
            end_utc="2026-08-20T22:00:00Z",
        )
        self.assertEqual("completed", edited["status"])
        self.assertEqual("specific_time", edited["range_mode"])

    def test_route_context_becomes_stale_and_destination_can_be_cleared(self):
        original_destination = {
            "name": "Harbor A",
            "latitude": 18.1,
            "longitude": -60.9,
            "arrival_radius_nm": 2.0,
        }
        preview = self.archive.preview_passage(
            passage_id=None,
            name="Context passage",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
            departure_latitude=18.0,
            departure_longitude=-61.0,
            destination=original_destination,
        )
        passage = self.archive.save_passage(
            passage_id=None,
            preview_token=preview["preview_token"],
            name="Context passage",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
            departure_latitude=18.0,
            departure_longitude=-61.0,
            destination=original_destination,
        )
        self.archive.add_route_version(
            passage["id"],
            "Comparison",
            "great_circle_reference",
            "context-route",
            [[-61.0, 18.0], [-60.9, 18.1]],
            summary={"context_hash": database.route_context_hash(passage, None)},
        )
        self.assertEqual(
            "current",
            self.archive.passage_detail(passage["id"])["route"]["context_status"],
        )

        replacement = {
            "name": "Harbor B",
            "latitude": 18.2,
            "longitude": -60.8,
            "arrival_radius_nm": 2.0,
        }
        edit_preview = self.archive.preview_passage(
            passage_id=passage["id"],
            name="Context passage",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
            departure_latitude=18.0,
            departure_longitude=-61.0,
            destination=replacement,
        )
        self.archive.save_passage(
            passage_id=passage["id"],
            preview_token=edit_preview["preview_token"],
            name="Context passage",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
            departure_latitude=18.0,
            departure_longitude=-61.0,
            destination=replacement,
        )
        self.assertEqual(
            "stale",
            self.archive.passage_detail(passage["id"])["route"]["context_status"],
        )

        clear_preview = self.archive.preview_passage(
            passage_id=passage["id"],
            name="Context passage",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
            departure_latitude=18.0,
            departure_longitude=-61.0,
            destination=None,
            clear_destination=True,
        )
        self.assertEqual(2, clear_preview["destination_versions_removed"])
        cleared = self.archive.save_passage(
            passage_id=passage["id"],
            preview_token=clear_preview["preview_token"],
            name="Context passage",
            start_utc="2026-08-20T19:00:00Z",
            end_utc=None,
            departure_latitude=18.0,
            departure_longitude=-61.0,
            destination=None,
            clear_destination=True,
        )
        self.assertIsNone(cleared["destination_name"])
        self.assertEqual([], cleared["destination_versions"])

    def test_backfill_job_is_chunked_resumable_and_preview_only(self):
        job = self.archive.create_backfill_job(
            phase="preview",
            start_utc="2026-01-01T00:00:00Z",
            end_utc="2026-01-20T00:00:00Z",
            chunk_days=7,
        )
        self.assertEqual(3, job["chunks_total"])
        first = self.archive.next_backfill_chunk(job["id"])
        self.assertEqual(0, first["chunk_index"])
        updated = self.archive.complete_backfill_chunk(
            job["id"],
            first["id"],
            returned=10,
            inserted=0,
            duplicated=2,
            rejected=0,
            first_recorded_at_utc="2026-01-01T01:00:00Z",
            last_recorded_at_utc="2026-01-07T23:00:00Z",
        )
        self.assertEqual(1, updated["chunks_completed"])
        self.assertEqual("running", updated["status"])
        second = self.archive.next_backfill_chunk(job["id"])
        failed = self.archive.fail_backfill_chunk(
            job["id"], second["id"], "temporary source failure"
        )
        self.assertEqual("failed", failed["status"])
        resumed = self.archive.next_backfill_chunk(job["id"])
        self.assertEqual(second["id"], resumed["id"])
        self.assertEqual("running", resumed["status"])

    def test_startup_closes_a_completed_backfill_import_after_power_loss(self):
        preview = self.archive.create_backfill_job(
            phase="preview",
            start_utc="2026-01-01T00:00:00Z",
            end_utc="2026-01-02T00:00:00Z",
        )
        preview_chunk = self.archive.next_backfill_chunk(preview["id"])
        self.archive.complete_backfill_chunk(
            preview["id"],
            preview_chunk["id"],
            returned=0,
            inserted=0,
            duplicated=0,
            rejected=0,
            first_recorded_at_utc=None,
            last_recorded_at_utc=None,
        )
        import_id = self.archive.begin_import(
            "garmin_mapshare", "Power-loss test", "b" * 64
        )
        commit = self.archive.create_backfill_job(
            phase="commit",
            start_utc="2026-01-01T00:00:00Z",
            end_utc="2026-01-02T00:00:00Z",
            preview_job_id=preview["id"],
            import_id=import_id,
        )
        commit_chunk = self.archive.next_backfill_chunk(commit["id"])
        self.archive.complete_backfill_chunk(
            commit["id"],
            commit_chunk["id"],
            returned=0,
            inserted=0,
            duplicated=0,
            rejected=0,
            first_recorded_at_utc=None,
            last_recorded_at_utc=None,
        )
        self.assertEqual("running", self.archive.list_imports()[0]["status"])
        self.archive.initialize()
        self.assertEqual("completed", self.archive.list_imports()[0]["status"])

    def test_recorder_preview_is_non_mutating_and_explains_duplicates(self):
        existing = parser.TrackRecord(
            recorded_at_utc="2026-08-20T20:00:00Z",
            latitude=18.0,
            longitude=-61.0,
            source_event_id="recorder-1",
        )
        unseen = parser.TrackRecord(
            recorded_at_utc="2026-08-20T20:10:00Z",
            latitude=18.01,
            longitude=-60.99,
            source_event_id="recorder-2",
        )
        self.archive.ingest_records([existing], "ha_recorder")
        before = self.archive.dashboard_state()["archive"]["total_points"]
        preview = self.archive.preview_records([existing, unseen], "ha_recorder")
        after = self.archive.dashboard_state()["archive"]["total_points"]
        self.assertEqual(1, preview["duplicated"])
        self.assertEqual(1, preview["new"])
        self.assertEqual(before, after)

    def test_weather_cache_preserves_missing_values_as_null(self):
        result = self.archive.save_weather_samples(
            [{
                "provider": "xweather",
                "quality_state": "modeled",
                "valid_at_utc": "2026-08-20T20:00:00Z",
                "latitude": 18.0,
                "longitude": -61.0,
                "wind_speed_kn": 12.5,
                "wave_height_m": None,
                "conditions_available": True,
                "maritime_available": False,
                "warnings": ["Marine data unavailable"],
            }]
        )
        self.assertEqual(1, result["stored"])
        sample = self.archive.query_weather_samples()[0]
        self.assertEqual(12.5, sample["wind_speed_kn"])
        self.assertIsNone(sample["wave_height_m"])
        self.assertFalse(sample["maritime_available"])

    def test_weather_failure_gap_expires_but_historical_model_does_not(self):
        target = "2025-08-20T20:00:00Z"
        base = {
            "provider": "xweather",
            "valid_at_utc": target,
            "latitude": 18.0,
            "longitude": -61.0,
            "conditions_available": False,
            "maritime_available": False,
        }
        self.archive.save_weather_samples(
            [{**base, "purpose": "track", "quality_state": "unavailable"}]
        )
        self.assertIsNotNone(
            self.archive.cached_weather_sample(
                latitude=18.0, longitude=-61.0, valid_at_utc=target
            )
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE weather_samples SET requested_at_utc='2000-01-01T00:00:00Z'"
            )
        self.assertIsNone(
            self.archive.cached_weather_sample(
                latitude=18.0, longitude=-61.0, valid_at_utc=target
            )
        )

        self.archive.save_weather_samples(
            [{
                **base,
                "purpose": "route",
                "route_candidate": "direct",
                "quality_state": "modeled",
                "wind_speed_kn": 12.0,
                "conditions_available": True,
            }]
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE weather_samples SET requested_at_utc='2000-01-01T00:00:00Z' "
                "WHERE quality_state='modeled'"
            )
        self.assertEqual(
            12.0,
            self.archive.cached_weather_sample(
                latitude=18.0, longitude=-61.0, valid_at_utc=target
            )["wind_speed_kn"],
        )

    def test_import_rollback_only_removes_that_batch(self):
        live = parser.parse_kml(KML)[:1]
        self.archive.ingest_records(live, "garmin_mapshare")
        import_id = self.archive.begin_import("predictwind_snapshot", "snapshot.json", "a" * 64)
        imported = parser.records_from_mappings(
            [{"t": 1787256000, "p": [17.8, -61.4], "bsp": 4.5}]
        )
        self.archive.ingest_records(
            imported, "predictwind_snapshot", import_id=import_id
        )
        self.archive.finish_import(import_id)
        result = self.archive.rollback_import(import_id)
        self.assertEqual(1, result["removed"])
        state = self.archive.dashboard_state()
        self.assertEqual({"garmin_mapshare": 1}, state["archive"]["counts_by_source"])

    def test_exports_are_portable(self):
        self.archive.ingest_records(parser.parse_kml(KML), "garmin_mapshare")
        points = self.archive.query_points()["points"]
        for format_name, expected in (("csv", "recorded_at_utc"), ("geojson", "FeatureCollection"), ("gpx", "<trkpt")):
            _suffix, _mime, content = exporting.export_points(points, format_name)
            self.assertIn(expected, content)

    def test_csv_keeps_modeled_values_distinct_from_observations(self):
        self.archive.ingest_records(parser.parse_kml(KML), "garmin_mapshare")
        points = self.archive.query_points()["points"]
        weather = [{
            "track_point_id": points[0]["id"],
            "provider": "xweather",
            "quality_state": "modeled",
            "valid_at_utc": points[0]["recorded_at_utc"],
            "requested_at_utc": "2026-08-22T00:00:00Z",
            "wind_speed_kn": 12.5,
            "wave_height_m": 1.2,
            "warnings": [],
        }]
        _suffix, _mime, content = exporting.export_points(
            points, "csv", weather_samples=weather
        )
        self.assertIn("model_provider", content)
        self.assertIn("xweather,modeled", content)
        self.assertIn("12.5", content)


if __name__ == "__main__":
    unittest.main()
