#!/usr/bin/env python3
"""Run standalone structural checks for a BlueSky Passage source bundle."""

from __future__ import annotations

import ast
import compileall
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
if HERE.name == "tools":
    # Source-tree layout.
    ROOT = HERE.parent
    DOCUMENTS = ROOT
    TESTS = ROOT / "tests"
    TOOLS = ROOT / "tools"
else:
    # Home Assistant installation-ZIP layout:
    # /config/bluesky_passage_docs/validate_bundle.py
    ROOT = HERE.parent
    DOCUMENTS = HERE
    TESTS = HERE / "tests"
    TOOLS = ROOT / "bluesky_passage_tools"
COMPONENT = ROOT / "custom_components" / "bluesky_passage"

REQUIRED_MANIFEST_KEYS = {
    "domain",
    "name",
    "version",
    "codeowners",
    "config_flow",
    "documentation",
    "issue_tracker",
    "iot_class",
}
PRIVATE_PUBLICATION_PATTERNS = (
    (
        re.compile(
            r"https://(?:www\.)?predictwind\.com/tracking/"
            r"(?!YOUR-PUBLIC-ID(?:[\s\"'<>]|$))[^\s\"'<>]+"
        ),
        "specific PredictWind tracking URL",
    ),
    (
        re.compile(
            r"https://share\.garmin\.com/"
            r"(?!Feed/Share/\{\}|\{|YOUR-)[A-Za-z0-9_-]+"
        ),
        "specific Garmin share URL",
    ),
    (
        re.compile(
            r"notify\.mobile_app_(?!(?:your_phone|name)\b)[A-Za-z0-9_]+"
        ),
        "specific mobile notification action",
    ),
    (
        re.compile(r"vol\.Required\(\s*CONF_LINK_NAME\s*,\s*default="),
        "hard-coded MapShare default",
    ),
)


def _load_json(path: Path, errors: list[str]) -> dict:
    """Load an object-shaped JSON document and report a useful error."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        errors.append(f"{path.relative_to(ROOT)}: {err}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)}: expected a JSON object")
        return {}
    return value


def main() -> int:
    errors: list[str] = []
    manifest = _load_json(COMPONENT / "manifest.json", errors)
    _load_json(COMPONENT / "strings.json", errors)
    _load_json(COMPONENT / "translations" / "en.json", errors)
    hacs = _load_json(ROOT / "hacs.json", errors)

    version = (DOCUMENTS / "VERSION").read_text(encoding="utf-8").strip()
    if manifest.get("version") != version:
        errors.append("manifest.json and VERSION disagree")
    if version not in (DOCUMENTS / "README.md").read_text(encoding="utf-8"):
        errors.append("README does not contain VERSION")
    if f"## {version}" not in (DOCUMENTS / "CHANGELOG.md").read_text(
        encoding="utf-8"
    ):
        errors.append("CHANGELOG does not contain the current VERSION")

    missing_manifest = sorted(REQUIRED_MANIFEST_KEYS - manifest.keys())
    if missing_manifest:
        errors.append(
            "manifest.json is missing: " + ", ".join(missing_manifest)
        )
    if manifest.get("domain") != "bluesky_passage":
        errors.append("manifest.json domain is not bluesky_passage")
    manifest_keys = list(manifest)
    expected_manifest_keys = ["domain", "name", *sorted(manifest_keys[2:])]
    if manifest_keys != expected_manifest_keys:
        errors.append(
            "manifest.json keys must be domain, name, then alphabetical"
        )
    if manifest.get("codeowners") != ["@psuslick"]:
        errors.append("manifest.json codeowners does not identify @psuslick")
    if hacs.get("name") != "BlueSky Passage":
        errors.append("hacs.json name is not BlueSky Passage")

    integration_dirs = sorted(
        path.name
        for path in (ROOT / "custom_components").iterdir()
        if path.is_dir()
    )
    if integration_dirs != ["bluesky_passage"]:
        errors.append(
            "custom_components must contain only the bluesky_passage integration"
        )

    for required in (
        ROOT / "README.md",
        ROOT / "LICENSE",
        ROOT / ".github" / "workflows" / "validate.yml",
        COMPONENT / "brand" / "icon.svg",
        COMPONENT / "brand" / "icon.png",
        COMPONENT / "data" / "landmask_1_25min.bit.gz",
        COMPONENT / "coastline.py",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "COPYING.LGPL-DATA",
        ROOT / "COPYING.LESSER-DATA",
    ):
        if not required.is_file():
            errors.append(
                f"Required repository file is absent: {required.relative_to(ROOT)}"
            )

    icon_png = COMPONENT / "brand" / "icon.png"
    if icon_png.is_file():
        data = icon_png.read_bytes()
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            errors.append("brand/icon.png is not a valid PNG header")
        else:
            width, height = struct.unpack(">II", data[16:24])
            if (width, height) != (256, 256):
                errors.append("brand/icon.png must be exactly 256 x 256 pixels")

    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".png", ".pyc"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, description in PRIVATE_PUBLICATION_PATTERNS:
            if pattern.search(content):
                errors.append(
                    f"Found {description} in {path.relative_to(ROOT)}"
                )

    if not compileall.compile_dir(COMPONENT, quiet=1):
        errors.append("Python integration compile failed")
    if not compileall.compile_dir(TOOLS, quiet=1):
        errors.append("Python tools compile failed")

    init_text = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    if "CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)" not in init_text:
        errors.append("Config-entry-only integration is missing CONFIG_SCHEMA")

    javascript = COMPONENT / "frontend" / "bluesky-passage-panel.js"
    if shutil.which("node"):
        result = subprocess.run(
            ["node", "--check", str(javascript)], capture_output=True, text=True
        )
        if result.returncode:
            errors.append(f"JavaScript syntax: {result.stderr.strip()}")

    frontend_text = javascript.read_text(encoding="utf-8")
    frontend_register_text = (COMPONENT / "frontend.py").read_text(encoding="utf-8")
    if "config_panel_domain=DOMAIN" in frontend_register_text:
        errors.append("Sidebar panel must not hijack the integration Options Flow")
    for required_frontend_marker in (
        "this._backfillStart =",
        "this._backfillEnd =",
        'event.target.id === "backfill-start"',
        'event.target.id === "backfill-end"',
        "Requested range:",
    ):
        if required_frontend_marker not in frontend_text:
            errors.append(
                f"Backfill UI persistence/range marker missing: {required_frontend_marker}"
            )
    # The inverse Web Mercator longitude must round-trip _project().  The 2.2.0
    # expression added 180 degrees during unprojection, teleporting the map on pan.
    if "const lon=((x/scale*360)%360+360)%360-180" not in frontend_text:
        errors.append("Map inverse-projection longitude regression guard failed")
    if "x/scale*360+180" in frontend_text:
        errors.append("Map inverse-projection contains the v2.2.0 180-degree offset bug")
    if 'value !== null && value !== undefined && value !== ""' not in frontend_text:
        errors.append("Frontend numeric validity must preserve missing values instead of coercing null to zero")
    if "max_points: 10000" not in frontend_text:
        errors.append("History/chart query must request the backend 10,000-point display limit")
    for chart_marker in (
        "chart-coverage",
        "No cached track-weather samples in this period",
        "this._query.range?.start_utc",
        "analytics-svg",
        "Gust envelope",
        "Modeled weather uses dashed interpolation",
    ):
        if chart_marker not in frontend_text:
            errors.append(f"v2.3 chart regression marker missing: {chart_marker}")

    for required_frontend_marker in (
        "_startMapDrag",
        "_moveMapDrag",
        "_endMapDrag",
        "_panMap",
        'addEventListener("pointerdown"',
        "Rejected · crosses modeled land",
        "Shortest ENC-valid reference",
        "Minimum upwind TWA / no-go (deg)",
    ):
        if required_frontend_marker not in frontend_text:
            errors.append(
                f"v2.2 map/routing UI marker missing: {required_frontend_marker}"
            )

    for required_v23_marker in (
        '_zoomMapAt',
        '_wheelMap',
        '_doubleClickMap',
        'Math.log2(distance/this._mapPinch.startDistance)',
        '.map-controls,.map-attribution,a,button,input,select,label',
        'Actual vs modeled',
        'Deviation scale',
        'Auto (±',
        '_deviationStorageKey',
        'Map connectors',
        'modeled_progress_percent',
    ):
        if required_v23_marker not in frontend_text:
            errors.append(f"v2.3 map/deviation UI marker missing: {required_v23_marker}")

    for required_v24_marker in (
        'data-picker="passage"',
        'Set departure, routing gates, and arrival on map',
        'Place departure',
        'Place arrival',
        '_placePassagePin(event, map)',
        '_clearPassagePin(pin)',
        'this._passageDraft.departure_latitude=lat',
        'this._passageDraft.destination_latitude=lat',
        'this._passageMapPoints',
    ):
        if required_v24_marker not in frontend_text:
            errors.append(f"v2.4 passage-pin UI marker missing: {required_v24_marker}")

    for required_v3_marker in (
        'add-routing-gate',
        'routing_gates',
        'under_keel_clearance_ft',
        'polar_text',
    ):
        if required_v3_marker not in frontend_text:
            errors.append(f"v3 routing editor marker missing: {required_v3_marker}")

    tracker_text = (COMPONENT / "device_tracker.py").read_text(encoding="utf-8")
    if 'from homeassistant.components.device_tracker import SourceType, TrackerEntity' not in tracker_text:
        errors.append("TrackerEntity must use the current homeassistant.components.device_tracker import path")
    if 'device_tracker.config_entry import TrackerEntity' in tracker_text:
        errors.append("Deprecated TrackerEntity config_entry alias must not be used")

    routing_text = (COMPONENT / "routing.py").read_text(encoding="utf-8")
    routing_engine_path = COMPONENT / "routing_engine" / "engine.py"
    routing_engine_init = COMPONENT / "routing_engine" / "__init__.py"
    for required in (routing_engine_path, routing_engine_init):
        if not required.is_file():
            errors.append(f"Routing Engine v3 file missing: {required.relative_to(ROOT)}")
    routing_engine_text = routing_engine_path.read_text(encoding="utf-8") if routing_engine_path.is_file() else ""
    for marker in (
        'ROUTING_ENGINE_VERSION = "weather-routing-v3-isochrone-enc-depth"',
        "minimum_upwind_twa_deg",
        "parse_pol_text",
        "under_keel_clearance_ft",
        "minimum_depth_m",
    ):
        if marker not in routing_text:
            errors.append(f"Routing Engine v3 vessel/profile marker missing: {marker}")
    for marker in (
        'ENGINE_VERSION = "weather-routing-v3-isochrone-enc-depth"',
        "class RoutingEngineError",
        "def route_passage(",
        "first_completion_iteration",
        "performance_on_heading",
        "segment_is_safe",
        '"method": "weather_routing_v3"',
    ):
        if marker not in routing_engine_text:
            errors.append(f"Routing Engine v3 core marker missing: {marker}")

    parser_text = (COMPONENT / "parser.py").read_text(encoding="utf-8")
    feed_text = (COMPONENT / "feed.py").read_text(encoding="utf-8")
    if "allow_empty: bool = False" not in parser_text or "allow_empty=bool(params)" not in feed_text:
        errors.append("Bounded Garmin empty-KML handling regression guard is missing")

    database_text = (COMPONENT / "database.py").read_text(encoding="utf-8")
    if '"routing_engine": "weather-routing-v3-isochrone-enc-depth"' not in database_text:
        errors.append("Route context does not invalidate pre-v3 route semantics")
    if 'routing_gates_json' not in database_text or '"routing_gates"' not in database_text:
        errors.append("Routing gates are missing from passage persistence/context")

    land_text = (COMPONENT / "land.py").read_text(encoding="utf-8")
    for marker in ("landmask_1_25min.bit.gz", "segment_is_water", "path_is_water"):
        if marker not in land_text:
            errors.append(f"Land-mask implementation marker missing: {marker}")
    calculations_text = (COMPONENT / "calculations.py").read_text(encoding="utf-8")
    for marker in (
        "route_deviation_analysis",
        "minimum_progress_nm",
        "signed_deviation_nm",
        "modeled_elapsed_to_progress_hours",
    ):
        if marker not in calculations_text:
            errors.append(f"v2.3 route-deviation calculation marker missing: {marker}")

    websocket_text = (COMPONENT / "websocket.py").read_text(encoding="utf-8")
    if 'runtime.async_passage_detail' not in websocket_text:
        errors.append("Passage detail WebSocket must dispatch through the BlueSkyRuntime facade")
    if 'runtime.coordinator.async_passage_detail' in websocket_text:
        errors.append("Passage detail WebSocket incorrectly dispatches to BlueSkyCoordinator")

    # Verify the dispatch target actually owns the method without importing Home Assistant.
    coordinator_source = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    coordinator_ast = ast.parse(coordinator_source)
    class_methods = {
        node.name: {child.name for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in coordinator_ast.body
        if isinstance(node, ast.ClassDef)
    }
    if "async_passage_detail" not in class_methods.get("BlueSkyRuntime", set()):
        errors.append("BlueSkyRuntime does not own async_passage_detail required by the WebSocket handler")
    for marker in ('include_analysis', 'passage_detail_failed'):
        if marker not in websocket_text:
            errors.append(f"v2.3.3 passage-detail resilience marker missing: {marker}")
    for marker in ('ROUTING_GATE_SCHEMA', 'routing_gates'):
        if marker not in websocket_text:
            errors.append(f"v3 routing gate WebSocket marker missing: {marker}")
    if 'include_analysis: !this._admin' not in frontend_text:
        errors.append("Admin passage editing must bypass supplemental live route analysis")
    coordinator_text = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    archive_text = (COMPONENT / "archive.py").read_text(encoding="utf-8")
    for marker in ('passage_edit_detail', 'async_passage_edit_detail'):
        if marker not in database_text + coordinator_text + archive_text:
            errors.append(f"v2.3.2 edit-detail isolation marker missing: {marker}")
    coastline_text = (COMPONENT / "coastline.py").read_text(encoding="utf-8")
    for marker in (
        'ENC_ROOT = "https://encdirect.noaa.gov/arcgis/rest/services/encdirect"',
        'EncGeometryClient',
        'EncConstraint',
        'strict_validate_route',
        '.Land_Area',
        '.Coverage_area',
        '.Depth_Area',
        '.Unsurveyed_Area',
        'segment_intersects_boundary',
        'coverage_for_path',
        'minimum_depth_m',
        'segment_is_safe',
        'path_is_safe',
        '"outFields": "*"',
    ):
        if marker not in coastline_text:
            errors.append(f"v3 ENC geography marker missing: {marker}")
    for marker in ('endpoint_adjustments', 'endpoint_notes', 'constraint.nearest_water_point'):
        if marker not in coordinator_text:
            errors.append(f"v2.5 endpoint-resolution marker missing: {marker}")
    if 'from .land import is_land' in coordinator_text or 'from .land import segment_is_water' in coordinator_text:
        errors.append("Production coordinator must not certify routes with the legacy raster land API")
    for marker in (
        'EncGeometryClient',
        'await self.coastline_client.async_prepare',
        'minimum_depth_m=profile.minimum_depth_m',
        'route_passage(',
        'RoutingEngineError',
        'constraint=constraint',
        'validation = strict_validate_route(',
        'final_validation',
        'Routing Engine v3 sailing route',
    ):
        if marker not in coordinator_text:
            errors.append(f"v3 production routing marker missing: {marker}")

    notifications_text = (COMPONENT / "notifications.py").read_text(encoding="utf-8")
    for marker in ('async_clear_routine', '_emergency_initialized', 'if not self.runtime.notifications_enabled:', 'if not force and not self.runtime.notifications_enabled', 'force=True'):
        if marker not in notifications_text:
            errors.append(f"v2.5 alert behavior marker missing: {marker}")
    for marker in ('notifications_set', 'runtime.async_set_notifications_enabled'):
        if marker not in websocket_text:
            errors.append(f"v2.5 alert WebSocket marker missing: {marker}")
    for marker in ('routine_alerts_enabled', 'emergency_alerts_always_enabled', 'async_set_notifications_enabled'):
        if marker not in coordinator_text:
            errors.append(f"v2.5 alert runtime marker missing: {marker}")
    for marker in ('toggle-alerts', 'Routine alerts on', 'Routine alerts off', 'Alerts ${runtime.routine_alerts_enabled ? "on" : "off"}'):
        if marker not in frontend_text:
            errors.append(f"v2.5 alert frontend marker missing: {marker}")
    for marker in ('Route validation', 'NOAA ENC Direct to GIS', 'Shortest ENC-valid reference'):
        if marker not in frontend_text:
            errors.append(f"v2.5 routing frontend marker missing: {marker}")

    backend_text = (COMPONENT / "websocket.py").read_text(encoding="utf-8")
    client_commands = set(re.findall(r'this\._call\("([a-z_]+)"', frontend_text))
    server_commands = set(re.findall(r'f"\{DOMAIN\}/([a-z_]+)"', backend_text))
    missing = sorted(client_commands - server_commands)
    if missing:
        errors.append(f"Frontend calls missing WebSocket commands: {', '.join(missing)}")

    scenario_runner = TOOLS / "run_routing_scenarios.py"
    if not scenario_runner.is_file():
        errors.append("Routing Engine v3 headless scenario runner is missing")
    else:
        scenario = subprocess.run(
            [sys.executable, str(scenario_runner)], capture_output=True, text=True, timeout=45
        )
        if scenario.returncode:
            errors.append(f"Routing Engine v3 scenarios failed: {scenario.stdout.strip()} {scenario.stderr.strip()}")
        else:
            try:
                scenario_payload = json.loads(scenario.stdout)
            except json.JSONDecodeError as err:
                errors.append(f"Routing scenario runner did not return JSON: {err}")
            else:
                if scenario_payload.get("engine_version") != "weather-routing-v3-isochrone-enc-depth":
                    errors.append("Routing scenario runner engine version mismatch")
                if scenario_payload.get("failed") != 0 or scenario_payload.get("passed", 0) < 7:
                    errors.append("Routing scenario runner did not pass all required scenarios")

    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        errors.append("Core regression tests failed")

    if errors:
        print("Bundle validation FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Bundle validation passed for BlueSky Passage {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
