"""Core archive/parser regression tests runnable with the Python standard library."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from _loader import calculations, database, exporting, migration, parser


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

    def test_ingest_is_atomic_and_deduplicates(self):
        records = parser.parse_kml(KML)
        first = self.archive.ingest_records(records, "garmin_mapshare", live=True)
        second = self.archive.ingest_records(records, "garmin_mapshare", live=True)
        self.assertEqual(2, first["inserted"])
        self.assertEqual(0, second["inserted"])
        state = self.archive.dashboard_state()
        self.assertEqual(2, state["archive"]["total_points"])
        self.assertEqual("All well", state["latest_message"]["text"])

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

    def test_passage_arrival_does_not_auto_complete(self):
        passage = self.archive.start_passage(
            "Test passage",
            started_at_utc="2026-08-20T19:00:00Z",
            destination={
                "name": "Test harbor",
                "latitude": 18.01,
                "longitude": -60.99,
                "arrival_radius_nm": 0.2,
            },
        )
        result = self.archive.ingest_records(
            parser.parse_kml(KML), "garmin_mapshare", live=True
        )
        self.assertIsNotNone(result["arrived_passage"])
        current = self.archive.dashboard_state()["passage"]
        self.assertEqual("arrived", current["status"])
        self.archive.end_passage(passage["id"], "2026-08-20T21:00:00Z")
        self.assertIsNone(self.archive.dashboard_state()["passage"])
        self.assertEqual(2, self.archive.dashboard_state()["archive"]["total_points"])

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


if __name__ == "__main__":
    unittest.main()
