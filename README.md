# BlueSky Passage

[![HACS custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![Version](https://img.shields.io/badge/version-2.1.0-blue.svg)](https://github.com/psuslick/bluesky-passage/releases)

BlueSky Passage 2.1.0 is a Home Assistant custom integration and responsive
sidebar panel for locally archiving and analyzing positions exposed through a
Garmin MapShare feed. It provides a continuous, non-purging source archive;
editable passage annotations; linked maps and charts; optional on-demand
Xweather wind/marine data; and a conservative comparison-route generator.

This repository contains no installation-specific share name, tracking URL,
coordinates, messages, IMEI, destination, provider credential, or archive
database. Test fixtures are synthetic; installation-specific values remain
inside Home Assistant.

See [RELEASE_DECISIONS.md](RELEASE_DECISIONS.md) for the explicit storage,
architecture, provider, passage, routing, privacy, and UX decisions that define
this release and should accompany future change requests.

## Read this first

- A recommended application-class or high-endurance microSD card is supported.
  An SSD is an optional resilience upgrade, not a v2.1.0 prerequisite.
- BlueSky Passage is supplementary. It is not a navigation, weather-routing,
  collision-avoidance, charting, or emergency system.
- The generated path is a **comparison estimate**, never a safe or navigable
  route. It does not avoid land, charted hazards, traffic, restricted areas,
  weather warnings, or local notices.
- Xweather is optional. Without credentials, route creation produces only an
  explicitly labeled great-circle reference.

## Requirements

- Home Assistant Core 2026.7.4 or newer
- HACS 2.x for the recommended installation path
- A public Garmin MapShare share name/URL, or its MapShare password if private
- Internet access for Garmin and online map tiles
- Administrator access for setup and data-changing actions
- Optional Xweather Weather API client ID and secret for modeled wind, waves,
  and currents
- Optional PredictWind public tracking URL for the on-demand PredictWind view

No YAML or separate add-on is required. The archive, weather adapter, route
comparison engine, panel, and authenticated API are one custom integration.

## Install or upgrade with HACS

1. Create a full Home Assistant backup.
2. In **HACS**, open the upper-right menu and select **Custom repositories**.
3. Add `https://github.com/psuslick/bluesky-passage` as an **Integration**.
4. Open **BlueSky Passage**, select **Download**, and choose version 2.1.0.
5. Restart Home Assistant; a browser reload alone is not sufficient.

For a fresh installation:

1. Open **Settings → Devices & services → Add integration**.
2. Search for **BlueSky Passage**.
3. Enter the MapShare share name or public URL.
4. Enter only the MapShare password if that share is protected. Do not enter a
   Garmin account password.
5. Optionally enter the public PredictWind tracking URL.
6. Submit, then open **BlueSky Passage** in the Home Assistant sidebar.

For an existing v2.0.0/v2.0.1 installation:

1. Do **not** delete the existing config entry.
2. Do **not** delete or move the existing archive.
3. After restart, open the panel and compare archive count, earliest/latest
   timestamp, size, and integrity with the pre-upgrade values.
4. Open **Settings → Devices & services → BlueSky Passage → Configure**.
5. Add optional Xweather and PredictWind values; leaving the Xweather secret
   blank preserves an existing saved secret.
6. Test the notification from **Data & settings**.

HACS manages only:

```text
/config/custom_components/bluesky_passage/
```

Home Assistant OS exposes `/config` as `/homeassistant` in File editor, so the
same integration may appear there as:

```text
/homeassistant/custom_components/bluesky_passage/
```

The durable archive remains outside the HACS-managed component folder:

```text
/config/bluesky_passage/archive.sqlite3
```

Normal HACS upgrades therefore replace code but do not replace the archive.
The integration domain, config-entry unique ID, entity unique IDs, and archive
path are unchanged from v2.0.x.

## Dashboard structure

The Home Assistant sidebar continues to navigate between Home Assistant areas.
BlueSky Passage subnavigation is a proper top tab bar inside its panel:

1. **Overview** — safety/source alerts, four current metrics, latest 24-hour
   track, exact selected-record detail, and latest inReach text.
2. **History & charts** — 24 hours by default; year/all/custom/passage ranges;
   local, modeled-weather, and on-demand PredictWind map views; explicit
   two-report selection; linked analytics and exports.
3. **Passages** — create, view, edit, backdate, open-end, close, or delete a
   passage annotation at any time; preview report coverage before saving; add
   or revise a destination; create a comparison path.
4. **Data & settings** — archive health, Garmin historical backfill, provider
   status, legacy Recorder recovery, vessel profile, manual imports,
   notification test, and integration configuration link.

All authenticated Home Assistant users can view the panel. Only administrators
can poll manually, change metadata, contact Xweather, backfill, import,
rollback, calculate paths, export, or test notifications.

## Storage and write behavior

The existing recommended-class microSD is adequate because v2.1.0 deliberately
keeps background work small:

- Garmin is polled every 10 minutes with a rolling 48-hour overlap.
- Stable source-scoped keys prevent repeat feed rows from becoming repeat
  archive rows.
- SQLite uses WAL mode, `synchronous=NORMAL`, a 1,000-page auto-checkpoint, and
  an 8 MiB journal-size limit.
- Historical retrieval runs in user-started, resumable seven-day chunks.
- Xweather and path calculations run only when requested; model results are
  cached and reused.
- Chart responses are bounded and retain endpoints and local SOG extrema.
- Home Assistant Recorder does not store the permanent source archive and does
  not control its retention.

An SSD becomes worthwhile for broader Home Assistant resilience, especially
with many high-frequency integrations, a large Recorder database, frequent
backups, or years of additional write-heavy workloads. It is not required to
turn on any BlueSky Passage feature.

Use full Home Assistant backups. Do not copy only `archive.sqlite3` while Home
Assistant is running: the active `-wal` and `-shm` companions may contain part
of the consistent state.

## Garmin archive and historical backfill

Normal collection asks Garmin for the last 48 hours and stores only unseen
records. The overlap tolerates delayed publication and brief outages without
creating duplicates.

To populate older records:

1. Open **Data & settings → Garmin historical backfill**.
2. Choose start and end dates.
3. Select **Preview Garmin availability**.
4. Leave the page open while seven-day chunks run. If the browser or Home
   Assistant disconnects, use **Resume failed chunk** later.
5. Review returned and duplicate counts plus first/last timestamps.
6. Select **Import previewed range** only after the preview is acceptable.

The commit is one provenance-tracked import batch. Rollback removes only rows
inserted by that batch. It does not remove normal Garmin rows, passages, or
weather data. Garmin controls what history its feed exposes; requesting a date
range cannot manufacture unavailable records.

Existing v2.0.x BlueSky Passage archive rows are already in the same database
and need no migration import. Manual JSON/GeoJSON/GPX import is a fallback for
records Garmin cannot supply.

### Recovering compatible v1 Recorder history

Use this only after a Garmin preview fails to return v1 points that are still
visible in **History**. It is intentionally a fallback because Home Assistant
Recorder normally has much shorter retention and reconstructing several entity
histories is less authoritative than importing original Garmin records.

1. Keep the v1 Garmin MapShare entities enabled until recovery is verified.
2. Open **Data & settings → Legacy Home Assistant Recorder recovery**.
3. Choose a range of at most 31 days. Ten days is the recommended first pass.
4. Confirm the automatically suggested legacy position, report-time, velocity,
   course, elevation, GPS, emergency, and text entities. Only position is
   required.
5. Select **Preview Recorder recovery**.
6. Review reconstructed, new, duplicate, rejected, first, and last values.
7. Select **Import previewed Recorder rows** only if the preview is credible.
8. Inspect the recovered map and point details before disabling v1.

The browser reads history through Home Assistant's authenticated
`history/history_during_period` WebSocket command. The integration does not
open or modify Home Assistant's database. The resulting `ha_recorder` batch is
stored in the dedicated archive and can be rolled back from the import table.
Identical values are matched to a position only when their history state is
temporally applicable; unavailable optional fields stay null. This remains a
best-effort reconstruction, so Garmin backfill is preferred.

## Passage model

A passage is metadata over a time range, not a recording switch:

- Global Garmin archiving continues whether zero, one, or many passages exist.
- An open-ended passage means “from this start time onward.” It does not mean
  active, underway, started in real time, or subject to automatic completion.
- A passage can be created after departure, edited after arrival, or deleted
  without changing raw report rows.
- Overlapping passages are allowed and shown during preview.
- Saving requires the exact preview token, so changing a time or destination
  forces coverage to be reviewed again.
- Destination changes create destination versions; historical raw reports are
  never copied into a passage.
- Clearing every destination field is an explicit previewed removal: the
  coverage card states how many saved destination versions will be removed,
  and the exact clear decision is locked into the preview token.

## Analytics and map selection

History defaults to the last 24 hours. The analytics graph uses one shared time
axis for:

- vessel SOG from Garmin — **observed**
- wind and optional gust from Xweather — **modeled**
- significant wave height from Xweather — **modeled**

Missing provider values remain gaps rather than zeros. Selecting **Select map
range** and then two report dots reloads the map and graph for that inclusive
time span. Clicking a dot in the map or speed series selects the exact source
record and never copies current text onto an older point.

The PredictWind frame is created only while that map view is selected. If the
provider prevents embedding, use **Open PredictWind map**. This integration
does not modify the PredictWind account or its route/history data.

## Optional Xweather configuration

1. Obtain a Weather API client ID and client secret whose subscription permits
   the Conditions and Maritime endpoints.
2. Open **Settings → Devices & services → BlueSky Passage → Configure**.
3. Enter both Xweather values and save.
4. In **History & charts**, choose **Weather model**, then select **Fetch /
   refresh model data**.

Credentials remain in the Home Assistant config entry and are sent only from
the backend to Xweather. They are not sent to the panel, stored in the archive,
included in diagnostics, or written into route summaries.

The integration requests at most 12 representative positions per operation,
with two concurrent position requests. Conditions and Maritime are evaluated
independently so wind may remain available when marine fields are not. Marine
history before 2024 is unavailable from Xweather's Maritime Archive and is
shown as a gap/warning rather than zero. A model period more than six hours
from the requested report time is rejected, and a transient unavailable result
expires from the request cache after one hour.

## Vessel profile and path creator

The vessel profile accepts whatever is known:

- hull configuration
- length overall and waterline length
- beam and draft
- displacement and sail area
- engine or observed cruise speed
- maximum comfortable wave height
- optional polar-table rows (`twa_deg`, `tws_kn`, `boat_speed_kn`)

No field is mandatory. Speed estimation falls back in this order: polar table,
observed cruise speed, engine cruise speed, hull-speed estimate, then a generic
5.5 kn comparison value. Saving a profile can recalculate the selected
passage's latest comparison.

For a passage with departure and destination, the path creator evaluates three
simple geodesic corridors: direct, port-offset, and starboard-offset. Each uses
four representative time/location samples. Where available, modeled wind,
waves, and currents modify estimated speed and a comfort-oriented risk score.
The best-scoring estimate is saved as a version alongside all candidate
summaries. Without usable weather, the direct line is saved as a
`great_circle_reference` and no optimization is claimed.

Every saved comparison includes a context fingerprint. Changing the passage
range, departure coordinates, destination version, or vessel profile marks the
older comparison stale and removes its map overlay until an administrator
recalculates it; the prior summary remains visible for audit.

This intentionally lean implementation does **not** contain electronic charts,
land masks, routing constraints, forecast ensembles, tack/gybe strategy,
weather warnings, traffic, COLREGS, or hazard avoidance. Never transfer its
coordinates to navigation equipment as an approved course.

## Notifications

Persistent Home Assistant notifications are enabled by default. An exact
Companion App `notify.mobile_app_...` action can be added under Configure after
testing it independently in Developer tools.

| Condition | Passage required | Behavior |
|---|---:|---|
| Emergency becomes active or clears | No | Always processed |
| Latest report exceeds stale threshold | No | One alert plus recovery |
| Explicit invalid GPS fix persists 10 minutes | No | One alert plus recovery |
| New inReach text appears | No | Includes the exact event text |
| Three Garmin polls fail | No | Source alert plus recovery |
| Home Assistant zone changes | No | Optional; disabled by default |

There is no passage-arrival notification or automatic passage transition in
v2.1.0 because passages are editable historical annotations.

## Privacy model

Stored locally:

- normalized and raw Garmin/import records
- positions, timestamps, messages, event text, and feed-supplied device IDs
- passage/destination versions and route comparison summaries
- vessel profile, import provenance, backfill progress, and cached normalized
  weather samples
- selected legacy entity identity inside any administrator-approved Recorder
  recovery batch
- provider values held by Home Assistant's config-entry storage

Network-visible only when relevant:

- Garmin receives normal/backfill feed requests.
- OpenStreetMap receives tile requests for displayed map areas.
- PredictWind receives a browser request only when its map/link is opened.
- Xweather receives coordinate/time samples only during an explicit weather or
  comparison request.

The authenticated panel receives normalized provider values but never the
Xweather secret or upstream response envelope. Diagnostics redact coordinates,
messages, URLs, passwords, mobile action names, and Xweather credential values.

## Troubleshooting

### Integration absent after download

Confirm this file exists and restart Home Assistant:

```text
/config/custom_components/bluesky_passage/manifest.json
```

Then hard-reload the browser and inspect Home Assistant logs for
`bluesky_passage`.

### Archive count does not increase

A successful overlapping poll creates no row unless Garmin exposes an unseen
record. Check source availability, last successful poll, and the bounded Garmin
request diagnostics rather than repeatedly selecting Refresh.

### All-time shows only one point

The archive currently contains one matching source row; the panel no longer
labels a default seven-day query as all time. Run a Garmin backfill preview and
verify Garmin actually returns older timestamps.

### Basemap blank but overlays appear

The browser cannot reach `tile.openstreetmap.org` or a filter blocks it. Local
archive data is unaffected.

### PredictWind frame blank

The link is absent, the browser blocks mixed/third-party content, or PredictWind
disallows embedding. Open the public link directly from the view.

### Xweather returns partial data

Wind and marine endpoints are independent. Subscription access, model coverage,
the Maritime ±forecast window, or Maritime Archive's 2024 start can leave some
fields missing. The dashboard keeps those fields null and displays warnings.

### Backfill interrupted

Return to **Data & settings** and select **Resume failed chunk**. Completed
chunks are not repeated as new rows because deduplication is source-stable.

## Future-change handoff

For a future maintainer or AI-assisted change, provide:

1. this README and `CHANGELOG.md`;
2. installed Home Assistant and BlueSky Passage versions;
3. redacted integration diagnostics;
4. the failing panel section and exact action sequence;
5. archive count/earliest/latest/integrity, never the private database unless
   intentionally sharing all positions/messages;
6. whether Xweather is configured (not the values);
7. whether storage is microSD or SSD.

Preserve these invariants: domain `bluesky_passage`, existing config entry and
entity unique IDs, archive path, raw-record immutability, source labels,
preview-before-passage-save, admin-only mutations, provider-secret isolation,
and explicit non-navigation labeling.

## Development validation

From the repository root:

```sh
python3 tools/validate_bundle.py
```

The release gate compiles the integration and tools, checks JSON/manifest/HACS
structure, validates frontend-to-backend command coverage and JavaScript
syntax, scans public text for installation-specific tracking values, and runs
32 standard-library regression tests including v2 archive migration, delayed
Garmin rows, malformed KML, resumable backfill, Recorder preview, and rollback.

The workflow also runs Hassfest and HACS validation. The local suite covers
parsing/units, date-bounded Garmin requests, deduplication, passage preview and
editing, backfill chunking, import rollback, null weather values, route
fallback behavior, exports, schema integrity, and frontend syntax/command
alignment.

## License

MIT. See [LICENSE](LICENSE).
