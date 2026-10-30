/* Running screen: one card per gateway, polled about once a second; the active gateway's
 * diagram and its action forms (built from GET /schema, see actions.js).
 *
 * Sign conventions are the control API's: battery_w + = discharging, grid_w + = importing.
 * Results and errors show on the page; no toasts.
 */
const POLL_MS = 1000;

function running() {
  return {
    gateways: [],
    active: null,
    loaded: false,
    error: '',
    schema: null,
    forms: {},        // action -> {field: raw string}
    results: {},      // "<gateway>/<action>" -> {ok, text}
    busy: '',
    _timer: null,

    get current() {
      return this.gateways.find(g => g.id === this.active) || null;
    },

    async start() {
      try { this.active = localStorage.getItem('emulator.active'); } catch (e) { /* private mode */ }
      await this.poll();
      this._timer = setInterval(() => this.poll(), POLL_MS);
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
    options(spec) { return choiceOptions(spec); },
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
    detailLine(g) {
      const parts = [g.profile, g.grid_code, g.legs.join(' '), g.local_time];
      if (g.location && g.location.timezone) parts.push(g.location.timezone);
      if (g.weather) parts.push(g.weather.model + ' weather');
      return parts.filter(Boolean).join(' · ');
    },
  };
}
