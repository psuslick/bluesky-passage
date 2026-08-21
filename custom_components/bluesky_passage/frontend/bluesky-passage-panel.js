const DOMAIN = "bluesky_passage";
const SOURCE_COLORS = {
  canonical: "#00bcd4",
  garmin_mapshare: "#03a9f4",
  predictwind_snapshot: "#ff9800",
  gpx_import: "#ab47bc",
};

const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const number = (value, digits = 1, suffix = "") =>
  Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits)}${suffix}` : "—";

const localTime = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
};

const utcTime = (value) => value ? String(value).replace("T", " ").replace("Z", " UTC") : "—";

const bytes = (value) => {
  let size = Number(value || 0);
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
};

class BlueSkyPassagePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._state = null;
    this._query = { points: [], daily_runs: [] };
    this._range = "current_passage";
    this._source = "canonical";
    this._selectedIndex = -1;
    this._center = { lat: 20, lon: -45 };
    this._zoom = 3;
    this._fitNext = true;
    this._choosingDestination = false;
    this._loading = false;
    this._initialized = false;
    this._unsubscribe = null;
    this._timer = null;
    this._resizeObserver = null;
    this._drag = null;
  }

  set hass(value) {
    this._hass = value;
    if (!this._initialized && this.isConnected) this._initialize();
    if (this._initialized) this._renderHeader();
  }

  set panel(value) { this._panel = value; }
  set route(value) { this._route = value; }
  set narrow(value) { this._narrow = value; }

  connectedCallback() {
    if (this._hass && !this._initialized) this._initialize();
    else if (this._hass && this._initialized && !this._timer) this._resume();
  }

  disconnectedCallback() {
    if (this._unsubscribe) this._unsubscribe();
    this._unsubscribe = null;
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
    if (this._resizeObserver) this._resizeObserver.disconnect();
    clearTimeout(this._reloadTimer);
  }

  async _resume() {
    this._timer = setInterval(() => this._renderHeader(), 60000);
    if (this._resizeObserver) {
      this._resizeObserver.observe(this.shadowRoot.getElementById("map"));
    }
    if (!this._unsubscribe) {
      try {
        this._unsubscribe = await this._hass.connection.subscribeEvents(
          () => this._scheduleReload(), "bluesky_passage_data_updated",
        );
      } catch (_error) {}
    }
    await this._load(false);
  }

  async _initialize() {
    this._initialized = true;
    this._renderShell();
    this._bindEvents();
    this._timer = setInterval(() => this._renderHeader(), 60000);
    this._resizeObserver = new ResizeObserver(() => this._renderMap());
    this._resizeObserver.observe(this.shadowRoot.getElementById("map"));
    try {
      this._unsubscribe = await this._hass.connection.subscribeEvents(
        () => this._scheduleReload(),
        "bluesky_passage_data_updated",
      );
    } catch (_error) {
      // Manual refresh and entity-state updates still work if event subscription
      // is unavailable in a future frontend build.
    }
    await this._load(true);
  }

  _scheduleReload() {
    clearTimeout(this._reloadTimer);
    this._reloadTimer = setTimeout(() => this._load(false), 600);
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-height:100%; color:var(--primary-text-color); background:var(--primary-background-color); }
        * { box-sizing:border-box; }
        button,input,select,textarea { font:inherit; }
        button { cursor:pointer; }
        .page { max-width:1700px; margin:0 auto; padding:20px; }
        header { display:flex; justify-content:space-between; align-items:flex-start; gap:18px; margin-bottom:16px; }
        h1 { font-size:28px; line-height:1.1; margin:0 0 6px; }
        h2 { font-size:18px; margin:0 0 14px; }
        h3 { font-size:15px; margin:18px 0 8px; }
        .subtle { color:var(--secondary-text-color); font-size:13px; }
        .actions { display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }
        .button { border:1px solid var(--divider-color); border-radius:10px; padding:9px 13px; color:var(--primary-text-color); background:var(--card-background-color); text-decoration:none; }
        .button:hover { border-color:var(--primary-color); }
        .button.primary { background:var(--primary-color); color:var(--text-primary-color, white); border-color:var(--primary-color); }
        .button.danger { color:var(--error-color,#db4437); }
        .button:disabled { opacity:.45; cursor:not-allowed; }
        .status { display:inline-flex; align-items:center; gap:7px; padding:5px 10px; border-radius:999px; font-weight:600; font-size:13px; background:rgba(76,175,80,.15); color:#4caf50; }
        .status.warn { background:rgba(255,152,0,.15); color:#ff9800; }
        .status.bad { background:rgba(244,67,54,.16); color:#f44336; }
        .notice { display:none; padding:12px 14px; border-radius:10px; margin-bottom:14px; background:rgba(255,152,0,.15); border:1px solid rgba(255,152,0,.5); }
        .notice.show { display:block; }
        .notice.error { background:rgba(244,67,54,.15); border-color:rgba(244,67,54,.5); }
        .metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:14px; }
        .metric,.card { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:13px; box-shadow:var(--ha-card-box-shadow,none); }
        .metric { padding:13px; min-height:78px; }
        .metric .label { color:var(--secondary-text-color); font-size:12px; margin-bottom:7px; }
        .metric .value { font-size:20px; font-weight:650; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .toolbar { display:flex; align-items:end; flex-wrap:wrap; gap:9px; padding:12px; margin-bottom:14px; }
        label { display:flex; flex-direction:column; gap:5px; color:var(--secondary-text-color); font-size:12px; }
        input,select,textarea { min-height:39px; border:1px solid var(--divider-color); border-radius:8px; padding:8px 10px; color:var(--primary-text-color); background:var(--secondary-background-color); }
        textarea { min-height:70px; resize:vertical; }
        .custom-range { display:none; gap:9px; }
        .custom-range.show { display:flex; }
        .map-layout { display:grid; grid-template-columns:minmax(0,2.15fr) minmax(300px,.85fr); gap:14px; margin-bottom:14px; }
        .map-card { padding:0; overflow:hidden; position:relative; }
        #map { position:relative; width:100%; height:590px; overflow:hidden; background:#11212b; touch-action:none; user-select:none; }
        #tiles,#overlay { position:absolute; inset:0; }
        #tiles img { position:absolute; width:256px; height:256px; user-select:none; pointer-events:none; }
        #overlay { width:100%; height:100%; overflow:visible; }
        .map-controls { position:absolute; z-index:4; top:10px; left:10px; display:flex; flex-direction:column; gap:5px; }
        .map-controls button { width:38px; height:38px; border:0; border-radius:8px; background:rgba(30,30,30,.88); color:white; font-size:20px; }
        .map-attribution { position:absolute; right:4px; bottom:3px; z-index:4; background:rgba(255,255,255,.78); color:#222; font-size:10px; padding:2px 4px; }
        .map-attribution a { color:#1565c0; }
        .map-hint { position:absolute; z-index:4; left:58px; top:10px; padding:8px 10px; border-radius:7px; color:white; background:rgba(0,0,0,.72); display:none; }
        .map-hint.show { display:block; }
        .detail { padding:16px; min-height:590px; overflow:auto; }
        .detail-grid { display:grid; grid-template-columns:1fr 1fr; gap:9px 14px; }
        .detail-item { min-width:0; }
        .detail-item.wide { grid-column:1/-1; }
        .detail-label { font-size:11px; color:var(--secondary-text-color); text-transform:uppercase; letter-spacing:.04em; }
        .detail-value { margin-top:3px; overflow-wrap:anywhere; }
        blockquote { margin:5px 0 0; padding:8px 10px; border-left:3px solid var(--primary-color); background:var(--secondary-background-color); white-space:pre-wrap; }
        .detail-actions { display:flex; flex-wrap:wrap; gap:7px; margin-top:14px; }
        .legend { position:absolute; z-index:4; bottom:20px; left:10px; padding:7px 9px; border-radius:7px; background:rgba(0,0,0,.7); color:#fff; font-size:11px; }
        .legend span { margin-right:9px; white-space:nowrap; }
        .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:4px; }
        .charts { display:grid; grid-template-columns:repeat(2,1fr); gap:14px; margin-bottom:14px; }
        .chart { padding:14px; min-height:238px; overflow:hidden; }
        .chart svg { width:100%; height:180px; display:block; }
        .chart-empty { height:175px; display:grid; place-items:center; color:var(--secondary-text-color); }
        .two-col { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px; }
        .section { padding:16px; }
        .form-grid { display:grid; grid-template-columns:repeat(4,minmax(120px,1fr)); gap:10px; }
        .form-grid .wide { grid-column:span 2; }
        .row { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-top:12px; }
        .table-wrap { overflow:auto; }
        table { width:100%; border-collapse:collapse; font-size:13px; }
        th,td { text-align:left; padding:8px; border-bottom:1px solid var(--divider-color); vertical-align:top; }
        th { color:var(--secondary-text-color); font-weight:600; }
        .admin-only.hidden { display:none; }
        .safety { padding:13px 15px; border-left:4px solid #ff9800; font-size:13px; }
        code { background:var(--secondary-background-color); padding:2px 5px; border-radius:4px; }
        footer { color:var(--secondary-text-color); font-size:12px; padding:4px 2px 20px; }
        @media (max-width:1100px) {
          .metrics { grid-template-columns:repeat(3,1fr); }
          .charts { grid-template-columns:1fr; }
          .map-layout { grid-template-columns:1fr; }
          .detail { min-height:auto; }
          .two-col { grid-template-columns:1fr; }
        }
        @media (max-width:650px) {
          .page { padding:12px; }
          header { flex-direction:column; }
          .actions { justify-content:flex-start; }
          .metrics { grid-template-columns:repeat(2,1fr); }
          #map { height:440px; }
          .form-grid { grid-template-columns:1fr 1fr; }
          .form-grid .wide { grid-column:1/-1; }
          .custom-range.show { flex-direction:column; }
        }
      </style>
      <div class="page">
        <header>
          <div><h1>BlueSky Passage</h1><div id="header-subtitle" class="subtle">Loading local archive…</div></div>
          <div class="actions">
            <span id="status" class="status">Loading</span>
            <a id="garmin-link" class="button" href="#" target="_blank" rel="noopener">Garmin MapShare</a>
            <a id="predictwind-link" class="button" href="#" target="_blank" rel="noopener">PredictWind</a>
            <button id="refresh" class="button primary">Refresh Garmin</button>
            <button id="test-notification" class="button admin-only">Test notification</button>
            <button id="options" class="button admin-only">Alert options</button>
          </div>
        </header>
        <div id="notice" class="notice"></div>
        <section id="metrics" class="metrics"></section>
        <section class="toolbar card">
          <label>Displayed period
            <select id="range">
              <option value="current_passage">Current passage</option>
              <option value="1d">Last 24 hours</option><option value="3d">Last 3 days</option>
              <option value="7d">Last 7 days</option><option value="30d">Last 30 days</option>
              <option value="1y">Last year</option><option value="all">All time</option>
              <option value="custom">Custom dates</option>
            </select>
          </label>
          <label>Source
            <select id="source"><option value="canonical">Combined track (Garmin preferred)</option><option value="all">All raw sources</option><option value="garmin_mapshare">Garmin live</option><option value="predictwind_snapshot">PredictWind import</option><option value="gpx_import">GPX import</option></select>
          </label>
          <div id="custom-range" class="custom-range">
            <label>Start (your local time)<input id="range-start" type="datetime-local"></label>
            <label>End (your local time)<input id="range-end" type="datetime-local"></label>
          </div>
          <button id="load-range" class="button">Load / fit map</button>
          <label class="admin-only">Export
            <select id="export-format"><option value="csv">CSV</option><option value="geojson">GeoJSON</option><option value="gpx">GPX</option></select>
          </label>
          <button id="export" class="button admin-only">Download range</button>
          <span id="point-count" class="subtle"></span>
        </section>
        <div class="map-layout">
          <section class="map-card card">
            <div id="map">
              <div id="tiles"></div><svg id="overlay"></svg>
              <div class="map-controls"><button id="zoom-in" title="Zoom in">+</button><button id="zoom-out" title="Zoom out">−</button><button id="fit" title="Fit displayed data">⌖</button></div>
              <div id="map-hint" class="map-hint">Select the exact destination point on the map</div>
              <div id="legend" class="legend"></div>
              <div class="map-attribution">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a></div>
            </div>
          </section>
          <aside id="detail" class="detail card"></aside>
        </div>
        <section class="charts">
          <div class="chart card"><h2>Speed over ground</h2><div id="speed-chart"></div></div>
          <div class="chart card"><h2>Destination VMC trend</h2><div id="vmc-chart"></div></div>
          <div class="chart card"><h2>Recorded distance</h2><div id="distance-chart"></div></div>
          <div class="chart card"><h2>Daily recorded run (UTC)</h2><div id="daily-chart"></div></div>
          <div class="chart card"><h2>Report gaps</h2><div id="gap-chart"></div></div>
        </section>
        <section class="two-col">
          <div class="section card"><h2>Passage and destination</h2><div id="passage-view"></div><div id="passage-admin" class="admin-only"></div></div>
          <div class="section card"><h2>Archive and source health</h2><div id="archive-view"></div></div>
        </section>
        <section id="data-tools" class="section card admin-only">
          <h2>Admin data tools</h2>
          <p class="subtle">Imports are manual, hashed, source-labelled, and rollbackable. Live Garmin records are never deleted by an import rollback.</p>
          <div class="form-grid">
            <label class="wide">Historical track file<input id="history-file" type="file" accept=".json,.geojson,.gpx,.xml,.html,.htm"></label>
            <label>History source<select id="history-source"><option value="predictwind_snapshot">PredictWind snapshot</option><option value="gpx_import">GPX track</option></select></label>
            <div class="row"><button id="import-history" class="button">Import history</button></div>
            <label class="wide">Planned route GPX<input id="route-file" type="file" accept=".gpx,.xml"></label>
            <label>Route label<input id="route-label" value="Planned route"></label>
            <div class="row"><button id="import-route" class="button">Attach route to passage</button></div>
          </div>
          <div id="import-progress" class="subtle"></div>
          <div id="imports"></div>
        </section>
        <section class="safety card">
          <strong>Safety:</strong> BlueSky Passage, its direct-reference line, ETA, and notifications are supplementary only. The direct line is not a navigational route and does not account for hazards, weather, currents, or routing constraints. Garmin/inReach emergency channels remain authoritative.
        </section>
        <footer>Vessel records and the archive stay inside Home Assistant. Garmin/PredictWind access and online OpenStreetMap tiles require the internet; the tile service can see the requesting IP address and viewed map area.</footer>
      </div>`;
  }

  _bindEvents() {
    const root = this.shadowRoot;
    root.getElementById("refresh").addEventListener("click", () => this._refresh());
    root.getElementById("test-notification").addEventListener("click", () => this._testNotification());
    root.getElementById("options").addEventListener("click", () => this._navigate("/config/integrations/integration/bluesky_passage"));
    root.getElementById("range").addEventListener("change", (event) => {
      this._range = event.target.value;
      root.getElementById("custom-range").classList.toggle("show", this._range === "custom");
    });
    root.getElementById("source").addEventListener("change", (event) => { this._source = event.target.value; });
    root.getElementById("load-range").addEventListener("click", () => this._load(true));
    root.getElementById("export").addEventListener("click", () => this._export());
    root.getElementById("zoom-in").addEventListener("click", () => { this._zoom = Math.min(18, this._zoom + 1); this._renderMap(); });
    root.getElementById("zoom-out").addEventListener("click", () => { this._zoom = Math.max(1, this._zoom - 1); this._renderMap(); });
    root.getElementById("fit").addEventListener("click", () => { this._fitMap(); this._renderMap(); });
    root.getElementById("import-history").addEventListener("click", () => this._importHistory());
    root.getElementById("import-route").addEventListener("click", () => this._importRoute());

    const map = root.getElementById("map");
    map.addEventListener("pointerdown", (event) => this._mapPointerDown(event));
    map.addEventListener("pointermove", (event) => this._mapPointerMove(event));
    map.addEventListener("pointerup", (event) => this._mapPointerUp(event));
    map.addEventListener("pointercancel", () => { this._drag = null; });
    map.addEventListener("wheel", (event) => {
      event.preventDefault();
      this._zoom = Math.max(1, Math.min(18, this._zoom + (event.deltaY < 0 ? 1 : -1)));
      this._renderMap();
    }, { passive: false });

    root.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const actions = {
        "start-passage": () => this._startPassage(),
        "set-destination": () => this._setDestination(),
        "choose-destination": () => this._chooseDestination(),
        "end-passage": () => this._endPassage(),
        "delete-passage": () => this._deletePassage(Number(button.dataset.id)),
        "rollback-import": () => this._rollbackImport(Number(button.dataset.id)),
        "previous-point": () => this._selectRelative(-1),
        "next-point": () => this._selectRelative(1),
        "copy-point": () => this._copySelected(),
      };
      if (actions[button.dataset.action]) actions[button.dataset.action]();
    });
    root.addEventListener("change", (event) => {
      if (event.target.id === "saved-destination") this._useSavedDestination(event.target.value);
    });
  }

  async _call(type, data = {}) {
    return this._hass.callWS({ type: `${DOMAIN}/${type}`, ...data });
  }

  _setBusy(busy, message = "") {
    this._loading = busy;
    this.shadowRoot.getElementById("refresh").disabled = busy;
    if (message) this._showNotice(message, false);
  }

  _showNotice(message, error = false) {
    const notice = this.shadowRoot.getElementById("notice");
    notice.textContent = message || "";
    notice.className = `notice${message ? " show" : ""}${error ? " error" : ""}`;
  }

  _customRange() {
    if (this._range !== "custom") return {};
    const start = this.shadowRoot.getElementById("range-start").value;
    const end = this.shadowRoot.getElementById("range-end").value;
    if (!start || !end) throw new Error("Choose both custom start and end dates.");
    return { start_utc: new Date(start).toISOString(), end_utc: new Date(end).toISOString() };
  }

  async _load(fit = false) {
    if (this._loading) return;
    this._setBusy(true);
    try {
      const custom = this._customRange();
      const [state, query] = await Promise.all([
        this._call("state"),
        this._call("points", { range: this._range, source: this._source, max_points: 4000, ...custom }),
      ]);
      this._state = state;
      this._query = query;
      if (this._selectedIndex >= query.points.length) this._selectedIndex = -1;
      this._renderAll();
      if (fit) this._fitMap();
      this._renderMap();
      this._showNotice("");
    } catch (error) {
      this._showNotice(error.message || String(error), true);
    } finally {
      this._setBusy(false);
    }
  }

  async _refresh() {
    if (!this._hass.user?.is_admin) return;
    this._setBusy(true, "Requesting one Garmin poll. The report timestamp changes only if Garmin has a newer point.");
    try {
      await this._call("refresh");
      this._setBusy(false);
      await this._load(false);
    } catch (error) {
      this._showNotice(error.message || String(error), true);
    } finally {
      this._setBusy(false);
    }
  }

  _renderAll() {
    const admin = Boolean(this._hass.user?.is_admin);
    this.shadowRoot.querySelectorAll(".admin-only").forEach((node) => node.classList.toggle("hidden", !admin));
    this.shadowRoot.getElementById("refresh").style.display = admin ? "" : "none";
    this._renderHeader();
    this._renderMetrics();
    this._renderDetail();
    this._renderCharts();
    this._renderPassage();
    this._renderArchive();
    this._renderImports();
    const count = this._query;
    this.shadowRoot.getElementById("point-count").textContent = count.decimated
      ? `${count.returned.toLocaleString()} displayed of ${count.total_matching.toLocaleString()} matching records`
      : `${(count.returned || 0).toLocaleString()} records`;
  }

  _renderHeader() {
    if (!this._state) return;
    const runtime = this._state.runtime || {};
    const status = this.shadowRoot.getElementById("status");
    status.textContent = runtime.status || "Unknown";
    const latest = this._state.latest;
    const computedAge = latest?.recorded_at_utc
      ? Math.max(0, (Date.now() - new Date(latest.recorded_at_utc).getTime()) / 60000)
      : null;
    const computedStale = runtime.monitoring && (computedAge == null || computedAge > runtime.stale_minutes);
    const bad = runtime.status === "EMERGENCY" || runtime.status === "Source unavailable";
    const warn = computedStale || runtime.gps_problem;
    if (!bad && computedStale) status.textContent = "Tracking stale";
    status.className = `status${bad ? " bad" : warn ? " warn" : ""}`;
    const age = computedAge == null ? "no report" : `${number(computedAge, 0)} min old`;
    const passage = this._state.passage;
    this.shadowRoot.getElementById("garmin-link").href = this._state.links?.garmin_mapshare || "#";
    const predictwindLink = this.shadowRoot.getElementById("predictwind-link");
    const predictwindUrl = this._state.links?.predictwind || "";
    predictwindLink.href = predictwindUrl || "#";
    predictwindLink.style.display = predictwindUrl ? "" : "none";
    this.shadowRoot.getElementById("header-subtitle").textContent =
      `${passage ? `${passage.name} · ${passage.status}` : "No active passage"} · ${latest ? age : "archive waiting for first point"}`;
  }

  _renderMetrics() {
    const state = this._state;
    const latest = state.latest || {};
    const metrics = state.metrics || {};
    const destination = state.destination;
    const items = [
      ["Latest report", localTime(latest.recorded_at_utc)],
      ["SOG", number(latest.sog_kn, 1, " kn")],
      ["COG true", number(latest.cog_true, 1, "°")],
      [destination ? `Range to ${destination.name}` : "Destination range", number(metrics.range_nm, 1, " nmi")],
      ["Closing rate", number(metrics.closing_rate_kn, 1, " kn")],
      ["ETA", metrics.eta_utc ? localTime(metrics.eta_utc) : (metrics.eta_status || "—")],
      ["Light at ETA", metrics.daylight_at_eta?.state || "—"],
    ];
    this.shadowRoot.getElementById("metrics").innerHTML = items.map(([label, value]) =>
      `<div class="metric"><div class="label">${esc(label)}</div><div class="value" title="${esc(value)}">${esc(value)}</div></div>`
    ).join("");
  }

  _renderDetail() {
    const detail = this.shadowRoot.getElementById("detail");
    const points = this._query.points || [];
    const point = points[this._selectedIndex];
    if (!point) {
      detail.innerHTML = `<h2>Selected record</h2><p class="subtle">Select a track dot to show the data actually associated with that record. Current text is never copied onto older points.</p>`;
      return;
    }
    const destination = this._state.destination;
    let pointRange = "—";
    let pointBearing = "—";
    if (destination && point.latitude != null && point.longitude != null) {
      pointRange = number(this._haversine(point.latitude, point.longitude, destination.latitude, destination.longitude), 2, " nmi");
      pointBearing = number(this._bearing(point.latitude, point.longitude, destination.latitude, destination.longitude), 1, "° true");
    }
    const field = (label, value, wide = false) => `<div class="detail-item${wide ? " wide" : ""}"><div class="detail-label">${esc(label)}</div><div class="detail-value">${esc(value ?? "—")}</div></div>`;
    detail.innerHTML = `
      <h2>Selected record ${this._selectedIndex + 1} of ${points.length}</h2>
      <div class="detail-grid">
        ${field("Local time", localTime(point.recorded_at_utc), true)}
        ${field("UTC", utcTime(point.recorded_at_utc), true)}
        ${field("Source", point.source)}${field("Source event ID", point.source_event_id)}
        ${field("Latitude", number(point.latitude, 6))}${field("Longitude", number(point.longitude, 6))}
        ${field("SOG", number(point.sog_kn, 1, " kn"))}${field("COG true", number(point.cog_true, 1, "°"))}
        ${field("Elevation", number(point.elevation_m, 1, " m"))}${field("Valid GPS fix", point.valid_gps_fix == null ? "Unknown" : point.valid_gps_fix ? "Yes" : "No")}
        ${field("Emergency", point.in_emergency == null ? "Unknown" : point.in_emergency ? "YES" : "No")}${field("Gap from prior", number(point.minutes_from_prior, 1, " min"))}
        ${field("Distance from prior", number(point.distance_from_prior_nm, 3, " nmi"))}${field("Cumulative displayed-range track", number(point.cumulative_distance_nm, 2, " nmi"))}
        ${field("Destination VMC", number(point.vmc_kn, 1, " kn"))}${field("Archived destination range", number(point.destination_range_nm, 2, " nmi"))}
        ${field("Range to current destination", pointRange)}${field("Bearing to current destination", pointBearing)}
        ${field("Quality flags", (point.quality_flags || []).join(", ") || "None", true)}
        <div class="detail-item wide"><div class="detail-label">Associated event</div><blockquote>${esc(point.event_text || "No event text on this record")}</blockquote></div>
        <div class="detail-item wide"><div class="detail-label">Associated message</div><blockquote>${esc(point.message_text || "No message on this record")}</blockquote></div>
      </div>
      <div class="detail-actions">
        <button class="button" data-action="previous-point" ${this._selectedIndex <= 0 ? "disabled" : ""}>Previous</button>
        <button class="button" data-action="next-point" ${this._selectedIndex >= points.length - 1 ? "disabled" : ""}>Next</button>
        <button class="button" data-action="copy-point">Copy coordinates</button>
        ${point.latitude != null && point.longitude != null ? `<a class="button" href="https://www.openstreetmap.org/?mlat=${encodeURIComponent(point.latitude)}&mlon=${encodeURIComponent(point.longitude)}#map=12/${encodeURIComponent(point.latitude)}/${encodeURIComponent(point.longitude)}" target="_blank" rel="noopener">Open map</a>` : ""}
      </div>`;
  }

  _renderPassage() {
    const state = this._state;
    const passage = state.passage;
    const destination = state.destination;
    const metrics = state.metrics || {};
    const view = this.shadowRoot.getElementById("passage-view");
    if (!passage) {
      view.innerHTML = `<p>No passage is active. The global archive still records live points. Start a passage to establish a departure time and optional destination.</p>`;
    } else {
      view.innerHTML = `
        <div class="detail-grid">
          <div><div class="detail-label">Passage</div><div class="detail-value">${esc(passage.name)}</div></div>
          <div><div class="detail-label">Status</div><div class="detail-value">${esc(passage.status)}</div></div>
          <div><div class="detail-label">Started</div><div class="detail-value">${esc(localTime(passage.started_at_utc))}</div></div>
          <div><div class="detail-label">Destination</div><div class="detail-value">${esc(destination?.name || "Track-only")}</div></div>
          <div><div class="detail-label">Recorded-track distance</div><div class="detail-value">${esc(number(metrics.recorded_track_nm, 2, " nmi"))}</div></div>
          <div><div class="detail-label">ETA method</div><div class="detail-value">${esc(metrics.eta_status || "—")}</div></div>
          <div><div class="detail-label">Direct-reference progress</div><div class="detail-value">${esc(number(metrics.direct_progress_nm, 1, " nmi"))} (${esc(number(metrics.direct_progress_percent, 1, "%"))})</div></div>
          <div><div class="detail-label">Cross-track from direct reference</div><div class="detail-value">${esc(number(metrics.cross_track_nm, 1, " nmi"))} ${esc(metrics.cross_track_side || "")}</div></div>
          <div><div class="detail-label">Light at ETA</div><div class="detail-value">${esc(metrics.daylight_at_eta?.state || "—")}</div></div>
          <div><div class="detail-label">Next solar event (UTC)</div><div class="detail-value">${esc(metrics.daylight_at_eta?.next_event || "—")} ${esc(utcTime(metrics.daylight_at_eta?.next_event_utc))}</div></div>
        </div>
        ${destination ? `<p class="subtle">The straight line and bearing to ${esc(destination.name)} are labelled direct reference only—not a navigational route. Arrival radius: ${esc(number(destination.arrival_radius_nm, 1, " nmi"))}.</p>` : ""}`;
    }
    if (!this._hass.user?.is_admin) return;
    const admin = this.shadowRoot.getElementById("passage-admin");
    const nowLocal = this._localInputValue(new Date());
    const saved = (state.destinations || []).map((item) =>
      `<option value="${item.id}">${esc(item.name)} · ${number(item.latitude, 4)}, ${number(item.longitude, 4)}</option>`
    ).join("");
    const destinationFields = `
      <h3>${passage ? "Set or revise destination" : "Optional starting destination"}</h3>
      <div class="form-grid">
        <label class="wide">Saved destination<select id="saved-destination"><option value="">Choose saved…</option>${saved}</select></label>
        <label class="wide">Destination name<input id="destination-name" value="${esc(destination?.name || "")}" placeholder="Choose an exact harbor or waypoint"></label>
        <label>Latitude<input id="destination-latitude" inputmode="decimal" value="${esc(destination?.latitude ?? "")}"></label>
        <label>Longitude<input id="destination-longitude" inputmode="decimal" value="${esc(destination?.longitude ?? "")}"></label>
        <label>Arrival radius (nmi)<input id="arrival-radius" type="number" min="0.1" max="100" step="0.1" value="${esc(destination?.arrival_radius_nm ?? 2)}"></label>
        <div class="row"><button class="button" data-action="choose-destination">Choose on map</button>${passage ? `<button class="button primary" data-action="set-destination">Save new destination version</button>` : ""}</div>
      </div>`;
    if (!passage) {
      admin.innerHTML = `
        <h3>Start a passage</h3>
        <div class="form-grid"><label class="wide">Passage name<input id="passage-name" value="Passage ${new Date().toLocaleDateString()}" maxlength="100"></label><label class="wide">Start time (your local time)<input id="passage-start" type="datetime-local" value="${nowLocal}"></label></div>
        ${destinationFields}<div class="row"><button class="button primary" data-action="start-passage">Start passage</button><span class="subtle">Leave destination blank for track-only mode.</span></div>`;
    } else {
      admin.innerHTML = `${destinationFields}<div class="row"><button class="button danger" data-action="end-passage">End passage manually</button><span class="subtle">Reaching the arrival radius marks arrived but never ends the passage automatically.</span></div>`;
    }
    const completed = (state.passages || []).filter((item) => item.status === "completed");
    if (completed.length) {
      admin.insertAdjacentHTML("beforeend", `<h3>Completed passage metadata</h3><div class="table-wrap"><table><thead><tr><th>Name</th><th>Started</th><th>Destination</th><th></th></tr></thead><tbody>${completed.map((item) => `<tr><td>${esc(item.name)}</td><td>${esc(localTime(item.started_at_utc))}</td><td>${esc(item.destination_name || "Track-only")}</td><td><button class="button danger" data-action="delete-passage" data-id="${item.id}">Delete metadata</button></td></tr>`).join("")}</tbody></table></div><p class="subtle">Deleting passage metadata does not delete global track points.</p>`);
    }
  }

  _renderArchive() {
    const archive = this._state.archive || {};
    const runtime = this._state.runtime || {};
    const counts = Object.entries(archive.counts_by_source || {}).map(([source, count]) => `${source}: ${Number(count).toLocaleString()}`).join(" · ") || "No records";
    this.shadowRoot.getElementById("archive-view").innerHTML = `
      <div class="detail-grid">
        <div><div class="detail-label">Archive records</div><div class="detail-value">${esc(Number(archive.total_points || 0).toLocaleString())}</div></div>
        <div><div class="detail-label">Database size</div><div class="detail-value">${esc(bytes(archive.database_bytes))}</div></div>
        <div><div class="detail-label">Earliest record</div><div class="detail-value">${esc(localTime(archive.first_recorded_at_utc))}</div></div>
        <div><div class="detail-label">Latest record</div><div class="detail-value">${esc(localTime(archive.last_recorded_at_utc))}</div></div>
        <div><div class="detail-label">Integrity</div><div class="detail-value">${esc(archive.integrity || "unknown")}</div></div>
        <div><div class="detail-label">Garmin poll</div><div class="detail-value">${runtime.source_available ? "Available" : "Unavailable"}</div></div>
        <div class="detail-item wide"><div class="detail-label">By source</div><div class="detail-value">${esc(counts)}</div></div>
        <div class="detail-item wide"><div class="detail-label">Last successful poll</div><div class="detail-value">${esc(localTime(runtime.last_poll_success_utc))}</div></div>
      </div>
      <p class="subtle">Stored indefinitely unless an administrator deliberately removes the integration archive. Home Assistant Recorder purge settings do not purge this database.</p>`;
  }

  _renderImports() {
    if (!this._hass.user?.is_admin) return;
    const imports = this._state.imports || [];
    const target = this.shadowRoot.getElementById("imports");
    if (!imports.length) { target.innerHTML = ""; return; }
    target.innerHTML = `<h3>Import batches</h3><div class="table-wrap"><table><thead><tr><th>File/source</th><th>Status</th><th>Rows</th><th>Imported</th><th></th></tr></thead><tbody>${imports.map((item) => `<tr><td>${esc(item.filename)}<br><span class="subtle">${esc(item.source)}</span></td><td>${esc(item.status)}</td><td>${item.rows_inserted.toLocaleString()} / ${item.rows_seen.toLocaleString()}</td><td>${esc(localTime(item.imported_at_utc))}</td><td>${item.status !== "rolled_back" ? `<button class="button danger" data-action="rollback-import" data-id="${item.id}">Rollback</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  _renderCharts() {
    const points = this._query.points || [];
    this.shadowRoot.getElementById("speed-chart").innerHTML = this._lineChart(points, "sog_kn", "kn", false);
    this.shadowRoot.getElementById("vmc-chart").innerHTML = this._lineChart(points, "vmc_kn", "kn", false);
    this.shadowRoot.getElementById("distance-chart").innerHTML = this._lineChart(points, "cumulative_distance_nm", "nmi", true);
    this.shadowRoot.getElementById("daily-chart").innerHTML = this._barChart(this._query.daily_runs || []);
    this.shadowRoot.getElementById("gap-chart").innerHTML = this._lineChart(points, "minutes_from_prior", "min", true);
  }

  _lineChart(points, key, unit, zeroBased) {
    const valid = points.filter((point) => Number.isFinite(Number(point[key])) && point.recorded_at_utc);
    if (valid.length < 2) return `<div class="chart-empty">More records are needed.</div>`;
    const width = 720, height = 180, left = 48, right = 12, top = 12, bottom = 30;
    const times = valid.map((point) => new Date(point.recorded_at_utc).getTime());
    const values = valid.map((point) => Number(point[key]));
    const minTime = Math.min(...times), maxTime = Math.max(...times);
    const minValue = zeroBased ? 0 : Math.min(0, ...values);
    const maxValue = Math.max(...values, 1);
    const x = (time) => left + (time - minTime) / Math.max(1, maxTime - minTime) * (width - left - right);
    const y = (value) => height - bottom - (value - minValue) / Math.max(.001, maxValue - minValue) * (height - top - bottom);
    const sources = [...new Set(valid.map((point) => point.display_track || point.source))];
    const lines = sources.map((source) => {
      const sourcePoints = valid.filter((point) => (point.display_track || point.source) === source);
      let path = "";
      sourcePoints.forEach((point, index) => {
        const command = index === 0 || point.break_before ? "M" : "L";
        path += `${command}${x(new Date(point.recorded_at_utc).getTime()).toFixed(1)},${y(Number(point[key])).toFixed(1)} `;
      });
      return `<path d="${path}" fill="none" stroke="${SOURCE_COLORS[source] || "#78909c"}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
    }).join("");
    const grid = [0, .5, 1].map((fraction) => {
      const value = minValue + (maxValue - minValue) * (1 - fraction);
      const yy = top + fraction * (height - top - bottom);
      return `<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" stroke="var(--divider-color)"/><text x="${left-6}" y="${yy+4}" text-anchor="end" fill="var(--secondary-text-color)" font-size="11">${value.toFixed(1)}</text>`;
    }).join("");
    return `<svg viewBox="0 0 ${width} ${height}" role="img">${grid}${lines}<text x="${left}" y="${height-7}" fill="var(--secondary-text-color)" font-size="11">${esc(new Date(minTime).toLocaleDateString())}</text><text x="${width-right}" y="${height-7}" text-anchor="end" fill="var(--secondary-text-color)" font-size="11">${esc(new Date(maxTime).toLocaleDateString())}</text><text x="5" y="12" fill="var(--secondary-text-color)" font-size="11">${esc(unit)}</text></svg>`;
  }

  _barChart(runs) {
    let data = runs.filter((item) => Number(item.distance_nm) > 0);
    if (this._source !== "all") data = data.filter((item) => item.source === this._source);
    else if (data.some((item) => item.source === "garmin_mapshare")) data = data.filter((item) => item.source === "garmin_mapshare");
    data = data.slice(-31);
    if (!data.length) return `<div class="chart-empty">More movement history is needed.</div>`;
    const width = 720, height = 180, left = 45, right = 10, top = 12, bottom = 34;
    const max = Math.max(...data.map((item) => Number(item.distance_nm)), 1);
    const slot = (width - left - right) / data.length;
    const bars = data.map((item, index) => {
      const barHeight = Number(item.distance_nm) / max * (height - top - bottom);
      const x = left + index * slot + 1;
      const y = height - bottom - barHeight;
      return `<rect x="${x}" y="${y}" width="${Math.max(1,slot-2)}" height="${barHeight}" fill="${SOURCE_COLORS[item.source] || "#03a9f4"}"><title>${esc(item.date_utc)}: ${Number(item.distance_nm).toFixed(1)} nmi</title></rect>`;
    }).join("");
    return `<svg viewBox="0 0 ${width} ${height}" role="img"><line x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}" stroke="var(--divider-color)"/>${bars}<text x="${left-5}" y="${top+5}" text-anchor="end" fill="var(--secondary-text-color)" font-size="11">${max.toFixed(1)}</text><text x="${left}" y="${height-9}" fill="var(--secondary-text-color)" font-size="11">${esc(data[0].date_utc)}</text><text x="${width-right}" y="${height-9}" text-anchor="end" fill="var(--secondary-text-color)" font-size="11">${esc(data.at(-1).date_utc)}</text><text x="5" y="12" fill="var(--secondary-text-color)" font-size="11">nmi</text></svg>`;
  }

  _project(lat, lon, zoom = this._zoom) {
    const scale = 256 * 2 ** zoom;
    const safeLat = Math.max(-85.05112878, Math.min(85.05112878, Number(lat)));
    const sinLat = Math.sin(safeLat * Math.PI / 180);
    return {
      x: (Number(lon) + 180) / 360 * scale,
      y: (0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * scale,
    };
  }

  _unproject(x, y, zoom = this._zoom) {
    const scale = 256 * 2 ** zoom;
    const lon = x / scale * 360 - 180;
    const n = Math.PI - 2 * Math.PI * y / scale;
    const lat = 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    return { lat, lon };
  }

  _screen(lat, lon, width, height) {
    const point = this._project(lat, lon);
    const center = this._project(this._center.lat, this._center.lon);
    const world = 256 * 2 ** this._zoom;
    let dx = point.x - center.x;
    if (dx > world / 2) dx -= world;
    if (dx < -world / 2) dx += world;
    return { x: width / 2 + dx, y: height / 2 + point.y - center.y };
  }

  _renderMap() {
    if (!this._state) return;
    const map = this.shadowRoot.getElementById("map");
    const width = map.clientWidth, height = map.clientHeight;
    if (!width || !height) return;
    const centerWorld = this._project(this._center.lat, this._center.lon);
    const tileSize = 256, count = 2 ** this._zoom;
    const minTileX = Math.floor((centerWorld.x - width / 2) / tileSize);
    const maxTileX = Math.floor((centerWorld.x + width / 2) / tileSize);
    const minTileY = Math.max(0, Math.floor((centerWorld.y - height / 2) / tileSize));
    const maxTileY = Math.min(count - 1, Math.floor((centerWorld.y + height / 2) / tileSize));
    const tiles = [];
    for (let x = minTileX; x <= maxTileX; x += 1) {
      for (let y = minTileY; y <= maxTileY; y += 1) {
        const wrappedX = ((x % count) + count) % count;
        const left = width / 2 + x * tileSize - centerWorld.x;
        const top = height / 2 + y * tileSize - centerWorld.y;
        // OpenStreetMap's public tile policy requires a valid browser Referer.
        // Send only the Home Assistant origin, never this panel's path.
        tiles.push(`<img alt="" draggable="false" referrerpolicy="origin" src="https://tile.openstreetmap.org/${this._zoom}/${wrappedX}/${y}.png" style="left:${left}px;top:${top}px">`);
      }
    }
    this.shadowRoot.getElementById("tiles").innerHTML = tiles.join("");

    const points = this._query.points || [];
    const paths = [];
    for (const source of [...new Set(points.map((point) => point.display_track || point.source))]) {
      let path = "";
      let hasSegment = false;
      points.forEach((point) => {
        if ((point.display_track || point.source) !== source || point.latitude == null || point.longitude == null) return;
        const pixel = this._screen(point.latitude, point.longitude, width, height);
        const command = !hasSegment || point.break_before ? "M" : "L";
        path += `${command}${pixel.x.toFixed(1)},${pixel.y.toFixed(1)} `;
        hasSegment = true;
      });
      if (path) paths.push(`<path d="${path}" fill="none" stroke="${SOURCE_COLORS[source] || "#78909c"}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" opacity=".85"/>`);
    }
    const route = this._state.planned_route?.coordinates || [];
    if (route.length > 1) {
      const routePath = route.map((coordinate, index) => {
        const pixel = this._screen(coordinate[1], coordinate[0], width, height);
        return `${index ? "L" : "M"}${pixel.x.toFixed(1)},${pixel.y.toFixed(1)}`;
      }).join(" ");
      paths.push(`<path d="${routePath}" fill="none" stroke="#66bb6a" stroke-width="3" stroke-dasharray="9 5"/>`);
    }
    const destination = this._state.destination;
    if (destination) {
      const valid = points.filter((point) => point.latitude != null && point.longitude != null && point.source === "garmin_mapshare");
      const startPoint = this._state.start_point || valid[0];
      if (startPoint?.latitude != null && startPoint?.longitude != null) {
        const first = this._screen(startPoint.latitude, startPoint.longitude, width, height);
        const dest = this._screen(destination.latitude, destination.longitude, width, height);
        paths.push(`<path d="M${first.x.toFixed(1)},${first.y.toFixed(1)} L${dest.x.toFixed(1)},${dest.y.toFixed(1)}" fill="none" stroke="#ffd54f" stroke-width="2" stroke-dasharray="4 7"><title>Direct Reference—Not a Navigational Route</title></path>`);
        paths.push(`<path d="M${dest.x},${dest.y-10} L${dest.x+10},${dest.y} L${dest.x},${dest.y+10} L${dest.x-10},${dest.y} Z" fill="#ffd54f" stroke="#111" stroke-width="2"><title>${esc(destination.name)}</title></path>`);
      }
    }
    const markers = points.map((point, index) => {
      if (point.latitude == null || point.longitude == null) return "";
      const pixel = this._screen(point.latitude, point.longitude, width, height);
      if (pixel.x < -10 || pixel.x > width + 10 || pixel.y < -10 || pixel.y > height + 10) return "";
      const selected = index === this._selectedIndex;
      return `<circle class="track-point" data-index="${index}" cx="${pixel.x.toFixed(1)}" cy="${pixel.y.toFixed(1)}" r="${selected ? 7 : 3.5}" fill="${SOURCE_COLORS[point.source] || "#78909c"}" stroke="${selected ? "white" : "#102027"}" stroke-width="${selected ? 2 : 1}" tabindex="0"><title>${esc(localTime(point.recorded_at_utc))}</title></circle>`;
    }).join("");
    const overlay = this.shadowRoot.getElementById("overlay");
    overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
    overlay.innerHTML = paths.join("") + markers;
    overlay.querySelectorAll(".track-point").forEach((marker) => {
      const choose = (event) => { event.stopPropagation(); this._selectedIndex = Number(marker.dataset.index); this._renderDetail(); this._renderMap(); };
      marker.addEventListener("click", choose);
      marker.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") choose(event); });
    });
    const sources = [...new Set(points.map((point) => point.source))];
    this.shadowRoot.getElementById("legend").innerHTML = sources.map((source) => `<span><i class="dot" style="background:${SOURCE_COLORS[source] || "#78909c"}"></i>${esc(source)}</span>`).join("") + (destination ? `<span><i class="dot" style="background:#ffd54f"></i>Direct reference</span>` : "") + (route.length ? `<span><i class="dot" style="background:#66bb6a"></i>Planned route</span>` : "");
  }

  _fitMap() {
    const coordinates = (this._query.points || [])
      .filter((point) => point.latitude != null && point.longitude != null)
      .map((point) => [Number(point.latitude), Number(point.longitude)]);
    if (this._state?.destination) coordinates.push([Number(this._state.destination.latitude), Number(this._state.destination.longitude)]);
    if (this._state?.start_point?.latitude != null && this._state?.start_point?.longitude != null) coordinates.push([Number(this._state.start_point.latitude), Number(this._state.start_point.longitude)]);
    for (const coordinate of this._state?.planned_route?.coordinates || []) coordinates.push([Number(coordinate[1]), Number(coordinate[0])]);
    if (!coordinates.length) return;
    const map = this.shadowRoot.getElementById("map");
    const minLat = Math.min(...coordinates.map((item) => item[0]));
    const maxLat = Math.max(...coordinates.map((item) => item[0]));
    const wrapped = coordinates.map((item) => ((item[1] % 360) + 360) % 360).sort((a,b) => a-b);
    let largestGap = -1, gapIndex = 0;
    wrapped.forEach((longitude, index) => {
      const next = index === wrapped.length - 1 ? wrapped[0] + 360 : wrapped[index + 1];
      if (next - longitude > largestGap) { largestGap = next - longitude; gapIndex = index; }
    });
    const arcStart = wrapped[(gapIndex + 1) % wrapped.length];
    const unwrapped = wrapped.map((longitude) => longitude < arcStart ? longitude + 360 : longitude);
    const lonSpan = Math.max(...unwrapped) - Math.min(...unwrapped);
    let centerLon = (Math.min(...unwrapped) + Math.max(...unwrapped)) / 2;
    if (centerLon > 180) centerLon -= 360;
    this._center = { lat: (minLat + maxLat) / 2, lon: centerLon };
    for (let zoom = 16; zoom >= 1; zoom -= 1) {
      const a = this._project(maxLat, centerLon, zoom);
      const b = this._project(minLat, centerLon, zoom);
      const xSpan = 256 * 2 ** zoom * lonSpan / 360;
      if (xSpan <= map.clientWidth * .82 && Math.abs(b.y - a.y) <= map.clientHeight * .78) {
        this._zoom = zoom;
        break;
      }
    }
  }

  _mapPointerDown(event) {
    if (event.target.classList?.contains("track-point")) return;
    const center = this._project(this._center.lat, this._center.lon);
    this._drag = { x: event.clientX, y: event.clientY, centerX: center.x, centerY: center.y, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  _mapPointerMove(event) {
    if (!this._drag) return;
    const dx = event.clientX - this._drag.x, dy = event.clientY - this._drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 5) this._drag.moved = true;
    const center = this._unproject(this._drag.centerX - dx, this._drag.centerY - dy);
    center.lon = ((center.lon + 540) % 360) - 180;
    this._center = center;
    this._renderMap();
  }

  _mapPointerUp(event) {
    if (!this._drag) return;
    const moved = this._drag.moved;
    this._drag = null;
    if (!moved && this._choosingDestination) {
      const rect = this.shadowRoot.getElementById("map").getBoundingClientRect();
      const center = this._project(this._center.lat, this._center.lon);
      const worldX = center.x + event.clientX - rect.left - rect.width / 2;
      const worldY = center.y + event.clientY - rect.top - rect.height / 2;
      const coordinate = this._unproject(worldX, worldY);
      const latInput = this.shadowRoot.getElementById("destination-latitude");
      const lonInput = this.shadowRoot.getElementById("destination-longitude");
      if (latInput && lonInput) {
        latInput.value = coordinate.lat.toFixed(6);
        lonInput.value = coordinate.lon.toFixed(6);
      }
      this._choosingDestination = false;
      this.shadowRoot.getElementById("map-hint").classList.remove("show");
      this._showNotice("Destination coordinates filled from the map. Review the exact point and select Save/Start to commit it.");
    }
  }

  _selectRelative(change) {
    const next = this._selectedIndex + change;
    if (next >= 0 && next < (this._query.points || []).length) {
      this._selectedIndex = next;
      const point = this._query.points[next];
      if (point.latitude != null && point.longitude != null) this._center = { lat: point.latitude, lon: point.longitude };
      this._renderDetail(); this._renderMap();
    }
  }

  async _copySelected() {
    const point = (this._query.points || [])[this._selectedIndex];
    if (!point || point.latitude == null || point.longitude == null) return;
    const text = `${point.latitude}, ${point.longitude}`;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    this._showNotice("Coordinates copied.");
  }

  _destinationFromForm(optional = false) {
    const name = this.shadowRoot.getElementById("destination-name")?.value.trim();
    const latitude = Number(this.shadowRoot.getElementById("destination-latitude")?.value);
    const longitude = Number(this.shadowRoot.getElementById("destination-longitude")?.value);
    const radius = Number(this.shadowRoot.getElementById("arrival-radius")?.value || 2);
    const blankCoordinates = !this.shadowRoot.getElementById("destination-latitude")?.value && !this.shadowRoot.getElementById("destination-longitude")?.value;
    if (optional && !name && blankCoordinates) return null;
    if (!name || !Number.isFinite(latitude) || !Number.isFinite(longitude)) throw new Error("Destination name, latitude, and longitude are all required when setting a destination.");
    if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) throw new Error("Destination coordinates are outside the valid range.");
    if (!Number.isFinite(radius) || radius < .1 || radius > 100) throw new Error("Arrival radius must be between 0.1 and 100 nmi.");
    return { name, latitude, longitude, arrival_radius_nm: radius };
  }

  async _startPassage() {
    try {
      const name = this.shadowRoot.getElementById("passage-name").value.trim();
      const localStart = this.shadowRoot.getElementById("passage-start").value;
      if (!name || !localStart) throw new Error("Passage name and start time are required.");
      const destination = this._destinationFromForm(true);
      await this._call("start_passage", { name, started_at_utc: new Date(localStart).toISOString(), ...(destination ? { destination } : {}) });
      await this._load(true);
      this._showNotice("Passage started. Live points continue in the global archive and are now evaluated against this passage.");
    } catch (error) { this._showNotice(error.message || String(error), true); }
  }

  async _setDestination() {
    try {
      const destination = this._destinationFromForm(false);
      await this._call("set_destination", { passage_id: this._state.passage.id, destination });
      await this._load(true);
      this._showNotice("A new destination version was saved; prior versions remain in the archive.");
    } catch (error) { this._showNotice(error.message || String(error), true); }
  }

  _chooseDestination() {
    this._choosingDestination = true;
    this.shadowRoot.getElementById("map-hint").classList.add("show");
    this.shadowRoot.getElementById("map").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  _useSavedDestination(id) {
    const destination = (this._state.destinations || []).find((item) => String(item.id) === String(id));
    if (!destination) return;
    this.shadowRoot.getElementById("destination-name").value = destination.name;
    this.shadowRoot.getElementById("destination-latitude").value = destination.latitude;
    this.shadowRoot.getElementById("destination-longitude").value = destination.longitude;
  }

  async _endPassage() {
    if (!confirm("End this passage? It remains available as completed history and live global archiving continues.")) return;
    try { await this._call("end_passage", { passage_id: this._state.passage.id }); await this._load(false); this._showNotice("Passage ended. Live global archiving continues."); }
    catch (error) { this._showNotice(error.message || String(error), true); }
  }

  async _deletePassage(id) {
    if (!confirm("Delete this completed passage's metadata? Global track records are retained.")) return;
    try { await this._call("delete_passage", { passage_id: id }); await this._load(false); }
    catch (error) { this._showNotice(error.message || String(error), true); }
  }

  async _testNotification() {
    try { await this._call("test_notification"); this._showNotice("Test sent. Open Home Assistant Notifications now and confirm it arrived."); }
    catch (error) { this._showNotice(error.message || String(error), true); }
  }

  async _export() {
    try {
      const format = this.shadowRoot.getElementById("export-format").value;
      this._setBusy(true, "Preparing authenticated export…");
      const result = await this._call("export", { format, range: this._range, source: this._source, ...this._customRange() });
      const blob = new Blob([result.content], { type: result.mime_type });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = result.filename; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      this._showNotice(result.decimated ? `Exported ${result.returned} of ${result.total_matching} matching records. Narrow the range for a complete export.` : `Exported ${result.returned} records.`);
    } catch (error) { this._showNotice(error.message || String(error), true); }
    finally { this._setBusy(false); }
  }

  async _importHistory() {
    const input = this.shadowRoot.getElementById("history-file");
    const file = input.files?.[0];
    if (!file) { this._showNotice("Choose a historical track file first.", true); return; }
    if (!confirm(`Import ${file.name} into the separate source-labelled archive?`)) return;
    const progress = this.shadowRoot.getElementById("import-progress");
    let importId = null;
    try {
      const buffer = await file.arrayBuffer();
      const sha256 = await this._sha256(buffer);
      const source = this.shadowRoot.getElementById("history-source").value;
      const records = this._parseHistoryFile(file.name, new TextDecoder().decode(buffer));
      if (!records.length) throw new Error("No timestamped position records were found in this file.");
      const begun = await this._call("import_begin", { source, filename: file.name, sha256 });
      importId = begun.import_id;
      let inserted = 0, rejected = 0;
      for (let offset = 0; offset < records.length; offset += 500) {
        const result = await this._call("import_chunk", { import_id: importId, source, records: records.slice(offset, offset + 500) });
        inserted += result.inserted; rejected += result.rejected;
        progress.textContent = `Processed ${Math.min(offset + 500, records.length).toLocaleString()} / ${records.length.toLocaleString()}…`;
      }
      await this._call("import_finish", { import_id: importId, notes: `${inserted} inserted; ${rejected} rejected before archive validation` });
      progress.textContent = `Import complete: ${inserted.toLocaleString()} new records; ${rejected.toLocaleString()} rejected. Exact duplicates were ignored.`;
      await this._load(true);
    } catch (error) {
      if (importId != null) {
        try { await this._call("import_finish", { import_id: importId, failed: true, notes: String(error.message || error).slice(0, 900) }); } catch (_ignored) {}
      }
      this._showNotice(error.message || String(error), true);
    }
  }

  _parseHistoryFile(filename, text) {
    if (/\.gpx$|\.xml$/i.test(filename)) return this._parseGpx(text).records;
    let value;
    if (/\.html?$/i.test(filename)) {
      const documentValue = new DOMParser().parseFromString(text, "text/html");
      const nextData = documentValue.querySelector("script#__NEXT_DATA__")?.textContent;
      if (!nextData) throw new Error("The HTML snapshot has no __NEXT_DATA__ payload. PredictWind may have changed its page format.");
      value = JSON.parse(nextData);
    } else {
      value = JSON.parse(text);
    }
    if (value?.type === "FeatureCollection" && Array.isArray(value.features)) return value.features;
    if (Array.isArray(value)) return value;
    const found = this._findRecordArray(value);
    if (!found.length) throw new Error("No array resembling timestamped route records was found.");
    return found;
  }

  _findRecordArray(root) {
    let best = [];
    const queue = [{ value: root, depth: 0 }];
    const seen = new Set();
    while (queue.length) {
      const { value, depth } = queue.shift();
      if (!value || typeof value !== "object" || seen.has(value) || depth > 12) continue;
      seen.add(value);
      if (Array.isArray(value)) {
        const sample = value.find((item) => item && typeof item === "object" && !Array.isArray(item));
        if (sample) {
          const hasTime = ["t", "time", "timestamp", "recorded_at_utc", "Time UTC"].some((key) => key in sample);
          const hasPosition = "p" in sample || "latitude" in sample || "lat" in sample || sample.type === "Feature";
          if (hasTime && hasPosition && value.length > best.length) best = value;
        }
        value.slice(0, 30).forEach((item) => queue.push({ value: item, depth: depth + 1 }));
      } else {
        Object.values(value).forEach((item) => queue.push({ value: item, depth: depth + 1 }));
      }
    }
    return best;
  }

  _parseGpx(text) {
    const xml = new DOMParser().parseFromString(text, "application/xml");
    if (xml.querySelector("parsererror")) throw new Error("The GPX/XML file is malformed.");
    let nodes = [...xml.getElementsByTagNameNS("*", "trkpt")];
    if (!nodes.length) nodes = [...xml.getElementsByTagNameNS("*", "rtept")];
    const records = nodes.map((node, index) => ({
      id: `gpx-${index}`,
      latitude: Number(node.getAttribute("lat")),
      longitude: Number(node.getAttribute("lon")),
      elevation: node.getElementsByTagNameNS("*", "ele")[0]?.textContent,
      timestamp: node.getElementsByTagNameNS("*", "time")[0]?.textContent,
    })).filter((item) => item.timestamp && Number.isFinite(item.latitude) && Number.isFinite(item.longitude));
    const coordinates = nodes.map((node) => [Number(node.getAttribute("lon")), Number(node.getAttribute("lat"))]).filter((item) => item.every(Number.isFinite));
    return { records, coordinates };
  }

  async _importRoute() {
    const passage = this._state.passage;
    if (!passage) { this._showNotice("Start a passage before attaching a planned route.", true); return; }
    const file = this.shadowRoot.getElementById("route-file").files?.[0];
    if (!file) { this._showNotice("Choose a GPX route file first.", true); return; }
    try {
      const buffer = await file.arrayBuffer();
      const parsed = this._parseGpx(new TextDecoder().decode(buffer));
      if (parsed.coordinates.length < 2) throw new Error("The GPX file has fewer than two route/track points.");
      const label = this.shadowRoot.getElementById("route-label").value.trim() || "Planned route";
      await this._call("route_add", { passage_id: passage.id, label, source: "gpx", sha256: await this._sha256(buffer), coordinates: parsed.coordinates });
      await this._load(true);
      this._showNotice("Planned route version attached. Route lines are advisory and remain separate from recorded tracks.");
    } catch (error) { this._showNotice(error.message || String(error), true); }
  }

  async _rollbackImport(id) {
    if (!confirm("Rollback this import batch? Only points inserted by this batch are removed; live Garmin points remain.")) return;
    try { const result = await this._call("import_rollback", { import_id: id }); await this._load(true); this._showNotice(`Rolled back ${result.removed} imported records.`); }
    catch (error) { this._showNotice(error.message || String(error), true); }
  }

  async _sha256(buffer) {
    if (globalThis.crypto?.subtle) {
      const digest = await crypto.subtle.digest("SHA-256", buffer);
      return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    }
    // Small synchronous SHA-256 fallback for HTTP local installations where
    // SubtleCrypto is unavailable because the browser lacks a secure context.
    const bytesValue = new Uint8Array(buffer);
    const words = [];
    for (let index = 0; index < bytesValue.length; index += 1) words[index >> 2] |= bytesValue[index] << (24 - (index % 4) * 8);
    const bitLength = bytesValue.length * 8;
    words[bitLength >> 5] |= 0x80 << (24 - bitLength % 32);
    words[((bitLength + 64 >> 9) << 4) + 15] = bitLength;
    const constants = [], primes = [];
    for (let candidate = 2; constants.length < 64; candidate += 1) {
      if (primes.every((prime) => candidate % prime)) {
        primes.push(candidate);
        constants.push((Math.pow(candidate, 1 / 3) * 0x100000000) | 0);
      }
    }
    let hash = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const right = (value, amount) => value >>> amount | value << (32 - amount);
    for (let offset = 0; offset < words.length; offset += 16) {
      const schedule = words.slice(offset, offset + 16), old = hash.slice();
      for (let index = 16; index < 64; index += 1) {
        const a = schedule[index - 15], b = schedule[index - 2];
        schedule[index] = (schedule[index - 16] + (right(a,7)^right(a,18)^(a>>>3)) + schedule[index - 7] + (right(b,17)^right(b,19)^(b>>>10))) | 0;
      }
      for (let index = 0; index < 64; index += 1) {
        const e = hash[4], a = hash[0];
        const temp1 = (hash[7] + (right(e,6)^right(e,11)^right(e,25)) + ((e&hash[5])^(~e&hash[6])) + constants[index] + schedule[index]) | 0;
        const temp2 = ((right(a,2)^right(a,13)^right(a,22)) + ((a&hash[1])^(a&hash[2])^(hash[1]&hash[2]))) | 0;
        hash = [(temp1+temp2)|0,hash[0],hash[1],hash[2],(hash[3]+temp1)|0,hash[4],hash[5],hash[6]];
      }
      hash = hash.map((value, index) => (value + old[index]) | 0);
    }
    return hash.map((value) => (value >>> 0).toString(16).padStart(8, "0")).join("");
  }

  _localInputValue(date) {
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 16);
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new Event("location-changed"));
  }

  _haversine(lat1, lon1, lat2, lon2) {
    const rad = (value) => value * Math.PI / 180;
    const dLat = rad(lat2 - lat1), dLon = rad(lon2 - lon1);
    const a = Math.sin(dLat/2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon/2) ** 2;
    return 3440.065 * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  _bearing(lat1, lon1, lat2, lon2) {
    const rad = (value) => value * Math.PI / 180;
    const y = Math.sin(rad(lon2-lon1)) * Math.cos(rad(lat2));
    const x = Math.cos(rad(lat1))*Math.sin(rad(lat2)) - Math.sin(rad(lat1))*Math.cos(rad(lat2))*Math.cos(rad(lon2-lon1));
    return (Math.atan2(y,x) * 180 / Math.PI + 360) % 360;
  }
}

if (!customElements.get("bluesky-passage-panel")) {
  customElements.define("bluesky-passage-panel", BlueSkyPassagePanel);
}
