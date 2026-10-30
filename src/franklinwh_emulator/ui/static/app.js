/* Running screen: one card per gateway, polled about once a second. Read-only.
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
    _timer: null,

    get current() {
      return this.gateways.find(g => g.id === this.active) || null;
    },

    async start() {
      try { this.active = localStorage.getItem('emulator.active'); } catch (e) { /* private mode */ }
      await this.poll();
      this._timer = setInterval(() => this.poll(), POLL_MS);
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
      if (g.weather) parts.push(g.weather.model + (g.weather.sky ? ' · ' + g.weather.sky : ''));
      return parts.filter(Boolean).join(' · ');
    },
  };
}
