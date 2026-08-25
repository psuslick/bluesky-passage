# BlueSky Passage

[![HACS custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![Version](https://img.shields.io/badge/version-2.5.0-blue.svg)](https://github.com/psuslick/bluesky-passage/releases)

BlueSky Passage 2.5.0 is a Home Assistant custom integration and responsive
sidebar panel for continuously archiving Garmin MapShare positions and analyzing
passages. It combines a non-purging local source archive, editable passage
annotations, linked maps/charts, optional Xweather wind/marine data, an
on-demand PredictWind view, and a water-constrained sailing-analysis engine.

## What changed in 2.5.0

Version 2.5.0 replaces the coarse-mask routing validity layer used by v2.2-v2.4. The first real passage test demonstrated that the bundled 1.25-arc-minute raster could miss thin barrier islands, so it is no longer permitted to certify or score a production route. Route generation now loads vector **NOAA ENC Direct to GIS** land and chart-coverage polygons on demand, performs land-boundary intersection checks on every candidate segment, and runs an independent high-resolution final validation before any modeled route can be saved.

The routing engine fingerprint is now `enc-isochrone-v4`. Every pre-v2.5 modeled route is intentionally stale and must be recalculated. The direct geodesic remains reference-only; sailing no-go headings remain hard-rejected; vessel polars/fallback performance, Xweather wind/waves/current, and the time-dependent beam/isochrone search remain part of route scoring. v2.5.0 deliberately **fails closed** when usable NOAA ENC coverage cannot be established instead of falling back to the old coarse raster. The initial high-confidence geography provider is therefore intended for NOAA-ENC-covered U.S. waters; global high-resolution coastline support remains future work.

This release also adds an in-panel **Routine alerts** switch under **Data & settings**. Disabling it immediately suppresses and dismisses routine stale-tracking, GPS, source, text, and optional zone notifications without changing the configured stale threshold. Garmin emergency-state notifications remain enabled independently. The Overview page and header show whether routine alerts are currently on or off, which is useful when a stationary inReach reports less frequently than it does underway.

## What changed in 2.4.0

Version 2.4.0 adds an interactive coordinate picker to passage creation and editing. Select **Place departure** or **Place arrival**, then click the map to populate the corresponding latitude and longitude fields. Existing passage tracks are shown as context when available; otherwise the picker starts from the latest archived vessel position when possible. The picker uses the same pan/zoom gestures and controls as the other BlueSky maps, and manual coordinate entry remains fully supported.

Any pin change invalidates an earlier coverage preview, so the passage must be previewed again before Save. This keeps the preview token tied to the exact coordinates being saved. The release also switches `TrackerEntity` to Home Assistant's current import path and removes the Core 2027.6 deprecation warning.

## What changed in 2.3.3

Version 2.3.3 fixes the WebSocket dispatch for **View / edit**. The v2.3.2 frontend correctly requested the metadata-only edit path, but the backend handler accidentally invoked `async_passage_detail(...)` on `BlueSkyCoordinator`; that method belongs to `BlueSkyRuntime`. The resulting `AttributeError` prevented passage details from loading. The handler now dispatches through the runtime facade, and bundle validation explicitly rejects the broken coordinator call.

The metadata-only edit isolation introduced in 2.3.2 remains in place: route payloads, coverage calculations, weather, and actual-vs-modeled analysis cannot block editing the passage record.

## What changed in 2.3.2

Version 2.3.2 corrected an incomplete isolation boundary in the passage editor. In 2.3.1 the frontend skipped live deviation analysis, but the backend still used the full passage-detail path, which deserialized saved routes and calculated archive coverage before returning edit metadata. A failure in any of that supplemental data could therefore still block **View / edit**. The admin editor uses a dedicated metadata-only database path.

## What changed in 2.3.0

Version 2.3.0 focuses on making the passage analysis understandable and directly comparable with the modeled sailing route. The analytics view now uses three stacked plots with one time axis: observed SOG, modeled wind with an optional gust envelope, and modeled wave height. Weather samples remain visibly sparse/dashed so they are not mistaken for Garmin observations.

Map interaction now supports reliable +/− controls, pointer-centered mouse-wheel/trackpad zoom, double-click zoom, two-finger pinch zoom, and drag panning. Map controls are excluded from drag capture.

Passage route analysis adds a live **Actual vs modeled** card. Archived Garmin positions are matched monotonically to the saved modeled sailing route, producing current/max port-starboard deviation, extra recorded distance, route-progress efficiency, modeled progress, and actual-vs-modeled time delta. The main deviation indicator is horizontal (port left, on-route center, starboard right) and supports Auto, fixed symmetric, or Custom ±nmi scale selection. The chosen override persists per passage in that browser. Optional representative connector lines can be shown on the passage map.

The route engine fingerprint is bumped to `isochrone-water-v2` because optimized routes now store elapsed time at route waypoints. Existing pre-2.3 saved route analyses are therefore intentionally marked stale; recalculate each route once after upgrade to enable the new timing and deviation semantics.

## What changed in 2.2.2

Version 2.2.2 carried forward the 2.2.1 map-pan correction and fixed the History & charts time-series presentation. Missing numeric values are no longer coerced to zero, the chart spans the selected query interval, the frontend requests up to the backend's 10,000-point display limit, and the chart reports observed/model sample coverage explicitly. Route-planner weather samples remain separate from observed-track weather because they describe different positions and predicted times.

## What changed in 2.2.0

Version 2.2.0 replaces the v2.1 three-corridor route scorer after that design
proved capable of scoring physically impossible routes. The direct geodesic is
now a reference only and is never a scored sailing candidate. Every scored
route segment must remain off the bundled dry-land mask; sailing headings inside
the vessel's no-go angle are rejected rather than assigned an artificially slow
straight-line speed. The weather search advances many candidate headings through
time, uses vessel polar/fallback performance, adds Xweather current as a vector
to through-water boat velocity, considers wind/waves and comfort penalties, and
keeps materially different alternatives.

The same release also carries forward the v2.1.1 integration-options and Garmin
backfill form fixes, allows valid empty historical Garmin KML intervals without
aborting a multi-chunk backfill, adds pointer/touch map panning plus accessible
pan controls, prevents route-card text truncation, and makes the actual track,
direct reference, shortest-water reference, selected sailing path, and alternate
paths visually distinct.

All route results produced by the older three-corridor engine are intentionally
made stale by the new routing-engine fingerprint and are suppressed from the map
until recalculated.

This repository contains no installation-specific share name, tracking URL,
coordinates, messages, IMEI, destination, provider credential, or archive
database. Test fixtures are synthetic; installation-specific values remain
inside Home Assistant.

See [RELEASE_DECISIONS.md](RELEASE_DECISIONS.md) for the explicit decisions that
define this release.

## Safety boundary

BlueSky Passage is a planning/analysis aid only. It is **not** a chart plotter,
navigation system, weather-routing authority, collision-avoidance system, or
emergency system. Garmin/inReach and the appropriate emergency-response channel
remain authoritative for SOS functions.

The v2.5 routing engine uses NOAA ENC Direct to GIS vector land and coverage
polygons as a hard geographic screen in supported U.S. waters. Every scored
segment must remain outside loaded ENC land polygons, and the completed route is
independently rechecked before it can be saved. Sailing headings inside the
configured no-go angle are also rejected rather than assigned an artificial
straight-line speed.

Those checks establish **coherence**, not navigational safety. ENC Direct to GIS
is itself a non-navigation GIS service, and BlueSky does not yet prove safe
depth, under-keel clearance, reefs/rocks, bridge clearance, traffic separation,
restricted areas, COLREGS compliance, warnings, local notices, or skipper
judgment. Verify every real voyage using appropriate certified/current charts,
forecasts, routing/navigation tools, and seamanship.

If high-resolution ENC coverage cannot be established for the full route,
BlueSky stops route generation. It does not silently fall back to the legacy
coarse land raster. If Xweather is unavailable, or a sailing vessel lacks a
usable wind vector, BlueSky may save only an explicitly labeled ENC-valid
geometric reference; it does not claim weather optimization.

## Requirements

- Home Assistant Core 2026.7.4 or newer
- HACS 2.x for the recommended installation path
- A public Garmin MapShare share name/URL, or its MapShare password if private
- Internet access for Garmin and online OpenStreetMap tiles
- Administrator access for setup and data-changing actions
- Optional Xweather Weather API client ID and secret with the required endpoint access
- Optional PredictWind public tracking URL for the on-demand PredictWind view

No YAML, external database server, or companion add-on is required. The archive,
weather adapter, sailing-analysis engine, panel, and authenticated API ship as
one custom integration.

## Install or upgrade with HACS

1. Create a full Home Assistant backup.
2. In **HACS**, add `https://github.com/psuslick/bluesky-passage` as a custom
   **Integration** if it is not already installed.
3. Open **BlueSky Passage** in HACS and install/update to version **2.5.0**.
4. Restart Home Assistant; a browser reload alone is not sufficient.
5. Hard-refresh the browser or reset the Companion App frontend cache if the old
   panel remains visible.
6. Open BlueSky Passage and verify archive count, earliest/latest timestamps,
   archive integrity, Garmin availability, and provider status.
7. Recalculate any passage route. All pre-2.5 modeled routes are intentionally stale and must be recalculated before using actual-vs-modeled comparison metrics.

For a fresh installation, open **Settings → Devices & services → Add
integration**, select **BlueSky Passage**, enter the Garmin MapShare share name
or public URL, enter only the MapShare password if the share is protected, and
optionally enter the PredictWind public tracking URL.

For an existing installation, do **not** delete the existing config entry and do
**not** delete or move the archive. Provider options are configured at
**Settings → Devices & services → BlueSky Passage → Configure**. Leaving a saved
Xweather secret blank preserves the existing secret.

HACS manages only:

```text
/config/custom_components/bluesky_passage/
```

The durable archive remains outside that directory:

```text
/config/bluesky_passage/archive.sqlite3
```

A normal HACS update therefore replaces integration code and bundled static data
without replacing the voyage archive. The integration domain, config-entry
unique ID, entity unique IDs, archive path, and SQLite schema remain compatible
with v2.1.x.

## Dashboard structure

BlueSky Passage keeps Home Assistant's global sidebar intact and uses four top
tabs inside its own panel:

1. **Overview** — source/safety state, current metrics including routine-alert state, latest 24-hour
   track, selected-record details, and latest exact inReach text.
2. **History & charts** — 24 hours by default; year/all/custom/passage ranges;
   linked Garmin map and analytics; on-demand Xweather model data; optional
   PredictWind view.
3. **Passages** — create/edit/backdate/open-end/close passage annotations,
   preview archive coverage, manage destinations, edit vessel performance, and
   calculate sailing-analysis routes.
4. **Data & settings** — archive health, Garmin historical backfill, provider
   status, the routine-alert on/off switch, Recorder recovery, manual imports,
   notification testing, and the integration configuration link.

All authenticated Home Assistant users can view the panel. Only administrators
can poll manually, contact Xweather, alter passage/profile data, backfill,
import, rollback, export, calculate routes, or test notifications.

## Map behavior

The built-in map supports mouse, pen, and touch interaction without an external
map-card dependency. Drag on the map to pan, use `+`/`-` to zoom, use the arrow
buttons as a non-drag panning alternative, and use the fit control to restore a
view around the selected data. A drag suppresses accidental report-point clicks.
Map center and zoom are maintained per map during ordinary redraws; changing the
selected range/source intentionally refits the relevant data.

Route layers are deliberately distinct:

- archived Garmin track: observed vessel movement;
- selected sailing-analysis path: solid green;
- alternative sailing candidates: faint dashed green;
- shortest-water geometric reference: amber dashed;
- direct geodesic reference: gray dotted when ENC-valid, red dotted when it
  intersects modeled land.

The direct geodesic remains visible for context even when rejected. It is never
a scored v2.2 sailing candidate.

The raster basemap comes from OpenStreetMap. Local archive overlays remain
available even if online basemap tiles cannot load.

## Storage and write behavior

A healthy application-class/high-endurance microSD with adequate free space can
run the current feature set. An SSD/NVMe remains a useful whole-system resilience
upgrade but is not required for BlueSky Passage.

Garmin is normally polled every ten minutes with a rolling 48-hour overlap.
Stable source-scoped keys deduplicate before insert. SQLite uses WAL mode,
`synchronous=NORMAL`, bounded checkpoints, and an 8 MiB journal-size limit.
Historical retrieval is administrator-started and chunked. Weather and routing
run only on request and normalized provider results are cached. Chart payloads
are bounded and shape-preserving.

The legacy 1.25-arc-minute comparison land mask remains bundled for backward
compatibility and regression tests, but v2.5 production route generation does
not use it as a validity source. NOAA ENC route geometry is requested only when
an administrator calculates a route and is cached in memory for a bounded
period; it creates no continuing archive writes.

Use full Home Assistant backups. Do not copy only the live `archive.sqlite3`
file while Home Assistant is running because its WAL/SHM companions may contain
part of the consistent state.

## Garmin archive and historical backfill

Normal collection requests a rolling 48-hour Garmin interval and stores only
unseen records. To populate older history, open **Data & settings → Garmin
historical backfill**, select a start/end range, and preview before import. The
selected dates persist across panel redraws and the active job card shows the
exact submitted range and chunk count. A second preview cannot be launched
while a job is pending/running.

Backfill runs in seven-day chunks and can resume after interruption. A valid KML
response containing no timestamped records for an older requested interval is
now treated as a successful empty chunk rather than an unusable feed. Malformed
XML, non-KML content, authentication problems, and genuinely unusable responses
still fail closed. This distinction prevents a legitimate gap in Garmin history
from aborting an otherwise recoverable multi-week or multi-year scan.

After preview, review returned/new/duplicate counts and first/last timestamps.
**Import previewed range** commits only the previewed new rows as one
provenance-tracked, rollbackable batch. Rollback removes only rows inserted by
that batch; normal Garmin rows, passages, and weather data remain untouched.
Garmin controls what history its public feed exposes; a requested date range
cannot recreate data Garmin no longer publishes.

### Recovering compatible v1 Recorder history

Use the Recorder fallback only when Garmin cannot reproduce older points that
are still available in Home Assistant History. The panel performs a bounded,
non-mutating preview first, dynamically suggests compatible legacy entities,
and stores an approved result as a source-labelled, rollbackable
`ha_recorder` batch. It remains best-effort reconstruction; original Garmin
records are preferred.

## Passage model

A passage is metadata over a time range, not a tracking switch. Garmin archiving
continues whether zero, one, or many passages exist. Passages may be created
before, during, or after travel; may be open-ended or fixed; may overlap; and
may be edited or deleted without changing raw source records.

Saving requires the exact preview token. Changing a boundary or destination
after preview forces coverage to be reviewed again. Destination changes create
versioned destination records rather than rewriting history. Clearing a
destination is an explicit previewed operation.

## Analytics and modeled weather

History defaults to the last 24 hours. The primary analytics graph shares one
time axis for observed Garmin SOG and optional modeled Xweather wind/gust/wave
series. Missing provider values remain gaps rather than zeros. A two-report map
range selection can reload the map and graph to the same inclusive interval.
Selecting a map point or chart point focuses the exact archived record; current
message text is never copied onto an older point.

The PredictWind frame is loaded only while that view is selected. If PredictWind
blocks embedding, use the direct-link action. BlueSky Passage does not modify a
PredictWind account or write routes/history to it.

## Optional Xweather configuration

1. Obtain a Weather API client ID and client secret whose subscription permits
   the required Conditions and Maritime endpoints.
2. Open **Settings → Devices & services → BlueSky Passage → Configure**.
3. Enter both Xweather values and save.
4. In **History & charts**, choose **Weather model** and select **Fetch / refresh
   model data** to test modeled track analytics.
5. In **Passages**, recalculate a route to use the v2.5 sailing search.

Credentials remain in the Home Assistant config entry and are sent only from
the backend to Xweather. They are not sent to panel JavaScript, stored in the
voyage archive, included in diagnostics, or written into route summaries.

Track analytics remain capped at 12 representative positions per operation.
The v2.5 route planner uses a separate bounded lattice of at most **11**
time/location positions around the shortest-water corridor, with two concurrent
position requests and the existing normalized cache. Conditions and Maritime
are evaluated independently so marine fields may remain available when wind is
not, and vice versa. For a sailing vessel, however, a usable wind vector is
required before the result can be called a sailing-weather search; otherwise
only the ENC-valid reference is saved.

Modeled values more than six hours from the requested sample time are rejected.
Transient unavailable results expire from the request cache after one hour.
Older Maritime Archive availability is provider-limited and missing historical
marine fields remain explicit gaps.

## Vessel profile and v2.5 sailing route analysis

The vessel profile accepts whatever is known: hull configuration, dimensions,
displacement, sail area, engine/observed cruise speed, maximum comfortable wave
height, minimum upwind true-wind angle, and optional polar rows containing
`twa_deg`, `tws_kn`, and `boat_speed_kn`.

For sailing vessels the minimum upwind TWA defaults to **40°** when not supplied.
The allowed profile value is clamped to a conservative 25–70° range. A heading
inside the no-go angle is invalid; the optimizer must tack/change heading rather
than move straight at a reduced speed. Motor-only hull configurations do not use
a sailing no-go angle.

Performance is resolved in this order: supplied polar interpolation when
available, otherwise the observed/engine/hull/generic speed baseline combined
with a conservative sail-angle/wind fallback. Xweather waves can reduce speed
and add risk/comfort penalties. Current speed/direction is added as a vector to
through-water vessel velocity, producing modeled course and speed over ground.

### Route-generation sequence

1. BlueSky requests NOAA ENC Direct to GIS metadata and vector polygons for the
   passage corridor across the available berthing, harbour, approach, coastal,
   and general scale bands.
2. Route endpoints must lie inside usable ENC chart coverage. A coordinate that
   falls just inside a coastal land polygon may be resolved to a nearby modeled
   water gate within the existing bounded endpoint allowance; the user's saved
   pin itself is never rewritten.
3. The engine creates an adaptive A* shortest-ENC-water reference. A proposed
   grid edge is rejected if its endpoints lie on land or if the segment crosses
   any loaded ENC land-polygon boundary.
4. Xweather is sampled on the existing bounded spatiotemporal lattice around the
   reference corridor.
5. The sailing optimizer advances a beam of candidate headings through time.
   Land crossings and no-go headings are discarded before scoring. Vessel
   performance, wind, waves/comfort, current-vector COG/SOG, elapsed time, and
   major maneuvers influence the result.
6. The selected route is then validated **again** by a separate final validator:
   the full path must remain inside ENC coverage and each segment is retested
   against vector land geometry with dense sampling. A failed final check means
   no modeled ideal route is saved.

The direct geodesic is always reference-only. A route cannot become a winner
merely because a straight line is shorter. If NOAA ENC geometry cannot be
loaded, if the route leaves supported chart coverage, or if the search cannot
produce a valid result, BlueSky reports the failure rather than certifying the
legacy coarse-mask path.

The initial v2.5 geography implementation is intentionally conservative and
U.S.-focused because NOAA ENC is the first high-resolution provider. It does not
yet claim global route validity. Future providers can implement the same
constraint interface without weakening the fail-closed rule.

## Notifications

Routine alerts can be enabled or disabled directly from **Data & settings →
Alerts**. The current state is also shown on the Overview page and in the header.
The switch updates the Home Assistant config-entry option immediately; a restart
is not required.

When routine alerts are **off**, BlueSky suppresses and dismisses stale-tracking,
invalid-GPS, Garmin-source failure/recovery, new inReach text, and optional
HA-zone notifications. The existing stale threshold is preserved, so turning
alerts back on resumes evaluation using the same configured threshold. This is
useful when a stationary vessel legitimately reports less often than the underway
cadence.

Garmin emergency-state notifications are deliberately independent of the routine
switch and remain enabled. The **Test notification** action is also forced so an
administrator can verify the notification path even while routine alerts are off.
The integration Options Flow remains the place to change the stale threshold,
mobile notification service, and zone-notification preference.

## Privacy model

Stored locally include normalized/raw Garmin/import rows, positions/timestamps,
messages/events, passage and destination versions, sailing-analysis summaries,
vessel profile, import/backfill provenance, normalized cached weather samples,
and provider option values held by Home Assistant's config-entry storage.

Network access occurs only when relevant: Garmin for feed/backfill requests,
OpenStreetMap for displayed basemap tiles, PredictWind only when its view/link is
opened, Xweather only during an administrator-requested weather or sailing
analysis operation, and NOAA ENC Direct to GIS only during administrator-requested route preparation/validation. The authenticated panel receives normalized provider values
but never the Xweather secret or raw upstream response envelope. Diagnostics
redact coordinates, messages, URLs, passwords, mobile action names, and Xweather
credential values.

## Troubleshooting

### Integration absent after download

Confirm `/config/custom_components/bluesky_passage/manifest.json` exists,
restart Home Assistant, hard-refresh the browser, and inspect **Settings →
System → Logs** for `bluesky_passage`.

### Integration cog loops back to BlueSky Passage

That was a v2.1.0 panel-registration bug. Version 2.1.1+ no longer registers the
sidebar as the integration configuration panel. Confirm the installed version,
restart Home Assistant, and clear the frontend cache.

### Archive count does not increase

A successful overlapping poll creates no row unless Garmin publishes an unseen
record. Check source availability and last successful poll rather than repeatedly
pressing Refresh.

### Backfill stops on an old interval

In v2.2, valid empty KML intervals continue as zero-record chunks. If a job still
fails, the response was malformed/non-KML, authentication/network access failed,
or Garmin returned a structure the parser cannot safely interpret. Preserve the
failed interval and sanitized error before changing parser behavior.

### All-time shows only one point

The local archive contains only one matching source row. Run a Garmin backfill
preview and verify Garmin actually exposes older records.

### Map will not pan

Version 2.3 supports drag panning, arrow-button panning, reliable +/− zoom, pointer-centered mouse-wheel/trackpad zoom, double-click zoom, and two-finger pinch zoom. If the map still behaves like an older version after upgrade, hard-refresh the browser or reset the Companion App frontend cache and confirm the footer reports 2.5.0.

### Basemap blank but overlays appear

The browser cannot reach `tile.openstreetmap.org` or a filter blocks it. Local
archive and route overlays are unaffected.

### Xweather returns partial data

Conditions and Maritime are independent. Subscription access, temporal model
coverage, Maritime forecast/archive windows, or provider gaps can leave some
fields null. BlueSky Passage keeps missing values null and shows warnings. A
sailing-weather route specifically requires a usable wind vector; marine-only
data cannot satisfy the no-go constraint.

### Sailing route cannot be generated

Check the Route geography status under Data & settings, the departure/destination pins, NOAA ENC coverage, Xweather warnings, vessel profile, and whether usable wind data exists for the requested period. v2.5 intentionally fails closed if high-resolution coastline coverage cannot be established or if the final validator rejects the route; it does not fall back to the legacy coarse mask.

## Future-change handoff

For future changes provide the current README and CHANGELOG, installed Home
Assistant and BlueSky Passage versions, redacted diagnostics, archive
count/earliest/latest/integrity, whether Xweather is configured (not the secret),
and the exact failing panel action or desired behavior.

Preserve these invariants unless deliberately redesigning them: domain
`bluesky_passage`; existing config entry/entity unique IDs; archive path; raw
record immutability; source labels; preview-before-passage-save; admin-only
mutations; provider-secret isolation; the hard rule that the direct geodesic is reference-only; NOAA-ENC/vector land and no-go rejection before scoring; independent final route validation; fail-closed behavior when trusted geography is unavailable; and explicit non-navigation labeling.

## Development validation

From the repository root:

```sh
python3 tools/validate_bundle.py
```

The release gate compiles Python, checks JSON/manifest/HACS structure, validates
frontend-to-backend command coverage and JavaScript syntax, scans public text for
installation-specific secrets/URLs, verifies the route/alert regression markers,
and runs the standard-library regression suite. v2.5 tests include synthetic
thin-island intersection, ENC-constrained A* detour, independent final-route
rejection/acceptance, sailing no-go behavior, Garmin backfill handling,
actual-vs-modeled calculations, passage editing, and the accumulated map/chart
regressions from earlier releases.

The validator also rejects production routing that imports the old raster as its
route-certification source and checks that the alert WebSocket/UI/state plumbing
is present. CI additionally runs Hassfest and HACS validation.

## Third-party land-mask data

`custom_components/bluesky_passage/data/landmask_1_25min.bit.gz` remains a
derived, bit-packed land/water mask generated from `basemap-data` 2.0.0
GSHHG-derived data. Licensing/attribution files remain included as
`THIRD_PARTY_NOTICES.md`, `COPYING.LGPL-DATA`, and `COPYING.LESSER-DATA`.

Beginning with v2.5, that raster is retained only for legacy compatibility and
tests; it is **not** permitted to certify production route validity. NOAA ENC
Direct to GIS geometry is fetched on demand from NOAA and is not redistributed
inside this repository.

## License

BlueSky Passage code is MIT licensed. See `LICENSE`. Third-party data included
for the land mask is covered separately as described above.
