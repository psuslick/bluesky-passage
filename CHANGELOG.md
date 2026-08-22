# Changelog

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
