# BlueSky Passage 2.2.0 release decisions

These are deliberate v2.2.0 choices. Revisit them explicitly in a future
version rather than treating them as accidental implementation details.

1. **Question: Is an SSD required?**  
   **Decision:** No. A healthy application-class/high-endurance microSD with
   adequate free space can run every v2.2 feature. SSD/NVMe remains a recommended
   later whole-system resilience upgrade.

2. **Question: Should this require a separate add-on/database/service?**  
   **Decision:** No. Ship one HACS custom integration containing the archive,
   authenticated API, panel, weather adapter, land-mask reader, and sailing
   analysis engine.

3. **Question: Where is permanent data stored?**  
   **Decision:** Preserve `/config/bluesky_passage/archive.sqlite3` outside the
   HACS-managed component directory, with no automatic time purge.

4. **Question: How is storage/write activity bounded?**  
   **Decision:** Poll Garmin every ten minutes with a rolling 48-hour overlap,
   deduplicate before insert, use bounded SQLite WAL/checkpoints, chunk backfill,
   cache normalized provider data, and run weather/routing only on administrator
   request. The bundled land mask is static and decompressed lazily in memory.

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
    around the water-valid baseline, with two concurrent position requests.
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

13. **Question: How is land handled?**  
    **Decision:** Bundle a 1.25-arc-minute dry-land mask derived from
    `basemap-data`/GSHHG data. Departure/destination must resolve to water for the
    comparison. Build a shortest-water A* reference when direct crosses land,
    and reject every scored segment that intersects the mask. Explicitly state
    that the mask is not a nautical chart and contains no depth/hazard/channel
    knowledge.

14. **Question: How is sailing feasibility handled?**  
    **Decision:** For sailing hulls, require a usable wind vector and reject
    headings inside the no-go angle. Never replace an impossible upwind heading
    with a merely slower straight-line speed. Candidate evolution must produce
    tacks/heading changes when required.

15. **Question: What does the v2.2 route engine do?**  
    **Decision:** Start from the water-valid geometric corridor and run a bounded,
    time-dependent beam/isochrone-style heading search. Evaluate destination
    headings, offsets, prior heading, and close-hauled options. Use polar/fallback
    vessel performance, wind, wave/comfort effects, and current-vector COG/SOG.
    Reject invalid states before scoring, keep the best complete path and up to
    two materially different alternatives, and save only a water-valid reference
    if the sailing search cannot close.

16. **Question: What happens when route semantics or inputs change?**  
    **Decision:** Fingerprint passage boundaries, departure, destination version,
    vessel-profile revision, and routing-engine version. The v2.2 engine version
    intentionally makes every v2.1 three-corridor result stale and suppresses
    its overlay until recalculated.

17. **Question: Is the generated path navigable?**  
    **Decision:** No. Hard land/no-go validity is a minimum coherence standard,
    not navigation certification. The engine does not know depths, reefs,
    bridges, traffic, restrictions, COLREGS, warnings, or local notices and the
    weather field is intentionally sparse/bounded.

18. **Question: How should route layers appear?**  
    **Decision:** Keep actual Garmin track, selected sailing path, alternatives,
    shortest-water reference, and direct reference visually distinct. A direct
    line that crosses modeled land remains visible only as a rejected reference.

19. **Question: How should map interaction work?**  
    **Decision:** Support mouse/pen/touch pointer dragging with per-map persistent
    view state, suppress accidental point selection after drag, keep zoom/fit,
    and provide arrow-button panning as an accessible non-drag alternative.

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

25. **Question: Do alerts depend on a passage?**  
    **Decision:** No. Emergency, stale tracking, invalid GPS, new text, source
    failure/recovery, and optional HA-zone changes operate from the continuous
    latest Garmin record.

26. **Question: What backup behavior is required?**  
    **Decision:** Require a full Home Assistant backup before upgrade and verify
    archive count, earliest/latest timestamps, integrity, source availability,
    and provider status afterward. Do not copy only the live SQLite main file
    because WAL companion data may be needed for a consistent snapshot.
