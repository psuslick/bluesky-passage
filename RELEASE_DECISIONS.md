# BlueSky Passage 2.1.0 release decisions

These are deliberate v2.1.0 choices. Revisit them explicitly in a future
version rather than treating them as accidental implementation details.

1. **Question: Is an SSD required?**  
   **Decision:** No. A genuine, healthy application-class or high-endurance
   microSD with adequate free space can run every v2.1.0 feature. An SSD/NVMe
   data disk remains a recommended later whole-system resilience upgrade.

2. **Question: Should this require a separate add-on, database server, or
   companion service?**  
   **Decision:** No. Ship one HACS custom integration containing the archive,
   authenticated API, panel, weather adapter, and comparison-path engine.

3. **Question: Where is permanent data stored?**  
   **Decision:** Keep the existing dedicated SQLite archive at
   `/config/bluesky_passage/archive.sqlite3`, outside the HACS-managed component
   directory, with no automatic time purge.

4. **Question: How is microSD write activity bounded?**  
   **Decision:** Poll Garmin every ten minutes, request a rolling 48-hour
   overlap, deduplicate before insert, use SQLite WAL/NORMAL with bounded
   checkpoints, chunk backfill, cache provider data, and run weather/routing
   only after an administrator requests it.

5. **Question: How is Garmin history recovered?**  
   **Decision:** Use a preview-first, resumable, rollbackable Garmin backfill in
   seven-day chunks, with a ten-year per-job ceiling. Garmin remains the first
   choice because it is the source record.

6. **Question: What if Garmin cannot reproduce old v1 points?**  
   **Decision:** Provide a guided, non-mutating Home Assistant Recorder fallback
   in the panel. Read at most 31 days per preview; default to ten days; require
   an explicit preview and store the result as a rollbackable `ha_recorder`
   source batch.

7. **Question: Is passage creation a live tracking switch?**  
   **Decision:** No. Tracking and retention are continuous. Passages are
   editable time annotations that can be created before, during, or after the
   represented travel and can be open-ended without becoming an operating
   state.

8. **Question: How are destination changes handled?**  
   **Decision:** Save effective-time destination versions. Clearing a
   destination is an explicitly previewed removal of that passage's destination
   versions. Either operation makes an older comparison stale until recalculated.

9. **Question: Are vessel specifications mandatory?**  
   **Decision:** No. Accept a partial profile and fall back through polar data,
   observed speed, engine speed, hull-speed estimate, then a labeled generic
   5.5-knot comparison value.

10. **Question: Which weather provider is used for analytics and routing?**  
    **Decision:** Use optional Xweather Weather API credentials on the Home
    Assistant backend only. Request no more than 12 representative positions
    per operation with two concurrent position requests. Cache normalized
    results; represent missing data as gaps; reject model times more than six
    hours from the requested timestamp; expire transient failures after one
    hour.

11. **Question: What is PredictWind used for?**  
    **Decision:** Keep the configured public tracking page as a lazy-loaded
    dashboard view with a direct-link fallback. Do not require PredictWind
    credentials, automate its account, or write routes/history to it.

12. **Question: What does the path creator produce?**  
    **Decision:** Compare direct, port-offset, and starboard-offset geodesic
    corridors. Use four representative model samples per corridor when
    available; otherwise save an explicit great-circle reference. Never label
    the result safe, optimal, or navigable, because it does not avoid land,
    hazards, traffic, restrictions, or warnings.

13. **Question: What happens when route inputs change?**  
    **Decision:** Fingerprint passage boundaries, departure, destination
    version, and vessel-profile revision. Preserve an older summary for audit,
    mark it stale, and suppress its map overlay until recalculated.

14. **Question: What is the dashboard information architecture?**  
    **Decision:** Preserve Home Assistant's left sidebar for global navigation.
    Use four proper top tabs inside BlueSky Passage: Overview, History & charts,
    Passages, and Data & settings.

15. **Question: What is the default analytical view?**  
    **Decision:** Last 24 hours, combined source track with Garmin preferred,
    vessel SOG observed, Xweather wind/waves modeled, and an explicit two-point
    map-range interaction that updates the same graph interval.

16. **Question: How should all-time history behave?**  
    **Decision:** Query the dedicated archive, not a fixed seven-day window.
    Bound browser payloads with shape-preserving downsampling while retaining
    endpoints, gaps, alerts, and local speed extrema.

17. **Question: Who can perform sensitive or data-changing actions?**  
    **Decision:** All authenticated Home Assistant users may view. Only
    administrators may poll manually, contact Xweather, create/edit/delete
    passages, backfill, import, rollback, export, calculate comparisons, change
    profiles, or test notifications.

18. **Question: What are the default units?**  
    **Decision:** Knots for speed and meters for height, configurable in the
    integration options without changing normalized archive values.

19. **Question: Do alerts depend on a passage?**  
    **Decision:** No. Emergency, stale tracking, invalid GPS, new text, source
    failure/recovery, and optional Home Assistant zone changes operate from the
    continuous latest Garmin record.

20. **Question: What backup behavior is required?**  
    **Decision:** Require a full Home Assistant backup before upgrade and verify
    archive count, earliest/latest timestamps, and integrity afterward. Do not
    copy only the live SQLite main file because WAL companion data may be needed
    for a consistent snapshot.

