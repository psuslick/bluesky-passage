# BlueSky Passage 3.0.0 release decisions

These are deliberate v3.0.0 choices. Revisit them explicitly in a future
version rather than treating them as accidental implementation details.

## Routing Engine v3 boundary

The previous production route scorer is retired from coordinator execution. Routing runs through the standalone `routing_engine` package; legacy `routing.py` remains only for compatibility helpers and historical regression tests. No weather or safety failure may fall back to a line labeled as an optimized/ideal route.

## Deterministic search bounds

Routing Engine v3 uses a deterministic destination-centered heading lattice, deterministic numeric tie-breaking, bounded beam width, and a hard search horizon. Once the first valid arrival is found, one additional generation is evaluated to catch a nearby better arrival and then the leg terminates. This rule was added after final release validation exposed an unnecessary post-completion search tail in the shallow-water scenario.

## Environment and current data

The routing engine consumes a provider-neutral environmental field. v3.0.0 still sources live wind, waves, and any available current vectors from Xweather. Direct NOAA S-111/RTOFS ingestion is intentionally deferred until its binary/current-product handling can be integrated and tested without adding an unreliable provider path.

## Safety and depth

NOAA ENC land, coverage, depth-area, and unsurveyed-area polygons are planning constraints, not certified navigation data. When draft is configured, minimum modeled water depth is draft plus the user under-keel margin (default 3 ft). Missing depth at a checked point fails closed rather than being silently assumed safe. A separate final validator must pass before a route is saved.

## Routing gates

Up to 12 ordered routing gates may be attached to a passage through the map editor. They are hard must-pass points, participate in route-context hashing, and invalidate an earlier passage preview if changed. Gates are never silently moved by the router.

## Licensing/source-reference decision

The repository remains MIT for v3.0.0. Bareboat Necessities, OpenCPN Weather Routing, and libweatherrouting were used as behavioral/architectural references, but this release does not vendor or copy their GPL-covered routing source. The v3 engine implementation in this repository is original BlueSky code.

## Routing validity reset

The only real-world v2.4 route test produced a path that visibly crossed the Outer Banks. v2.5 therefore treats the former coarse-mask routing model as unvalidated. Production route generation no longer uses the bundled 1.25-arc-minute raster to certify a route. NOAA ENC Direct to GIS vector land/coverage polygons are the initial hard geography provider, and the route is independently validated before save. If that provider cannot establish coverage, the engine fails closed.

## Routine-alert control

Routine stale/GPS/source/text/zone notifications can be switched on or off from BlueSky **Data & settings** without a restart. The Overview/header always disclose the current routine-alert state. Emergency-state notifications remain independent and forced on; disabling routine alerts must not suppress a Garmin emergency transition.


## Passage coordinate picker

Departure and arrival coordinates may be entered numerically or placed on an interactive map. Map placement is an editing aid only: it writes the chosen latitude/longitude into the same passage fields and does not bypass coverage preview, route land checks, or destination validation. Departure and arrival remain distinct explicit pins; the picker never silently derives or changes coordinates from the route solver.


## Passage editing is a metadata-only operation

The admin **View / edit** action uses a dedicated lightweight metadata query. It intentionally does not load or deserialize saved route versions, calculate route-context freshness, preview archive coverage, call providers, or refresh actual-vs-modeled analytics. Supplemental analysis can fail without making the user's passage metadata inaccessible.


## Coastal endpoint ambiguity

NOAA ENC vector land geometry is authoritative for the v2.5 comparison screen. A saved departure/destination pin that falls just inside a coastal land polygon may be resolved only to a nearby **modeled routing gate** within the existing bounded allowance (2 nmi for departure; destination uses at least 1.5 nmi and the configured arrival-radius concept, capped at 10 nmi). The original user coordinate is never rewritten, and any adjustment is disclosed. Interior route segments receive no endpoint exception.

## Passage-detail resilience

Opening the administrator edit form does not require live actual-vs-modeled analysis. Supplemental analysis is best-effort and may report an analysis-specific failure without blocking passage metadata access.

1. **Question: Is an SSD required?**  
   **Decision:** No. A healthy application-class/high-endurance microSD with
   adequate free space can run the current feature set. SSD/NVMe remains a recommended
   later whole-system resilience upgrade.

2. **Question: Should this require a separate add-on/database/service?**  
   **Decision:** No. Ship one HACS custom integration containing the archive,
   authenticated API, panel, weather adapter, NOAA ENC geography adapter, and
   sailing analysis engine.

3. **Question: Where is permanent data stored?**  
   **Decision:** Preserve `/config/bluesky_passage/archive.sqlite3` outside the
   HACS-managed component directory, with no automatic time purge.

4. **Question: How is storage/write activity bounded?**  
   **Decision:** Poll Garmin every ten minutes with a rolling 48-hour overlap,
   deduplicate before insert, use bounded SQLite WAL/checkpoints, chunk backfill,
   cache normalized provider data, and run weather/routing only on administrator
   request. NOAA ENC geometry is fetched only on route calculation and cached in
   memory for a bounded period; the legacy raster is not a production route-certification source.

5. **Question: How is Garmin history recovered?**  
   **Decision:** Use preview-first, resumable, rollbackable seven-day chunks with
   a ten-year per-job ceiling. Valid historical KML containing no timestamped
   records is a successful empty chunk; malformed/non-KML data still fails
   closed.

6. **Question: What if Garmin cannot reproduce old v1 points?**  
   **Decision:** Keep the guided, non-mutating Home Assistant Recorder fallback,
   bounded to 31 days per preview and stored only after an explicit approved
   preview as a rollbackable `ha_recorder` source batch.

7. **Question: Is passage creation a live tracking switch?**  
   **Decision:** No. Tracking/retention are continuous. Passages are editable
   temporal annotations that may be created before/during/after travel and may
   be open-ended without becoming an operating state.

8. **Question: How are destination changes handled?**  
   **Decision:** Keep effective-time destination versions. Clearing a destination
   is an explicitly previewed removal. Boundary/destination changes make older
   route analysis stale until recalculated.

9. **Question: Are vessel specifications mandatory?**  
   **Decision:** No. Accept a partial profile. Prefer supplied polar rows; then
   observed speed, engine speed, hull-speed estimate, and finally a labeled
   5.5-knot generic baseline. For sailing hulls, default the minimum upwind TWA
   to 40° unless supplied and clamp supplied values to 25–70°.

10. **Question: Which weather provider is used?**  
    **Decision:** Use optional Xweather Weather API credentials on the Home
    Assistant backend only. Track analytics remain capped at 12 representative
    locations. Route analysis uses a separate lattice of at most 11 positions
    around the ENC-valid baseline, with two concurrent position requests.
    Cache normalized values, preserve gaps, reject provider periods more than
    six hours from requested time, and expire transient unavailable results
    after one hour.

11. **Question: What is PredictWind used for?**  
    **Decision:** Keep the configured public tracking page as a lazy-loaded view
    with a direct-link fallback. Do not require PredictWind credentials,
    automate its account, or write routes/history to it.

12. **Question: Can the direct geodesic be a route candidate?**  
    **Decision:** No. It is display/reference geometry only in v2.2 and is never
    scored. If it intersects modeled land, show it as rejected rather than
    allowing its short distance to dominate route score.

13. **Question: How is route geography handled?**  
    **Decision:** In v2.5 production routing, use NOAA ENC Direct to GIS vector
    `Land_Area` and `Coverage_area` polygons across available chart scale bands.
    Dynamically discover layer IDs from service metadata rather than hardcoding
    them. Reject every candidate segment that intersects a loaded land polygon,
    require usable ENC coverage for the complete final path, and independently
    revalidate the selected route before save. Retain the old raster only for
    compatibility/tests. If trusted vector coverage is unavailable, fail closed.

14. **Question: How is sailing feasibility handled?**  
    **Decision:** For sailing hulls, require a usable wind vector and reject
    headings inside the no-go angle. Never replace an impossible upwind heading
    with a merely slower straight-line speed. Candidate evolution must produce
    tacks/heading changes when required.

15. **Question: What does the route engine do?**  
    **Decision:** Build an ENC-valid A* reference and run a bounded, time-dependent
    beam/isochrone-style sailing search. Evaluate destination headings, offsets,
    prior heading, and close-hauled options. Use polar/fallback vessel performance,
    wind, wave/comfort effects, and current-vector COG/SOG. Reject land/no-go
    states before scoring, keep the best complete path and up to two materially
    different alternatives, then pass the winner to a separate final validator.
    A route that fails that validator is not saved as an ideal route.

16. **Question: What happens when route semantics or inputs change?**  
    **Decision:** Fingerprint passage boundaries, departure, destination version,
    vessel-profile revision, and routing-engine version. The v2.5 `enc-isochrone-v4` fingerprint intentionally makes every pre-v2.5 route analysis stale so coarse-mask validity cannot be mixed with the new ENC/vector route semantics.

17. **Question: Is the generated path navigable?**  
    **Decision:** No. NOAA ENC vector land/coverage checks and sailing no-go
    rejection are minimum coherence standards, not navigation certification.
    ENC Direct to GIS is not a certified navigation product, and the engine does
    not yet establish safe depth, reef/rock or bridge clearance, traffic,
    restrictions, COLREGS, warnings, or local notices.

18. **Question: How should route layers appear?**  
    **Decision:** Keep actual Garmin track, selected sailing path, alternatives,
    shortest-water reference, and direct reference visually distinct. A direct
    line that crosses modeled land remains visible only as a rejected reference.

19. **Question: How should map interaction work?**  
    **Decision:** Support mouse/pen/touch pointer dragging with per-map persistent
    view state, suppress accidental point selection after drag, provide reliable
    +/− and fit controls, pointer-centered wheel/trackpad and double-click zoom,
    two-finger pinch zoom, and arrow-button panning as an accessible alternative.

20. **Question: What is the dashboard information architecture?**  
    **Decision:** Preserve Home Assistant's left sidebar for global navigation.
    Use four top tabs inside BlueSky Passage: Overview, History & charts,
    Passages, and Data & settings.

21. **Question: What is the default analytical view?**  
    **Decision:** Last 24 hours, combined source track with Garmin preferred,
    observed vessel SOG, modeled Xweather wind/waves, and an explicit two-report
    map-range interaction that updates the same graph interval.

22. **Question: How should all-time history behave?**  
    **Decision:** Query the dedicated archive, not a fixed seven-day window.
    Bound browser payloads with shape-preserving downsampling while retaining
    endpoints, gaps, alerts, and local speed extrema.

23. **Question: Who can perform sensitive/data-changing actions?**  
    **Decision:** All authenticated Home Assistant users may view. Only
    administrators may poll manually, contact Xweather, create/edit/delete
    passages, backfill, import, rollback, export, calculate routes, change
    profiles, or test notifications.

24. **Question: What are the default units?**  
    **Decision:** Knots for speed and meters for height, configurable in
    integration options without changing normalized archive values.

25. **Question: Do alerts depend on a passage, and can they be disabled?**  
    **Decision:** Alerts do not depend on a passage. Routine stale tracking,
    invalid GPS, new text, source failure/recovery, and optional HA-zone changes
    operate from the continuous latest Garmin record and can be disabled from
    Data & settings. Disabling routine alerts preserves the stale threshold and
    dismisses routine persistent notifications. Emergency-state transitions stay
    enabled independently and are never suppressed by the routine-alert switch.

26. **Question: What backup behavior is required?**  
    **Decision:** Require a full Home Assistant backup before upgrade and verify
    archive count, earliest/latest timestamps, integrity, source availability,
    and provider status afterward. Do not copy only the live SQLite main file
    because WAL companion data may be needed for a consistent snapshot.


27. **Question: How is actual track deviation matched to a modeled route?**  
    **Decision:** Project each Garmin report to the closest eligible point on the
    saved modeled polyline while enforcing nondecreasing modeled-route progress.
    This prevents a looping or nearby route segment from making the comparison
    jump backward. Report signed deviation as port-negative and starboard-positive.

28. **Question: How is route-deviation scale chosen?**  
    **Decision:** Default to the smallest symmetric automatic ±nmi scale with
    headroom. Allow fixed ±1/2/5/10/20/50/100 nmi and symmetric Custom overrides.
    Persist the display preference per passage in the browser. Never silently
    clip numeric values; flag visual overflow explicitly.

29. **Question: How should History analytics distinguish observations from models?**  
    **Decision:** Use three stacked plots on one time axis. Garmin SOG is the
    observed series. Wind/wave are sparse modeled samples joined with dashed
    interpolation and visible sample markers. Gust is an optional envelope rather
    than a competing equally weighted line. Missing values remain gaps.

30. **Question: Should actual-vs-modeled metrics require rerunning Xweather?**  
    **Decision:** No. Preserve the saved modeled route and refresh the Garmin-side
    comparison whenever passage detail is requested. Recalculate the weather route
    only when the route inputs/context change or the user explicitly requests it.
31. **Question: What geography coverage is claimed in the first rebuilt router?**  
    **Decision:** High-confidence production route generation is initially limited
    to corridors where NOAA ENC coverage can be established. Do not silently use
    the legacy global raster to claim worldwide route validity. A future global
    high-resolution provider must implement the same constraint/final-validation
    contract before global routing is advertised.

32. **Question: Should the search certify its own result?**  
    **Decision:** No. The search and final validator are separate stages. The final
    validator samples the complete selected path inside ENC coverage and rechecks
    every segment against vector land geometry at denser spacing. Failure blocks
    saving the route.

