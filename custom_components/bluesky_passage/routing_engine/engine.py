"""Deterministic, time-dependent sailing router for BlueSky Passage.

The engine is deliberately independent from Home Assistant.  It consumes a
vessel profile, an environmental field exposing ``at(lat, lon, time)``, and a
safety constraint exposing point/segment validation methods.  Geography and
weather retrieval live outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import math
from typing import Any, Iterable

from ..calculations import haversine_nm, initial_bearing_true, parse_utc
from ..routing import (
    VesselProfile,
    destination_point,
    path_distance,
    performance_on_heading,
    _angle_difference,
    _finite,
)

ENGINE_VERSION = "weather-routing-v3-isochrone-enc-depth"


class RoutingEngineError(ValueError):
    """A route could not be completed under the supplied hard constraints."""


@dataclass(frozen=True, slots=True)
class _State:
    latitude: float
    longitude: float
    at_utc: datetime
    elapsed_hours: float
    distance_nm: float
    risk_total: float
    path: tuple[tuple[float, float], ...]
    elapsed_path: tuple[float, ...]
    last_heading: float | None
    maneuvers: int
    weather_steps: int
    score: float


def _segment_safe(constraint: Any, start: tuple[float, float], end: tuple[float, float]) -> bool:
    fn = getattr(constraint, "segment_is_safe", None)
    if callable(fn):
        return bool(fn(start, end))
    return bool(constraint.segment_is_water(start, end))


def _point_safe(constraint: Any, point: tuple[float, float]) -> bool:
    fn = getattr(constraint, "point_is_safe", None)
    if callable(fn):
        return bool(fn(*point))
    return not bool(constraint.is_land(*point))


def _state_score(state: _State, destination: tuple[float, float], profile: VesselProfile) -> float:
    remaining = haversine_nm(state.latitude, state.longitude, *destination)
    # ETA dominates; risk and excessive maneuvering break otherwise similar ties.
    return state.elapsed_hours + remaining / max(profile.base_speed_kn, 1.0) + state.risk_total * 0.40 + state.maneuvers * 0.05


def _candidate_headings(
    state: _State,
    destination: tuple[float, float],
    weather: dict[str, Any] | None,
    profile: VesselProfile,
    *,
    heading_step_deg: int,
) -> list[float]:
    desired = initial_bearing_true(state.latitude, state.longitude, *destination)
    # A deterministic destination-centered lattice.  It is deliberately broad
    # enough for tacks and obstacle detours without evaluating all 360 degrees
    # at every state.
    step = max(5, min(30, int(heading_step_deg)))
    offsets = list(range(-120, 121, step))
    values = [desired + offset for offset in offsets]
    if state.last_heading is not None:
        values += [state.last_heading, state.last_heading - step, state.last_heading + step]
    wind_dir = _finite((weather or {}).get("wind_dir_deg"))
    if wind_dir is not None and not profile.is_motor_only:
        close = profile.no_go_angle_deg + 2.0
        values += [wind_dir - close, wind_dir + close]
    unique: dict[int, float] = {}
    for value in values:
        normalized = value % 360.0
        key = int(round(normalized * 10)) % 3600
        unique.setdefault(key, normalized)
    return sorted(unique.values(), key=lambda h: (_angle_difference(h, desired), h))


def _final_heading(
    state: _State,
    destination: tuple[float, float],
    profile: VesselProfile,
    weather: dict[str, Any] | None,
) -> dict[str, float] | None:
    desired_cog = initial_bearing_true(state.latitude, state.longitude, *destination)
    best: tuple[float, float, float, dict[str, float]] | None = None
    for offset in range(-90, 91, 2):
        heading = (desired_cog + offset) % 360.0
        perf = performance_on_heading(profile, heading, weather)
        if perf is None:
            continue
        error = _angle_difference(perf["cog_deg"], desired_cog)
        if error > 3.0:
            continue
        rank = (error, -perf["sog_kn"], heading, perf)
        if best is None or rank[:3] < best[:3]:
            best = rank
    return best[3] if best else None


def _route_one_leg(
    start: tuple[float, float],
    destination: tuple[float, float],
    departure: datetime,
    profile: VesselProfile,
    environment: Any,
    constraint: Any,
    *,
    starting_elapsed_hours: float = 0.0,
    starting_path: tuple[tuple[float, float], ...] | None = None,
    starting_elapsed_path: tuple[float, ...] | None = None,
    starting_heading: float | None = None,
    heading_step_deg: int = 10,
    beam_width: int = 64,
) -> tuple[_State, dict[str, int]]:
    if not _point_safe(constraint, start):
        raise RoutingEngineError("Routing start is not inside the configured safe-water constraint")
    if not _point_safe(constraint, destination):
        raise RoutingEngineError("Routing destination is not inside the configured safe-water constraint")

    direct_nm = haversine_nm(*start, *destination)
    nominal_h = direct_nm / max(profile.base_speed_kn, 1.0)
    # Short coastal legs need finer steps; long offshore legs can expand farther.
    step_h = 0.75 if direct_nm < 25 else 1.25 if direct_nm < 80 else 2.0 if direct_nm < 220 else 3.0
    max_iterations = min(260, max(24, int(math.ceil(max(8.0, nominal_h) / step_h * 3.2)) + 12))
    max_elapsed = starting_elapsed_hours + max(24.0, nominal_h * 4.5 + 18.0)
    arrival_nm = max(0.20, min(1.0, profile.base_speed_kn * step_h * 0.15))
    bin_nm = max(1.0, profile.base_speed_kn * step_h * 0.40)

    path = starting_path or (start,)
    elapsed_path = starting_elapsed_path or (starting_elapsed_hours,)
    initial = _State(
        start[0], start[1], departure + timedelta(hours=starting_elapsed_hours),
        starting_elapsed_hours, 0.0, 0.0, path, elapsed_path,
        starting_heading, 0, 0, 0.0,
    )
    initial = replace(initial, score=_state_score(initial, destination, profile))
    frontier = [initial]
    completed: list[_State] = []
    first_completion_iteration: int | None = None
    diagnostics = {"iterations": 0, "expanded": 0, "pruned": 0, "safety_rejects": 0, "no_go_rejects": 0, "weather_rejects": 0}

    for iteration in range(max_iterations):
        diagnostics["iterations"] = iteration + 1
        expanded: list[_State] = []
        for state in frontier:
            if state.elapsed_hours >= max_elapsed:
                continue
            remaining = haversine_nm(state.latitude, state.longitude, *destination)
            weather = environment.at(state.latitude, state.longitude, state.at_utc)
            if not profile.is_motor_only and (
                not weather or _finite(weather.get("wind_speed_kn")) is None or _finite(weather.get("wind_dir_deg")) is None
            ):
                diagnostics["weather_rejects"] += 1
                continue

            if remaining <= max(arrival_nm, profile.base_speed_kn * step_h * 1.30):
                perf = _final_heading(state, destination, profile, weather)
                if perf is not None and _segment_safe(constraint, (state.latitude, state.longitude), destination):
                    close_h = remaining / max(perf["sog_kn"], 0.2)
                    if close_h <= step_h * 1.6:
                        maneuver = int(state.last_heading is not None and _angle_difference(state.last_heading, perf["heading_deg"]) >= 60)
                        elapsed = state.elapsed_hours + close_h + maneuver * 0.03
                        done = _State(
                            destination[0], destination[1], departure + timedelta(hours=elapsed), elapsed,
                            state.distance_nm + remaining, state.risk_total + perf["risk"] * close_h,
                            state.path + (destination,), state.elapsed_path + (elapsed,), perf["heading_deg"],
                            state.maneuvers + maneuver, state.weather_steps + 1, 0.0,
                        )
                        done = replace(done, score=_state_score(done, destination, profile))
                        completed.append(done)
                        if first_completion_iteration is None:
                            first_completion_iteration = iteration
                        continue

            for heading in _candidate_headings(state, destination, weather, profile, heading_step_deg=heading_step_deg):
                perf = performance_on_heading(profile, heading, weather)
                if perf is None:
                    diagnostics["no_go_rejects"] += 1
                    continue
                dt = step_h
                max_move = perf["sog_kn"] * dt
                if remaining < max_move * 1.2:
                    dt = max(0.20, min(step_h, remaining / max(perf["sog_kn"], 0.3)))
                move_nm = perf["sog_kn"] * dt
                next_point = destination_point(state.latitude, state.longitude, perf["cog_deg"], move_nm)
                diagnostics["expanded"] += 1
                if not _point_safe(constraint, next_point) or not _segment_safe(constraint, (state.latitude, state.longitude), next_point):
                    diagnostics["safety_rejects"] += 1
                    continue
                next_remaining = haversine_nm(*next_point, *destination)
                # Permit tacks and obstacle detours, but bound backwards motion.
                if next_remaining > remaining + max(10.0, move_nm * 1.05):
                    diagnostics["pruned"] += 1
                    continue
                maneuver = int(state.last_heading is not None and _angle_difference(state.last_heading, heading) >= 60)
                elapsed = state.elapsed_hours + dt + maneuver * 0.03
                if elapsed > max_elapsed:
                    diagnostics["pruned"] += 1
                    continue
                candidate = _State(
                    next_point[0], next_point[1], departure + timedelta(hours=elapsed), elapsed,
                    state.distance_nm + move_nm, state.risk_total + perf["risk"] * dt,
                    state.path + (next_point,), state.elapsed_path + (elapsed,), heading,
                    state.maneuvers + maneuver, state.weather_steps + 1, 0.0,
                )
                candidate = replace(candidate, score=_state_score(candidate, destination, profile))
                expanded.append(candidate)

        if first_completion_iteration is not None and iteration >= first_completion_iteration + 1:
            # One additional generation is enough to catch a nearby better arrival
            # without an unbounded tail after first completion.
            break
        if not expanded:
            break

        # Deterministic dominance/pruning: include heading sector in the state bin
        # so opposite tacks are not collapsed into one spatial cell.  Tie-breaking
        # uses only numeric state attributes, never object/hash order.
        best: dict[tuple[int, int, int], _State] = {}
        mean_lat = math.radians(start[0])
        for state in expanded:
            x = (state.longitude - start[1]) * 60.0 * max(0.2, math.cos(mean_lat))
            y = (state.latitude - start[0]) * 60.0
            heading_sector = int(((state.last_heading or 0.0) % 360.0) // 30.0)
            key = (int(round(x / bin_nm)), int(round(y / bin_nm)), heading_sector)
            prior = best.get(key)
            rank = (state.score, state.elapsed_hours, state.risk_total, state.distance_nm, state.latitude, state.longitude, state.last_heading or -1.0)
            if prior is None:
                best[key] = state
            else:
                prior_rank = (prior.score, prior.elapsed_hours, prior.risk_total, prior.distance_nm, prior.latitude, prior.longitude, prior.last_heading or -1.0)
                if rank < prior_rank:
                    best[key] = state
                diagnostics["pruned"] += 1
        frontier = sorted(
            best.values(),
            key=lambda s: (s.score, s.elapsed_hours, s.risk_total, s.distance_nm, s.latitude, s.longitude, s.last_heading or -1.0),
        )[:beam_width]

    if not completed:
        raise RoutingEngineError(
            "No sailing route could be completed with the available weather and hard safety constraints"
        )
    completed.sort(key=lambda s: (s.score, s.elapsed_hours, s.distance_nm, s.maneuvers, s.latitude, s.longitude))
    return completed[0], diagnostics


def _candidate(state: _State, departure: datetime, key: str, label: str) -> dict[str, Any]:
    coordinates = [[round(lon, 6), round(lat, 6)] for lat, lon in state.path]
    return {
        "key": key,
        "label": label,
        "coordinates": coordinates,
        "waypoints": [
            {"longitude": round(point[1], 6), "latitude": round(point[0], 6), "elapsed_hours": round(elapsed, 3)}
            for point, elapsed in zip(state.path, state.elapsed_path)
        ],
        "distance_nm": round(path_distance(coordinates), 2),
        "estimated_hours": round(state.elapsed_hours, 2),
        "eta_utc": (departure + timedelta(hours=state.elapsed_hours)).isoformat().replace("+00:00", "Z"),
        "risk_score": round(state.risk_total / max(state.elapsed_hours, 1.0), 3),
        "weather_coverage_steps": state.weather_steps,
        "maneuvers": state.maneuvers,
        "no_go_violations": 0,
        "score": round(state.score, 3),
    }


def route_passage(
    start: tuple[float, float],
    destination: tuple[float, float],
    departure_at_utc: str | datetime,
    profile: VesselProfile,
    environment: Any,
    constraint: Any,
    *,
    gates: Iterable[tuple[float, float]] = (),
    heading_step_deg: int = 10,
    beam_width: int = 64,
) -> dict[str, Any]:
    """Route sequentially through optional ordered gates and return diagnostics."""
    departure = parse_utc(departure_at_utc)
    targets = [*list(gates), destination]
    if not targets:
        raise RoutingEngineError("A destination is required")

    current = start
    state: _State | None = None
    aggregate = {"iterations": 0, "expanded": 0, "pruned": 0, "safety_rejects": 0, "no_go_rejects": 0, "weather_rejects": 0}
    for target in targets:
        state, diag = _route_one_leg(
            current, target, departure, profile, environment, constraint,
            starting_elapsed_hours=state.elapsed_hours if state else 0.0,
            starting_path=state.path if state else None,
            starting_elapsed_path=state.elapsed_path if state else None,
            starting_heading=state.last_heading if state else None,
            heading_step_deg=heading_step_deg,
            beam_width=beam_width,
        )
        for key, value in diag.items():
            aggregate[key] += value
        current = target

    assert state is not None
    selected = _candidate(state, departure, "optimized_1", "Optimized sailing path")
    return {
        "engine_version": ENGINE_VERSION,
        "method": "weather_routing_v3",
        "selected": selected,
        "candidates": [selected],
        "weather_used": True,
        "profile": {**profile.completeness, "base_speed_kn": round(profile.base_speed_kn, 2)},
        "routing_gates": [[round(lon, 6), round(lat, 6)] for lat, lon in gates],
        "diagnostics": aggregate,
        "disclaimer": "Planning/analysis route only. Hard model constraints are not a substitute for certified charts, notices, COLREGS, or skipper judgment.",
    }
