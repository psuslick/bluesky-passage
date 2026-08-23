const DOMAIN = "bluesky_passage";
const COLORS = {
  speed: "#03a9f4", wind: "#ffb300", gust: "#ef5350", wave: "#7e57c2",
  garmin_mapshare: "#03a9f4", predictwind_snapshot: "#ff9800",
  gpx_import: "#ab47bc", ha_recorder: "#26a69a", csv_import: "#78909c",
};

const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
const finite = (value) => value !== null && value !== undefined && value !== "" && typeof value !== "boolean" && Number.isFinite(Number(value));
const num = (value, digits = 1, unit = "") => finite(value) ? `${Number(value).toFixed(digits)}${unit}` : "—";
const local = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString([], { timeZoneName: "short" });
};
const bytes = (value) => {
  let result = Number(value || 0); let index = 0;
  const units = ["B", "KB", "MB", "GB"];
  while (result >= 1024 && index < units.length - 1) { result /= 1024; index += 1; }
  return `${result.toFixed(index ? 1 : 0)} ${units[index]}`;
};

class BlueSkyPassagePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._ready = false;
    this._state = null;
    this._query = { points: [], weather_samples: [], daily_runs: [] };
    this._tab = "overview";
    this._range = "24h";
    this._source = "canonical";
    this._customStart = "";
    this._customEnd = "";
    this._mapMode = "local";
    this._selectedIndex = -1;
    this._selectRange = false;
    this._selectionStart = null;
    this._selectionEnd = null;
    this._showGust = false;
    this._passageId = null;
    this._passageDetail = null;
    this._passageDraft = null;
    this._passagePreview = null;
    this._backfillJob = null;
    this._backfillStart = this._dateInput(new Date(Date.now() - 365 * 86400000));
    this._backfillEnd = this._dateInput(new Date());
    this._recorderPreview = null;
    this._recorderRecords = [];
    this._recorderSelections = {};
    this._recorderStart = "";
    this._recorderEnd = "";
    this._busy = "";
    this._notice = null;
    this._mapViews = {};
    this._mapDrag = null;
    this._mapPointers = new Map();
    this._mapPinch = null;
    this._mapWheelDelta = {};
    this._showDeviationOverlay = false;
    this._deviationScales = this._loadDeviationScales();
    this._suppressPointClickUntil = 0;
    this._unsubscribe = null;
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this._ready) this._initialize();
  }
  set panel(value) { this._panel = value; }
  set route(value) { this._route = value; }
  set narrow(value) { this._narrow = value; }
  get _admin() { return Boolean(this._hass?.user?.is_admin); }
  get _speedUnit() { return this._state?.runtime?.units?.speed || "kn"; }
  get _heightUnit() { return this._state?.runtime?.units?.height || "m"; }

  connectedCallback() {
    if (this._hass && !this._ready) this._initialize();
  }

  disconnectedCallback() {
    if (this._unsubscribe) this._unsubscribe();
    this._unsubscribe = null;
  }

  async _initialize() {
    this._ready = true;
    this._renderShell();
    this._bindEvents();
    try {
      this._unsubscribe = await this._hass.connection.subscribeEvents(
        () => this._load(false), "bluesky_passage_data_updated",
      );
    } catch (_error) { /* Manual refresh remains available. */ }
    await this._load(true);
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;min-height:100%;background:var(--primary-background-color);color:var(--primary-text-color)}
        *{box-sizing:border-box}button,input,select,textarea{font:inherit}button{cursor:pointer}
        .page{max-width:1540px;margin:auto;padding:22px}.header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}
        h1{font-size:29px;margin:0 0 5px;line-height:1.1}h2{font-size:19px;margin:0 0 14px}h3{font-size:15px;margin:0 0 9px}
        .muted{color:var(--secondary-text-color);font-size:13px}.header-actions,.row,.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:9px}
        .status{display:inline-flex;padding:6px 10px;border-radius:999px;background:rgba(76,175,80,.14);color:#4caf50;font-weight:650;font-size:13px}
        .status.warn{color:#ff9800;background:rgba(255,152,0,.14)}.status.bad{color:#ef5350;background:rgba(239,83,80,.14)}
        .button{min-height:44px;display:inline-flex;align-items:center;border:1px solid var(--divider-color);border-radius:9px;padding:9px 13px;background:var(--card-background-color);color:var(--primary-text-color);text-decoration:none}
        .button:hover,.button:focus-visible{border-color:var(--primary-color);outline:none}.button.primary{background:var(--primary-color);border-color:var(--primary-color);color:var(--text-primary-color,#fff)}
        .button.danger{color:var(--error-color,#ef5350)}.button:disabled{opacity:.45;cursor:not-allowed}.button.small{padding:6px 9px;font-size:12px}
        .tabs{display:flex;gap:28px;border-bottom:1px solid var(--divider-color);margin-top:20px;overflow:auto}
        .tab{position:relative;border:0;background:transparent;color:var(--secondary-text-color);padding:14px 2px 13px;white-space:nowrap;font-weight:600}
        .tab[aria-selected="true"]{color:var(--primary-color)}.tab[aria-selected="true"]:after{content:"";position:absolute;height:3px;border-radius:3px;left:0;right:0;bottom:-1px;background:var(--primary-color)}
        .notice{margin:14px 0 0;padding:11px 13px;border-radius:9px;border:1px solid rgba(255,152,0,.45);background:rgba(255,152,0,.12)}
        .notice.error{border-color:rgba(239,83,80,.55);background:rgba(239,83,80,.12)}
        main{padding-top:16px}.card,.metric{background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:13px;box-shadow:var(--ha-card-box-shadow,none)}
        .card{padding:16px}.metrics{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:11px;margin-bottom:14px}
        .metric{padding:14px;min-height:84px}.metric-label{color:var(--secondary-text-color);font-size:12px;margin-bottom:8px}.metric-value{font-size:20px;font-weight:680;overflow-wrap:anywhere}
        .grid-main{display:grid;grid-template-columns:minmax(0,2.1fr) minmax(290px,.9fr);gap:14px}.stack{display:grid;gap:14px}.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
        .map-card{padding:0;overflow:hidden}.map{height:540px;position:relative;overflow:hidden;background:#b9d7e5;user-select:none;touch-action:none;cursor:grab}.map.dragging{cursor:grabbing}.map.small{height:420px}
        .tiles,.overlay{position:absolute;inset:0}.tiles img{position:absolute;width:256px;height:256px;pointer-events:none}.overlay{width:100%;height:100%;overflow:visible}
        .map-controls{position:absolute;left:10px;top:10px;z-index:4;display:grid;grid-template-columns:44px 44px 44px;gap:5px}.map-controls button{width:44px;height:44px;border:0;border-radius:7px;background:rgba(28,31,34,.9);color:#fff;font-size:18px}.map-controls .wide{grid-column:span 3}.map-controls .spacer{visibility:hidden}
        .map-attribution{position:absolute;right:3px;bottom:2px;z-index:4;background:rgba(255,255,255,.8);color:#222;padding:2px 4px;font-size:10px}.map-attribution a{color:#1565c0}
        .legend{position:absolute;left:9px;bottom:10px;z-index:4;padding:6px 8px;border-radius:7px;background:rgba(0,0,0,.7);color:white;font-size:11px}.swatch{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 8px}.swatch:first-child{margin-left:0}
        .record{min-height:180px}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:11px}.detail-grid .wide{grid-column:1/-1}.detail-label{font-size:11px;color:var(--secondary-text-color);text-transform:uppercase;letter-spacing:.04em}.detail-value{margin-top:3px;overflow-wrap:anywhere}
        blockquote{margin:5px 0 0;padding:8px 10px;border-left:3px solid var(--primary-color);background:var(--secondary-background-color);white-space:pre-wrap}
        .toolbar{padding:12px;margin-bottom:14px;align-items:end}label{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--secondary-text-color)}
        input,select,textarea{min-height:39px;border:1px solid var(--divider-color);border-radius:8px;padding:8px 10px;background:var(--secondary-background-color);color:var(--primary-text-color)}textarea{min-height:78px;resize:vertical}
        .segmented{display:inline-flex;border:1px solid var(--divider-color);border-radius:9px;overflow:hidden}.segmented button{border:0;border-right:1px solid var(--divider-color);background:var(--card-background-color);color:var(--secondary-text-color);padding:8px 11px}.segmented button:last-child{border-right:0}.segmented button.active{background:var(--primary-color);color:var(--text-primary-color,#fff)}
        .chart{padding:16px}.chart svg{display:block;width:100%;height:auto}.chart-coverage{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--secondary-text-color);font-size:12px;margin:0 0 8px}.chart-hint{font-size:12px;color:var(--secondary-text-color);background:var(--secondary-background-color);border-radius:7px;padding:9px 11px;margin:0 0 10px}.chart-legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;margin-bottom:7px}.chart-legend i{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:5px}.chart-legend .series-missing{opacity:.5}.analytics-svg{min-height:440px}.analytics-note{margin-top:6px;font-size:11px;color:var(--secondary-text-color)}
        .deviation-panel{margin-top:16px;padding-top:16px;border-top:1px solid var(--divider-color)}.card>.deviation-panel{margin-top:0;padding-top:0;border-top:0}.deviation-head{display:flex;justify-content:space-between;align-items:end;gap:12px;flex-wrap:wrap}.deviation-controls{display:flex;gap:9px;align-items:end;flex-wrap:wrap}.deviation-scale-wrap{margin:14px 4px 8px}.deviation-labels{display:grid;grid-template-columns:1fr auto 1fr;font-size:11px;color:var(--secondary-text-color);margin-bottom:7px}.deviation-labels span:last-child{text-align:right}.deviation-track{position:relative;height:18px;border-radius:999px;background:var(--secondary-background-color);border:1px solid var(--divider-color);overflow:visible}.deviation-track:before{content:"";position:absolute;left:50%;top:-7px;bottom:-7px;width:2px;background:var(--primary-text-color);opacity:.55}.deviation-range{position:absolute;top:5px;height:6px;border-radius:6px;background:var(--secondary-text-color);opacity:.45}.deviation-marker{position:absolute;top:50%;width:16px;height:16px;border-radius:50%;transform:translate(-50%,-50%);background:var(--primary-color);border:2px solid var(--card-background-color);box-shadow:0 0 0 1px var(--divider-color)}.deviation-overflow{margin-top:7px;font-size:12px;color:#ff9800}.deviation-ticks{display:flex;justify-content:space-between;margin-top:6px;font-size:10px;color:var(--secondary-text-color)}.deviation-history{margin-top:14px}.deviation-history svg{display:block;width:100%;height:auto;min-height:160px}.comparison-submetrics{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:9px;margin-top:12px}.comparison-submetric{padding:10px 11px;border-radius:9px;background:var(--secondary-background-color)}.comparison-submetric strong{display:block;margin-top:4px;font-size:15px}
        .table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--divider-color);vertical-align:top}th{color:var(--secondary-text-color);font-weight:600}
        .form-grid{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:11px}.span2{grid-column:span 2}.span4{grid-column:1/-1}.section-title{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}
        .coverage{border-left:4px solid var(--primary-color);padding:12px;background:var(--secondary-background-color);border-radius:7px}.warning{border-left-color:#ff9800}.danger-text{color:var(--error-color,#ef5350)}
        .progress{height:8px;background:var(--secondary-background-color);border-radius:8px;overflow:hidden;margin:9px 0}.progress span{display:block;height:100%;background:var(--primary-color)}
        .empty{min-height:160px;display:grid;place-items:center;text-align:center;color:var(--secondary-text-color);padding:24px}.predictwind{height:540px;width:100%;border:0;background:var(--secondary-background-color)}
        details{margin-top:10px}summary{cursor:pointer;color:var(--secondary-text-color)}.safety{margin-top:14px;border-left:4px solid #ff9800;font-size:13px}footer{font-size:12px;color:var(--secondary-text-color);padding:14px 2px 30px}
        @media(max-width:1050px){.grid-main,.two{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.map{height:470px}.form-grid{grid-template-columns:repeat(2,1fr)}.span4{grid-column:1/-1}}
        @media(max-width:620px){.page{padding:12px}.header{flex-direction:column}.tabs{gap:20px}.metrics{grid-template-columns:1fr 1fr}.map{height:390px}.form-grid{grid-template-columns:1fr}.span2,.span4{grid-column:1}.detail-grid{grid-template-columns:1fr}.detail-grid .wide{grid-column:1}.comparison-submetrics{grid-template-columns:1fr 1fr}}
        @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
      </style>
      <div class="page"><div id="header"></div><div id="tabs"></div><div id="notice-area"></div><main id="content"></main><footer id="footer"></footer></div>`;
  }

  _bindEvents() {
    this.shadowRoot.addEventListener("click", (event) => {
      const target = event.target.closest("[data-action]");
      if (!target) return;
      const action = target.dataset.action;
      if (action === "point" && Date.now() < this._suppressPointClickUntil) return;
      const actions = {
        tab: () => this._setTab(target.dataset.tab), refresh: () => this._refresh(),
        load: () => this._loadQuery(true), zoomin: () => this._zoomMap(target.dataset.map, 1),
        zoomout: () => this._zoomMap(target.dataset.map, -1), fit: () => this._fitMap(target.dataset.map),
        panleft: () => this._panMap(target.dataset.map, -0.25, 0), panright: () => this._panMap(target.dataset.map, 0.25, 0),
        panup: () => this._panMap(target.dataset.map, 0, -0.25), pandown: () => this._panMap(target.dataset.map, 0, 0.25),
        point: () => this._selectPoint(Number(target.dataset.index)),
        "deviation-point": () => this._selectReportId(Number(target.dataset.reportId)),
        "range-select": () => { this._selectRange = !this._selectRange; this._selectionStart = null; this._selectionEnd = null; this._renderContent(); },
        "clear-range": () => this._clearPointRange(), weather: () => this._weather(),
        "map-mode": () => { this._mapMode = target.dataset.mode; this._renderContent(); },
        export: () => this._export(), "open-options": () => this._navigate("/config/integrations/integration/bluesky_passage"),
        "new-passage": () => this._editPassage(null), "edit-passage": () => this._editPassage(Number(target.dataset.id)),
        "cancel-passage": () => { this._passageDraft = null; this._passagePreview = null; this._renderContent(); },
        "preview-passage": () => this._previewPassage(), "save-passage": () => this._savePassage(),
        "delete-passage": () => this._deletePassage(Number(target.dataset.id)), "plan-route": () => this._planRoute(Number(target.dataset.id)),
        "save-profile": () => this._saveProfile(), "backfill-preview": () => this._startBackfillPreview(),
        "backfill-commit": () => this._startBackfillCommit(Number(target.dataset.id)),
        "backfill-resume": () => this._runBackfill(Number(target.dataset.id), true),
        "backfill-cancel": () => this._cancelBackfill(Number(target.dataset.id)),
        "recorder-preview": () => this._previewRecorder(),
        "recorder-import": () => this._importRecorder(),
        integrity: () => this._integrity(), "test-notification": () => this._testNotification(),
        "rollback-import": () => this._rollbackImport(Number(target.dataset.id)), "import-history": () => this._importHistory(),
      };
      if (actions[action]) actions[action]();
    });
    this.shadowRoot.addEventListener("change", (event) => {
      if (event.target.id === "range") { this._range = event.target.value; if (this._range === "custom") this._renderContent(); else this._loadQuery(true); }
      if (event.target.id === "source") { this._source = event.target.value; this._loadQuery(true); }
      if (event.target.id === "gust") { this._showGust = event.target.checked; this._renderContent(); }
      if (event.target.id === "history-passage") { this._passageId = event.target.value ? Number(event.target.value) : null; if (this._range === "passage") this._loadQuery(true); }
      if (event.target.id === "end-mode") { this._capturePassageDraft(); this._renderContent(); }
      if (event.target.id === "history-file") this._renderContent();
      if (event.target.id === "backfill-start") this._backfillStart = event.target.value;
      if (event.target.id === "backfill-end") this._backfillEnd = event.target.value;
      if (event.target.id === "deviation-scale") { this._setDeviationScale(event.target.value); this._renderContent(); }
      if (event.target.id === "deviation-custom") { this._setDeviationCustom(event.target.value); this._renderContent(); }
      if (event.target.id === "deviation-overlay") { this._showDeviationOverlay = event.target.checked; this._renderContent(); }
    });
    this.shadowRoot.addEventListener("keydown", (event) => {
      if (["point", "deviation-point"].includes(event.target.dataset?.action) && ["Enter", " "].includes(event.key)) {
        event.preventDefault();
        if (event.target.dataset.action === "point") this._selectPoint(Number(event.target.dataset.index));
        else this._selectReportId(Number(event.target.dataset.reportId));
        return;
      }
      if (!event.target.matches('[role="tab"]') || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const tabs = ["overview", "history", "passages", "settings"];
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const next = tabs[(tabs.indexOf(this._tab) + direction + tabs.length) % tabs.length];
      this._setTab(next); this.shadowRoot.querySelector(`[data-tab="${next}"]`)?.focus();
    });
    this.shadowRoot.addEventListener("pointerdown", (event) => this._startMapDrag(event));
    this.shadowRoot.addEventListener("pointermove", (event) => this._moveMapDrag(event));
    this.shadowRoot.addEventListener("pointerup", (event) => this._endMapDrag(event));
    this.shadowRoot.addEventListener("pointercancel", (event) => this._endMapDrag(event));
    this.shadowRoot.addEventListener("wheel", (event) => this._wheelMap(event), { passive: false });
    this.shadowRoot.addEventListener("dblclick", (event) => this._doubleClickMap(event));
  }

  async _call(name, data = {}) {
    return this._hass.callWS({ type: `${DOMAIN}/${name}`, ...data });
  }

  async _load(initial = false) {
    try {
      if (initial) this._busy = "Loading local archive…";
      this._state = await this._call("state");
      if (!this._passageId && this._state.passages?.length) this._passageId = this._state.passages[0].id;
      await this._loadQuery(initial);
    } catch (error) { this._show(error.message || String(error), true); }
    finally { this._busy = ""; this._render(); }
  }

  _customRange() {
    if (this._range !== "custom") return {};
    const start = this.shadowRoot.getElementById("range-start")?.value || this._customStart;
    const end = this.shadowRoot.getElementById("range-end")?.value || this._customEnd;
    if (!start || !end) throw new Error("Choose both custom dates.");
    this._customStart = start; this._customEnd = end;
    return { start_utc: new Date(start).toISOString(), end_utc: new Date(end).toISOString() };
  }

  async _loadQuery(fit = false) {
    if (!this._hass) return;
    if (fit) this._mapViews = {};
    const payload = { range: this._range, source: this._source, max_points: 10000, ...this._customRange() };
    if (this._range === "passage") {
      if (!this._passageId) throw new Error("Choose a passage.");
      payload.passage_id = this._passageId;
    } else if (this._passageId) payload.passage_id = this._passageId;
    if (this._selectionStart && this._selectionEnd) {
      payload.start_report_id = this._selectionStart;
      payload.end_report_id = this._selectionEnd;
    }
    this._query = await this._call("points", payload);
    this._selectedIndex = this._query.points.length ? this._query.points.length - 1 : -1;
    this._render();
  }

  _render() {
    if (!this._state) return;
    this._renderHeader(); this._renderTabs(); this._renderNotice(); this._renderContent();
    this.shadowRoot.getElementById("footer").textContent = `BlueSky Passage ${this._state.runtime?.integration_version || ""} · Position history, passage annotations, vessel profile, and cached model samples remain in the dedicated local archive. Online source and map requests disclose the requesting IP address to their provider.`;
  }

  _renderHeader() {
    const runtime = this._state.runtime || {};
    const bad = runtime.status === "EMERGENCY" || !runtime.source_available;
    const warn = runtime.is_stale || runtime.gps_problem;
    this.shadowRoot.getElementById("header").innerHTML = `
      <div class="header"><div><h1>BlueSky Passage</h1><div class="muted">${esc(runtime.status)} · ${this._state.archive?.total_points?.toLocaleString?.() || 0} archived Garmin/source records</div></div>
      <div class="header-actions"><span class="status ${bad ? "bad" : warn ? "warn" : ""}">${esc(runtime.status)}</span></div></div>`;
  }

  _renderTabs() {
    const names = { overview: "Overview", history: "History & charts", passages: "Passages", settings: "Data & settings" };
    this.shadowRoot.getElementById("tabs").innerHTML = `<nav class="tabs" role="tablist" aria-label="BlueSky Passage sections">${Object.entries(names).map(([key, name]) => `<button class="tab" role="tab" aria-selected="${this._tab === key}" tabindex="${this._tab === key ? 0 : -1}" data-action="tab" data-tab="${key}">${name}</button>`).join("")}</nav>`;
  }

  _renderNotice() {
    const area = this.shadowRoot.getElementById("notice-area");
    area.innerHTML = this._notice ? `<div class="notice ${this._notice.error ? "error" : ""}">${esc(this._notice.text)}</div>` : "";
  }

  _renderContent() {
    const content = this.shadowRoot.getElementById("content");
    if (!content || !this._state) return;
    if (this._tab === "overview") content.innerHTML = this._overviewHtml();
    if (this._tab === "history") content.innerHTML = this._historyHtml();
    if (this._tab === "passages") content.innerHTML = this._passagesHtml();
    if (this._tab === "settings") content.innerHTML = this._settingsHtml();
    requestAnimationFrame(() => this._afterRender());
  }

  _afterRender() {
    if (this._tab === "overview") this._drawMap("overview-map");
    if (this._tab === "history" && this._mapMode !== "predictwind") this._drawMap("history-map");
    if (this._tab === "passages" && this.shadowRoot.getElementById("passage-map")) {
      const route = this._passageDetail?.route?.context_status === "current" ? this._passageDetail.route : null;
      this._drawMap("passage-map", route);
    }
  }

  _alertsHtml() {
    const runtime = this._state.runtime || {}; const latest = this._state.latest || {};
    const alerts = [];
    if (latest.in_emergency === true) alerts.push(["error", "Garmin reports emergency mode. Use Garmin’s authoritative emergency process."]);
    if (!runtime.source_available) alerts.push(["error", `Garmin source unavailable: ${runtime.source_error || "unknown error"}. Stored history remains available.`]);
    if (runtime.is_stale) {
      const age = Math.round(Number(runtime.report_age_minutes || 0)); const threshold = Number(runtime.stale_minutes || 0);
      alerts.push(["", runtime.report_age_minutes == null ? "No Garmin report is archived yet." : `Latest report is ${age} minute${age === 1 ? "" : "s"} old; alert threshold is ${threshold} minute${threshold === 1 ? "" : "s"}.`]);
    }
    if (runtime.gps_problem) alerts.push(["", "The latest record explicitly reports an invalid GPS fix."]);
    return alerts.map(([kind, text]) => `<div class="notice ${kind}">${esc(text)}</div>`).join("");
  }

  _overviewHtml() {
    const latest = this._state.latest || {};
    return `${this._alertsHtml()}<section class="metrics">
      ${this._metric("Latest report", local(latest.recorded_at_utc))}${this._metric("Speed over ground", this._speed(latest.sog_kn))}
      ${this._metric("Course over ground", num(latest.cog_true, 0, "° true"))}${this._metric("GPS fix", latest.valid_gps_fix == null ? "Unknown" : latest.valid_gps_fix ? "Valid" : "Invalid")}
      </section><div class="grid-main"><section class="card map-card">${this._mapHtml("overview-map", false)}</section>
      <aside class="stack"><section class="card record"><h2>Latest report</h2>${this._recordHtml(latest)}</section>
      <section class="card"><h2>Latest inReach text</h2>${this._state.latest_message ? `<blockquote>${esc(this._state.latest_message.text)}</blockquote><p class="muted">${local(this._state.latest_message.recorded_at_utc)}</p>` : `<div class="empty">No text message is archived.</div>`}</section></aside></div>
      <section class="card safety"><strong>Supplementary display:</strong> this panel is not a substitute for Garmin emergency channels, certified charts, forecasts, notices, traffic awareness, or skipper judgment.</section>`;
  }

  _metric(label, value) { return `<article class="metric"><div class="metric-label">${esc(label)}</div><div class="metric-value" title="${esc(value)}">${esc(value)}</div></article>`; }

  _historyHtml() {
    const passages = this._state.passages || [];
    const custom = this._range === "custom";
    const rangeText = this._selectionStart && this._selectionEnd ? "Map selection" : this._range;
    const metrics = this._query.metrics || {}; const latest = this._query.points?.at(-1) || {};
    const destinationMetrics = this._query.destination ? `<section class="metrics">${this._metric("Destination range", num(metrics.range_nm, 1, " nmi"))}${this._metric("Recent closing rate", this._speed(metrics.closing_rate_kn))}${this._metric("VMC at latest report", this._speed(latest.vmc_kn))}${this._metric("Estimated arrival", metrics.eta_utc ? local(metrics.eta_utc) : metrics.eta_status || "—")}${this._metric("Light at ETA", metrics.daylight_at_eta?.state || "—")}</section>` : "";
    const rangeOptions = [["24h","Last 24 hours"],["3d","Last 3 days"],["7d","Last 7 days"],["30d","Last 30 days"],["1y","Last year"],["all","All archived time"],["passage","Selected passage"],["custom","Custom dates"]];
    return `<section class="toolbar card"><label>Displayed period<select id="range">${rangeOptions.map(([value,label]) => `<option value="${value}" ${this._range === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label>Passage context<select id="history-passage"><option value="">None</option>${passages.map((item) => `<option value="${item.id}" ${this._passageId === item.id ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label>
      ${custom ? `<label>Start<input id="range-start" type="datetime-local" value="${esc(this._customStart)}"></label><label>End<input id="range-end" type="datetime-local" value="${esc(this._customEnd)}"></label>` : ""}
      ${custom ? `<button class="button primary" data-action="load">Apply dates</button>` : ""}<button class="button ${this._selectRange ? "primary" : ""}" data-action="range-select">${this._selectRange ? "Selecting two reports…" : "Select map range"}</button>
      ${this._selectionStart ? `<button class="button" data-action="clear-range">Clear selection</button>` : ""}
      <span class="muted">${esc(rangeText)} · ${this._query.returned || 0}${this._query.decimated ? ` of ${this._query.total_matching}` : ""} records</span></section>${destinationMetrics}
      <div class="section-title"><div class="segmented" role="group" aria-label="Map view"><button class="${this._mapMode === "local" ? "active" : ""}" data-action="map-mode" data-mode="local">Local track</button><button class="${this._mapMode === "weather" ? "active" : ""}" data-action="map-mode" data-mode="weather">Weather model</button><button class="${this._mapMode === "predictwind" ? "active" : ""}" data-action="map-mode" data-mode="predictwind">PredictWind</button></div>
      <div class="row">${this._admin && this._mapMode === "weather" ? `<button class="button" data-action="weather" ${!this._state.weather?.configured || this._busy ? "disabled" : ""}>${this._busy || "Fetch / refresh model data"}</button>` : ""}${this._admin ? `<button class="button" data-action="export" ${this._busy ? "disabled" : ""}>Export CSV</button>` : ""}</div></div>
      ${this._mapMode === "predictwind" ? this._predictWindHtml() : `<div class="grid-main"><section class="card map-card">${this._mapHtml("history-map", false)}</section><aside class="card record"><h2>Selected report</h2>${this._recordHtml(this._query.points[this._selectedIndex])}</aside></div>`}
      <section class="card chart"><div class="section-title"><div><h2>Passage analytics</h2><div class="muted">Observed vessel speed with modeled wind and waves over the same time axis. Missing provider values remain gaps.</div></div><label><span>Include wind gusts</span><input id="gust" type="checkbox" ${this._showGust ? "checked" : ""}></label></div>${this._chartHtml()}</section>
      <details class="card"><summary>Advanced source and data details</summary><div class="toolbar"><label>Source<select id="source">${[["garmin_mapshare","Garmin MapShare"],["canonical","Combined (Garmin preferred)"],["all","All source rows"],["predictwind_snapshot","PredictWind import"],["gpx_import","GPX import"],["ha_recorder","Home Assistant Recorder import"],["csv_import","CSV import"]].map(([value,label]) => `<option value="${value}" ${this._source === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><p class="muted">Weather series are Xweather model values cached on demand, not measurements from the vessel.</p></div></details>`;
  }

  _predictWindHtml() {
    const url = this._state.links?.predictwind;
    if (!url) return `<section class="card empty"><div><h2>PredictWind link not configured</h2><p>Add the public tracking URL in the integration Configure dialog.</p><button class="button primary" data-action="open-options">Open Configure</button></div></section>`;
    return `<section class="card map-card"><iframe class="predictwind" src="${esc(url)}" title="PredictWind tracking map" loading="lazy" referrerpolicy="no-referrer"></iframe><div class="card"><p class="muted">Loaded only while this view is selected. If PredictWind blocks embedding, open it directly.</p><a class="button" href="${esc(url)}" target="_blank" rel="noopener">Open PredictWind map</a></div></section>`;
  }

  _mapHtml(id, small) {
    return `<div id="${id}" class="map ${small ? "small" : ""}" data-map-id="${id}"><div class="tiles"></div><svg class="overlay" role="img" aria-label="Archived vessel track and comparison route"></svg><div class="map-controls"><button data-action="zoomout" data-map="${id}" title="Zoom out">−</button><button data-action="panup" data-map="${id}" title="Pan north">↑</button><button data-action="zoomin" data-map="${id}" title="Zoom in">+</button><button data-action="panleft" data-map="${id}" title="Pan west">←</button><button data-action="fit" data-map="${id}" title="Fit track">⌖</button><button data-action="panright" data-map="${id}" title="Pan east">→</button><span class="spacer"></span><button data-action="pandown" data-map="${id}" title="Pan south">↓</button><span class="spacer"></span></div><div class="legend"><i class="swatch" style="background:${COLORS.speed}"></i>${esc(this._sourceLabel(this._source))}${this._mapMode === "weather" ? `<i class="swatch" style="background:${COLORS.wind}"></i>Modeled wind` : ""}</div><div class="map-attribution">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a></div></div>`;
  }

  _recordHtml(point) {
    if (!point) return `<div class="empty">Select a report point to inspect its associated data.</div>`;
    return `<div class="detail-grid"><div class="detail-item wide"><div class="detail-label">Recorded</div><div class="detail-value">${local(point.recorded_at_utc)}</div></div>
      <div><div class="detail-label">Latitude</div><div class="detail-value">${num(point.latitude, 5)}</div></div><div><div class="detail-label">Longitude</div><div class="detail-value">${num(point.longitude, 5)}</div></div>
      <div><div class="detail-label">SOG</div><div class="detail-value">${this._speed(point.sog_kn)}</div></div><div><div class="detail-label">COG</div><div class="detail-value">${num(point.cog_true, 0, "° true")}</div></div>
      <div><div class="detail-label">Elevation</div><div class="detail-value">${this._height(point.elevation_m)}</div></div><div><div class="detail-label">GPS fix</div><div class="detail-value">${point.valid_gps_fix == null ? "Unknown" : point.valid_gps_fix ? "Valid" : "Invalid"}</div></div>
      ${point.destination_name ? `<div><div class="detail-label">Range to ${esc(point.destination_name)}</div><div class="detail-value">${num(point.destination_range_nm, 1, " nmi")}</div></div><div><div class="detail-label">Velocity made good</div><div class="detail-value">${this._speed(point.vmc_kn)}</div></div>` : ""}
      <div class="wide"><div class="detail-label">Source / quality</div><div class="detail-value">${esc(this._sourceLabel(point.source))} · ${esc((point.quality_flags || []).join(", ") || "No flags")}</div></div>
      ${point.message_text ? `<div class="wide"><div class="detail-label">Text on this record</div><blockquote>${esc(point.message_text)}</blockquote></div>` : ""}</div>`;
  }

  _chartHtml() {
    const points = this._query.points || []; const weather = this._query.weather_samples || [];
    const speed = points.map((item) => [new Date(item.recorded_at_utc).getTime(), this._speedValue(item.sog_kn), item.id, Boolean(item.break_before)]);
    const wind = weather.map((item) => [new Date(item.valid_at_utc).getTime(), this._speedValue(item.wind_speed_kn)]);
    const gust = weather.map((item) => [new Date(item.valid_at_utc).getTime(), this._speedValue(item.wind_gust_kn)]);
    const wave = weather.map((item) => [new Date(item.valid_at_utc).getTime(), this._heightValue(item.wave_height_m)]);
    const speedCount = speed.filter((item) => finite(item[1])).length;
    const windCount = wind.filter((item) => finite(item[1])).length;
    const gustCount = gust.filter((item) => finite(item[1])).length;
    const waveCount = wave.filter((item) => finite(item[1])).length;
    const all = [...speed, ...wind, ...(this._showGust ? gust : []), ...wave].filter((item) => finite(item[1]) && finite(item[0]));
    const rangeStart = this._query.range?.start_utc ? new Date(this._query.range.start_utc).getTime() : NaN;
    const rangeEnd = this._query.range?.end_utc ? new Date(this._query.range.end_utc).getTime() : (this._query.range?.name && this._query.range.name !== "all" ? Date.now() : NaN);
    const dataMinX = all.length ? Math.min(...all.map((item) => item[0])) : NaN;
    const dataMaxX = all.length ? Math.max(...all.map((item) => item[0])) : NaN;
    const minX = finite(rangeStart) ? rangeStart : dataMinX;
    const maxX = finite(rangeEnd) ? rangeEnd : dataMaxX;
    const totalMatching = Number(this._query.total_matching ?? points.length);
    const trackCoverage = `${points.length.toLocaleString()} displayed${this._query.decimated ? ` of ${totalMatching.toLocaleString()} matching` : ""} reports · ${speedCount.toLocaleString()} with SOG`;
    const weatherCoverage = weather.length ? `${weather.length.toLocaleString()} cached track-weather samples · ${windCount.toLocaleString()} wind · ${waveCount.toLocaleString()} wave${this._showGust ? ` · ${gustCount.toLocaleString()} gust` : ""}` : "No cached track-weather samples in this period";
    const coverage = `<div class="chart-coverage"><span>${esc(trackCoverage)}</span><span>${esc(weatherCoverage)}</span></div>`;
    const modelHint = !weather.length ? `<div class="chart-hint">Wind and wave values used by a route calculation are intentionally not overlaid on the observed-track chart because they describe candidate route positions/times. To populate this chart, choose <strong>Weather model</strong> above and use <strong>Fetch / refresh model data</strong>.</div>` : "";
    if (all.length < 2 || !finite(minX) || !finite(maxX)) return `${coverage}${modelHint}<div class="empty">More valid observations are needed for this period. Missing values are left as gaps rather than plotted as zero.</div>`;

    const niceMax = (value) => { const raw=Math.max(.1,Number(value)||0); const power=10**Math.floor(Math.log10(raw)); const scaled=raw/power; const nice=scaled<=1?1:scaled<=2?2:scaled<=5?5:10; return nice*power; };
    const maxSpeed = niceMax(Math.max(1, ...speed.filter((i)=>finite(i[1])).map((i)=>i[1])));
    const maxWind = niceMax(Math.max(1, ...[...wind, ...(this._showGust ? gust : [])].filter((i)=>finite(i[1])).map((i)=>i[1])));
    const maxWave = niceMax(Math.max(.5, ...wave.filter((i)=>finite(i[1])).map((i)=>i[1])));
    const w=1000,left=64,right=24,top=18,panelH=112,gap=30,bottom=48;
    const speedTop=top, windTop=speedTop+panelH+gap, waveTop=windTop+panelH+gap, h=waveTop+panelH+bottom;
    const x=(v)=>left+(v-minX)/Math.max(maxX-minX,1)*(w-left-right);
    const y=(v,maxValue,panelTop)=>panelTop+(1-v/maxValue)*panelH;
    const pathSegments=(values,maxValue,panelTop)=>{const segments=[];let segment=[];for(const item of values){if(!finite(item[1])||!finite(item[0])||item[3]){if(segment.length)segments.push(segment);segment=[];}if(finite(item[1])&&finite(item[0]))segment.push(item);}if(segment.length)segments.push(segment);return segments.map((items)=>items.map((item,index)=>`${index?"L":"M"}${x(item[0]).toFixed(1)},${y(item[1],maxValue,panelTop).toFixed(1)}`).join(" "));};
    const grid=(panelTop,maxValue,unit,label)=>`<g><text x="${left}" y="${panelTop-6}" fill="var(--primary-text-color)" font-size="12" font-weight="600">${esc(label)}</text>${[0,.25,.5,.75,1].map((f)=>{const yy=panelTop+panelH-f*panelH;const value=maxValue*f;return `<line x1="${left}" x2="${w-right}" y1="${yy}" y2="${yy}" stroke="var(--divider-color)"/><text x="${left-8}" y="${yy+4}" text-anchor="end" fill="var(--secondary-text-color)" font-size="10">${value.toFixed(maxValue<2?1:0)}${f===1?` ${esc(unit)}`:""}</text>`;}).join("")}</g>`;
    const gustBands=[]; let band=[];
    for(let i=0;i<Math.max(wind.length,gust.length);i+=1){const a=wind[i],b=gust[i];if(a&&b&&finite(a[0])&&finite(a[1])&&finite(b[1]))band.push([a[0],a[1],b[1]]);else if(band.length){gustBands.push(band);band=[];}}
    if(band.length)gustBands.push(band);
    const gustBandSvg=this._showGust?gustBands.map((items)=>{const upper=items.map((item)=>`${x(item[0]).toFixed(1)},${y(item[2],maxWind,windTop).toFixed(1)}`);const lower=[...items].reverse().map((item)=>`${x(item[0]).toFixed(1)},${y(item[1],maxWind,windTop).toFixed(1)}`);return `<polygon points="${[...upper,...lower].join(" ")}" fill="${COLORS.gust}" opacity=".16"/><path d="${items.map((item,index)=>`${index?"L":"M"}${x(item[0]).toFixed(1)},${y(item[2],maxWind,windTop).toFixed(1)}`).join(" ")}" fill="none" stroke="${COLORS.gust}" stroke-width="1.5" stroke-dasharray="5 4"/>`;}).join(""):"";
    const tickCount=5; const xTicks=Array.from({length:tickCount},(_,i)=>minX+(maxX-minX)*i/(tickCount-1));
    const selectedTime=this._selectedIndex>=0&&points[this._selectedIndex]?new Date(points[this._selectedIndex].recorded_at_utc).getTime():null;
    return `${coverage}${modelHint}<div class="chart-legend"><span><i style="background:${COLORS.speed}"></i>Vessel SOG · observed</span><span class="${windCount?"":"series-missing"}"><i style="background:${COLORS.wind}"></i>Wind · modeled samples</span>${this._showGust?`<span class="${gustCount?"":"series-missing"}"><i style="background:${COLORS.gust}"></i>Gust envelope</span>`:""}<span class="${waveCount?"":"series-missing"}"><i style="background:${COLORS.wave}"></i>Wave height · modeled samples</span></div>
      <svg class="analytics-svg" viewBox="0 0 ${w} ${h}" aria-label="Observed vessel speed, modeled wind, and modeled wave height on a shared time axis">
        ${grid(speedTop,maxSpeed,this._speedUnit,"Vessel speed")}${grid(windTop,maxWind,this._speedUnit,"Wind")}${grid(waveTop,maxWave,this._heightUnit,"Sea state")}
        ${pathSegments(speed,maxSpeed,speedTop).map((d)=>`<path d="${d}" fill="none" stroke="${COLORS.speed}" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join("")}
        ${gustBandSvg}
        ${pathSegments(wind,maxWind,windTop).map((d)=>`<path d="${d}" fill="none" stroke="${COLORS.wind}" stroke-width="2" stroke-dasharray="7 5" vector-effect="non-scaling-stroke"/>`).join("")}
        ${wind.filter((i)=>finite(i[0])&&finite(i[1])).map((i)=>`<circle cx="${x(i[0])}" cy="${y(i[1],maxWind,windTop)}" r="3.2" fill="${COLORS.wind}"><title>${esc(local(new Date(i[0]).toISOString()))}: wind ${i[1].toFixed(1)} ${esc(this._speedUnit)}</title></circle>`).join("")}
        ${pathSegments(wave,maxWave,waveTop).map((d)=>`<path d="${d}" fill="none" stroke="${COLORS.wave}" stroke-width="2" stroke-dasharray="7 5" vector-effect="non-scaling-stroke"/>`).join("")}
        ${wave.filter((i)=>finite(i[0])&&finite(i[1])).map((i)=>`<circle cx="${x(i[0])}" cy="${y(i[1],maxWave,waveTop)}" r="3.2" fill="${COLORS.wave}"><title>${esc(local(new Date(i[0]).toISOString()))}: waves ${i[1].toFixed(2)} ${esc(this._heightUnit)}</title></circle>`).join("")}
        ${selectedTime!=null?`<line x1="${x(selectedTime)}" x2="${x(selectedTime)}" y1="${speedTop}" y2="${waveTop+panelH}" stroke="var(--primary-text-color)" stroke-width="1" stroke-dasharray="4 4" opacity=".55"/>`:""}
        ${speed.filter((item)=>finite(item[1])&&finite(item[0])).map((item)=>`<circle data-action="point" data-index="${points.findIndex((p)=>p.id===item[2])}" cx="${x(item[0])}" cy="${y(item[1],maxSpeed,speedTop)}" r="8" fill="${COLORS.speed}" opacity=".001" tabindex="0" role="button" aria-label="Select report ${esc(local(new Date(item[0]).toISOString()))}"><title>${esc(local(new Date(item[0]).toISOString()))}: SOG ${item[1].toFixed(1)} ${esc(this._speedUnit)}</title></circle>`).join("")}
        <g fill="var(--secondary-text-color)" font-size="10">${xTicks.map((value,index)=>`<line x1="${x(value)}" x2="${x(value)}" y1="${speedTop}" y2="${waveTop+panelH}" stroke="var(--divider-color)" opacity=".35"/><text x="${x(value)}" y="${h-14}" text-anchor="${index===0?"start":index===xTicks.length-1?"end":"middle"}">${esc(new Date(value).toLocaleString([], {month:"numeric",day:"numeric",hour:"numeric",minute:"2-digit"}))}</text>`).join("")}</g>
      </svg><div class="analytics-note">Observed SOG is continuous only where Garmin reports exist. Modeled weather uses dashed interpolation between the displayed sample dots; gaps remain gaps.</div>`;
  }

  _passagesHtml() {
    const passages = this._state.passages || [];
    if (this._passageDraft) return this._passageFormHtml();
    return `<div class="section-title"><div><h2>Passages</h2><div class="muted">Editable time annotations over one continuous archive. Open-ended means “from this date onward”; it is not a live operating mode.</div></div>${this._admin ? `<button class="button primary" data-action="new-passage">Create passage</button>` : ""}</div>
      <section class="card table-wrap"><table><thead><tr><th>Name</th><th>Time range</th><th>Destination</th><th>Coverage</th><th></th></tr></thead><tbody>${passages.length ? passages.map((item) => `<tr><td><strong>${esc(item.name)}</strong><br><span class="muted">${item.ended_at_utc ? "Specific range" : "Open-ended range"}</span></td><td>${local(item.started_at_utc)}<br>${item.ended_at_utc ? `to ${local(item.ended_at_utc)}` : "No end date"}</td><td>${esc(item.destination_name || "—")}</td><td>${Number(item.report_count || 0).toLocaleString()} reports</td><td><div class="row"><button class="button small" data-action="edit-passage" data-id="${item.id}" ${this._busy ? "disabled" : ""}>${this._admin ? "View / edit" : "View"}</button>${this._admin && item.destination_name ? `<button class="button small" data-action="plan-route" data-id="${item.id}" ${this._busy ? "disabled" : ""}>${this._busy || "Calculate sailing route"}</button>` : ""}${this._admin ? `<button class="button small danger" data-action="delete-passage" data-id="${item.id}" ${this._busy ? "disabled" : ""}>Delete</button>` : ""}</div></td></tr>`).join("") : `<tr><td colspan="5"><div class="empty">No passage annotations yet. Your Garmin archive continues independently.</div></td></tr>`}</tbody></table></section>
      ${this._passageDetail ? `<div class="grid-main" style="margin-top:14px"><section class="card map-card">${this._mapHtml("passage-map",true)}</section><section class="card"><h2>${esc(this._passageDetail.name)}</h2><p>${local(this._passageDetail.started_at_utc)} ${this._passageDetail.ended_at_utc ? `to ${local(this._passageDetail.ended_at_utc)}` : "onward"}</p><p>Destination: ${esc(this._passageDetail.destination_name || "not set")} · ${Number(this._passageDetail.coverage?.report_count || 0).toLocaleString()} reports</p>${this._passageDetail.route ? this._routeHtml(this._passageDetail.route) : `<p class="muted">No calculated comparison is saved for this passage.</p>`}</section></div>${this._passageDetail.route?.context_status === "current" ? `<section class="card" style="margin-top:14px">${this._deviationHtml(this._passageDetail.route.summary?.actual?.modeled_comparison)}</section>` : ""}` : ""}`;
  }

  _newDraft() {
    const now = new Date(); return { id: null, name: "", start: this._localInput(now), end_mode: "open", end: "", departure_name: "", departure_latitude: "", departure_longitude: "", notes: "", destination_name: "", destination_latitude: "", destination_longitude: "", destination_effective: "", arrival_radius_nm: 2, destination_notes: "" };
  }

  async _editPassage(id) {
    this._passagePreview = null; this._passageDetail = null;
    if (id != null) this._passageId = id;
    if (id == null) this._passageDraft = this._newDraft();
    else {
      try {
        const item = await this._call("passage_detail", { passage_id: id, include_analysis: !this._admin }); this._passageDetail = item;
        if (!this._admin) {
          this._passageDraft = null;
          this._query = await this._call("points", { range:"passage", passage_id:id, source:"garmin_mapshare", max_points:10000 });
          this._selectedIndex = this._query.points.length ? this._query.points.length - 1 : -1;
          this._renderContent();
          return;
        }
        this._passageDraft = { id, name: item.name, start: this._localInput(new Date(item.started_at_utc)), end_mode: item.ended_at_utc ? "specific" : "open", end: item.ended_at_utc ? this._localInput(new Date(item.ended_at_utc)) : "", departure_name: item.departure_name || "", departure_latitude: item.departure_latitude ?? "", departure_longitude: item.departure_longitude ?? "", notes: item.notes || "", destination_name: item.destination_name || "", destination_latitude: item.destination_latitude ?? "", destination_longitude: item.destination_longitude ?? "", destination_effective: item.destination_effective_at_utc ? this._localInput(new Date(item.destination_effective_at_utc)) : "", arrival_radius_nm: item.arrival_radius_nm ?? 2, destination_notes: item.destination_versions?.at(-1)?.notes || "" };
      } catch (error) { this._show(error.message || String(error), true); }
    }
    this._renderContent();
  }

  _passageFormHtml() {
    const d = this._passageDraft; const preview = this._passagePreview;
    return `<div class="section-title"><div><h2>${d.id ? "Edit passage" : "Create passage"}</h2><div class="muted">Nothing is copied or deleted from the raw report archive.</div></div><button class="button" data-action="cancel-passage">Back to passages</button></div>
      <section class="card"><div class="form-grid"><label class="span2">Passage name<input id="p-name" value="${esc(d.name)}"></label><label>Start date and time<input id="p-start" type="datetime-local" value="${esc(d.start)}"></label><label>Range type<select id="end-mode"><option value="open" ${d.end_mode === "open" ? "selected" : ""}>Open-ended</option><option value="specific" ${d.end_mode === "specific" ? "selected" : ""}>Specific end</option></select></label>
      ${d.end_mode === "specific" ? `<label>End date and time<input id="p-end" type="datetime-local" value="${esc(d.end)}"></label>` : ""}<label class="span2">Departure name (optional)<input id="p-departure" value="${esc(d.departure_name)}"></label><label>Departure latitude (optional)<input id="p-departure-lat" type="number" step="any" value="${esc(d.departure_latitude)}"></label><label>Departure longitude (optional)<input id="p-departure-lon" type="number" step="any" value="${esc(d.departure_longitude)}"></label><label class="span4">Notes<textarea id="p-notes">${esc(d.notes)}</textarea></label></div>
      <h3 style="margin-top:20px">Destination (optional)</h3><div class="form-grid"><label class="span2">Destination name<input id="p-destination" value="${esc(d.destination_name)}"></label><label>Latitude<input id="p-destination-lat" type="number" step="any" value="${esc(d.destination_latitude)}"></label><label>Longitude<input id="p-destination-lon" type="number" step="any" value="${esc(d.destination_longitude)}"></label><label>Effective from (optional)<input id="p-destination-effective" type="datetime-local" value="${esc(d.destination_effective)}"></label><label>Context radius<input id="p-radius" type="number" min=".1" max="100" step=".1" value="${esc(d.arrival_radius_nm)}"></label><label class="span2">Destination notes<input id="p-destination-notes" value="${esc(d.destination_notes)}"></label></div>
      <div class="row"><button class="button primary" data-action="preview-passage">Preview archive coverage</button>${preview ? `<button class="button primary" data-action="save-passage">Save passage</button>` : ""}</div></section>
      ${preview ? `<section class="coverage ${preview.conflicts?.length || preview.destination_versions_removed ? "warning" : ""}" style="margin-top:14px"><strong>Coverage preview</strong><p>${preview.report_count.toLocaleString()} Garmin reports · ${preview.gap_count} gaps over 90 minutes · first ${local(preview.first_report_utc)} · last ${local(preview.last_report_utc)}</p>${preview.conflicts?.length ? `<p><strong>Overlaps:</strong> ${preview.conflicts.map((item) => esc(item.name)).join(", ")}. Overlap is allowed; confirm it is intentional.</p>` : ""}${preview.destination_versions_removed ? `<p><strong>Destination removal:</strong> saving will remove ${preview.destination_versions_removed} saved destination version${preview.destination_versions_removed === 1 ? "" : "s"} from this passage and mark its route comparison stale.</p>` : ""}<span class="muted">Raw reports unchanged: yes. Save is locked to this exact preview.</span></section><section class="card map-card" style="margin-top:14px">${this._mapHtml("passage-map",true)}</section>` : ""}
      ${this._passageDetail?.route ? `<section class="card" style="margin-top:14px">${this._routeHtml(this._passageDetail.route)}</section>` : ""}`;
  }

  _capturePassageDraft() {
    if (!this._passageDraft) return;
    const get = (id) => this.shadowRoot.getElementById(id)?.value ?? "";
    Object.assign(this._passageDraft, { name: get("p-name"), start: get("p-start"), end_mode: get("end-mode") || this._passageDraft.end_mode, end: get("p-end"), departure_name: get("p-departure"), departure_latitude: get("p-departure-lat"), departure_longitude: get("p-departure-lon"), notes: get("p-notes"), destination_name: get("p-destination"), destination_latitude: get("p-destination-lat"), destination_longitude: get("p-destination-lon"), destination_effective: get("p-destination-effective"), arrival_radius_nm: get("p-radius") || 2, destination_notes: get("p-destination-notes") });
  }

  _passagePayload() {
    this._capturePassageDraft(); const d = this._passageDraft;
    if (!d.name.trim() || !d.start) throw new Error("Passage name and start are required.");
    const payload = { ...(d.id ? { passage_id: d.id } : {}), name: d.name.trim(), start_utc: new Date(d.start).toISOString(), end_utc: d.end_mode === "specific" ? new Date(d.end).toISOString() : null, departure_name: d.departure_name.trim(), notes: d.notes };
    if ((d.departure_latitude === "") !== (d.departure_longitude === "")) throw new Error("Enter both departure coordinates or neither.");
    if (d.departure_latitude !== "") { payload.departure_latitude = Number(d.departure_latitude); payload.departure_longitude = Number(d.departure_longitude); }
    const anyDestination = d.destination_name || d.destination_latitude !== "" || d.destination_longitude !== "";
    if (anyDestination) {
      if (!d.destination_name || d.destination_latitude === "" || d.destination_longitude === "") throw new Error("Destination name, latitude, and longitude are required together.");
      payload.destination = { name: d.destination_name.trim(), latitude: Number(d.destination_latitude), longitude: Number(d.destination_longitude), arrival_radius_nm: Number(d.arrival_radius_nm || 2), effective_at_utc: d.destination_effective ? new Date(d.destination_effective).toISOString() : payload.start_utc, notes: d.destination_notes };
    }
    payload.clear_destination = !payload.destination && Boolean(d.id);
    return payload;
  }

  async _previewPassage() { try { const payload=this._passagePayload(); this._passagePreview = await this._call("passage_preview", payload); const end=payload.end_utc || this._state.archive?.last_recorded_at_utc || new Date().toISOString(); this._query=await this._call("points",{range:"custom",start_utc:payload.start_utc,end_utc:end,source:"garmin_mapshare",max_points:10000}); this._selectedIndex=this._query.points.length?this._query.points.length-1:-1; this._renderContent(); } catch (error) { this._show(error.message || String(error), true); } }
  async _savePassage() { try { const payload = this._passagePayload(); await this._call("passage_save", { ...payload, preview_token: this._passagePreview.preview_token }); this._passageDraft = null; this._passagePreview = null; this._passageDetail = null; await this._load(false); this._setTab("passages"); this._show("Passage annotation saved. Raw Garmin reports were not modified."); } catch (error) { this._show(error.message || String(error), true); } }
  async _deletePassage(id) { if (!confirm("Delete this passage annotation? Raw reports and weather samples remain.")) return; try { await this._call("passage_delete", { passage_id: id }); await this._load(false); this._show("Passage metadata deleted; raw reports retained."); } catch (error) { this._show(error.message || String(error), true); } }

  async _planRoute(id) {
    this._passageId = id;
    try { this._busy = "Calculating route…"; this._show("Building a water-valid, sailing-aware weather route…"); this._renderContent(); const route = await this._call("route_plan", { passage_id: id }); this._passageDetail = await this._call("passage_detail", { passage_id: id }); this._query = await this._call("points", { range:"passage", passage_id:id, source:"garmin_mapshare", max_points:10000 }); this._selectedIndex = this._query.points.length ? this._query.points.length - 1 : -1; this._show(route.summary?.method === "xweather_sailing_search" ? "Water-valid sailing-weather route saved." : "Water-valid reference saved. No weather sailing optimization is claimed."); this._renderContent(); }
    catch (error) { this._show(error.message || String(error), true); } finally { this._busy = ""; this._renderContent(); }
  }

  _routeHtml(route) {
    const s=route.summary||{}; const selected=s.selected||{}; const actual=s.actual||{}; const reference=s.reference||{}; const baseline=s.baseline||{};
    const contextWarning=route.context_status&&route.context_status!=="current"?`<div class="notice">${esc(route.context_warning||"Recalculate this route before using it for analysis.")}</div>`:"";
    const method=s.method==="xweather_sailing_search"?"Sailing weather search":"Water-valid reference";
    const directState=reference.water_valid===false?"Rejected · crosses modeled land":reference.water_valid===true?"Water-valid reference only":"Not evaluated";
    return `<h2>Sailing route analysis</h2>${contextWarning}<div class="metrics">${this._metric("Method",method)}${this._metric("Selected distance",num(selected.distance_nm,1," nmi"))}${this._metric("Estimated duration",num(selected.estimated_hours,1," h"))}${this._metric("Estimated arrival",local(selected.eta_utc))}</div>
      <div class="coverage"><strong>Hard validity checks</strong><p>Selected path: ${selected.land_valid===false?'<span class="danger-text">invalid</span>':"water-valid in bundled land mask"} · no-go violations: ${selected.no_go_violations==null?"not evaluated":Number(selected.no_go_violations)} · assumed minimum upwind TWA: ${num(s.profile?.no_go_angle_deg,1,"°")}</p><p><strong>Direct geodesic:</strong> ${esc(directState)} · ${num(reference.distance_nm,1," nmi")}. The direct line is never a scored candidate.</p><p><strong>Shortest water reference:</strong> ${num(baseline.distance_nm,1," nmi")} · reference only.</p>${s.endpoint_notes?.length?`<p><strong>Coastal endpoint handling:</strong> ${s.endpoint_notes.map(esc).join(" ")}</p>`:""}</div>
      <h3 style="margin-top:14px">Actual track ${actual.through_report_utc?`through ${local(actual.through_report_utc)}`:"within the saved analysis range"}</h3><div class="metrics">${this._metric("Recorded distance",num(actual.recorded_distance_nm,1," nmi"))}${this._metric("Observed elapsed",num(actual.elapsed_hours,1," h"))}${this._metric("Range remaining",num(actual.range_remaining_nm,1," nmi"))}</div><p class="muted">${esc(actual.coverage_note||"Actual analysis requires archived Garmin reports.")}</p>
      <p class="muted">${esc(s.disclaimer||"Planning/analysis aid only—not a navigable route.")}</p>${s.warnings?.length?`<p class="danger-text">${s.warnings.map(esc).join(" · ")}</p>`:""}
      <details><summary>Advanced route-search details</summary><div class="table-wrap"><table><thead><tr><th>Searched result</th><th>Distance</th><th>Estimated hours</th><th>Risk</th><th>Major turns</th><th>Weather steps</th></tr></thead><tbody>${(s.candidates||[]).map((item)=>`<tr><td>${esc(item.label)}${item.key===selected.key?" · selected":""}</td><td>${num(item.distance_nm,1," nmi")}</td><td>${num(item.estimated_hours,1)}</td><td>${num(item.risk_score,1)}</td><td>${item.maneuvers??"—"}</td><td>${item.weather_coverage_steps??0}</td></tr>`).join("")}</tbody></table><p class="muted">Great-circle diagnostic: maximum cross-track distance from the direct reference ${num(actual.max_cross_track_from_direct_nm,1," nmi")}.</p></div></details>`;
  }

  _deviationStorageKey() { return "bluesky_passage_deviation_scales_v1"; }
  _loadDeviationScales() {
    try { const value=JSON.parse(localStorage.getItem(this._deviationStorageKey())||"{}"); return value&&typeof value==="object"?value:{}; }
    catch(_error){ return {}; }
  }
  _saveDeviationScales() { try { localStorage.setItem(this._deviationStorageKey(),JSON.stringify(this._deviationScales)); } catch(_error){} }
  _deviationSetting() { const key=String(this._passageId||this._passageDetail?.id||"default"); return this._deviationScales[key]||{mode:"auto",custom:20}; }
  _setDeviationScale(mode) { const key=String(this._passageId||this._passageDetail?.id||"default"); const current=this._deviationSetting(); this._deviationScales[key]={...current,mode}; this._saveDeviationScales(); }
  _setDeviationCustom(value) { const parsed=Math.max(.1,Math.min(1000,Number(value)||20)); const key=String(this._passageId||this._passageDetail?.id||"default"); this._deviationScales[key]={mode:"custom",custom:parsed}; this._saveDeviationScales(); }
  _autoDeviationScale(value) { const required=Math.max(.1,Number(value)||0)*1.12; return [1,2,5,10,20,50,100,200,500,1000].find((item)=>item>=required)||Math.ceil(required/1000)*1000; }
  _deviationValue(value) { if(!finite(value))return "—"; const number=Math.abs(Number(value)); if(number<.05)return "On modeled route"; return `${number.toFixed(1)} nmi ${Number(value)<0?"port":"starboard"}`; }
  _hoursDelta(value) { if(!finite(value))return "—"; const hours=Number(value); const totalMinutes=Math.round(Math.abs(hours)*60); if(totalMinutes<1)return "On modeled time"; const h=Math.floor(totalMinutes/60),m=totalMinutes%60; return `${hours>0?"+":"−"}${h}h ${m}m ${hours>0?"behind":"ahead"}`; }
  _deviationHtml(comparison) {
    if(!comparison?.available)return `<div class="deviation-panel"><div class="deviation-head"><div><h3>Actual vs modeled</h3><div class="muted">${esc(comparison?.reason||"Recalculate the sailing route to compare the recorded track with the modeled path.")}</div></div></div></div>`;
    const setting=this._deviationSetting(); const autoScale=this._autoDeviationScale(Math.max(Number(comparison.port_extent_nm||0),Number(comparison.starboard_extent_nm||0),Number(comparison.current_deviation_nm||0)));
    const selectedMode=setting.mode||"auto"; const scale=selectedMode==="auto"?autoScale:selectedMode==="custom"?Math.max(.1,Number(setting.custom||20)):Number(selectedMode);
    const toPercent=(value)=>50+Math.max(-scale,Math.min(scale,Number(value)||0))/scale*50;
    const port=-Number(comparison.port_extent_nm||0),starboard=Number(comparison.starboard_extent_nm||0),current=Number(comparison.current_signed_deviation_nm||0);
    const rangeLeft=Math.min(toPercent(port),toPercent(starboard)),rangeWidth=Math.abs(toPercent(starboard)-toPercent(port));
    const overflow=Math.abs(current)>scale||Math.max(Math.abs(port),Math.abs(starboard))>scale;
    const options=["auto","1","2","5","10","20","50","100"].map((value)=>`<option value="${value}" ${selectedMode===value?"selected":""}>${value==="auto"?`Auto (±${autoScale} nmi)`:`±${value} nmi`}</option>`).join("");
    const samples=comparison.samples||[]; const w=900,h=180,left=54,right=20,top=18,bottom=36; const x=(value)=>left+Math.max(0,Math.min(100,Number(value)||0))/100*(w-left-right); const y=(value)=>top+(scale-Math.max(-scale,Math.min(scale,Number(value)||0)))/(2*scale)*(h-top-bottom);
    const path=samples.length?samples.map((item,index)=>`${index?"L":"M"}${x(item.modeled_progress_percent).toFixed(1)},${y(item.signed_deviation_nm).toFixed(1)}`).join(" "):"";
    const extra=finite(comparison.extra_distance_nm)?`${Number(comparison.extra_distance_nm)>=0?"+":""}${Number(comparison.extra_distance_nm).toFixed(1)} nmi`:"—";
    return `<div class="deviation-panel"><div class="deviation-head"><div><h3>Actual vs modeled</h3><div class="muted">Recorded Garmin track compared with the saved modeled sailing path through ${num(comparison.modeled_progress_percent,0,"%")}. Negative is port; positive is starboard.</div></div><div class="deviation-controls"><label>Deviation scale<select id="deviation-scale">${options}<option value="custom" ${selectedMode==="custom"?"selected":""}>Custom…</option></select></label>${selectedMode==="custom"?`<label>± nmi<input id="deviation-custom" type="number" min="0.1" max="1000" step="0.1" value="${esc(setting.custom||20)}"></label>`:""}<label><span>Map connectors</span><input id="deviation-overlay" type="checkbox" ${this._showDeviationOverlay?"checked":""}></label></div></div>
      <div class="metrics" style="margin-top:12px">${this._metric("Current deviation",this._deviationValue(current))}${this._metric("Max deviation",`${num(comparison.max_deviation_nm,1," nmi")} ${esc(comparison.max_deviation_side||"")}`)}${this._metric("Extra distance",extra)}${this._metric("Efficiency",num(comparison.distance_efficiency_percent,0,"%"))}</div>
      <div class="deviation-scale-wrap"><div class="deviation-labels"><span>PORT</span><span>ON MODELED ROUTE</span><span>STARBOARD</span></div><div class="deviation-track" role="img" aria-label="Current route deviation ${esc(this._deviationValue(current))}; display scale plus or minus ${scale} nautical miles"><span class="deviation-range" style="left:${rangeLeft}%;width:${Math.max(1,rangeWidth)}%"></span><span class="deviation-marker" style="left:${toPercent(current)}%" title="${esc(this._deviationValue(current))}"></span></div><div class="deviation-ticks"><span>−${scale} nmi</span><span>0</span><span>+${scale} nmi</span></div>${overflow?`<div class="deviation-overflow">One or more deviations exceed the selected ±${scale} nmi display scale; values are clipped visually, not numerically.</div>`:""}</div>
      <div class="deviation-history"><div class="muted" style="margin-bottom:5px">Deviation over modeled-route progress</div>${samples.length?`<svg viewBox="0 0 ${w} ${h}" aria-label="Port and starboard deviation over modeled route progress"><line x1="${left}" x2="${w-right}" y1="${y(0)}" y2="${y(0)}" stroke="var(--primary-text-color)" opacity=".45"/><line x1="${left}" x2="${w-right}" y1="${y(scale/2)}" y2="${y(scale/2)}" stroke="var(--divider-color)"/><line x1="${left}" x2="${w-right}" y1="${y(-scale/2)}" y2="${y(-scale/2)}" stroke="var(--divider-color)"/><path d="${path}" fill="none" stroke="var(--primary-color)" stroke-width="2.2" vector-effect="non-scaling-stroke"/>${samples.map((item)=>`<circle data-action="deviation-point" data-report-id="${item.report_id}" cx="${x(item.modeled_progress_percent)}" cy="${y(item.signed_deviation_nm)}" r="7" opacity=".001" tabindex="0" role="button"><title>${esc(local(item.recorded_at_utc))}: ${esc(this._deviationValue(item.signed_deviation_nm))}</title></circle>`).join("")}<g fill="var(--secondary-text-color)" font-size="10"><text x="${left-8}" y="${top+4}" text-anchor="end">STARBOARD</text><text x="${left-8}" y="${y(0)+4}" text-anchor="end">ROUTE</text><text x="${left-8}" y="${h-bottom+4}" text-anchor="end">PORT</text>${[0,25,50,75,100].map((value)=>`<text x="${x(value)}" y="${h-10}" text-anchor="${value===0?"start":value===100?"end":"middle"}">${value}%</text>`).join("")}</g></svg>`:`<div class="empty">No deviation history is available.</div>`}</div>
      <div class="comparison-submetrics"><div class="comparison-submetric"><span class="muted">Modeled progress</span><strong>${num(comparison.modeled_progress_percent,0,"%")}</strong></div><div class="comparison-submetric"><span class="muted">Actual distance</span><strong>${num(comparison.actual_distance_nm,1," nmi")}</strong></div><div class="comparison-submetric"><span class="muted">Modeled distance to progress</span><strong>${num(comparison.modeled_distance_to_progress_nm,1," nmi")}</strong></div><div class="comparison-submetric"><span class="muted">Time delta</span><strong>${esc(this._hoursDelta(comparison.time_delta_hours))}</strong></div></div><p class="muted">${esc(comparison.coverage_note||"")}</p></div>`;
  }

  _settingsHtml() {
    const archive = this._state.archive || {}; const weather = this._state.weather || {}; const profile = this._state.vessel_profile?.profile || {}; const jobs = this._state.backfill_jobs || []; const job = this._backfillJob || jobs[0];
    const progress = job ? Math.round(100 * Number(job.chunks_completed || 0) / Math.max(Number(job.chunks_total || 1), 1)) : 0;
    if (!this._admin) return `<section class="card"><h2>Data & settings</h2><p>Administrator access is required to change data, run provider requests, export, or test notifications.</p><div class="detail-grid"><div><div class="detail-label">Archived records</div><div class="detail-value">${Number(archive.total_points || 0).toLocaleString()}</div></div><div><div class="detail-label">Integrity</div><div class="detail-value">${esc(archive.integrity)}</div></div><div><div class="detail-label">Xweather</div><div class="detail-value">${weather.configured ? "Configured" : "Not configured"}</div></div></div></section>`;
    return `<div class="two"><section class="card"><h2>Archive health</h2><div class="detail-grid"><div><div class="detail-label">Records</div><div class="detail-value">${Number(archive.total_points || 0).toLocaleString()}</div></div><div><div class="detail-label">On-disk size</div><div class="detail-value">${bytes(archive.database_bytes)}</div></div><div><div class="detail-label">Earliest</div><div class="detail-value">${local(archive.first_recorded_at_utc)}</div></div><div><div class="detail-label">Latest</div><div class="detail-value">${local(archive.last_recorded_at_utc)}</div></div><div><div class="detail-label">Schema</div><div class="detail-value">v${archive.schema_version}</div></div><div><div class="detail-label">Integrity</div><div class="detail-value">${esc(archive.integrity)}</div></div></div><div class="row"><button class="button" data-action="integrity">Run integrity check</button><button class="button" data-action="test-notification">Test notification</button><button class="button" data-action="open-options">Integration Configure</button></div></section>
      <section class="card"><h2>Online sources</h2><p><strong>Garmin:</strong> ${this._state.runtime?.source_available ? "available" : "unavailable"}<br><span class="muted">Normal 10-minute poll · rolling 48-hour request · unseen records only</span></p><p><strong>Xweather:</strong> ${weather.configured ? "configured" : "not configured"}<br><span class="muted">Backend-only credentials · on-demand cache · ${Number(weather.stored_samples || 0).toLocaleString()} stored model samples</span></p>${weather.last_error ? `<p class="danger-text">${esc(weather.last_error)}</p>` : ""}<p><strong>PredictWind:</strong> ${this._state.links?.predictwind ? "public link configured" : "not configured"}</p><div class="row"><a class="button" href="${esc(this._state.links?.garmin_mapshare || "#")}" target="_blank" rel="noopener">Open Garmin MapShare</a>${this._state.links?.predictwind ? `<a class="button" href="${esc(this._state.links.predictwind)}" target="_blank" rel="noopener">Open PredictWind</a>` : ""}<button class="button" data-action="refresh" ${this._busy ? "disabled" : ""}>${this._busy || "Refresh Garmin now"}</button></div></section></div>
      <section class="card" style="margin-top:14px"><div class="section-title"><div><h2>Garmin historical backfill</h2><div class="muted">Preview first. Requests run in resumable seven-day chunks; duplicates are ignored. Commit creates one rollbackable import batch.</div></div></div><div class="toolbar"><label>Start date<input id="backfill-start" type="date" value="${esc(this._backfillStart)}"></label><label>End date<input id="backfill-end" type="date" value="${esc(this._backfillEnd)}"></label><button class="button primary" data-action="backfill-preview" ${job && ["pending","running"].includes(job.status) ? "disabled" : ""}>Preview Garmin availability</button></div>
      ${job ? `<div class="coverage ${job.status === "failed" ? "warning" : ""}"><strong>${esc(job.phase)} job #${job.id} · ${esc(job.status)}</strong><p class="muted">Requested range: ${local(job.start_utc)} → ${local(job.end_utc)} · ${Number(job.chunks_total || 0).toLocaleString()} chunk${Number(job.chunks_total || 0) === 1 ? "" : "s"}</p><div class="progress"><span style="width:${progress}%"></span></div><p>${job.chunks_completed}/${job.chunks_total} chunks · ${Number(job.records_returned || 0).toLocaleString()} returned · ${Number(job.records_inserted || 0).toLocaleString()} ${job.phase === "preview" ? "expected new" : "inserted"} · ${Number(job.records_duplicated || 0).toLocaleString()} already archived</p>${job.error ? `<p class="danger-text">${esc(job.error)}</p>` : ""}<div class="row">${job.phase === "preview" && job.status === "completed" ? `<button class="button primary" data-action="backfill-commit" data-id="${job.id}">Import previewed range</button>` : ""}${job.status === "failed" ? `<button class="button" data-action="backfill-resume" data-id="${job.id}">Resume failed chunk</button>` : ""}${["pending","running","failed"].includes(job.status) ? `<button class="button danger" data-action="backfill-cancel" data-id="${job.id}">Cancel</button>` : ""}</div></div>` : ""}</section>
      <section class="card" style="margin-top:14px"><h2>Vessel performance profile</h2><p class="muted">All fields are optional. Missing values use an observed, hull-speed, or generic fallback in that order. Saving recalculates the selected passage route when a passage context is chosen.</p><div class="form-grid">
      ${this._profileField("Vessel name", "vessel_name", profile.vessel_name)}${this._profileField("Hull configuration", "hull_configuration", profile.hull_configuration)}${this._profileField("Length overall (ft)", "length_overall_ft", profile.length_overall_ft, true)}${this._profileField("Waterline length (ft)", "waterline_length_ft", profile.waterline_length_ft, true)}${this._profileField("Beam (ft)", "beam_ft", profile.beam_ft, true)}${this._profileField("Draft (ft)", "draft_ft", profile.draft_ft, true)}${this._profileField("Displacement (lb)", "displacement_lb", profile.displacement_lb, true)}${this._profileField("Sail area (sq ft)", "sail_area_sqft", profile.sail_area_sqft, true)}${this._profileField("Engine cruise (kn)", "engine_cruise_speed_kn", profile.engine_cruise_speed_kn, true)}${this._profileField("Observed cruise (kn)", "observed_cruise_speed_kn", profile.observed_cruise_speed_kn, true)}${this._profileField("Comfortable wave max (m)", "max_comfortable_wave_m", profile.max_comfortable_wave_m, true)}${this._profileField("Minimum upwind TWA / no-go (deg)", "minimum_upwind_twa_deg", profile.minimum_upwind_twa_deg, true)}
      <label class="span4">Polar table JSON (optional)<textarea id="profile-polar" placeholder='[{"twa_deg":60,"tws_kn":12,"boat_speed_kn":6.2}]'>${esc(profile.polar_table?.length ? JSON.stringify(profile.polar_table, null, 2) : "")}</textarea></label></div><div class="row"><label>Recalculate passage<select id="profile-passage"><option value="">None</option>${(this._state.passages || []).map((item) => `<option value="${item.id}" ${this._passageId === item.id ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label><button class="button primary" data-action="save-profile">Save performance profile</button></div></section>
      ${this._recorderRecoveryHtml()}
      <section class="card" style="margin-top:14px"><h2>Manual source import</h2><p class="muted">Use only when Garmin backfill cannot supply a source. JSON/GeoJSON and GPX rows are source-labelled and rollbackable.</p><div class="toolbar"><label>File<input id="history-file" type="file" accept=".json,.geojson,.gpx,.xml"></label><label>Source<select id="history-source"><option value="predictwind_snapshot">PredictWind snapshot</option><option value="gpx_import">GPX import</option><option value="csv_import">CSV-normalized JSON</option></select></label><button class="button" data-action="import-history">Import file</button></div><div class="table-wrap"><table><thead><tr><th>Batch</th><th>Source</th><th>Status</th><th>Rows</th><th></th></tr></thead><tbody>${(this._state.imports || []).map((item) => `<tr><td>${esc(item.filename)}</td><td>${esc(item.source)}</td><td>${esc(item.status)}</td><td>${item.rows_inserted}/${item.rows_seen}</td><td>${["completed","failed"].includes(item.status) ? `<button class="button small danger" data-action="rollback-import" data-id="${item.id}">Rollback</button>` : ""}</td></tr>`).join("")}</tbody></table></div></section>
      <section class="card safety"><strong>Storage choice:</strong> the current application/high-endurance microSD is supported. This release bounds the SQLite WAL, deduplicates overlapping polls, chunks backfill, and never runs weather/routing continuously. An SSD is a later resilience upgrade, not an installation requirement.</section>`;
  }

  _recorderCandidates(domains, pattern) {
    return Object.entries(this._hass?.states || {})
      .filter(([entityId, state]) => {
        if (!domains.some((domain) => entityId.startsWith(`${domain}.`))) return false;
        const name = String(state.attributes?.friendly_name || "").toLowerCase();
        return !entityId.includes("bluesky_passage")
          && !name.startsWith("bluesky passage")
          && state.attributes?.source !== "garmin_mapshare";
      })
      .map(([entityId, state]) => {
        const name = String(state.attributes?.friendly_name || entityId);
        const searchable = `${entityId} ${name}`.toLowerCase();
        let score = pattern.test(searchable) ? 20 : 0;
        if (/inreach|garmin|mapshare/.test(searchable)) score += 6;
        if (entityId.startsWith("device_tracker.") && finite(state.attributes?.latitude) && finite(state.attributes?.longitude)) score += 10;
        return { entityId, name, score };
      })
      .sort((first, second) => second.score - first.score || first.name.localeCompare(second.name));
  }

  _recorderEntitySelect(label, id, key, domains, pattern, required = false) {
    const candidates = this._recorderCandidates(domains, pattern);
    if (!(key in this._recorderSelections)) {
      this._recorderSelections[key] = candidates[0]?.score > 0 ? candidates[0].entityId : "";
    }
    const selected = this._recorderSelections[key] || "";
    return `<label>${esc(label)}<select id="${id}"><option value="">${required ? "Select an entity" : "Not used"}</option>${candidates.map((item) => `<option value="${esc(item.entityId)}" ${selected === item.entityId ? "selected" : ""}>${esc(item.name)} · ${esc(item.entityId)}</option>`).join("")}</select></label>`;
  }

  _recorderRecoveryHtml() {
    const start = this._recorderStart || this._dateInput(new Date(Date.now() - 10 * 86400000));
    const end = this._recorderEnd || this._dateInput(new Date());
    const preview = this._recorderPreview;
    return `<details class="card" style="margin-top:14px" ${preview ? "open" : ""}><summary><strong>Legacy Home Assistant Recorder recovery (fallback)</strong></summary>
      <p class="muted">Use this only if the Garmin backfill preview cannot reproduce v1 history that is still visible in Home Assistant History. It reads selected entities through Home Assistant's authenticated history API, previews duplicates, and writes a separate rollbackable source batch. Recorder itself is never changed.</p>
      <div class="form-grid"><label>Start date<input id="recorder-start" type="date" value="${esc(start)}"></label><label>End date<input id="recorder-end" type="date" value="${esc(end)}"></label>
      ${this._recorderEntitySelect("Position entity (required)", "recorder-tracker", "tracker", ["device_tracker"], /inreach|position|tracker|mapshare/, true)}
      ${this._recorderEntitySelect("Report-time sensor", "recorder-time", "time", ["sensor"], /last.?updated|last.?report|timestamp|report.?time/)}
      ${this._recorderEntitySelect("Velocity / SOG sensor", "recorder-speed", "speed", ["sensor"], /velocity|speed.?over.?ground|\bsog\b/)}
      ${this._recorderEntitySelect("Course / COG sensor", "recorder-course", "course", ["sensor"], /course|course.?over.?ground|\bcog\b/)}
      ${this._recorderEntitySelect("Elevation sensor", "recorder-elevation", "elevation", ["sensor"], /elevation|altitude/)}
      ${this._recorderEntitySelect("Valid GPS sensor", "recorder-gps", "gps", ["binary_sensor"], /valid.?gps|gps.?fix/)}
      ${this._recorderEntitySelect("Emergency sensor", "recorder-emergency", "emergency", ["binary_sensor"], /emergency|\bsos\b/)}
      ${this._recorderEntitySelect("Last-text sensor", "recorder-text", "text", ["sensor"], /last.?text|message/)}
      </div><div class="row"><button class="button" data-action="recorder-preview" ${this._busy ? "disabled" : ""}>${this._busy || "Preview Recorder recovery"}</button></div>
      ${preview ? `<div class="coverage ${preview.rejected ? "warning" : ""}"><strong>Recorder preview</strong><p>${Number(preview.returned || 0).toLocaleString()} reconstructed rows · ${Number(preview.new || 0).toLocaleString()} new · ${Number(preview.duplicated || 0).toLocaleString()} already archived · ${Number(preview.rejected || 0).toLocaleString()} rejected</p><p>First ${local(preview.first_recorded_at_utc)} · last ${local(preview.last_recorded_at_utc)}</p><p class="muted">This is best-effort reconstruction from entity state history. Review the imported track afterward; optional values remain blank when no nearby source state exists.</p>${preview.new ? `<button class="button primary" data-action="recorder-import">Import previewed Recorder rows</button>` : ""}</div>` : ""}</details>`;
  }

  _captureRecorderSelections() {
    const fields = { tracker:"recorder-tracker", time:"recorder-time", speed:"recorder-speed", course:"recorder-course", elevation:"recorder-elevation", gps:"recorder-gps", emergency:"recorder-emergency", text:"recorder-text" };
    for (const [key, id] of Object.entries(fields)) this._recorderSelections[key] = this.shadowRoot.getElementById(id)?.value || "";
    this._recorderStart = this.shadowRoot.getElementById("recorder-start")?.value || "";
    this._recorderEnd = this.shadowRoot.getElementById("recorder-end")?.value || "";
  }

  _historyRows(history, entityId) {
    let carriedAttributes = {};
    return (history?.[entityId] || []).map((item) => {
      if (item.a || item.attributes) carriedAttributes = { ...(item.a || item.attributes) };
      const rawTime = item.lu ?? item.last_updated ?? item.last_changed;
      const timeMs = typeof rawTime === "number" ? rawTime * (rawTime < 1e12 ? 1000 : 1) : new Date(rawTime).getTime();
      return { state: item.s ?? item.state, attributes: { ...carriedAttributes }, timeMs };
    }).filter((item) => Number.isFinite(item.timeMs)).sort((first, second) => first.timeMs - second.timeMs);
  }

  _stateAt(rows, timeMs, maxAgeMinutes = null) {
    let previous = null; let following = null;
    for (const row of rows) {
      if (row.timeMs <= timeMs) previous = row;
      else { following = row; break; }
    }
    if (previous && (maxAgeMinutes == null || timeMs - previous.timeMs <= maxAgeMinutes * 60000)) return { ...previous, deltaMs: timeMs - previous.timeMs };
    if (following && following.timeMs - timeMs <= Math.min(maxAgeMinutes ?? 2, 2) * 60000) return { ...following, deltaMs: following.timeMs - timeMs };
    return null;
  }

  _historyNumber(row, entityId, kind) {
    if (!row || !finite(row.state)) return null;
    let value = Number(row.state);
    const unit = String(row.attributes?.unit_of_measurement || this._hass.states?.[entityId]?.attributes?.unit_of_measurement || "").toLowerCase();
    if (kind === "speed") {
      if (/km\/h|kph/.test(unit)) value /= 1.852;
      else if (/mph/.test(unit)) value /= 1.150779448;
      else if (/m\/s/.test(unit)) value *= 1.943844492;
    }
    if (kind === "elevation" && /ft|feet/.test(unit)) value *= 0.3048;
    return Number.isFinite(value) ? value : null;
  }

  _historyBoolean(row) {
    if (!row) return null;
    const value = String(row.state ?? "").toLowerCase();
    if (["on","true","yes","valid","1","connected"].includes(value)) return true;
    if (["off","false","no","invalid","0","disconnected"].includes(value)) return false;
    return null;
  }

  _recorderRecordsFromHistory(history, startMs) {
    const selected = this._recorderSelections;
    const trackerRows = this._historyRows(history, selected.tracker);
    const rows = Object.fromEntries(Object.entries(selected).filter(([key, entityId]) => key !== "tracker" && entityId).map(([key, entityId]) => [key, this._historyRows(history, entityId)]));
    const deviceName = this._hass.states?.[selected.tracker]?.attributes?.friendly_name || "Legacy Home Assistant tracker";
    const unique = new Map();
    for (const tracker of trackerRows) {
      const latitude = Number(tracker.attributes?.latitude); const longitude = Number(tracker.attributes?.longitude);
      if (!finite(latitude) || !finite(longitude) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) continue;
      let recordedMs = tracker.timeMs;
      const timeState = this._stateAt(rows.time || [], tracker.timeMs, 20);
      const sourceTime = timeState ? new Date(String(timeState.state)).getTime() : NaN;
      if (Number.isFinite(sourceTime) && Math.abs(sourceTime - tracker.timeMs) <= 30 * 60000) recordedMs = sourceTime;
      else {
        const attributeTime = new Date(tracker.attributes?.recorded_at_utc || "").getTime();
        if (Number.isFinite(attributeTime) && Math.abs(attributeTime - tracker.timeMs) <= 30 * 60000) recordedMs = attributeTime;
      }
      if (tracker.timeMs <= startMs + 1000 && recordedMs === tracker.timeMs) continue;
      const recordedAt = new Date(recordedMs).toISOString();
      const speedRow = this._stateAt(rows.speed || [], tracker.timeMs, 20);
      const courseRow = this._stateAt(rows.course || [], tracker.timeMs, 20);
      const elevationRow = this._stateAt(rows.elevation || [], tracker.timeMs, 20);
      const gpsRow = this._stateAt(rows.gps || [], tracker.timeMs);
      const emergencyRow = this._stateAt(rows.emergency || [], tracker.timeMs);
      const textRow = this._stateAt(rows.text || [], tracker.timeMs, 2);
      const textValue = textRow && !["unknown","unavailable","none",""]
        .includes(String(textRow.state ?? "").trim().toLowerCase()) ? String(textRow.state) : null;
      const record = {
        timestamp: recordedAt, latitude, longitude,
        source_event_id: `recorder:${selected.tracker}:${recordedAt}:${latitude.toFixed(5)}:${longitude.toFixed(5)}`,
        device_name: deviceName,
        sog_kn: this._historyNumber(speedRow, selected.speed, "speed"),
        cog_true: this._historyNumber(courseRow, selected.course, "course"),
        elevation: this._historyNumber(elevationRow, selected.elevation, "elevation"),
        valid_gps_fix: this._historyBoolean(gpsRow),
        in_emergency: this._historyBoolean(emergencyRow),
        message_text: textValue,
      };
      unique.set(record.source_event_id, record);
    }
    return [...unique.values()].sort((first, second) => first.timestamp.localeCompare(second.timestamp));
  }

  async _previewRecorder() {
    try {
      this._captureRecorderSelections();
      if (!this._recorderSelections.tracker) throw new Error("Select the legacy position entity first.");
      if (!this._recorderStart || !this._recorderEnd) throw new Error("Choose both Recorder dates.");
      const start = new Date(`${this._recorderStart}T00:00:00`); const end = new Date(`${this._recorderEnd}T23:59:59.999`);
      if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || end <= start) throw new Error("Recorder end date must be after the start date.");
      if (end - start > 31 * 86400000) throw new Error("Preview at most 31 days at a time to keep Home Assistant responsive.");
      const entityIds = [...new Set(Object.values(this._recorderSelections).filter(Boolean))];
      this._busy = "Reading Recorder history…"; this._show("Reading the selected Home Assistant history without modifying Recorder…"); this._renderContent();
      const history = await this._hass.callWS({ type:"history/history_during_period", start_time:start.toISOString(), end_time:end.toISOString(), entity_ids:entityIds, minimal_response:false, no_attributes:false, significant_changes_only:false });
      const records = this._recorderRecordsFromHistory(history, start.getTime());
      if (!records.length) throw new Error("No timestamped positions were reconstructed. Check the entity and Recorder date range.");
      if (records.length > 10000) throw new Error("More than 10,000 positions were reconstructed. Choose a shorter date range.");
      const preview = await this._call("import_preview", { source:"ha_recorder", records });
      this._recorderRecords = records;
      this._recorderPreview = { ...preview, start_utc:start.toISOString(), end_utc:end.toISOString() };
      this._show("Recorder preview complete. Review the counts before importing."); this._renderContent();
    } catch (error) { this._show(error.message || String(error), true); }
    finally { this._busy = ""; this._renderContent(); }
  }

  async _importRecorder() {
    if (!this._recorderPreview || !this._recorderRecords.length) return;
    if (!confirm("Import exactly the previewed Recorder rows? The resulting batch can be rolled back.")) return;
    let importId = null;
    try {
      const serialized = JSON.stringify(this._recorderRecords);
      const sha256 = await this._sha256(new TextEncoder().encode(serialized));
      const filename = `Home Assistant Recorder ${this._recorderPreview.start_utc.slice(0,10)} to ${this._recorderPreview.end_utc.slice(0,10)}`;
      importId = (await this._call("import_begin", { source:"ha_recorder", filename, sha256 })).import_id;
      for (let offset = 0; offset < this._recorderRecords.length; offset += 500) await this._call("import_chunk", { import_id:importId, source:"ha_recorder", records:this._recorderRecords.slice(offset, offset + 500) });
      await this._call("import_finish", { import_id:importId });
      const inserted = this._recorderPreview.new;
      this._recorderPreview = null; this._recorderRecords = [];
      await this._load(false); this._show(`Recorder recovery imported ${Number(inserted).toLocaleString()} new rows. Inspect the recovered track before retiring v1.`);
    } catch (error) {
      if (importId != null) { try { await this._call("import_finish", { import_id:importId, failed:true, notes:String(error).slice(0,900) }); } catch (_ignored) {} }
      this._show(error.message || String(error), true);
    }
  }

  _profileField(label, key, value, numeric = false) { return `<label>${esc(label)}<input id="profile-${key}" ${numeric ? 'type="number" step="any" min="0"' : ""} value="${esc(value ?? "")}"></label>`; }

  async _saveProfile() {
    try {
      const keys = ["vessel_name","hull_configuration","length_overall_ft","waterline_length_ft","beam_ft","draft_ft","displacement_lb","sail_area_sqft","engine_cruise_speed_kn","observed_cruise_speed_kn","max_comfortable_wave_m","minimum_upwind_twa_deg"];
      const profile = {}; for (const key of keys) { const value = this.shadowRoot.getElementById(`profile-${key}`).value.trim(); if (value !== "") profile[key] = ["vessel_name","hull_configuration"].includes(key) ? value : Number(value); }
      const polar = this.shadowRoot.getElementById("profile-polar").value.trim(); if (polar) { profile.polar_table = JSON.parse(polar); if (!Array.isArray(profile.polar_table)) throw new Error("Polar table JSON must be an array."); }
      const passage = this.shadowRoot.getElementById("profile-passage").value;
      const result = await this._call("profile_save", { profile, ...(passage ? { passage_id: Number(passage) } : {}) }); await this._load(false); this._show(result.route_recalculated ? "Profile saved and the selected passage comparison was recalculated." : "Vessel profile saved.");
    } catch (error) { this._show(error.message || String(error), true); }
  }

  async _startBackfillPreview() {
    try {
      const start = this.shadowRoot.getElementById("backfill-start")?.value || this._backfillStart;
      const end = this.shadowRoot.getElementById("backfill-end")?.value || this._backfillEnd;
      if (!start || !end) throw new Error("Choose both backfill dates.");
      this._backfillStart = start; this._backfillEnd = end;
      const startUtc = new Date(`${start}T00:00:00`).toISOString();
      const endUtc = new Date(`${end}T23:59:59.999`).toISOString();
      if (new Date(startUtc) >= new Date(endUtc)) throw new Error("Backfill end date must be after the start date.");
      this._backfillJob = await this._call("backfill_preview", { start_utc:startUtc, end_utc:endUtc });
      this._renderContent();
      await this._runBackfill(this._backfillJob.id);
    } catch (error) { this._show(error.message || String(error), true); }
  }
  async _startBackfillCommit(id) { if (!confirm("Import the previewed Garmin range? The batch can be rolled back later.")) return; try { this._backfillJob = await this._call("backfill_commit", { preview_job_id: id }); this._renderContent(); await this._runBackfill(this._backfillJob.id); } catch (error) { this._show(error.message || String(error), true); } }
  async _runBackfill(id, resume = false) { try { if (resume) { this._backfillJob = await this._call("backfill_step", { job_id:id }); this._renderContent(); } while (!["completed","failed","cancelled"].includes(this._backfillJob.status)) { this._backfillJob = await this._call("backfill_step", { job_id: id }); this._renderContent(); await new Promise((resolve) => setTimeout(resolve, 350)); } await this._load(false); this._show(this._backfillJob.phase === "preview" ? "Backfill preview complete. Review counts before importing." : "Garmin historical import complete."); } catch (error) { this._show(error.message || String(error), true); await this._load(false); } }
  async _cancelBackfill(id) { if (!confirm("Cancel this backfill job? Completed chunks and any already imported rows remain in the rollbackable batch.")) return; try { this._backfillJob = await this._call("backfill_cancel", { job_id:id }); await this._load(false); this._show("Backfill job cancelled."); } catch (error) { this._show(error.message || String(error), true); } }

  async _weather() { try { this._busy = "Fetching model data…"; this._show("Fetching and caching representative model conditions…"); this._renderContent(); const payload = { range: this._range, ...(this._passageId ? { passage_id: this._passageId } : {}), ...this._customRange() }; if (this._selectionStart && this._selectionEnd) Object.assign(payload, { start_report_id: this._selectionStart, end_report_id: this._selectionEnd }); const result = await this._call("weather_enrich", payload); await this._loadQuery(false); const warning = result.warnings?.length ? ` ${result.warnings[0]}` : ""; this._show(`Stored model data for ${result.available} of ${result.requested} representative positions. Missing fields remain gaps.${warning}`, result.available === 0); } catch (error) { this._show(error.message || String(error), true); } finally { this._busy = ""; this._renderContent(); } }
  async _refresh() { try { this._busy = "Refreshing Garmin…"; this._show("Requesting the latest rolling Garmin window…"); this._renderContent(); await this._call("refresh"); await this._load(false); this._show("Garmin refresh completed."); } catch (error) { this._show(error.message || String(error), true); } finally { this._busy = ""; this._renderContent(); } }
  async _integrity() { try { const result = await this._call("integrity"); await this._load(false); this._show(`Archive integrity: ${result.integrity}.`); } catch (error) { this._show(error.message || String(error), true); } }
  async _testNotification() { try { await this._call("test_notification"); this._show("Test notification sent. Confirm it in Home Assistant Notifications."); } catch (error) { this._show(error.message || String(error), true); } }

  async _export() {
    try { const payload = { format: "csv", range: this._range, source: this._source, ...this._customRange() }; if (this._passageId) payload.passage_id = this._passageId; if (this._selectionStart && this._selectionEnd) Object.assign(payload, { start_report_id: this._selectionStart, end_report_id: this._selectionEnd }); const result = await this._call("export", payload); const url = URL.createObjectURL(new Blob([result.content], { type: result.mime_type })); const link = document.createElement("a"); link.href = url; link.download = result.filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); this._show(`Exported ${result.returned.toLocaleString()} records.`); } catch (error) { this._show(error.message || String(error), true); }
  }

  async _importHistory() {
    const file = this.shadowRoot.getElementById("history-file")?.files?.[0]; if (!file) { this._show("Choose a file first.", true); return; }
    let importId = null;
    try { const text = await file.text(); const source = this.shadowRoot.getElementById("history-source").value; const records = /\.gpx$|\.xml$/i.test(file.name) ? this._parseGpx(text) : this._parseJson(text); if (!records.length) throw new Error("No timestamped position rows were found."); const sha256 = await this._sha256(new TextEncoder().encode(text)); importId = (await this._call("import_begin", { source, filename: file.name, sha256 })).import_id; for (let offset = 0; offset < records.length; offset += 500) await this._call("import_chunk", { import_id: importId, source, records: records.slice(offset, offset+500) }); await this._call("import_finish", { import_id: importId }); await this._load(false); this._show("Source-labelled import complete."); }
    catch (error) { if (importId != null) { try { await this._call("import_finish", { import_id: importId, failed: true, notes: String(error).slice(0,900) }); } catch (_ignored) {} } this._show(error.message || String(error), true); }
  }
  _parseJson(text) { const value = JSON.parse(text); if (Array.isArray(value)) return value; if (value?.type === "FeatureCollection") return value.features || []; for (const item of Object.values(value || {})) if (Array.isArray(item)) return item; return []; }
  _parseGpx(text) { const xml = new DOMParser().parseFromString(text, "application/xml"); if (xml.querySelector("parsererror")) throw new Error("GPX/XML is malformed."); return [...xml.getElementsByTagNameNS("*", "trkpt")].map((node, index) => ({ id: `gpx-${index}`, latitude: Number(node.getAttribute("lat")), longitude: Number(node.getAttribute("lon")), elevation: node.getElementsByTagNameNS("*", "ele")[0]?.textContent, timestamp: node.getElementsByTagNameNS("*", "time")[0]?.textContent })).filter((item) => item.timestamp && finite(item.latitude) && finite(item.longitude)); }
  async _rollbackImport(id) { if (!confirm("Rollback this import batch? Only rows inserted by this batch are removed.")) return; try { const result = await this._call("import_rollback", { import_id: id }); await this._load(false); this._show(`Rolled back ${result.removed} imported rows.`); } catch (error) { this._show(error.message || String(error), true); } }

  async _selectPoint(index) {
    const point = this._query.points[index]; if (!point) return;
    this._selectedIndex = index;
    if (this._selectRange) {
      if (!this._selectionStart || this._selectionEnd) { this._selectionStart = point.id; this._selectionEnd = null; this._show(`First report selected: ${local(point.recorded_at_utc)}. Choose the other boundary.`); }
      else { const first = this._query.points.find((item) => item.id === this._selectionStart); this._selectionEnd = point.id; this._selectRange = false; await this._loadQuery(true); this._show(`Map range selected between ${local(first?.recorded_at_utc)} and ${local(point.recorded_at_utc)} · ${Number(this._query.returned || 0).toLocaleString()} reports.`); return; }
    }
    this._renderContent();
  }
  _selectReportId(reportId) {
    const index=(this._query.points||[]).findIndex((item)=>Number(item.id)===Number(reportId));
    if(index>=0)this._selectPoint(index);
  }

  async _clearPointRange() { this._selectionStart = null; this._selectionEnd = null; this._selectRange = false; await this._loadQuery(true); }

  _routeForMap(id) {
    if (id === "passage-map" && this._passageDetail?.route?.context_status === "current") return this._passageDetail.route;
    return null;
  }

  _drawMap(id, route = null) {
    const map = this.shadowRoot.getElementById(id); if (!map) return;
    const points = (this._query.points || []).filter((item) => finite(item.latitude) && finite(item.longitude));
    const routeCoordinates = route?.coordinates || [];
    const summary = route?.summary || {};
    const referenceCoordinates = summary.reference?.coordinates || [];
    const baselineCoordinates = summary.baseline?.coordinates || [];
    const alternativeCoordinates = (summary.candidates || []).filter((item) => item.key !== summary.selected?.key).map((item) => item.coordinates || []);
    const all = [...points.map((p) => [Number(p.latitude), Number(p.longitude)]), ...routeCoordinates.map((p) => [Number(p[1]), Number(p[0])])];
    if (!all.length) { map.querySelector(".tiles").innerHTML = ""; map.querySelector(".overlay").innerHTML = `<text x="50%" y="50%" text-anchor="middle" fill="#345">No valid positions in this range</text>`; return; }
    const width = map.clientWidth || 900, height = map.clientHeight || 500; const fitted = this._fitView(all, width, height);
    const state = this._mapViews[id] || { lat:fitted.lat, lon:fitted.lon, zoom:fitted.zoom };
    state.zoom = Math.max(1, Math.min(18, Number(state.zoom ?? fitted.zoom)));
    state.lat = finite(state.lat) ? Number(state.lat) : fitted.lat; state.lon = finite(state.lon) ? Number(state.lon) : fitted.lon;
    this._mapViews[id] = state;
    const zoom = state.zoom; const center = this._project(state.lat, state.lon, zoom); const world = 256 * 2 ** zoom;
    const tiles = []; const left = center.x - width/2, top = center.y - height/2; const minTileX = Math.floor(left/256), maxTileX = Math.floor((left+width)/256), minTileY = Math.floor(top/256), maxTileY = Math.floor((top+height)/256);
    for (let tx=minTileX; tx<=maxTileX; tx+=1) for (let ty=minTileY; ty<=maxTileY; ty+=1) { if (ty < 0 || ty >= 2**zoom) continue; const wrapped = ((tx % 2**zoom)+2**zoom)%2**zoom; tiles.push(`<img alt="" src="https://tile.openstreetmap.org/${zoom}/${wrapped}/${ty}.png" style="left:${tx*256-left}px;top:${ty*256-top}px">`); }
    map.querySelector(".tiles").innerHTML = tiles.join(""); const xy = (lat,lon) => { const p=this._project(lat,lon,zoom); let x=p.x-left; while (x < -world/2) x+=world; while (x > width+world/2) x-=world; return [x,p.y-top]; };
    const segments=[]; let current=[]; points.forEach((p) => { if (p.break_before && current.length) { segments.push(current); current=[]; } current.push(xy(Number(p.latitude),Number(p.longitude))); }); if (current.length) segments.push(current);
    const tracks = segments.map((segment) => `<polyline points="${segment.map((p)=>p.join(",")).join(" ")}" fill="none" stroke="${COLORS.speed}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`).join("");
    const polyline = (coordinates, stroke, widthValue, dash = "", opacity = 1) => coordinates.length ? `<polyline points="${coordinates.map((p)=>xy(Number(p[1]),Number(p[0])).join(",")).join(" ")}" fill="none" stroke="${stroke}" stroke-width="${widthValue}" ${dash ? `stroke-dasharray="${dash}"` : ""} opacity="${opacity}" stroke-linecap="round" stroke-linejoin="round"/>` : "";
    const referenceLine = polyline(referenceCoordinates, summary.reference?.water_valid === false ? "#ef5350" : "#90a4ae", 2, "3 7", .9);
    const baselineLine = polyline(baselineCoordinates, "#ffb300", 2, "7 6", .7);
    const alternatives = alternativeCoordinates.map((coordinates)=>polyline(coordinates,"#66bb6a",2,"4 5",.35)).join("");
    const routeLine = polyline(routeCoordinates, "#43a047", 4, "", 1);
    const dots = points.map((p,pointIndex) => { const index=this._query.points.findIndex((item)=>item.id===p.id); const [x,y]=xy(Number(p.latitude),Number(p.longitude)); const selected=index===this._selectedIndex; const boundary=[this._selectionStart,this._selectionEnd].includes(p.id); const current=pointIndex===points.length-1; const visible=current&&finite(p.cog_true)?`<path d="M0 -11 L7 8 L0 5 L-7 8 Z" transform="translate(${x} ${y}) rotate(${Number(p.cog_true)})" fill="${boundary?"#ffeb3b":COLORS.speed}" stroke="#fff" stroke-width="2"/>`:`<circle cx="${x}" cy="${y}" r="${boundary?9:selected?8:5}" fill="${boundary?"#ffeb3b":COLORS.speed}" stroke="#fff" stroke-width="${selected||boundary?2:1}"/>`; return `<g class="track-point" data-action="point" data-index="${index}" tabindex="0" role="button" aria-label="Report ${esc(local(p.recorded_at_utc))}">${visible}<circle cx="${x}" cy="${y}" r="22" fill="transparent"><title>${esc(local(p.recorded_at_utc))}</title></circle></g>`; }).join("");
    const arrows = this._mapMode === "weather" ? (this._query.weather_samples || []).filter((s)=>finite(s.wind_dir_deg)).map((s)=>{const [x,y]=xy(Number(s.latitude),Number(s.longitude)); return `<g transform="translate(${x} ${y}) rotate(${Number(s.wind_dir_deg)})"><path d="M0 9 L0 -9 M0 -9 L-4 -3 M0 -9 L4 -3" stroke="${COLORS.wind}" stroke-width="2" fill="none"><title>Modeled wind ${this._speed(s.wind_speed_kn)}</title></path></g>`;}).join("") : "";
    const deviationConnectors = this._showDeviationOverlay ? (summary.actual?.modeled_comparison?.connectors || []).map((item)=>{const first=xy(Number(item.latitude),Number(item.longitude));const second=xy(Number(item.matched_latitude),Number(item.matched_longitude));return `<line x1="${first[0]}" y1="${first[1]}" x2="${second[0]}" y2="${second[1]}" stroke="var(--primary-text-color)" stroke-width="1.2" stroke-dasharray="3 4" opacity=".45"><title>${esc(this._deviationValue(item.signed_deviation_nm))}</title></line>`;}).join("") : "";
    map.querySelector(".overlay").setAttribute("viewBox",`0 0 ${width} ${height}`); map.querySelector(".overlay").innerHTML = `${tracks}${deviationConnectors}${referenceLine}${baselineLine}${alternatives}${routeLine}${arrows}${dots}`;
    const legend = [`<i class="swatch" style="background:${COLORS.speed}"></i>${esc(this._sourceLabel(this._source))}`];
    if (routeCoordinates.length) legend.push(`<i class="swatch" style="background:#43a047"></i>Selected sailing path`);
    if (referenceCoordinates.length) legend.push(`<i class="swatch" style="background:${summary.reference?.water_valid === false ? "#ef5350" : "#90a4ae"}"></i>Direct reference${summary.reference?.water_valid === false ? " · crosses land" : ""}`);
    if (this._mapMode === "weather") legend.push(`<i class="swatch" style="background:${COLORS.wind}"></i>Modeled wind`);
    map.querySelector(".legend").innerHTML = legend.join("");
  }

  _zoomMap(id, delta) {
    const map=this.shadowRoot.getElementById(id); if(!map)return;
    const rect=map.getBoundingClientRect(); this._zoomMapAt(id,delta,rect.left+rect.width/2,rect.top+rect.height/2);
  }
  _zoomMapAt(id, delta, clientX, clientY) {
    const map=this.shadowRoot.getElementById(id); if(!map)return;
    const state=this._mapViews[id]; if(!state){this._drawMap(id,this._routeForMap(id));return;}
    const oldZoom=Number(state.zoom); const newZoom=Math.max(1,Math.min(18,oldZoom+delta)); if(newZoom===oldZoom)return;
    const rect=map.getBoundingClientRect(); const anchorX=Math.max(0,Math.min(rect.width,clientX-rect.left)); const anchorY=Math.max(0,Math.min(rect.height,clientY-rect.top));
    const oldCenter=this._project(state.lat,state.lon,oldZoom); const oldAnchorX=oldCenter.x+(anchorX-rect.width/2); const oldAnchorY=oldCenter.y+(anchorY-rect.height/2); const anchorGeo=this._unproject(oldAnchorX,oldAnchorY,oldZoom); const newAnchor=this._project(anchorGeo.lat,anchorGeo.lon,newZoom);
    const newCenter=this._unproject(newAnchor.x-(anchorX-rect.width/2),newAnchor.y-(anchorY-rect.height/2),newZoom); state.zoom=newZoom;state.lat=newCenter.lat;state.lon=newCenter.lon;this._drawMap(id,this._routeForMap(id));
  }
  _fitMap(id) { if (!id) return; delete this._mapViews[id]; this._drawMap(id,this._routeForMap(id)); }
  _panMap(id, xFraction, yFraction) {
    const map=this.shadowRoot.getElementById(id); const state=this._mapViews[id]; if (!map||!state) return;
    const center=this._project(state.lat,state.lon,state.zoom); const next=this._unproject(center.x+map.clientWidth*xFraction,center.y+map.clientHeight*yFraction,state.zoom);
    state.lat=next.lat;state.lon=next.lon;this._drawMap(id,this._routeForMap(id));
  }
  _wheelMap(event) {
    const map=event.target.closest?.(".map"); if(!map||event.target.closest?.(".map-controls,.map-attribution"))return;
    event.preventDefault(); const id=map.dataset.mapId; this._mapWheelDelta[id]=(this._mapWheelDelta[id]||0)+event.deltaY;
    if(Math.abs(this._mapWheelDelta[id])<45)return; const delta=this._mapWheelDelta[id]<0?1:-1; this._mapWheelDelta[id]=0; this._zoomMapAt(id,delta,event.clientX,event.clientY);
  }
  _doubleClickMap(event) {
    const map=event.target.closest?.(".map"); if(!map||event.target.closest?.(".map-controls,.map-attribution"))return; event.preventDefault(); this._zoomMapAt(map.dataset.mapId,1,event.clientX,event.clientY);
  }
  _startMapDrag(event) {
    const map=event.target.closest?.(".map"); if(!map||event.button>0||event.target.closest?.(".map-controls,.map-attribution,a,button,input,select,label"))return;
    const id=map.dataset.mapId; const state=this._mapViews[id]; if(!state)return;
    this._mapPointers.set(event.pointerId,{id,x:event.clientX,y:event.clientY}); try{map.setPointerCapture(event.pointerId);}catch(_error){}
    const same=[...this._mapPointers.entries()].filter(([,item])=>item.id===id);
    if(same.length>=2){const first=same[same.length-2][1],second=same[same.length-1][1];const distance=Math.hypot(second.x-first.x,second.y-first.y);this._mapPinch={id,startDistance:Math.max(distance,1),startZoom:state.zoom,lastZoom:state.zoom,midX:(first.x+second.x)/2,midY:(first.y+second.y)/2};this._mapDrag=null;map.classList.add("dragging");event.preventDefault();return;}
    const center=this._project(state.lat,state.lon,state.zoom);this._mapDrag={id,pointerId:event.pointerId,startX:event.clientX,startY:event.clientY,centerX:center.x,centerY:center.y,moved:false};map.classList.add("dragging");event.preventDefault();
  }
  _moveMapDrag(event) {
    if(this._mapPointers.has(event.pointerId)){const item=this._mapPointers.get(event.pointerId);item.x=event.clientX;item.y=event.clientY;}
    if(this._mapPinch){const same=[...this._mapPointers.values()].filter((item)=>item.id===this._mapPinch.id);if(same.length>=2){const first=same[0],second=same[1];const distance=Math.max(1,Math.hypot(second.x-first.x,second.y-first.y));const target=Math.max(1,Math.min(18,Math.round(this._mapPinch.startZoom+Math.log2(distance/this._mapPinch.startDistance))));if(target!==this._mapPinch.lastZoom){const delta=target-this._mapPinch.lastZoom;const midX=(first.x+second.x)/2,midY=(first.y+second.y)/2;this._zoomMapAt(this._mapPinch.id,delta,midX,midY);this._mapPinch.lastZoom=target;}event.preventDefault();return;}}
    const drag=this._mapDrag;if(!drag||drag.pointerId!==event.pointerId)return;const map=this.shadowRoot.getElementById(drag.id);const state=this._mapViews[drag.id];if(!map||!state)return;const dx=event.clientX-drag.startX,dy=event.clientY-drag.startY;if(Math.hypot(dx,dy)>5)drag.moved=true;const next=this._unproject(drag.centerX-dx,drag.centerY-dy,state.zoom);state.lat=next.lat;state.lon=next.lon;this._drawMap(drag.id,this._routeForMap(drag.id));event.preventDefault();
  }
  _endMapDrag(event) {
    const pointer=this._mapPointers.get(event.pointerId); if(pointer)this._mapPointers.delete(event.pointerId);
    const drag=this._mapDrag; if(drag&&drag.pointerId===event.pointerId){const map=this.shadowRoot.getElementById(drag.id);if(drag.moved)this._suppressPointClickUntil=Date.now()+350;map?.classList.remove("dragging");try{map?.releasePointerCapture(event.pointerId);}catch(_error){}this._mapDrag=null;}
    if(this._mapPinch){const remaining=[...this._mapPointers.values()].filter((item)=>item.id===this._mapPinch.id);if(remaining.length<2){const map=this.shadowRoot.getElementById(this._mapPinch.id);map?.classList.remove("dragging");this._mapPinch=null;this._suppressPointClickUntil=Date.now()+350;}}
  }


  _fitView(coordinates, width, height) {
    if (coordinates.length === 1) return { lat: coordinates[0][0], lon: coordinates[0][1], zoom: 7 };
    const latMin=Math.min(...coordinates.map((p)=>p[0])),latMax=Math.max(...coordinates.map((p)=>p[0]));
    const wrapped=coordinates.map((p)=>((p[1]%360)+360)%360).sort((a,b)=>a-b); let gap=-1,gapIndex=0;
    wrapped.forEach((value,index)=>{const next=index===wrapped.length-1?wrapped[0]+360:wrapped[index+1];if(next-value>gap){gap=next-value;gapIndex=index;}});
    const arcStart=wrapped[(gapIndex+1)%wrapped.length];const unwrapped=wrapped.map((value)=>value<arcStart?value+360:value);const lonSpan=Math.max(...unwrapped)-Math.min(...unwrapped);let lon=(Math.min(...unwrapped)+Math.max(...unwrapped))/2;if(lon>180)lon-=360;const lat=(latMin+latMax)/2;
    for(let z=16;z>=1;z-=1){const a=this._project(latMax,lon,z),b=this._project(latMin,lon,z);const xSpan=256*2**z*lonSpan/360;if(xSpan<width*.82&&Math.abs(b.y-a.y)<height*.78)return{lat,lon,zoom:z};} return{lat,lon,zoom:1};
  }
  _project(lat,lon,zoom){const scale=256*2**zoom;const bounded=Math.max(-85.0511,Math.min(85.0511,lat));const sin=Math.sin(bounded*Math.PI/180);return{x:(lon+180)/360*scale,y:(.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*scale};}
  _unproject(x,y,zoom){const scale=256*2**zoom;const lon=((x/scale*360)%360+360)%360-180;const n=Math.PI-2*Math.PI*y/scale;const lat=180/Math.PI*Math.atan(Math.sinh(n));return{lat:Math.max(-85.0511,Math.min(85.0511,lat)),lon};}

  _speedValue(value) { if (!finite(value)) return null; const knots=Number(value); return this._speedUnit === "km/h" ? knots*1.852 : this._speedUnit === "mph" ? knots*1.150779 : knots; }
  _heightValue(value) { if (!finite(value)) return null; return this._heightUnit === "ft" ? Number(value)*3.28084 : Number(value); }
  _speed(value) { const converted=this._speedValue(value); return converted == null ? "—" : `${converted.toFixed(1)} ${this._speedUnit}`; }
  _height(value) { const converted=this._heightValue(value); return converted == null ? "—" : `${converted.toFixed(1)} ${this._heightUnit}`; }
  _sourceLabel(value) { return ({garmin_mapshare:"Garmin MapShare",predictwind_snapshot:"PredictWind import",gpx_import:"GPX import",ha_recorder:"Home Assistant Recorder import",csv_import:"CSV import",canonical:"Combined track"})[value] || value || "—"; }

  async _sha256(bytesValue) {
    if (globalThis.crypto?.subtle) { const digest=await crypto.subtle.digest("SHA-256",bytesValue);return [...new Uint8Array(digest)].map((b)=>b.toString(16).padStart(2,"0")).join(""); }
    const words=[]; for(let index=0;index<bytesValue.length;index+=1) words[index>>2]|=bytesValue[index]<<(24-(index%4)*8);
    const bitLength=bytesValue.length*8; words[bitLength>>5]|=0x80<<(24-bitLength%32); words[((bitLength+64>>9)<<4)+15]=bitLength;
    const constants=[],primes=[]; for(let candidate=2;constants.length<64;candidate+=1){if(primes.every((prime)=>candidate%prime)){primes.push(candidate);constants.push((Math.pow(candidate,1/3)*0x100000000)|0);}}
    let hash=[0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]; const right=(value,amount)=>value>>>amount|value<<(32-amount);
    for(let offset=0;offset<words.length;offset+=16){const schedule=words.slice(offset,offset+16),old=hash.slice();for(let index=16;index<64;index+=1){const a=schedule[index-15],b=schedule[index-2];schedule[index]=(schedule[index-16]+(right(a,7)^right(a,18)^(a>>>3))+schedule[index-7]+(right(b,17)^right(b,19)^(b>>>10)))|0;}for(let index=0;index<64;index+=1){const e=hash[4],a=hash[0];const temp1=(hash[7]+(right(e,6)^right(e,11)^right(e,25))+((e&hash[5])^(~e&hash[6]))+constants[index]+schedule[index])|0;const temp2=((right(a,2)^right(a,13)^right(a,22))+((a&hash[1])^(a&hash[2])^(hash[1]&hash[2])))|0;hash=[(temp1+temp2)|0,hash[0],hash[1],hash[2],(hash[3]+temp1)|0,hash[4],hash[5],hash[6]];}hash=hash.map((value,index)=>(value+old[index])|0);}
    return hash.map((value)=>(value>>>0).toString(16).padStart(8,"0")).join("");
  }
  _setTab(tab) { this._tab = tab; this._notice = null; this._render(); }
  _show(text, error = false) { this._notice = { text, error }; this._renderNotice(); }
  _navigate(path) { history.pushState(null,"",path); window.dispatchEvent(new Event("location-changed")); }
  _localInput(date) { const offset=date.getTimezoneOffset()*60000;return new Date(date.getTime()-offset).toISOString().slice(0,16); }
  _dateInput(date) { return this._localInput(date).slice(0,10); }
}

if (!customElements.get("bluesky-passage-panel")) customElements.define("bluesky-passage-panel", BlueSkyPassagePanel);
