# Changelog

## 2.0.1 — 2026-08-21

- Added the repository metadata, release documentation, validation workflow,
  and local brand assets required for installation as a HACS custom repository.
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
