/* Energy diagram for the active gateway: the owner's design (private/ui-design, "Battery Flow
 * Diagram", boards A and B; #205), drawn as one SVG in the design's 1240-wide coordinates.
 *
 * Built from the control API's state, so only modelled values appear: grid (one line per
 * phase), service meter, main panel and home, PV on the aGate's AC port or remote PV through
 * an aPbox, one card per aPower, generator, smart circuits and their loads. Dashed lines move
 * in the direction power flows. Signs are the API's: battery_w + = discharging, grid_w + =
 * importing. Board C (aPower S DC) and board D (non-backup panel), V2L and prices are not
 * modelled, so not drawn. The header (site, place, weather, models) is HTML above the drawing.
 * A grouped site (#241: one grid connection, one single-phase aGate per phase) is board E:
 * renderGroupDiagram, with each phase's Home Loads added (board E leaves them out).
 */
const NOT_MODELLED = [
  'per-aPower SoC and power (each card shows the combined battery: its SoC and an even share)',
  'two PV inputs on the aGate', 'aPower S DC hybrid', 'V2L', 'non-backup panel',
  'EV battery SoC', 'hot-water temperature', 'tariff prices', 'remote (cloud) VPP mode',
  'network connection (Ethernet, WiFi, 4G)',
];
const SKY_LABELS = { clear: 'clear sky', broken: 'broken cloud', overcast: 'overcast', storm: 'storm' };
function skyLabel(sky) { return SKY_LABELS[sky] || sky; }

// Small inline icons for the weather line (decorative; the text beside them says the same).
const SUN_ICON = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5"></circle>' +
  [0, 45, 90, 135, 180, 225, 270, 315].map(a => `<line x1="12" y1="2.5" x2="12" y2="5" transform="rotate(${a} 12 12)"></line>`).join('') + '</svg>';
const MOON_ICON = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 3.5a8.5 8.5 0 1 0 5 15 7 7 0 0 1-5-15z"></path></svg>';

// The design's icons (48×48 strokes), as drawn there.
const ICONS = {
  grid: '<path d="M24 4 L16 44 M24 4 L32 44 M11 12 H37 M9 20 H39 M18.5 32 H29.5 M17 20 L30 32 M31 20 L18 32 M14 12 L24 20 L34 12 M15 44 H33"></path>',
  meter: '<rect x="8" y="4" width="32" height="40" rx="3"></rect><circle cx="24" cy="17" r="8"></circle><path d="M24 17 L28 13"></path><rect x="14" y="31" width="20" height="7" rx="1.5"></rect><path d="M19 31 V38 M24 31 V38 M29 31 V38"></path>',
  panel: '<rect x="9" y="4" width="30" height="40" rx="3"></rect><path d="M24 8 V40 M14 12 H20 M28 12 H34 M14 19 H20 M28 19 H34 M14 26 H20 M28 26 H34 M14 33 H20 M28 33 H34"></path>',
  home: '<path d="M6 22 L24 6 L42 22 M10 19 V42 H38 V19 M20 42 V30 H28 V42"></path>',
  solar: '<path d="M8 10 H40 L44 32 H4 Z M6 21 H42 M18 10 L16 32 M30 10 L32 32 M24 32 V40 M14 40 H34"></path>',
  agate: '<rect x="11" y="8" width="26" height="36" rx="4"></rect><path d="M19 4.5 H29"></path><rect class="fill" x="15.5" y="26" width="17" height="14" rx="1.5"></rect><rect class="fill half" x="15.5" y="20" width="17" height="4" rx="1.5"></rect><path d="M25.5 11 L21 17 H27 L22.5 23" stroke-width="2"></path>',
  generator: '<rect x="5" y="10" width="38" height="26" rx="3"></rect><path d="M26 14 L19 24 H25 L22 32 L29 22 H23 Z"></path><circle cx="13" cy="40" r="3"></circle><circle cx="35" cy="40" r="3"></circle>',
  switchOn: '<rect x="5" y="15" width="38" height="18" rx="9"></rect><circle class="fill" cx="34" cy="24" r="5.5"></circle>',
  switchOff: '<rect x="5" y="15" width="38" height="18" rx="9"></rect><circle cx="14" cy="24" r="5"></circle>',
  hvac: '<rect x="4" y="10" width="40" height="28" rx="3"></rect><circle cx="17" cy="24" r="8"></circle><path d="M17 16 V32 M9 24 H25 M11.5 18.5 L22.5 29.5 M22.5 18.5 L11.5 29.5"></path><path d="M31 17 H39 M31 24 H39 M31 31 H39"></path>',
  hotWater: '<rect x="13" y="5" width="22" height="36" rx="8"></rect><path d="M19 24 C21 21 21 18.5 19 15.5 C17 12.5 17 10.5 19 8.5 M24 24 C26 21 26 18.5 24 15.5 C22 12.5 22 10.5 24 8.5 M29 24 C31 21 31 18.5 29 15.5 C27 12.5 27 10.5 29 8.5" stroke-width="1.9"></path><path d="M17 31 H31 M20 41 V45 M28 41 V45"></path>',
  ev: '<path d="M4 32 V26 L9 17 H33 L40 24 L44 26 V32 Z M8 24 H41"></path><circle class="hole" cx="13" cy="33" r="4"></circle><circle class="hole" cx="35" cy="33" r="4"></circle><path d="M25 2 L20 9 H26 L22 15" stroke-width="2"></path>',
};
// Appliance → icon for the load under a smart circuit (the design's HVAC, hot water and EV).
const LOAD_ICONS = { aircon: 'hvac', hvac: 'hvac', hot_water: 'hotWater', ev: 'ev' };
// The appliance on the circuit, else the circuit's name ("Water Heater", "EV Charger"), else a panel.
function loadIcon(appliance, name) {
  if (LOAD_ICONS[appliance]) return LOAD_ICONS[appliance];
  const n = (name || '').toLowerCase();
  return /water/.test(n) ? 'hotWater' : /\bev\b|car|vehicle/.test(n) ? 'ev'
       : /air|hvac|heat pump|cool/.test(n) ? 'hvac' : 'panel';
}

const IDLE_W = 10;
const DASH_PERIOD_MS = 800;    // one dash cycle; matches the CSS animation

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function kwText(w) { return (Math.abs(w) / 1000).toFixed(2) + ' kW'; }   // direction is shown by the line

// A dashed connector through the given points; dir +1 = flows from first point to last.
// The diagram is redrawn on every poll; a negative delay keeps the dashes moving smoothly.
function link(points, w, dir, extra) {
  const d = 'M ' + points.map(p => p[0] + ' ' + p[1]).join(' L ');
  const cls = Math.abs(w) < IDLE_W ? 'idle' : (dir * Math.sign(w) > 0 ? 'fwd' : 'rev');
  const delay = -((performance.now() % DASH_PERIOD_MS) / 1000).toFixed(3);
  return `<path class="flow ${cls} ${extra || ''}" d="${d}" style="animation-delay:${delay}s"></path>`;
}
// The same connector once per phase, as the design: 1 phase the middle line, 2 the sides, 3 all.
function phaseLinks(points, offset, legs, w, dir) {
  const offs = legs === 1 ? [0] : legs === 2 ? [-10, 10] : [-10, 0, 10];
  return offs.map(o => link(points.map(([x, y]) => offset === 'y' ? [x, y + o] : [x + o, y]), w, dir)).join('');
}

function icon(name, x, y, size, cls) {
  return `<svg class="ic ${cls || ''}" x="${x}" y="${y}" width="${size}" height="${size}" viewBox="0 0 48 48" aria-hidden="true">${ICONS[name]}</svg>`;
}
function text(x, y, s, cls) { return `<text class="${cls}" x="${x}" y="${y}">${esc(s)}</text>`; }
// A rounded box with an icon in the middle (meter, panel, aGate) or icon and two lines (tiles).
function box(x, y, w, h, cls) { return `<rect class="box ${cls || ''}" x="${x}" y="${y}" width="${w}" height="${h}" rx="${w > 100 ? 18 : 14}"></rect>`; }

// 'charging' | 'discharging' | 'standby' (off-grid versions included), for the colour classes.
function statusKey(g) {
  const n = (g.run_status_name || '').toLowerCase();
  return n.includes('discharging') ? 'discharging' : n.includes('charging') ? 'charging'
       : n.includes('standby') ? 'standby' : '';
}

// The service meter: the main breaker's amps and rating (#156); red when tripped.
function meterText(g) {
  const legs = g.legs.length + '-phase';
  const s = g.service;
  if (!s) return { value: '', sub: legs, lines: ['no rating set'], cls: '' };
  const lines = [(s.rating_w / 1000).toFixed(1) + ' kW'];
  if (s.open) lines.push(['tripped', 'c-red']);
  else if (s.trip_progress > 0) lines.push(['tripping ' + Math.round(s.trip_progress * 100) + ' %', 'c-orange']);
  return { value: Math.round(s.main_breaker_a) + ' A', sub: legs, lines, cls: s.open ? 'bad' : '' };
}

// Header (#205): the site's name, where it is (and its local time), and its hardware.
function siteName(g) { return (g.location && g.location.name) || g.id; }
function placeText(g) {
  const l = g.location || {};
  const place = [l.city, l.state, l.postcode, l.country].filter(Boolean).join(', ');
  return [place, typeof localTimeText === 'function' ? localTimeText(g) : ''].filter(Boolean).join(' · ');
}
function modelSummary(g) {
  const m = g.model;
  if (!m) return '';
  const p = m.apower;
  const kwp = g.weather && g.weather.kwp;
  return [m.agate.name, p.units + '× ' + p.name, +(p.kwh * p.units).toFixed(1) + ' kWh',
          kwp ? 'PV ' + kwp + ' kWp' : null].filter(Boolean).join(' · ');
}
// The design's configuration label: how solar reaches the aGate.
function configLabel(g) {
  const inputs = Object.keys(g.pv_inputs || {}).filter(k => g.pv_inputs[k] > 0);
  const remote = inputs.filter(k => k !== 'proximal');
  return [inputs.includes('proximal') ? 'Solar → aGate AC port' : null,
          remote.length ? 'Remote PV ×' + remote.length + ' → aPbox' : null].filter(Boolean).join(' · ') || 'No solar';
}

function weatherLine(g) {
  const ov = g.overrides || {};
  const forced = k => ov[k] != null;
  const w = g.weather;
  const sun = g.sun;
  // PV cell temperature and its effect on output (#109), e.g. "PV cells 58 °C (−5 %)" (or °F).
  const pvTempText = t => {
    if (!t || t.cell_c == null) return null;
    const pct = Math.round((t.factor - 1) * 100);
    return 'PV cells ' + temp(t.cell_c, 0) + ' (' + (pct > 0 ? '+' : pct < 0 ? '−' : '±') + Math.abs(pct) + ' %)';
  };
  const ic = sun ? (sun.up ? SUN_ICON : MOON_ICON) : '';
  const head = [
    sun ? (sun.up ? 'Day' : 'Night') + ' · sun ' + sun.elevation_deg + '°' : 'No location, so no sun position',
    w ? skyLabel(w.sky || w.model) : 'bell curve (no weather model)',
    w && w.poa_w_m2 != null ? w.poa_w_m2 + ' W/m² on the array' : null,
    'ambient ' + temp(g.ambient_c) + (forced('ambient_c') ? ' (forced)' : ''),
    pvTempText(g.pv_temperature),
    forced('solar_w') ? (g.cap_forced_solar ? forcedSolarText(g)
      : 'solar forced ' + kwText(ov.solar_w) + ', not capped by the sun') : null,
    curtailText(g) || null,
    'cabinet ' + temp(g.cabinet_c) + ' (' + (g.install
      ? g.install.label + ', surroundings ' + temp(g.install.surroundings_c)
      : 'modelled') + ')',
    'inverter ' + (g.inverter_state_name || '').toLowerCase()].filter(Boolean).join(' · ');
  return `<p class="diagram-head">${ic}<span>${esc(head)}</span></p>`;
}

function renderDiagram(g, _unit, all) {
  if (!g) return '';
  if (g.group && all) {                       // a grouped site: all its aGates in one drawing (#241)
    const ids = Object.values(g.group.members);
    const members = ids.map(id => all.find(x => x.id === id)).filter(Boolean);
    if (members.length === ids.length) return renderGroupDiagram(g, members);
  }
  const out = [];
  const links = [];
  const ov = g.overrides || {};
  const forced = k => ov[k] != null;   // a value set by pv / load / ambient, not the model
  const legs = Math.max(1, Math.min(3, g.legs.length));

  // Grid → service meter → aGate (+ = import, towards the aGate), one line per phase.
  links.push(phaseLinks([[142, 470], [245, 470]], 'y', legs, g.grid_w, +1));
  links.push(phaseLinks([[355, 470], [520, 470]], 'y', legs, g.grid_w, +1));
  const gridCls = g.grid_connected ? '' : 'bad';
  out.push(icon('grid', 80, 440, 60, gridCls));
  out.push(text(110, 522, 'Grid', 'lbl ok ' + gridCls));
  out.push(text(110, 550, kwText(g.grid_w), 'val'));
  out.push(text(110, 570, g.grid_connected ? (g.grid_w > IDLE_W ? 'importing' : g.grid_w < -IDLE_W ? 'exporting' : 'connected')
                                         : 'down' + (g.trip_reason ? ' · ' + g.trip_reason : ''), 'sub ' + gridCls));
  const m = meterText(g);
  out.push(text(300, 376, 'Service Meter', 'lbl'));
  out.push(text(300, 398, (m.value ? m.value + ' · ' : 'no rating set · ') + m.sub, 'svc ' + m.cls));
  out.push(box(245, 415, 110, 110, m.cls), icon('meter', 268, 438, 64, m.cls));
  (m.value ? m.lines : []).forEach((l, i) => {
    const [t, c] = Array.isArray(l) ? l : [l, ''];
    out.push(text(300, 545 + i * 18, t, 'sub ' + c));
  });

  // aGate backup port → main panel → home loads.
  links.push(phaseLinks([[600, 380], [600, 255]], 'x', legs, g.load_w, +1));
  links.push(phaseLinks([[545, 200], [432, 200]], 'y', legs, g.load_w, +1));
  out.push(`<text class="lbl" x="600" y="131">Main Panel <tspan class="muted">· backed up</tspan></text>`);
  out.push(box(545, 145, 110, 110), icon('panel', 568, 168, 64));
  out.push(`<text class="side" x="620" y="321">${esc(kwText(g.load_w))}</text>`);
  out.push(icon('home', 336, 156, 88, forced('load_w') ? 'forced' : ''));
  out.push(text(380, 266, 'Home Loads' + (forced('load_w') ? ' · forced' : ''), 'lbl b'));
  out.push(text(380, 292, kwText(g.load_w), 'val'));

  // PV: the aGate's AC port (board A), and remote inputs through an aPbox (board B).
  const inputs = Object.keys(g.pv_inputs || {}).filter(k => g.pv_inputs[k] > 0);
  const remote = inputs.filter(k => k !== 'proximal').slice(0, 2);
  const pvTile = (x, label, w, closed) => {
    out.push(icon('solar', x, 158, 60, closed ? (forced('solar_w') ? 'forced' : '') : 'dim'));
    out.push(text(x + 30, 238, label, 'lbl s'));
    out.push(text(x + 30, 262, kwText(w), 'val m'));
    out.push(text(x + 30, 280, (closed ? 'relay closed' : 'relay open') + (forced('solar_w') ? ' · forced' : ''), 'sub'));
  };
  if (inputs.includes('proximal')) {
    const w = g.pv_by_input.proximal || 0;
    if (remote.length) {                          // both: the aGate's port takes the right column
      links.push(link([[1008, 290], [1008, 392], [680, 392]], w, +1));
      pvTile(978, 'PV · AC port', w, g.pv_relays.proximal);
    } else {
      links.push(link([[760, 290], [760, 400], [680, 400]], w, +1));
      pvTile(730, 'PV · AC port', w, g.pv_relays.proximal);
    }
  }
  if (remote.length) {
    const total = remote.reduce((s, k) => s + (g.pv_by_input[k] || 0), 0);
    remote.forEach((k, i) => {
      const x = 740 + i * 120;
      const w = g.pv_by_input[k] || 0;
      links.push(link([[x, 290], [x, 326], [i ? 840 : 760, 326]], w, +1));
      pvTile(x - 30, 'Remote PV ' + k.replace('remote', ''), w, g.pv_relays[k]);
    });
    links.push(link([[800, 352], [800, 410], [680, 410]], total, +1));
    out.push(box(760, 300, 80, 52, 'apbox'), text(800, 331, 'aPbox', 'lbl b'));
  }

  // aPower cards, one per unit (per-aPower values are not modelled: each shows the combined
  // battery's SoC and an even share of its power). + = discharging (out of the battery).
  const units = Math.max(1, g.apower_units);
  const shown = Math.min(units, 4);
  const each = g.battery_w / units;
  const p = g.model ? g.model.apower : null;
  const name = p ? p.name : 'aPower';
  const spec = p ? p.kwh + ' kWh · ' + p.kw + ' kW inverter'
                 : (g.apower_rating.wh / 1000).toFixed(1) + ' kWh · ' + (g.apower_rating.w / 1000) + ' kW inverter';
  const soc = Math.max(0, Math.min(100, g.soc));
  const C = 2 * Math.PI * 33;
  links.push(link([[680, 470], [880, 470]], g.battery_w, -1));
  for (let i = 0; i < shown; i++) {
    const top = 414 + i * 140, cy = top + 56;
    if (i) links.push(link([[840, cy - 140], [840, cy], [880, cy]], each, -1));
    const dir = Math.abs(each) < IDLE_W ? '' : each > 0 ? ' ↓' : ' ↑';
    out.push(`<g class="bat">${box(880, top, 268, 112, 'card')}
      <circle class="ring-bg" cx="${934}" cy="${cy}" r="33"></circle>
      <circle class="ring" cx="${934}" cy="${cy}" r="33" stroke-dasharray="${(soc / 100 * C).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 934 ${cy})"></circle>
      ${text(934, cy + 6, Math.round(soc) + '%', 'pct')}
      <text class="bn" x="986" y="${cy - 16}">${esc(name + ' ' + (i + 1))}</text>
      <text class="bs" x="986" y="${cy + 5}">${esc(spec)}</text>
      <text class="bp c-${statusKey(g)}" x="986" y="${cy + 27}">${esc(kwText(each) + dir)}</text></g>`);
  }
  if (units > shown) out.push(text(1014, 414 + shown * 140 + 20, '+ ' + (units - shown) + ' more aPower', 'sub'));

  // aGate in the middle; its status dot follows the card's health (groups.js cardHealth).
  const force = g.force;   // an M704 setpoint (FORCE); the app calls it VPP Mode
  const health = typeof cardHealth === 'function' ? cardHealth(g).state : '';
  out.push(box(520, 380, 160, 180, 'agate' + (force ? ' vpp' : '')), icon('agate', 556, 426, 88));
  out.push(`<circle class="dot ${health}" cx="679" cy="379" r="9"></circle>`);
  out.push(text(600, 586, 'FranklinWH aGate', 'lbl l'));
  out.push(`<text class="sub" x="600" y="608"><tspan class="c-${({ 1: 'backup', 2: 'self', 3: 'tou' })[g.native_mode]}">${esc(g.native_mode_name)}</tspan>` +
    ` · <tspan class="c-${statusKey(g)}">${esc(g.run_status_name)}</tspan>` +
    (force ? ` · <tspan class="c-orange">FORCE ${esc(force.direction)}</tspan>` : '') + '</text>');
  out.push(text(470, 600, g.grid_connected ? 'reserve ' + Math.round(g.active_reserve) + '%'
                                           : 'off-grid floor ' + Math.round(g.offgrid_floor_soc) + '%', 'sub end'));

  // I/O bus: generator, smart circuits 1–3, and the loads on them.
  links.push(link([[600, 616], [600, 660]], 0, +1));
  links.push(link([[402, 660], [784, 660]], 0, +1));
  const gen = g.generator || {};
  links.push(link([[402, 690], [402, 660]], g.generator_w || 0, +1));
  out.push(box(358, 690, 88, 88, gen.installed ? 'tile' : 'tile dim'), icon('generator', 384, 700, 36, gen.installed ? '' : 'dim'));
  out.push(text(402, 754, 'Generator', 'tt'), text(402, 770, gen.installed ? gen.state_name + ' · ' + kwText(g.generator_w) : 'not fitted', 'ts'));
  const circuits = g.circuits || {};
  const xs = { 1: 544, 2: 656, 3: 784 };
  if (circuits[1] || circuits[2]) {
    out.push(`<rect class="band" x="488" y="674" width="224" height="116" rx="18"></rect>`);
    out.push(`<text class="band-l" x="510" y="678">SC 1 + 2</text>`);
  }
  [1, 2, 3].forEach(n => {
    const c = circuits[n];
    if (!c) return;
    const x = xs[n];
    if (n !== 1) links.push(link([[x, 660], [x, n === 2 ? 674 : 690]], 0, +1));
    else links.push(link([[x, 660], [x, 674]], 0, +1));
    out.push(box(x - 44, 690, 88, 88, c.on ? 'tile on' : 'tile'), icon(c.on ? 'switchOn' : 'switchOff', x - 19, 698, 38, c.on ? '' : 'dim'));
    out.push(text(x, 754, 'SW ' + n, 'tt'), text(x, 770, c.on ? 'On' : 'Off', 'ts' + (c.on ? ' ok' : '')));
    const app = Object.entries(g.appliances || {}).find(([, a]) => a.circuit === n);
    links.push(link([[x, 778], [x, 846]], c.power_w, +1));
    out.push(icon(loadIcon(app && app[0], c.name), x - 27, 848, 54, c.on ? '' : 'dim'));
    out.push(text(x, 922, c.name, 'lbl'), text(x, 946, kwText(c.power_w), 'val s'));
  });

  const label = `Energy flow for ${g.id}: grid ${kwText(g.grid_w)}, home ${kwText(g.load_w)}, ` +
    `solar ${kwText(g.solar_w)}, battery ${Math.round(g.soc)}% ${kwText(g.battery_w)}`;
  const tags = [g.location && g.location.state, configLabel(g)].filter(Boolean)
    .map((t, i) => `<span class="dt-tag${i === 0 && g.location.state ? '' : ' cfg'}">${esc(t)}</span>`).join('');
  return `<div class="diagram-top"><div class="dt-left">
      <p class="dt-site">${esc(siteName(g))}</p>
      ${placeText(g) ? `<p class="dt-place">${esc(placeText(g))}</p>` : ''}
      ${weatherLine(g)}
      <p class="dt-tags">${tags}</p></div>
      ${modelSummary(g) ? `<p class="dt-model">${esc(modelSummary(g))}</p>` : ''}</div>
    <div class="diagram-wrap"><svg class="diagram" viewBox="20 100 1200 880" role="img" aria-label="${esc(label)}">
      ${links.join('')}${out.join('')}
    </svg></div>
    <p class="diagram-note">Not modelled, so not shown: ${esc(NOT_MODELLED.join(', '))}.</p>`;
}

// Board E (#241): an AU three-phase site, one single-phase aGate per phase, in the design's
// 1340-wide coordinates. One row per aGate: its phase line from the service meter, its aPowers,
// then AC solar or its smart circuits and their loads, and its phase's Home Loads (not on the
// board; the owner asked for them). g is the active member, shown with a thicker outline.
function renderGroupDiagram(g, members) {
  const out = [];
  const links = [];
  const sum = k => members.reduce((s, m) => s + (m[k] || 0), 0);
  const n = members.length;
  const up = members.every(m => m.grid_connected);
  const tripped = members.some(m => m.service && m.service.open);
  const gridW = sum('grid_w');

  // Grid → service meter (one line per phase) → each aGate on its own phase.
  const tops = [170, 480, 790];
  members.forEach((m, i) => {
    const y = 540 + (i - (n - 1) / 2) * 10;
    links.push(link([[134, y], [210, y]], m.grid_w, +1));
    const T = tops[i], ym = 511 + 35 * i, yg = T + 66;
    links.push(link(i === 1 ? [[320, ym], [420, ym]] : [[320, ym], [370, ym], [370, yg], [420, yg]], m.grid_w, +1));
  });
  const gridCls = up ? '' : 'bad';
  out.push(icon('grid', 70, 518, 64, gridCls));
  out.push(text(102, 604, 'Grid', 'lbl ok ' + gridCls));
  out.push(text(102, 630, kwText(gridW), 'val'));
  out.push(text(102, 650, up ? (gridW > IDLE_W ? 'importing' : gridW < -IDLE_W ? 'exporting' : 'connected')
                             : 'down' + (members[0].trip_reason ? ' · ' + members[0].trip_reason : ''), 'sub ' + gridCls));
  const svc = members[0].service;
  const vll = Math.round(g.nominal_v_ln * Math.sqrt(3) / 10) * 10;   // 230 V L-N → 400 V
  const mcls = tripped ? 'bad' : '';
  out.push(text(265, 456, 'Service Meter', 'lbl'));
  out.push(text(265, 478, svc ? n + ' × ' + Math.round(svc.main_breaker_a) + ' A · ' + vll + ' V' : 'no rating set · ' + n + '-phase', 'svc ' + mcls));
  out.push(box(210, 495, 110, 110, mcls), icon('meter', 233, 518, 64, mcls));
  out.push(text(265, 630, 'Home Loads, all phases', 'lbl s'), text(265, 654, kwText(sum('load_w')), 'val m'));

  members.forEach((m, i) => {
    const T = tops[i];
    const leg = m.group.phase;
    // The phase's label and its import, beside its line (left of the riser for L1 and L3).
    const [lx, ly, end] = i === 1 ? [370, 532, ''] : [358, i === 0 ? 372 : 712, ' end'];
    out.push(text(lx, ly, leg, 'phase' + end));
    out.push(text(lx, i === 1 ? 572 : ly + 18, (m.service ? Math.round(m.service.main_breaker_a) + ' A · ' : '') + kwText(m.grid_w), 'sub' + end));
    out.push(text(490, T - 24, 'aGate ' + (i + 1) + ' · ' + leg, 'lbl b'));
    const health = typeof cardHealth === 'function' ? cardHealth(m).state : '';
    out.push(box(420, T, 140, 140, 'agate' + (m.force ? ' vpp' : '') + (m.id === g.id ? ' active' : '')),
             icon('agate', 446, T + 26, 88));
    out.push(`<circle class="dot ${health}" cx="559" cy="${T + 1}" r="9"></circle>`);

    // aPowers under the aGate: the combined battery (per-aPower values are not modelled).
    const p = m.model ? m.model.apower : null;
    const units = Math.max(1, m.apower_units);
    const soc = Math.max(0, Math.min(100, m.soc));
    const C = 2 * Math.PI * 26, cy = T + 209;
    links.push(link([[490, T + 140], [490, T + 164]], m.battery_w, -1));
    const dir = Math.abs(m.battery_w) < IDLE_W ? '' : m.battery_w > 0 ? ' ↓' : ' ↑';
    out.push(`<g class="bat">${box(400, T + 164, 180, 90, 'tile')}
      <circle class="ring-bg" cx="438" cy="${cy}" r="26"></circle>
      <circle class="ring" cx="438" cy="${cy}" r="26" stroke-dasharray="${(soc / 100 * C).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 438 ${cy})"></circle>
      ${text(438, cy + 5, Math.round(soc) + '%', 'pct s')}
      <text class="bn" x="474" y="${cy - 10}">${esc((units > 1 ? units + '× ' : '') + (p ? p.name : 'aPower'))}</text>
      <text class="bp c-${statusKey(m)}" x="474" y="${cy + 14}">${esc(kwText(m.battery_w) + dir)}</text></g>`);

    // A row tile: icon, label on the left, kW on the right.
    const tile = (x, y, w, ic, label, watts, cls, icCls) => {
      out.push(box(x, y, w, 46, cls), icon(ic, x + 8, y + 4, 38, icCls || ''));
      out.push(text(x + 54, y + 28, label, 'gl'), text(x + w - 12, y + 28, watts == null ? '' : kwText(watts), 'gv'));
    };
    const circuits = Object.entries(m.circuits || {});
    const pvW = (m.pv_by_input || {}).proximal || 0;
    const hasPv = m.solar_peak_w > 0 && (m.pv_inputs || {}).proximal > 0;
    if (circuits.length) {
      out.push(`<rect class="band" x="636" y="${T}" width="264" height="140" rx="18"></rect>`);
      out.push(`<text class="band-l" x="652" y="${T + 4}">SC ${circuits.map(([k]) => k).join(' + ')}</text>`);
      links.push(link([[560, T + 66], [610, T + 66]], 0, +1));
      links.push(link([[610, T + 39], [610, T + 101]], 0, +1));
      circuits.slice(0, 2).forEach(([k, c], j) => {
        const y = T + 16 + 62 * j;
        const app = Object.entries(m.appliances || {}).find(([, a]) => String(a.circuit) === k);
        links.push(link([[610, y + 23], [656, y + 23]], c.power_w, +1));
        tile(656, y, 228, c.on ? 'switchOn' : 'switchOff', 'SC ' + k + ' · ' + (c.on ? 'On' : 'Off'), null,
             c.on ? 'tile on' : 'tile', c.on ? '' : 'dim');
        const used = app || Math.abs(c.power_w) >= IDLE_W;
        links.push(link([[884, y + 23], [960, y + 23]], c.power_w, +1));
        if (used) tile(960, y, 320, loadIcon(app && app[0], c.name), app ? app[0].replace(/_/g, ' ') : c.name, c.power_w, 'tile', c.on ? '' : 'dim');
        else tile(960, y, 320, 'panel', 'Unused', null, 'tile unused', 'dim');
      });
    }
    if (hasPv) {
      const label = 'AC solar · ' + +(m.solar_peak_w / 1000).toFixed(1) + ' kWp' +
                    ((m.pv_relays || {}).proximal === false ? ' · relay open' : '');
      if (!circuits.length) {
        links.push(link([[656, T + 66], [560, T + 66]], pvW, +1));
        out.push(box(656, T + 35, 320, 70, 'tile'), icon('solar', 668, T + 46, 48));
        out.push(text(728, T + 64, label, 'gl'), text(728, T + 88, kwText(pvW), 'gv l'));
      } else {
        links.push(link([[960, T + 173], [930, T + 173], [930, T + 146], [640, T + 146], [640, T + 128], [560, T + 128]], pvW, +1));
        tile(960, T + 150, 320, 'solar', label, pvW, 'tile');
      }
    }
    // This phase's backed-up house load (all of it, smart circuits included).
    links.push(link([[560, T + 118], [620, T + 118], [620, T + 173], [656, T + 173]], m.load_w, +1));
    tile(656, T + 150, 320, 'home', 'Home Loads · ' + leg, m.load_w, 'tile');
  });

  const label = `Energy flow for ${g.group.id}: grid ${kwText(gridW)}, home ${kwText(sum('load_w'))}; ` +
    members.map(m => `${m.group.phase} ${m.id}: grid ${kwText(m.grid_w)}, home ${kwText(m.load_w)}, ` +
                     `solar ${kwText(m.solar_w)}, battery ${Math.round(m.soc)}% ${kwText(m.battery_w)}`).join('; ');
  const agate = g.model ? g.model.agate.name : 'aGate';
  const ap = g.model ? g.model.apower : null;
  const units = sum('apower_units');
  const kwp = members.reduce((s, m) => s + (m.solar_peak_w > 0 && (m.pv_inputs || {}).proximal > 0 ? m.solar_peak_w : 0), 0);
  const model = [n + '× ' + agate + ' (one per phase)', ap ? units + '× ' + ap.name : null,
                 ap ? +(ap.kwh * units).toFixed(1) + ' kWh' : null, kwp ? 'PV ' + +(kwp / 1000).toFixed(1) + ' kWp' : null]
    .filter(Boolean).join(' · ');
  const tags = [g.location && g.location.state, n + '-phase · one aGate per phase']
    .filter(Boolean).map((t, i) => `<span class="dt-tag${i === 0 && g.location.state ? '' : ' cfg'}">${esc(t)}</span>`).join('');
  return `<div class="diagram-top"><div class="dt-left">
      <p class="dt-site">${esc((g.location && g.location.name) || g.group.id)}</p>
      ${placeText(g) ? `<p class="dt-place">${esc(placeText(g))}</p>` : ''}
      ${weatherLine(g)}
      <p class="dt-tags">${tags}</p></div>
      <p class="dt-model">${esc(model)}</p></div>
    <div class="diagram-wrap"><svg class="diagram group" viewBox="20 120 1300 940" role="img" aria-label="${esc(label)}">
      ${links.join('')}${out.join('')}
    </svg></div>
    <p class="diagram-note">Not modelled, so not shown: ${esc(NOT_MODELLED.join(', '))}.</p>`;
}
