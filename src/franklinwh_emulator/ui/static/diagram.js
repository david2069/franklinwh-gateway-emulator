/* Energy diagram for the active gateway, after the owner's design (docs/UI_PLAN.md).
 *
 * Built as an SVG string from the control API's state, so only modelled values appear:
 * grid, home, PV per input (aGate or aPbox), the battery (one combined unit), generator and
 * smart circuits. Dashed lines move in the direction power flows. Signs are the API's:
 * battery_w + = discharging, grid_w + = importing.
 */
const NOT_MODELLED = [
  'per-aPower SoC and power (one combined battery)', 'two PV inputs on the aGate',
  'aPower S DC hybrid', 'V2L', 'non-backup panel', 'service rating', 'EV battery SoC',
  'hot-water temperature', 'tariff prices', 'remote (cloud) VPP mode',
  'network connection (Ethernet, WiFi, 4G)',
];
const SKY_LABELS = { clear: 'clear sky', broken: 'broken cloud', overcast: 'overcast', storm: 'storm' };
function skyLabel(sky) { return SKY_LABELS[sky] || sky; }

// Small inline icons (decorative; the text beside them says the same).
const SUN_ICON = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5"></circle>' +
  [0, 45, 90, 135, 180, 225, 270, 315].map(a => `<line x1="12" y1="2.5" x2="12" y2="5" transform="rotate(${a} 12 12)"></line>`).join('') + '</svg>';
const MOON_ICON = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 3.5a8.5 8.5 0 1 0 5 15 7 7 0 0 1-5-15z"></path></svg>';
const IDLE_W = 10;
const DASH_PERIOD_MS = 1000;   // one dash cycle; matches the CSS animation

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function kwText(w) { return (Math.abs(w) / 1000).toFixed(2) + ' kW'; }   // direction is shown by the line

// A dashed connector through the given points; dir +1 = flows from first point to last.
// The diagram is redrawn on every poll; a negative delay keeps the dashes moving smoothly.
function link(points, w, dir) {
  const d = 'M ' + points.map(p => p[0] + ' ' + p[1]).join(' L ');
  const cls = Math.abs(w) < IDLE_W ? 'idle' : (dir * Math.sign(w) > 0 ? 'fwd' : 'rev');
  const delay = -((performance.now() % DASH_PERIOD_MS) / 1000).toFixed(3);
  return `<path class="flow ${cls}" d="${d}" style="animation-delay:${delay}s"></path>`;
}

function node(x, y, w, h, title, value, sub, cls, lines, valueCls) {
  // ``lines``: extra small lines under the value (the aGate's status); each a string or
  // [text, class]. ``valueCls``: a colour class for the value.
  const extra = (lines || []).map((l, i) => {
    const [t, c] = Array.isArray(l) ? l : [l, ''];
    return `<text class="s ${c}" x="${w / 2}" y="${74 + i * 18}">${esc(t)}</text>`;
  }).join('');
  const vy = lines && lines.length ? 48 : h / 2 + 10;
  return `<g class="node ${cls || ''}" transform="translate(${x - w / 2} ${y - h / 2})">
    <rect width="${w}" height="${h}" rx="12"></rect>
    <text class="t" x="${w / 2}" y="22">${esc(title)}</text>
    ${value ? `<text class="v ${valueCls || ''}" x="${w / 2}" y="${vy}">${esc(value)}</text>` : ''}
    ${extra}
    ${sub ? `<text class="s" x="${w / 2}" y="${h - 12}">${esc(sub)}</text>` : ''}
  </g>`;
}

// 'charging' | 'discharging' | 'standby' (off-grid versions included), for the colour classes.
function statusKey(g) {
  const n = (g.run_status_name || '').toLowerCase();
  return n.includes('discharging') ? 'discharging' : n.includes('charging') ? 'charging'
       : n.includes('standby') ? 'standby' : '';
}

function renderDiagram(g) {
  if (!g) return '';
  const out = [];
  const links = [];
  const ov = g.overrides || {};
  const forced = k => ov[k] != null;   // a value set by pv / load / ambient, not the model
  const agate = [500, 300];

  // Grid → service meter → aGate (+ = import, towards the aGate).
  const legs = g.legs.length;
  links.push(link([[150, 300], [250, 300]], g.grid_w, +1));
  links.push(link([[330, 300], [430, 300]], g.grid_w, +1));
  out.push(node(90, 300, 120, 86, 'Grid', kwText(g.grid_w),
    g.grid_connected ? (g.grid_w > IDLE_W ? 'importing' : g.grid_w < -IDLE_W ? 'exporting' : 'connected')
                     : 'down' + (g.trip_reason ? ' · ' + g.trip_reason : ''),
    g.grid_connected ? '' : 'bad'));
  out.push(node(290, 300, 80, 86, 'Meter', '', legs + '-phase'));

  // aGate → main panel → home.
  links.push(link([[500, 240], [500, 160]], g.load_w, +1));
  links.push(link([[440, 110], [360, 110]], g.load_w, +1));
  out.push(node(500, 110, 120, 80, 'Main panel', '', 'backed up'));
  out.push(node(290, 110, 130, 86, 'Home', kwText(g.load_w), forced('load_w') ? 'load · forced' : 'load',
    forced('load_w') ? 'forced' : ''));

  // PV: the aGate's own input, and remote inputs through an aPbox.
  const inputs = Object.keys(g.pv_inputs || {}).filter(k => g.pv_inputs[k] > 0);
  const remote = inputs.filter(k => k !== 'proximal');
  if (inputs.includes('proximal')) {
    const w = g.pv_by_input.proximal || 0;
    links.push(link([[700, 150], [700, 270], [570, 270]], w, +1));
    out.push(node(700, 105, 130, 86, 'PV · aGate', kwText(w),
      (g.pv_relays.proximal ? 'relay closed' : 'relay open') + (forced('solar_w') ? ' · forced' : ''),
      g.pv_relays.proximal ? (forced('solar_w') ? 'forced' : '') : 'dim'));
  }
  if (remote.length) {
    const total = remote.reduce((s, k) => s + (g.pv_by_input[k] || 0), 0);
    remote.forEach((k, i) => {
      const x = 830 + i * 140;
      const w = g.pv_by_input[k] || 0;
      links.push(link([[x, 150], [x, 175], [880, 175], [880, 195]], w, +1));
      out.push(node(x, 105, 120, 86, 'Remote PV ' + k.replace('remote', ''), kwText(w),
        (g.pv_relays[k] ? 'relay closed' : 'relay open') + (forced('solar_w') ? ' · forced' : ''),
        g.pv_relays[k] ? (forced('solar_w') ? 'forced' : '') : 'dim'));
    });
    links.push(link([[880, 235], [880, 255], [570, 255]], total, +1));
    out.push(node(880, 215, 90, 40, 'aPbox', '', ''));
  }

  // Battery: one combined unit (per-aPower values are not modelled). + = discharging.
  links.push(link([[570, 330], [770, 330]], g.battery_w, -1));
  const r = g.apower_rating;
  out.push(node(860, 345, 180, 100, 'aPower ×' + g.apower_units,
    Math.round(g.soc) + '% · ' + kwText(g.battery_w),
    (r.wh / 1000).toFixed(1) + ' kWh · ' + (r.w / 1000) + ' kW each' +
    (Math.abs(g.battery_w) < IDLE_W ? '' : g.battery_w > 0 ? ' · out' : ' · in')));

  // aGate in the middle: mode, then its status. Battery + = discharging (out of the battery).
  const force = g.force;   // an M704 setpoint (FORCE); the app calls it VPP Mode
  out.push(node(agate[0], agate[1], 180, 156, 'aGate', g.native_mode_name,
    force ? 'FORCE · ' + force.direction : '', force ? 'agate vpp' : 'agate', [
      [g.run_status_name + ' · ' + kwText(g.battery_w), 'c-' + statusKey(g)],
      'SoC ' + Math.round(g.soc) + '% · ' + (g.grid_connected ? 'reserve ' + Math.round(g.active_reserve)
                                                             : 'off-grid floor ' + Math.round(g.offgrid_floor_soc)) + '%',
      'inverter ' + g.inverter_state_name.toLowerCase() + ' · cabinet ' + temp(g.cabinet_c),
    ], 'c-' + ({ 1: 'backup', 2: 'self', 3: 'tou' })[g.native_mode]));

  // Bottom row: generator and smart circuits.
  const gen = g.generator || {};
  links.push(link([[230, 470], [230, 400], [450, 400], [450, 360]], g.generator_w || 0, +1));
  out.push(node(230, 510, 130, 80, 'Generator', gen.installed ? kwText(g.generator_w) : '',
    gen.installed ? gen.state_name : 'not fitted', gen.installed ? '' : 'dim'));
  [1, 2, 3].forEach((n, i) => {
    const c = (g.circuits || {})[n];
    if (!c) return;
    const x = 400 + i * 165;
    links.push(link([[540, 360], [540, 430], [x, 430], [x, 470]], c.power_w, +1));
    const names = Object.entries(g.appliances || {}).filter(([, a]) => a.circuit === n).map(([k]) => k);
    out.push(node(x, 510, 155, 80, 'SW' + n + ' · ' + c.name, kwText(c.power_w),
      (c.on ? 'on' : 'off') + (names.length ? ' · ' + names.join(', ') : ''), c.on ? '' : 'dim'));
  });

  const w = g.weather;
  const sun = g.sun;
  // PV cell temperature and its effect on output (#109), e.g. "PV cells 58 °C (−5 %)" (or °F).
  const pvTempText = t => {
    if (!t || t.cell_c == null) return null;
    const pct = Math.round((t.factor - 1) * 100);
    return 'PV cells ' + temp(t.cell_c, 0) + ' (' + (pct > 0 ? '+' : pct < 0 ? '−' : '±') + Math.abs(pct) + ' %)';
  };
  const icon = sun ? (sun.up ? SUN_ICON : MOON_ICON) : '';
  const head = [
    sun ? (sun.up ? 'Day' : 'Night') + ' · sun ' + sun.elevation_deg + '°' : 'No location, so no sun position',
    w ? skyLabel(w.sky || w.model) : 'bell curve (no weather model)',
    w && w.poa_w_m2 != null ? w.poa_w_m2 + ' W/m² on the array' : null,
    'ambient ' + temp(g.ambient_c) + (forced('ambient_c') ? ' (forced)' : ''),
    pvTempText(g.pv_temperature),
    forced('solar_w') ? (g.cap_forced_solar
      ? 'solar forced to ' + kwText(ov.solar_w) + (g.solar_w < ov.solar_w - 1 ? ', capped at ' + kwText(g.solar_w) + ' by the sun' : '')
      : 'solar forced to ' + kwText(ov.solar_w) + ', not capped by the sun') : null,
    'cabinet ' + temp(g.cabinet_c) + ' (' + (g.install
      ? g.install.label + ', surroundings ' + temp(g.install.surroundings_c)
      : 'modelled') + ')'].filter(Boolean).join(' · ');
  const label = `Energy flow for ${g.id}: grid ${kwText(g.grid_w)}, home ${kwText(g.load_w)}, ` +
    `solar ${kwText(g.solar_w)}, battery ${Math.round(g.soc)}% ${kwText(g.battery_w)}`;
  return `<p class="diagram-head">${icon}<span>${esc(head)}</span></p>
    <div class="diagram-wrap"><svg class="diagram" viewBox="0 50 1060 520" role="img" aria-label="${esc(label)}">
      ${links.join('')}${out.join('')}
    </svg></div>
    <p class="diagram-note">Not modelled, so not shown: ${esc(NOT_MODELLED.join(', '))}.</p>`;
}
