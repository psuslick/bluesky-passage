# Changelog

## 2.3.1 — 2026-08-23

### Fixed

- Fixed **View / edit** so administrator passage editing no longer depends on supplemental actual-vs-modeled analysis. Live route analysis is now best-effort and cannot block access to passage metadata; unexpected detail failures are logged with an explicit Home Assistant error instead of surfacing only as “Unknown error.”
- Added coastal endpoint ambiguity handling for the bundled 1.25-arc-minute land mask. A departure or destination that falls in a coarse shoreline cell can be shifted a small, bounded distance to the nearest modeled-water cell for route computation while the saved Garmin position/destination remains unchanged.
- Destination endpoint resolution respects the configured arrival-radius concept and remains bounded; genuinely inland departure/destination coordinates still fail closed rather than being silently routed to a distant coast.
- Route summaries now disclose any endpoint adjustment and its distance. Interior route segments still use the same hard land-intersection test; this does not weaken land rejection for the route itself.
- Bumped the route context engine to `isochrone-water-v3` so any v2.3.0 route is marked stale and recalculated with the corrected coastal-endpoint semantics.
- Added regression tests for a coarse coastal land cell resolving to nearby modeled water and for a genuinely inland coordinate refusing the same 2 nmi adjustment.

## 2.3.0 — 2026-08-23

- Redesigns **Passage analytics** as three vertically stacked plots on one shared time axis: observed SOG, modeled wind/gust envelope, and modeled wave height. Modeled samples are explicitly dotted/dashed rather than presented with the same visual weight as Garmin observations.
- Adds normal map zoom behavior: reliable +/− controls, mouse-wheel/trackpad zoom centered on the pointer, double-click zoom, and two-finger pinch zoom while preserving drag-to-pan. Map controls no longer start a drag gesture.
- Adds a live **Actual vs modeled** route-deviation analysis for current saved routes. Garmin reports are projected monotonically onto the modeled sailing route so the matched route position never moves backward.
- Adds current deviation, maximum deviation, extra recorded distance, distance efficiency, modeled progress, modeled distance-to-progress, and modeled-vs-actual time delta. Port is negative/left and starboard is positive/right.
- Adds the requested horizontal port/on-route/starboard deviation indicator, a progress-based deviation history chart, and optional representative deviation connectors on the passage map.
- Adds deviation-scale controls with **Auto**, fixed ±1/2/5/10/20/50/100 nmi choices, and a symmetric **Custom** override. Manual scale choices persist per passage in the browser; values beyond a manual scale are explicitly marked as visually clipped rather than silently hidden.
- Stores modeled elapsed time at optimized-route waypoints so time-to-equivalent-progress comparisons use the route search timeline instead of simple whole-route proportional timing.
- Refreshes actual-vs-modeled metrics when passage detail is requested, without recalculating the weather route.
- Bumps the route context engine to `isochrone-water-v2`; saved pre-2.3 route analyses are intentionally marked stale and must be recalculated once to gain waypoint timing/deviation semantics.
- Raises passage-map point requests to the same 10,000-point frontend display limit used by History & charts.
- Adds route-deviation regression coverage for port/starboard sign, monotonic route progress, and modeled waypoint-time interpolation.

## 2.2.2 — 2026-08-23

### Fixed

- Fixed the frontend numeric-validity helper so `null`, `undefined`, blank strings, and booleans are not coerced to numeric zero. Missing SOG/wind/wave values now remain true chart gaps instead of creating false zero readings.
- Made the analytics x-axis honor the exact selected query range even when the first/last records have missing numeric values.
- Raised the History & charts point request from 4,000 to the backend-supported 10,000 display points and retained explicit decimation reporting beyond that bound.
- Added chart coverage counts for returned reports, valid SOG values, cached track-weather samples, wind samples, wave samples, and optional gust samples.
- Added an explicit explanation when no track-weather samples are cached; route-candidate weather is intentionally not mixed into the observed-track graph.
- Carries forward the v2.2.1 inverse-Web-Mercator map-pan correction.

## 2.2.1 — 2026-08-23

### Fixed

- Corrected the inverse Web Mercator longitude calculation used by map panning.
  Dragging or using the pan buttons no longer shifts the map approximately 180°
  in longitude (for example, from coastal North Carolina to East Asia).
- Added a bundle regression guard for the project/unproject longitude round trip so
  this map-navigation failure cannot silently return in a future release.

## 2.2.0 — 2026-08-23

- Replaced the v2.1 direct/port/starboard corridor scorer with a bounded, time-dependent sailing heading search; the direct geodesic is now reference-only and can never win by being physically impossible but shorter.
- Added a bundled 1.25-arc-minute dry-land mask and adaptive shortest-water A* baseline. Departure/destination and every scored ground-track segment must remain off modeled land.
- Added hard sailing no-go constraints with a configurable/default minimum upwind TWA; an impossible upwind heading is rejected instead of assigned a slower straight-line speed.
- Added smoother polar interpolation, conservative fallback sailing performance, wave/comfort penalties, current-vector COG/SOG, close-hauled candidate generation, major-maneuver accounting, and up to two materially different alternatives.
- Made sailing-weather optimization require a usable wind vector; partial marine-only Xweather data now falls back to an explicitly labeled water-valid reference instead of implying a sailing solution.
- Bounded route weather sampling to an 11-position spatiotemporal lattice around the water-valid corridor and retained backend-only credentials, two-position concurrency, normalized cache reuse, and explicit provider gaps.
- Added routing-engine versioning to route context fingerprints so all pre-v2.2 three-corridor results become stale and their overlays remain suppressed until recalculated.
- Reworked route-map layers: actual Garmin track, selected sailing path, alternate candidates, shortest-water reference, and direct geodesic are visually distinct; a direct reference crossing modeled land is shown as rejected.
- Added mouse/pen/touch drag-to-pan maps, per-map persistent view state, post-drag point-click suppression, and accessible arrow-button panning while retaining zoom and fit controls.
- Removed route-card ellipsis truncation so method and ETA text can wrap.
- Fixed Garmin historical backfill so valid empty date-bounded KML is a successful zero-record chunk instead of aborting the job as an unusable feed; malformed/non-KML content still fails closed.
- Preserved the v2.1.1 backfill date persistence/range audit and integration Options Flow routing fixes.
- Added third-party notices/licenses for the derived GSHHG/basemap-data land mask and expanded the standard-library regression suite to 37 tests, including Hampton-to-Beaufort land rejection, synthetic upwind tacking, no-go rejection, and missing-wind fail-closed behavior.

## 2.1.1 — 2026-08-22

- Fixed Garmin historical-backfill date inputs so administrator selections persist across Home Assistant data-event and panel redraws instead of reverting to the one-year defaults.
- Added the exact requested start/end range and chunk count to the backfill job card so the submitted range is auditable before import.
- Disabled starting another preview while the current backfill job is pending/running and added explicit reversed-date validation.
- Removed `config_panel_domain` from the custom sidebar registration so Home Assistant's integration cog opens the real Options Flow instead of looping back to BlueSky Passage.
- Added bundle validation guards for both regressions.

## 2.1.0 — 2026-08-22

- Rebuilt the panel around four proper top tabs: Overview, History & charts,
  Passages, and Data & settings; Home Assistant's global sidebar is untouched.
- Reduced Overview to four high-value current metrics, a latest-24-hour map,
  selected-record detail, conditional alerts, and the latest exact text event.
- Changed History & charts to a 24-hour default and added explicit two-report
  map range selection linked to one observed/model analytics graph.
- Added an on-demand PredictWind frame/link and backend-only Xweather Conditions,
  Maritime, and Maritime Archive adapters with partial-data/null handling.
- Added a normalized, non-purging weather cache that distinguishes track
  analytics samples from route-candidate samples, expires transient failures,
  and rejects model periods more than six hours from the requested timestamp.
- Added a partial vessel performance profile and an internal comparison-path
  engine evaluating direct, port-offset, and starboard-offset corridors.
- Explicitly labels weather-informed comparisons versus great-circle fallback;
  neither is described as a navigable or safe route.
- Replaced active/arrived/start/end passage state with editable, backdatable,
  open-ended or fixed temporal annotations over the continuous global archive.
- Added exact archive-coverage preview tokens, overlap/gap disclosure, immutable
  raw-report behavior, destination versions, and recalculation after profile
  changes.
- Made destination removal part of the signed preview contract and disclose
  the number of destination versions removed before save.
- Added route-context validation so destination, passage-boundary, or vessel-
  profile changes mark an older comparison stale and suppress its map overlay
  until it is recalculated.
- Added bounded Garmin `d1`/`d2` requests, a 48-hour normal polling overlap,
  and preview-first, rollbackable, resumable historical backfill in seven-day
  chunks.
- Added an administrator-guided, preview-first legacy Home Assistant Recorder
  recovery fallback with dynamic entity suggestions, 31-day bounded reads,
  null-safe temporal matching, provenance, and rollback.
- Added microSD-conscious WAL/checkpoint limits, bounded shape-preserving chart
  payloads, provider caching, and no continuous weather/routing worker.
- Removed passage prerequisites from stale, GPS, message, source, and optional
  zone notifications; removed automatic arrival semantics.
- Added Xweather credential redaction, provider/source diagnostics, archive
  integrity action, 32-test regression coverage including an actual v2 archive
  migration, and the full v2.1 handoff runbook in README.

## 2.0.1 — 2026-08-21

- Added the repository metadata, release documentation, validation workflow,
  and local brand assets required for installation as a HACS custom repository.
- Added the config-entry-only schema and canonical manifest key ordering
  required by Hassfest.
- Removed the installation-specific MapShare default, PredictWind tracking URL,
  mobile notification example, destination references, and private identifiers
  from the public source tree.
- Added an optional PredictWind tracking URL field that is configured and kept
  inside Home Assistant rather than embedded in published source code.
- Changed new config-entry titles to the generic `BlueSky Passage` title.
- Added a conservative upgrade cleanup that renames only an exact legacy
  `BlueSky Passage (share-name)` title and preserves user-customized titles and
  all entity IDs.
- Further redacted diagnostics so tracking URLs and mobile notification action
  names are represented only by present/not-present booleans.

## 2.0.0 — 2026-08-21

- Replaced the v1 native-dashboard/Recorder-history design with a UI-configured
  custom integration and automatically registered sidebar panel.
- Added a dedicated WAL-mode SQLite archive with no automatic time purge.
- Added ingestion and deduplication of every timestamped Garmin KML record
  exposed by each poll.
- Added event/message association with the exact source point.
- Added passage lifecycle, saved destinations, immutable destination revisions,
  arrival-radius state, and manual passage completion.
- Added a dependency-free Web Mercator/OpenStreetMap panel with selectable time
  ranges, source filters, clickable point detail, gap breaks, antimeridian-aware
  display, and record navigation.
- Added combined-track selection that prefers Garmin for exact timestamp
  overlaps without deleting any raw source.
- Added SOG, VMC, recorded-distance, UTC daily-run, and report-gap charts.
- Added closing-rate ETA, destination daylight-at-ETA, direct-reference
  progress, and display-only cross-track distance with safety boundaries.
- Added persistent notifications, optional tested mobile action, and a panel
  notification test.
- Preserved optional Home Assistant zone-change notifications, off by default.
- Added admin-only authenticated passage, destination, import, rollback, route,
  export, and manual-poll API commands.
- Added PredictWind/GeoJSON/GPX history import, GPX planned-route versions, and
  CSV/GeoJSON/GPX exports.
- Added a manual PredictWind public-page snapshot tool with provenance.
- Added redacted diagnostics and standard-library core regression tests.
- Added a side-by-side v1 validation, cutover, rollback, drive-migration,
  privacy, storage, and future-handoff runbook.
