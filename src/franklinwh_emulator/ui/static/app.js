/* Running screen: one card per gateway, polled about once a second; the active gateway's
 * diagram and its action forms (built from GET /schema, see actions.js).
 *
 * Sign conventions are the control API's: battery_w + = discharging, grid_w + = importing.
 * Results and errors show on the page; no toasts.
 */
const POLL_MS = 1000;
const DOCS_URL = 'https://david2069.github.io/franklinwh-gateway-emulator/';
// Each actions group's docs page (sections of the docs site).
const GROUP_DOCS = {
  'Grid and phases': 'grid/', 'Inputs': 'site-model/#weather', 'Battery and modes': 'control-api/',
  'Loads': 'site-model/#house-load', 'Generator': 'site-model/#generator-ags', 'CSIP and comms': 'grid/',
  'Alarms and faults': 'emulated/#protocol-detail',
};
const VERSION_CHECK_MS = 5000;

function running() {
  return {
    gateways: [],
    active: null,
    loaded: false,
    error: '',
    schema: null,
    version: null,
    sessionsList: [],  // saved sessions, newest first
    saveName: '',
    restoreName: '',
    sessionMsg: null,  // {ok, text, details}: the last save/restore, shown until the next
    log: [],           // the emulator's action log (newest last)
    stale: '',          // why this page is out of date (the emulator restarted or changed)
    staleNewCode: false,
    layout: 'right',  // where the actions go: 'right' (beside, on wide screens) or 'below'
    groupBy: 'none',  // cards grouped by location (groups.js): none | country | state | city | postcode | site
    compact: false,   // one-line cards
    tempUnit: 'C',    // temperatures shown in °C or °F (units.js); the emulator stays metric
    collapsed: {},    // group label -> true when collapsed
    forms: {},        // action -> {field: raw string}
    results: {},      // "<gateway>/<action>" -> {ok, text}
    touDrafts: {},    // gateway -> the TOU editor's draft (tou.js), while it is open
    busy: '',
    _timer: null,

    get current() {
      return this.gateways.find(g => g.id === this.active) || null;
    },

    async start() {
      try {
        this.active = localStorage.getItem('emulator.active');
        this.layout = localStorage.getItem('emulator.layout') || 'right';
        const gb = localStorage.getItem('emulator.groupBy');
        if (gb && GROUP_BY[gb]) this.groupBy = gb;
        this.compact = localStorage.getItem('emulator.compact') === '1';
        this.tempUnit = localStorage.getItem('emulator.tempUnit') || defaultTempUnit(navigator.language);   // US → °F
        this.collapsed = JSON.parse(localStorage.getItem('emulator.collapsed') || '{}') || {};
      } catch (e) { /* private mode */ }
      if (!TEMP_UNITS[this.tempUnit]) this.tempUnit = defaultTempUnit(navigator.language);
      setTempUnit(this.tempUnit);
      this._get('/version').then(v => { this.version = v; }).catch(() => {});
      this._versionTimer = setInterval(() => this.checkVersion(), VERSION_CHECK_MS);
      await this.poll();
      this._timer = setInterval(() => this.poll(), POLL_MS);
      this.loadSessions();
      try {
        this.schema = await this._get('/schema');
        for (const [name, a] of Object.entries(this.schema.actions)) this.forms[name] = formFromBody(a, {});
      } catch (e) {
        this.error = 'Can\'t load the action list: ' + e.message;
      }
    },

    async poll() {
      try {
        const list = await this._get('/gateways');
        const states = await Promise.all(list.map(g => this._get('/gateways/' + encodeURIComponent(g.id))));
        this.gateways = states;
        if (!states.some(g => g.id === this.active)) this.active = states.length ? states[0].id : null;
        this.error = '';
        this.loaded = true;
        this._get('/log').then(l => { this.log = l; }).catch(() => {});
      } catch (e) {
        this.error = 'Can\'t reach the emulator: ' + e.message + '. Retrying every second.';
      }
    },

    async _get(path) {
      const r = await fetch(path, { cache: 'no-store' });
      if (!r.ok) throw new Error(path + ' answered ' + r.status);
      return r.json();
    },

    // -- actions ---------------------------------------------------------------------------
    groupNames() { return this.schema ? Object.keys(this.schema.groups) : []; },
    actionsIn(group) { return this.schema.groups[group]; },
    action(name) { return this.schema.actions[name]; },
    fieldsOf(name) { return Object.entries(this.action(name).fields); },
    kind(spec) { return fieldKind(spec); },
    options(spec) { return choiceOptions(spec, this.current); },
    fieldId(name, field) { return 'f-' + name + '-' + field; },
    required(name, field) { return (this.action(name).required || []).includes(field); },
    hint(spec) {
      const t = [].concat(spec.type).join(' or ');
      return [spec.unit, spec.doc, this.kind(spec) === 'list' ? 'comma separated' : '',
              spec.nullable && this.kind(spec) !== 'choice' ? 'null allowed' : '', t]
        .filter(Boolean).join(' · ');
    },
    result(name) { return this.results[this.active + '/' + name] || null; },
    fillExample(name, i) {
      if (i === '') return;
      this.forms[name] = formFromBody(this.action(name), this.action(name).examples[Number(i)]);
    },

    async submit(name) {
      const gw = this.active;
      const key = gw + '/' + name;
      let body;
      try {
        body = buildBody(this.action(name), this.forms[name]);
      } catch (e) {
        this.results[key] = { ok: false, text: e.message };
        return;
      }
      this.busy = name;
      try {
        const r = await fetch('/gateways/' + encodeURIComponent(gw) + '/' + name, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await r.json();
        if (!r.ok) {
          this.results[key] = { ok: false, text: data.error || ('answered ' + r.status) };
          return;
        }
        const i = this.gateways.findIndex(g => g.id === gw);
        if (i >= 0) this.gateways[i] = data;
        this.results[key] = { ok: true, text: 'Applied to ' + gw + ' at ' + new Date().toLocaleTimeString() +
                                               ': ' + JSON.stringify(body) };
      } catch (e) {
        this.results[key] = { ok: false, text: 'Not sent: ' + e.message };
      } finally {
        this.busy = '';
      }
    },

    // -- links to the docs site ---------------------------------------------------------------
    docsUrl(page) { return DOCS_URL + page; },
    groupDocs(group) { return GROUP_DOCS[group] || 'control-api/'; },

    // -- Modbus TCP on/off through the gateway's own Direct Connect listener (1205 sunsMdEn) ---
    modbusText(g) {
      if (g.rebooting) return 'Rebooting… Modbus TCP and Direct Connect are down';
      if (g.modbus_enabled && g.modbus_listening) return 'Modbus TCP Server on · ' + this.addr(g.address);
      if (g.modbus_enabled) return 'Modbus TCP Server opening…';
      if (g.modbus_listening) return 'Modbus TCP Server closing…';
      return 'Modbus TCP Server off';
    },
    async dcModbus(on) {
      const gw = this.active;
      const key = gw + '/dc-modbus';
      this.busy = 'dc-modbus';
      try {
        const data = await this._post('/gateways/' + encodeURIComponent(gw) + '/dc-modbus', { on });
        const i = this.gateways.findIndex(g => g.id === gw);
        if (i >= 0) this.gateways[i] = data;
        this.results[key] = { ok: true, text: 'Direct Connect 1205 sunsMdEn=' + (on ? 1 : 0) + ' sent to ' +
          this.addr(data.dc_address) + ' at ' + new Date().toLocaleTimeString() + '; the aGate ' +
          (on ? 'opens' : 'closes') + ' Modbus TCP in about 3 s.' };
      } catch (e) {
        this.results[key] = { ok: false, text: e.message };
      } finally {
        this.busy = '';
      }
    },

    async dcReboot() {
      const gw = this.active;
      const key = gw + '/dc-reboot';
      this.busy = 'dc-reboot';
      try {
        const data = await this._post('/gateways/' + encodeURIComponent(gw) + '/dc-reboot', {});
        const i = this.gateways.findIndex(g => g.id === gw);
        if (i >= 0) this.gateways[i] = data;
        this.results[key] = { ok: true, text: 'Direct Connect 1721 reboot sent to ' + this.addr(data.dc_address) +
          ' at ' + new Date().toLocaleTimeString() + '; both listeners come back in about 20 s, on the same ports.' };
      } catch (e) {
        this.results[key] = { ok: false, text: e.message };
      } finally {
        this.busy = '';
      }
    },

    // -- TOU schedule (tou.js) ---------------------------------------------------------------
    touEditing() { return !!this.touDrafts[this.active]; },
    touDraftNow() { return this.touDrafts[this.active]; },
    touEdit() {
      const d = touDraft(this.current.tou);
      for (const w of Object.keys(this.schema.tou.waves)) d.rates[w] = d.rates[w] || { buy: '', sell: '' };
      this.touDrafts[this.active] = d;
    },
    touCancel() { delete this.touDrafts[this.active]; },
    touRate(w) { return this.touDraftNow().rates[w]; },
    async touSend(reset) {
      const gw = this.active;
      const key = gw + '/tou-editor';
      let body;
      try {
        body = reset ? { reset: true } : touBody(this.touDrafts[gw]);
      } catch (e) {
        this.results[key] = { ok: false, text: e.message };
        return;
      }
      this.busy = 'tou';
      try {
        const data = await this._post('/gateways/' + encodeURIComponent(gw) + '/tou', body);
        const i = this.gateways.findIndex(g => g.id === gw);
        if (i >= 0) this.gateways[i] = data;
        delete this.touDrafts[gw];
        this.results[key] = { ok: true, text: (reset ? 'Default schedule restored on ' : 'Schedule applied to ') + gw +
                                               ' at ' + new Date().toLocaleTimeString() };
      } catch (e) {
        this.results[key] = { ok: false, text: e.message };
      } finally {
        this.busy = '';
      }
    },
    touNote(g) {
      const a = g.tou.active;
      if (g.native_mode !== 3) return 'Applies in TOU mode; this gateway is in ' + g.native_mode_name + '.';
      return 'Now: ' + a.name + ' · ' + a.dispatch + ' · ' + a.wave + ' · ' + Math.round(a.remaining_min) + ' min left';
    },
    touAria(g) {
      return 'TOU schedule: ' + g.tou.schedule.map(b => b.startHourTime + ' to ' + b.endHourTime + ' ' + b.dispatch + ' ' + b.wave).join('; ');
    },
    touPrice(g, wave) {
      const r = g.tou.rates[String(wave)];
      if (!r) return '—';
      return (r.buy == null ? '—' : r.buy) + ' / ' + (r.sell == null ? '—' : r.sell);
    },

    // -- sessions: save what was done, restore it after a restart --------------------------
    async loadSessions() {
      try { this.sessionsList = await this._get('/sessions'); } catch (e) { this.sessionsList = []; }
      if (!this.sessionsList.some(s => s.name === this.restoreName))
        this.restoreName = this.sessionsList.length ? this.sessionsList[0].name : '';
    },
    async _post(path, body) {
      const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || ('answered ' + r.status));
      return data;
    },
    async saveSession() {
      try {
        const r = await this._post('/session/save', this.saveName.trim() ? { name: this.saveName.trim() } : {});
        this.sessionMsg = { ok: true, text: 'Saved "' + r.name + '": ' + r.actions + ' actions (incl. each gateway\'s SoC) to ' + r.path, details: [] };
        this.saveName = '';
        await this.loadSessions();
        this.restoreName = r.name;
      } catch (e) {
        this.sessionMsg = { ok: false, text: 'Not saved: ' + e.message, details: [] };
      }
    },
    async restoreSession() {
      if (!this.restoreName) return;
      try {
        const r = await this._post('/session/restore', { name: this.restoreName });
        this.sessionMsg = { ok: r.failed === 0,
          text: 'Restored "' + r.name + '": ' + r.applied + ' applied, ' + r.failed + ' failed',
          details: r.results.filter(x => !x.ok).map(x => x.gateway + ' ' + x.do + ' ' + JSON.stringify(x.with) + ': ' + x.error) };
        await this.poll();
      } catch (e) {
        this.sessionMsg = { ok: false, text: 'Not restored: ' + e.message, details: [] };
      }
    },
    recentLog() { return this.log.slice(-40).reverse(); },
    logLine(e) {
      return e.at.slice(11, 19) + ' · ' + e.gateway + ' · ' + e.action + ' ' + JSON.stringify(e.body) +
             (e.source !== 'api' ? ' · ' + e.source : '') + (e.ok ? '' : ' · ' + e.error);
    },

    // The emulator restarted, or now runs other code: this page (and its state) is out of date.
    async checkVersion() {
      let v;
      try { v = await this._get('/version'); } catch (e) { return; }   // down: the poll says so
      if (!this.version) { this.version = v; return; }
      const was = this.version;
      if (v.started === was.started && v.version === was.version && v.commit === was.commit) return;
      const newCode = v.version !== was.version || v.commit !== was.commit;
      this.stale = newCode
        ? 'The emulator now runs a new version (' + this.versionText() + ' → v' + v.version +
          (v.commit ? ' · ' + v.commit : '') + '). Reload this page to use it.'
        : 'The emulator restarted. Its gateways started fresh, so earlier actions (overrides, ' +
          'alarms, modes) were not kept.';
      this.staleNewCode = newCode;
    },
    versionText() {
      if (!this.version) return '';
      return 'v' + this.version.version + (this.version.commit ? ' · ' + this.version.commit : '');
    },
    setView(key, value) {
      this[key] = value;
      if (key === 'tempUnit') setTempUnit(value);
      try { localStorage.setItem('emulator.' + key, key === 'compact' ? (value ? '1' : '0') : value); } catch (e) { /* private mode */ }
    },
    toggleGroup(label) {
      this.collapsed = { ...this.collapsed, [label]: !this.collapsed[label] };
      try { localStorage.setItem('emulator.collapsed', JSON.stringify(this.collapsed)); } catch (e) { /* private mode */ }
    },
    toggleLayout() {
      this.layout = this.layout === 'right' ? 'below' : 'right';
      try { localStorage.setItem('emulator.layout', this.layout); } catch (e) { /* private mode */ }
    },

    select(id) {
      this.active = id;
      try { localStorage.setItem('emulator.active', id); } catch (e) { /* private mode */ }
    },

    kw(w) { const v = w / 1000; return (Math.abs(v) < 0.005 ? 0 : v).toFixed(2) + ' kW'; },
    pct(v) { return Math.round(v) + '%'; },
    addr(a) { return a ? a[0] + ':' + a[1] : ''; },

    // Direction as a word after the value: battery out = discharging; grid in = importing.
    batteryDir(g) { return Math.abs(g.battery_w) < 1 ? '' : (g.battery_w > 0 ? ' out' : ' in'); },
    gridDir(g) { return Math.abs(g.grid_w) < 1 ? '' : (g.grid_w > 0 ? ' in' : ' out'); },
    gridState(g) {
      if (g.grid_connected) return 'grid connected';
      return 'islanded' + (g.trip_reason ? ' · ' + g.trip_reason : '');
    },
    faultList(g) {
      const f = g.faults || {};
      const on = Object.keys(f).filter(k => f[k] && k !== 'range_fault');
      if (f.range_fault) on.push('range ' + f.range_fault.start + '–' + f.range_fault.end);
      return on;
    },
    // Colour classes: mode by native_mode (15507: 1 Backup, 2 Self, 3 TOU); battery by run status.
    modeClass(g) { return ({ 1: 'mode-backup', 2: 'mode-self', 3: 'mode-tou' })[g.native_mode] || ''; },
    statusClass(g) {
      const n = (g.run_status_name || '').toLowerCase();
      return n.includes('discharging') ? 'st-discharging' : n.includes('charging') ? 'st-charging'
           : n.includes('standby') ? 'st-standby' : '';
    },
    reserveText(g) {
      return g.grid_connected ? 'reserve ' + Math.round(g.active_reserve) + '%'
                              : 'off-grid floor ' + Math.round(g.offgrid_floor_soc) + '%';
    },
    // Values forced through pv / load / ambient (null = the model's own).
    overrideList(g) {
      const o = g.overrides || {};
      const out = [];
      if (o.solar_w != null) {
        const capped = g.cap_forced_solar && g.solar_w < o.solar_w - 1;
        out.push('solar forced ' + this.kw(o.solar_w) + (capped ? ', capped at ' + this.kw(g.solar_w) + ' by the sun' : ''));
      }
      if (o.load_w != null) out.push('load forced ' + this.kw(o.load_w));
      if (o.ambient_c != null) out.push('ambient forced ' + temp(o.ambient_c));
      return out;
    },
    detailLine(g) {
      const parts = [g.profile, g.grid_code, g.legs.join(' '), g.local_time];
      if (g.location && g.location.timezone) parts.push(g.location.timezone);
      if (g.weather) parts.push(g.weather.model + ' weather');
      return parts.filter(Boolean).join(' · ');
    },
  };
}
