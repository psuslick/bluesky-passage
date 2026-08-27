#!/usr/bin/env python3
"""Run deterministic, Home-Assistant-independent Routing Engine v3 scenarios.

This runner exercises the same pure routing engine that Home Assistant calls,
using fixed environmental and safety fixtures so failures are reproducible.
It prints a JSON result and exits non-zero if any scenario fails.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TESTS = ROOT / "tests" if (ROOT / "tests").is_dir() else HERE / "tests"
if not TESTS.is_dir():
    raise SystemExit("Could not locate BlueSky Passage standalone test loader")
sys.path.insert(0, str(TESTS))

from _loader import coastline, routing, routing_engine  # noqa: E402


class ConstantEnvironment:
    """Simple deterministic environmental field for acceptance scenarios."""

    def __init__(
        self,
        *,
        wind_dir: float = 0.0,
        wind_speed: float = 14.0,
        wave_height: float = 0.5,
        current_speed: float = 0.0,
        current_dir: float = 0.0,
        missing: bool = False,
    ) -> None:
        self.missing = missing
        self.value = {
            "wind_speed_kn": wind_speed,
            "wind_dir_deg": wind_dir,
            "wave_height_m": wave_height,
            "wave_period_s": 8.0,
            "current_speed_kn": current_speed,
            "current_dir_deg": current_dir,
        }

    def at(self, latitude: float, longitude: float, valid_at_utc: Any) -> dict[str, float] | None:
        del latitude, longitude, valid_at_utc
        return None if self.missing else dict(self.value)


def rectangle(
    west: float,
    south: float,
    east: float,
    north: float,
    source: str = "scenario",
    properties: dict[str, Any] | None = None,
):
    ring = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    return coastline.Polygon(ring, [], (west, south, east, north), source, properties or {})


def constraint(*, land=None, minimum_depth_m=None, shallow=None):
    coverage = [rectangle(-2, -2, 3, 2, "coverage")]
    depth = []
    if minimum_depth_m is not None:
        depth.append(rectangle(-2, -2, 3, 2, "deep", {"DRVAL1": 30.0}))
        if shallow:
            depth.append(rectangle(*shallow, "shallow", {"DRVAL1": 1.0}))
    return coastline.EncConstraint(
        land=land or [],
        coverage=coverage,
        bands=("scenario",),
        query_bbox=(-2, -2, 3, 2),
        fetched_at_utc="2026-08-26T00:00:00Z",
        depth=depth,
        unsurveyed=[],
        minimum_depth_m=minimum_depth_m,
    )


def profile():
    return routing.VesselProfile.from_mapping(
        {
            "hull_configuration": "monohull sailboat",
            "observed_cruise_speed_kn": 6.0,
            "minimum_upwind_twa_deg": 40.0,
        }
    )


def _run_route(*, start=(0.0, 0.0), destination=(0.0, 0.5), env=None, safety=None, gates=(), beam=64):
    return routing_engine.route_passage(
        start,
        destination,
        "2026-08-26T00:00:00Z",
        profile(),
        env or ConstantEnvironment(),
        safety or constraint(),
        gates=gates,
        heading_step_deg=10,
        beam_width=beam,
    )


def _scenario_open_water() -> dict[str, Any]:
    result = _run_route(env=ConstantEnvironment(wind_dir=0))
    assert result["selected"]["distance_nm"] < 32.5
    assert result["selected"]["no_go_violations"] == 0
    return result


def _scenario_upwind_tack() -> dict[str, Any]:
    result = _run_route(destination=(0.45, 0.0), env=ConstantEnvironment(wind_dir=0), beam=72)
    assert result["selected"]["distance_nm"] > 27.0
    assert result["selected"]["maneuvers"] >= 1
    return result


def _scenario_barrier() -> dict[str, Any]:
    barrier = rectangle(0.23, -0.16, 0.27, 0.16, "thin-barrier")
    safe = constraint(land=[barrier])
    result = _run_route(env=ConstantEnvironment(wind_dir=0), safety=safe, beam=72)
    assert safe.path_is_safe(result["selected"]["coordinates"])
    assert result["selected"]["distance_nm"] > 30.5
    return result


def _scenario_shallow() -> dict[str, Any]:
    safe = constraint(minimum_depth_m=3.0, shallow=(0.20, -0.14, 0.30, 0.14))
    result = _run_route(env=ConstantEnvironment(wind_dir=0), safety=safe, beam=72)
    assert safe.path_is_safe(result["selected"]["coordinates"])
    assert result["diagnostics"]["expanded"] < 100000
    return result


def _scenario_cross_current() -> dict[str, Any]:
    # Eastbound target with a 3 kn north-setting current should require a
    # materially southerly heading at one or more steps while the COG remains
    # generally eastbound.
    result = _run_route(env=ConstantEnvironment(wind_dir=0, current_speed=3.0, current_dir=0.0), beam=72)
    coords = result["selected"]["coordinates"]
    assert result["selected"]["distance_nm"] > 0
    assert len(coords) >= 2
    # Current compensation should be reflected by path geometry rather than a
    # pathological northward drift away from the destination.
    max_lat = max(abs(point[1]) for point in coords)
    assert max_lat < 0.12
    return result


def _scenario_gate() -> dict[str, Any]:
    gate = (0.18, 0.24)
    result = _run_route(env=ConstantEnvironment(wind_dir=0), gates=[gate], beam=72)
    assert [round(gate[1], 6), round(gate[0], 6)] in result["selected"]["coordinates"]
    return result


def _scenario_missing_weather() -> dict[str, Any]:
    try:
        _run_route(env=ConstantEnvironment(missing=True))
    except routing_engine.RoutingEngineError as err:
        message = str(err)
        assert "No sailing route" in message
        return {"expected_failure": True, "message": message}
    raise AssertionError("Missing weather unexpectedly produced a sailing route")


SCENARIOS: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("open_water_crosswind", _scenario_open_water),
    ("direct_upwind_tacking", _scenario_upwind_tack),
    ("thin_barrier_island", _scenario_barrier),
    ("shallow_shortcut", _scenario_shallow),
    ("strong_cross_current", _scenario_cross_current),
    ("ordered_routing_gate", _scenario_gate),
    ("missing_weather_fails_closed", _scenario_missing_weather),
]


def main() -> int:
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    failed = False
    for name, fn in SCENARIOS:
        scenario_started = time.monotonic()
        try:
            output = fn()
            selected = output.get("selected") if isinstance(output, dict) else None
            results.append(
                {
                    "name": name,
                    "status": "pass",
                    "elapsed_s": round(time.monotonic() - scenario_started, 3),
                    "distance_nm": selected.get("distance_nm") if isinstance(selected, dict) else None,
                    "estimated_hours": selected.get("estimated_hours") if isinstance(selected, dict) else None,
                    "expanded": (output.get("diagnostics") or {}).get("expanded") if isinstance(output, dict) else None,
                    "expected_failure": bool(output.get("expected_failure")) if isinstance(output, dict) else False,
                }
            )
        except Exception as err:  # scenario runner must report all failures
            failed = True
            results.append(
                {
                    "name": name,
                    "status": "fail",
                    "elapsed_s": round(time.monotonic() - scenario_started, 3),
                    "error": f"{type(err).__name__}: {err}",
                }
            )
    payload = {
        "engine_version": routing_engine.ENGINE_VERSION,
        "scenario_count": len(results),
        "passed": sum(item["status"] == "pass" for item in results),
        "failed": sum(item["status"] == "fail" for item in results),
        "elapsed_s": round(time.monotonic() - started, 3),
        "scenarios": results,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
