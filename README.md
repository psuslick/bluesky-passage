# BlueSky Passage

[![HACS custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![Version](https://img.shields.io/badge/version-2.3.2-blue.svg)](https://github.com/psuslick/bluesky-passage/releases)

BlueSky Passage 2.3.2 is a Home Assistant custom integration and responsive
sidebar panel for continuously archiving Garmin MapShare positions and analyzing
passages. It combines a non-purging local source archive, editable passage
annotations, linked maps/charts, optional Xweather wind/marine data, an
on-demand PredictWind view, and a water-constrained sailing-analysis engine.

## What changed in 2.3.2

Version 2.3.2 corrects an incomplete isolation boundary in the passage editor. In 2.3.1 the frontend skipped live deviation analysis, but the backend still used the full passage-detail path, which deserialized saved routes and calculated archive coverage before returning edit metadata. A failure in any of that supplemental data could therefore still block **View / edit**. The admin editor now uses a dedicated metadata-only database path. Route payloads, coverage calculations, weather, and actual-vs-modeled analysis cannot block editing the passage record.

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

The v2.3 routing engine enforces a coarse dry-land constraint and sailing no-go
constraint because a route that crosses modeled land or assumes an impossible
upwind heading is not useful even as a comparison. That does **not** make the
result navigable. The bundled land mask is not a nautical chart and does not
know depths, reefs, rocks, bridge clearances, channels, restricted areas,
traffic, COLREGS, weather warnings, local notices, or skipper judgment. Verify
any real voyage with appropriate charts, forecasts, routing/navigation tools,
and seamanship.

If Xweather is unavailable, or a sailing vessel lacks a usable wind vector for
the requested period, BlueSky Passage saves only an explicitly labeled
**water-valid geometric reference**. It does not manufacture a weather-optimized
sailing path.

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
3. Open **BlueSky Passage** in HACS and install/update to version **2.3.2**.
4. Restart Home Assistant; a browser reload alone is not sufficient.
5. Hard-refresh the browser or reset the Companion App frontend cache if the old
   panel remains visible.
6. Open BlueSky Passage and verify archive count, earliest/latest timestamps,
   archive integrity, Garmin availability, and provider status.
7. Recalculate any passage route. Pre-2.3 route analyses are intentionally
   stale and must be recalculated once before using the new actual-vs-modeled comparison.

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

1. **Overview** — source/safety state, four current metrics, latest 24-hour
   track, selected-record details, and latest exact inReach text.
2. **History & charts** — 24 hours by default; year/all/custom/passage ranges;
   linked Garmin map and analytics; on-demand Xweather model data; optional
   PredictWind view.
3. **Passages** — create/edit/backdate/open-end/close passage annotations,
   preview archive coverage, manage destinations, edit vessel performance, and
   calculate sailing-analysis routes.
4. **Data & settings** — archive health, Garmin historical backfill, provider
   status, Recorder recovery, manual imports, notification testing, and the
   integration configuration link.

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
- direct geodesic reference: gray dotted when water-valid, red dotted when it
  intersects modeled land.

The direct geodesic remains visible for context even when rejected. It is never
a scored v2.2 sailing candidate.

The raster basemap comes from OpenStreetMap. Local archive overlays remain
available even if online basemap tiles cannot load.

## Storage and write behavior

A healthy application-class/high-endurance microSD with adequate free space can
run every v2.2 feature. An SSD/NVMe remains a useful whole-system resilience
upgrade but is not required for BlueSky Passage.

Garmin is normally polled every ten minutes with a rolling 48-hour overlap.
Stable source-scoped keys deduplicate before insert. SQLite uses WAL mode,
`synchronous=NORMAL`, bounded checkpoints, and an 8 MiB journal-size limit.
Historical retrieval is administrator-started and chunked. Weather and routing
run only on request and normalized provider results are cached. Chart payloads
are bounded and shape-preserving.

The bundled 1.25-arc-minute comparison land mask is about 0.4 MiB compressed.
Its roughly 18.7 MiB bitset is decompressed lazily in memory when routing first
needs it; it does not create ongoing disk writes.

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
5. In **Passages**, recalculate a route to use the v2.2 sailing search.

Credentials remain in the Home Assistant config entry and are sent only from
the backend to Xweather. They are not sent to panel JavaScript, stored in the
voyage archive, included in diagnostics, or written into route summaries.

Track analytics remain capped at 12 representative positions per operation.
The v2.2 route planner uses a separate bounded lattice of at most **11**
time/location positions around the shortest-water corridor, with two concurrent
position requests and the existing normalized cache. Conditions and Maritime
are evaluated independently so marine fields may remain available when wind is
not, and vice versa. For a sailing vessel, however, a usable wind vector is
required before the result can be called a sailing-weather search; otherwise
only the water-valid reference is saved.

Modeled values more than six hours from the requested sample time are rejected.
Transient unavailable results expire from the request cache after one hour.
Older Maritime Archive availability is provider-limited and missing historical
marine fields remain explicit gaps.

## Vessel profile and v2.2 sailing route analysis

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
through-water vessel velocity, producing a modeled course and speed over ground.

### Route-generation sequence

The route planner first checks whether departure and destination are water
points in the comparison mask. It builds a coarse shortest-water reference with
an adaptive A* search when the direct segment intersects land, then revalidates
every simplified segment against the higher-resolution mask. It samples a small
spatiotemporal Xweather field around that water corridor. The sailing search
then advances a beam of materially different candidate states over time,
examining destination-oriented headings, alternative offsets, prior heading,
and close-hauled headings. Land crossings and no-go headings are discarded
before scoring. Current modifies COG/SOG; wind, waves, vessel performance,
travel time, comfort risk, and major maneuvers influence candidate score.

The engine stores the best complete result and up to two materially different
alternatives. The direct great-circle line and shortest-water line are references
only. If the weather search cannot close a valid route, it saves the water-valid
reference with an explicit warning instead of inventing an optimized route.

This remains a deliberately bounded analytical search, not a commercial
navigation-grade isochrone router. The weather field is sparse/interpolated and
the land mask is coarse. Treat the result as a way to compare plausible sailing
behavior against observed Garmin history—not as coordinates to transfer to a
navigation device.

## Notifications

Persistent Home Assistant notifications are enabled by default. An exact
Companion App `notify.mobile_app_...` action can be added through Configure after
it has been tested independently in Developer Tools. Emergency transitions,
stale tracking, persistent invalid GPS, new inReach text, Garmin source failure
and recovery, and optional HA-zone changes operate from the continuous latest
Garmin record and do not depend on a passage being active.

## Privacy model

Stored locally include normalized/raw Garmin/import rows, positions/timestamps,
messages/events, passage and destination versions, sailing-analysis summaries,
vessel profile, import/backfill provenance, normalized cached weather samples,
and provider option values held by Home Assistant's config-entry storage.

Network access occurs only when relevant: Garmin for feed/backfill requests,
OpenStreetMap for displayed basemap tiles, PredictWind only when its view/link is
opened, and Xweather only during an administrator-requested weather or sailing
analysis operation. The authenticated panel receives normalized provider values
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

Version 2.3 supports drag panning, arrow-button panning, reliable +/− zoom, pointer-centered mouse-wheel/trackpad zoom, double-click zoom, and two-finger pinch zoom. If the map still behaves like an older version after upgrade, hard-refresh the browser or reset the Companion App frontend cache and confirm the footer reports 2.3.2.

### Basemap blank but overlays appear

The browser cannot reach `tile.openstreetmap.org` or a filter blocks it. Local
archive and route overlays are unaffected.

### Xweather returns partial data

Conditions and Maritime are independent. Subscription access, temporal model
coverage, Maritime forecast/archive windows, or provider gaps can leave some
fields null. BlueSky Passage keeps missing values null and shows warnings. A
sailing-weather route specifically requires a usable wind vector; marine-only
data cannot satisfy the no-go constraint.

### Sailing search returns only a water-valid reference

Check Xweather provider warnings, vessel profile, departure/destination water
positions, and whether usable wind data exists for the requested period. The
optimizer intentionally fails closed rather than scoring a land-crossing or
no-go route.

## Future-change handoff

For future changes provide the current README and CHANGELOG, installed Home
Assistant and BlueSky Passage versions, redacted diagnostics, archive
count/earliest/latest/integrity, whether Xweather is configured (not the secret),
and the exact failing panel action or desired behavior.

Preserve these invariants unless deliberately redesigning them: domain
`bluesky_passage`; existing config entry/entity unique IDs; archive path; raw
record immutability; source labels; preview-before-passage-save; admin-only
mutations; provider-secret isolation; the v2.2 hard rule that direct geodesic is
reference-only; land/no-go rejection before route scoring; and explicit
non-navigation labeling.

## Development validation

From the repository root:

```sh
python3 tools/validate_bundle.py
```

The release gate compiles Python, checks JSON/manifest/HACS structure, validates
frontend-to-backend command coverage and JavaScript syntax, scans public text for
installation-specific secrets/URLs, verifies the bundled land-mask/notices and
v2.2 routing/map/backfill regression markers, and runs **37** standard-library
regression tests. The suite includes archive migration/deduplication, Garmin
bounded history and valid-empty historical KML, passage preview/editing,
rollback, weather gap handling, Hampton-to-Beaufort land rejection, no-go
rejection, synthetic upwind tacking, missing-wind fail-closed behavior, and
frontend configuration/backfill safeguards.

The CI workflow also runs Hassfest and HACS validation.

## Third-party land-mask data

`custom_components/bluesky_passage/data/landmask_1_25min.bit.gz` is a derived,
bit-packed dry-land mask generated from the `basemap-data` 2.0.0 GSHHG-derived
land/sea mask. Licensing/attribution files are included as
`THIRD_PARTY_NOTICES.md`, `COPYING.LGPL-DATA`, and `COPYING.LESSER-DATA`.
The mask is included only to reject obviously impossible land-crossing
comparison segments; it is not a nautical chart.

## License

BlueSky Passage code is MIT licensed. See `LICENSE`. Third-party data included
for the land mask is covered separately as described above.
