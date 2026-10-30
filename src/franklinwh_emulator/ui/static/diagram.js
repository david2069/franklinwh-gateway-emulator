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
  'hot-water temperature', 'tariff prices',
];
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

function node(x, y, w, h, title, value, sub, cls) {
  return `<g class="node ${cls || ''}" transform="translate(${x - w / 2} ${y - h / 2})">
    <rect width="${w}" height="${h}" rx="12"></rect>
    <text class="t" x="${w / 2}" y="22">${esc(title)}</text>
    ${value ? `<text class="v" x="${w / 2}" y="${h / 2 + 10}">${esc(value)}</text>` : ''}
    ${sub ? `<text class="s" x="${w / 2}" y="${h - 12}">${esc(sub)}</text>` : ''}
  </g>`;
}

function renderDiagram(g) {
  if (!g) return '';
  const out = [];
  const links = [];
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
  out.push(node(290, 110, 130, 86, 'Home', kwText(g.load_w), 'load'));

  // PV: the aGate's own input, and remote inputs through an aPbox.
  const inputs = Object.keys(g.pv_inputs || {}).filter(k => g.pv_inputs[k] > 0);
  const remote = inputs.filter(k => k !== 'proximal');
  if (inputs.includes('proximal')) {
    const w = g.pv_by_input.proximal || 0;
    links.push(link([[700, 150], [700, 270], [570, 270]], w, +1));
    out.push(node(700, 105, 130, 86, 'PV · aGate', kwText(w), g.pv_relays.proximal ? 'relay closed' : 'relay open',
      g.pv_relays.proximal ? '' : 'dim'));
  }
  if (remote.length) {
    const total = remote.reduce((s, k) => s + (g.pv_by_input[k] || 0), 0);
    remote.forEach((k, i) => {
      const x = 830 + i * 140;
      const w = g.pv_by_input[k] || 0;
      links.push(link([[x, 150], [x, 175], [880, 175], [880, 195]], w, +1));
      out.push(node(x, 105, 120, 86, 'Remote PV ' + k.replace('remote', ''), kwText(w),
        g.pv_relays[k] ? 'relay closed' : 'relay open', g.pv_relays[k] ? '' : 'dim'));
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

  // aGate in the middle.
  out.push(node(agate[0], agate[1], 140, 120, 'aGate', g.native_mode_name,
    g.remote_control && g.remote_control.enabled ? 'VPP dispatch' : '', 'agate'));

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
  const head = ['Weather: ' + (w ? (w.sky || w.model) : 'bell curve (no weather model)'),
    w && w.poa_w_m2 != null ? w.poa_w_m2 + ' W/m² on the array' : null,
    g.ambient_c != null ? g.ambient_c.toFixed(1) + ' °C' : null].filter(Boolean).join(' · ');
  const label = `Energy flow for ${g.id}: grid ${kwText(g.grid_w)}, home ${kwText(g.load_w)}, ` +
    `solar ${kwText(g.solar_w)}, battery ${Math.round(g.soc)}% ${kwText(g.battery_w)}`;
  return `<p class="diagram-head">${esc(head)}</p>
    <div class="diagram-wrap"><svg class="diagram" viewBox="0 50 1060 520" role="img" aria-label="${esc(label)}">
      ${links.join('')}${out.join('')}
    </svg></div>
    <p class="diagram-note">Not modelled, so not shown: ${esc(NOT_MODELLED.join(', '))}.</p>`;
}
