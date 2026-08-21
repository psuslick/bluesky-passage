# BlueSky Passage

[![HACS custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/docs/faq/custom_repositories/)
[![Version](https://img.shields.io/badge/version-2.0.1-blue.svg)](https://github.com/psuslick/bluesky-passage/releases)

BlueSky Passage is a Home Assistant custom integration and sidebar panel for
archiving and reviewing positions exposed by a Garmin MapShare feed. It adds a
local, non-purging voyage archive, passage and destination management, maps,
charts, imports, exports, and supplementary tracking notifications.

The repository contains no vessel share name, tracking URL, coordinates,
messages, IMEI, destination, password, or archived database. Those values are
entered or collected only inside the user's Home Assistant installation.

## Requirements

- Home Assistant Core 2026.7.4 or newer
- HACS 2.x for the recommended installation path
- A public Garmin MapShare share name or URL
- Internet access for Garmin polling and the online OpenStreetMap basemap
- Administrator access for setup and data-changing actions

PredictWind is optional. Its public tracking URL is entered locally and is
used only as a dashboard link; history import remains a deliberate manual
operation because the public-page payload is undocumented.

## Install with HACS

This repository is initially distributed as a HACS custom repository.

1. Open **HACS** in Home Assistant.
2. Open the upper-right menu and choose **Custom repositories**.
3. Add:

   ```text
   https://github.com/psuslick/bluesky-passage
   ```

4. Select category **Integration**.
5. Select **Add**.
6. Search HACS for **BlueSky Passage**.
7. Open it and select **Download**.
8. Restart Home Assistant.
9. Open **Settings → Devices & services → Add integration**.
10. Search for **BlueSky Passage** and complete the local setup form.

Do not paste Garmin account credentials. Use only the MapShare password when a
share feed is password-protected; leave it blank for a public feed.

## Upgrade from a manual v2.0.0 installation

The HACS version intentionally retains the same integration domain and archive
location. Existing configuration entries, entities, passage metadata, and
history remain compatible.

1. Create a full Home Assistant backup.
2. Confirm the existing archive is healthy in the BlueSky Passage panel.
3. Add this repository to HACS and download v2.0.1.
4. Restart Home Assistant.
5. Open the panel and confirm archive count, earliest/latest timestamps, and
   integrity status.
6. Open **Settings → Devices & services**, find BlueSky Passage, and select
   **Configure** to enter an optional PredictWind tracking URL. v2.0.0 stored
   that particular link in source code; v2.0.1 deliberately does not.
7. Test the notification path.

If the old generated config-entry title exactly matches `BlueSky Passage
(share-name)`, v2.0.1 changes that local title to `BlueSky Passage` during
setup. A title the user deliberately customized is preserved. Entity IDs are
not renamed.

HACS manages only:

```text
/config/custom_components/bluesky_passage/
```

The archive remains outside that folder at:

```text
/config/bluesky_passage/archive.sqlite3
```

Normal HACS upgrades therefore do not replace or delete the archive.

## What it provides

- A dedicated SQLite archive with no automatic time purge
- A 10-minute Garmin MapShare KML poll
- Stable source-scoped deduplication
- Exact message/event association with the source record
- Passage lifecycle: active, arrived, and manually completed
- Saved destinations and immutable destination revisions
- Arrival-radius detection without automatic passage completion
- Current-passage, 1/3/7/30-day, 1-year, all-time, and custom ranges
- Garmin, PredictWind import, GPX import, combined, and all-raw views
- An OpenStreetMap basemap with local track overlays
- Clickable track dots and fixed per-record detail
- SOG, destination VMC, recorded-distance, daily-run, and report-gap charts
- Destination range, bearing, recent closing-rate ETA, and daylight at ETA
- Direct great-circle reference and display-only cross-track distance
- Planned GPX route versions kept separate from actual tracks
- CSV, GeoJSON, and GPX exports
- Hash-identified, rollbackable history imports
- Redacted Home Assistant diagnostics

## Permissions

- All authenticated Home Assistant users can view the panel, positions, and
  messages.
- Only Home Assistant administrators can poll manually, test notifications,
  manage passages or destinations, import, rollback, attach routes, or export.

Do not give Home Assistant accounts to people who should not see voyage data.

## Notifications

Local persistent Home Assistant notifications are enabled by default. An exact
Companion App `notify.mobile_app_...` action can be added under the integration
options after it has been tested independently in Developer tools.

| Condition | Passage required | Behavior |
|---|---:|---|
| Emergency becomes active or clears | No | Always enabled |
| Latest report exceeds stale threshold | Yes | One alert and recovery update |
| Explicit invalid GPS fix persists 10 minutes | Yes | One alert and recovery update |
| New text event | Yes | Includes that event's text |
| Arrival radius entered | Yes | Marks arrived and notifies |
| Three Garmin polls fail | Yes | Source warning and recovery update |
| Home Assistant zone changes | Yes | Optional; disabled by default |

Home Assistant notifications are supplementary. Garmin/inReach and the
appropriate emergency-response channel remain authoritative.

## Archive behavior

The archive uses SQLite WAL mode and `synchronous=NORMAL`. At one report every
10 minutes, a continuously reporting source would produce at most about 52,560
poll opportunities per year. Duplicate source records are ignored.

Home Assistant Recorder retention does not purge this archive. Current-state
entities created by the integration still follow Recorder policy, while the
underlying source records remain in the dedicated archive.

Use Home Assistant backups. Do not manually copy only the SQLite file while
Home Assistant is running because WAL companion files may also be active.

## History imports

Administrators can import timestamped:

- PredictWind snapshot JSON or captured HTML
- GeoJSON point features
- historical GPX tracks

Imports are source-labelled, hashed, deduplicated, recorded as batches, and
rollbackable. Rolling back an import removes only rows inserted by that batch;
it never removes live Garmin records.

The bundled manual snapshot tool requires an explicit URL so no private
tracking identifier is published in this repository:

```sh
python3 tools/snapshot_predictwind.py --url "https://www.predictwind.com/tracking/YOUR-PUBLIC-ID"
```

The public PredictWind page schema is undocumented and can change. Never import
an empty or unverified extraction.

## Privacy model

Stored locally:

- normalized and raw source records
- coordinates and timestamps
- messages and event text
- device identifiers supplied by the feed
- passages, destinations, route versions, and import provenance
- optional MapShare password and PredictWind link in Home Assistant config

Network-visible:

- Garmin receives MapShare feed requests
- PredictWind is contacted only when its link is opened or the manual snapshot
  tool is deliberately run
- OpenStreetMap receives tile requests, including public IP, displayed map
  area, and the Home Assistant site origin as the browser referrer

The panel uses Home Assistant's authenticated WebSocket. It embeds no
long-lived Home Assistant token or database password. Diagnostics omit
coordinates, messages, passwords, mobile action names, and tracking URLs.

## Safety limitations

BlueSky Passage is not a navigation system.

- The yellow direct-reference line is not a safe routed course.
- Range, bearing, closing rate, ETA, and cross-track values do not model land,
  hazards, currents, weather, routing constraints, or vessel performance.
- `Course` is treated as approximate COG true; `Velocity` as SOG.
- Recorded-track distance joins discrete reports with straight segments.
- A stale report does not prove distress; a fresh report does not prove safety.

## Troubleshooting

### Integration is absent after HACS download

Confirm this file exists and restart Home Assistant:

```text
/config/custom_components/bluesky_passage/manifest.json
```

Then hard-reload the browser and check Home Assistant logs for
`bluesky_passage`.

### Archive count does not increase

A successful poll does not create a new row unless Garmin exposes an unseen
record. Check **Last successful poll** and **Garmin source available** rather
than repeatedly pressing Refresh.

### Basemap is blank but points appear

The browser cannot reach `tile.openstreetmap.org`, or a filter blocks it. The
archive and overlays are unaffected.

### Reporting a problem

Use the issue tracker only after removing:

- coordinates and routes
- messages
- IMEI/device identifiers
- MapShare and PredictWind links
- passwords and tokens
- database files

The integration's diagnostics intentionally report only redacted health and
version information.

## Development validation

From the repository root:

```sh
python3 tools/validate_bundle.py
```

The repository also runs Hassfest, HACS validation, and the bundled regression
suite through GitHub Actions.

## License

MIT. See [LICENSE](LICENSE).
