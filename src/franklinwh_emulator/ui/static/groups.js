/* Gateway cards grouped by location (#86): country, state, city, postcode, site ID or timezone;
 * and the detail card's gateway picker, filtered by the same fields (#142); and the card
 * border's colour when a gateway is faulted or offline (#168).
 *
 * Pure functions (no DOM), so they can be tested without a browser. Each group's label
 * includes the levels above it (state → "AU · NSW") so equal names in different places
 * don't merge. Gateways without the field go last, under "no <field>".
 */
const GROUP_BY = {
  none: 'None', country: 'Country', state: 'State / province', city: 'Suburb / city',
  postcode: 'Postcode', site: 'Site ID', timezone: 'Timezone',
};

function _loc(g) { return (g && g.location) || {}; }

// The gateway's timezone: its IANA zone, else its fixed offset ("UTC+13:00"), else ''.
function zoneOf(g) {
  const t = (g && g.local_time) || '';
  return _loc(g).timezone || (t.length > 19 ? 'UTC' + (t.endsWith('Z') ? '' : t.slice(19)) : '');
}

// The group label for one gateway, or '' when it lacks the field.
function groupLabel(g, by) {
  const l = _loc(g);
  const parts = {
    country: [l.country],
    state: [l.country, l.state],
    city: [l.country, l.state, l.city],
    postcode: [l.country, l.postcode],
    site: [l.site_id],
    timezone: [zoneOf(g)],
  }[by];
  if (!parts) return '';
  const own = parts[parts.length - 1];
  return own ? parts.filter(Boolean).join(' · ') : '';
}

// [{label, missing, items}] in label order, the "no <field>" group last. 'none': one group.
function groupGateways(gateways, by) {
  if (!GROUP_BY[by] || by === 'none') return [{ label: '', missing: false, items: gateways.slice() }];
  const groups = new Map();
  for (const g of gateways) {
    const label = groupLabel(g, by);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(g);
  }
  const named = [...groups.keys()].filter(Boolean).sort((a, b) => a.localeCompare(b));
  const out = named.map(label => ({ label, missing: false, items: groups.get(label) }));
  if (groups.has('')) {
    out.push({ label: 'no ' + GROUP_BY[by].toLowerCase(), missing: true, items: groups.get('') });
  }
  return out;
}

// "11:45 · Australia/Sydney", or "11:45 · UTC+13:00" for a fixed offset (from local_time).
function localTimeText(g) {
  const t = (g && g.local_time) || '';
  const hm = t.slice(11, 16);
  const zone = zoneOf(g);
  return [hm, zone].filter(Boolean).join(' · ');
}

// -- the gateway picker (#142) --------------------------------------------------------------------
// Filters, in narrowing order; each matches one field exactly ('' = any).
const PICK_FILTERS = [['country', 'Country'], ['state', 'State / province'], ['postcode', 'Postcode'], ['timezone', 'Timezone']];

function pickField(g, f) { return f === 'timezone' ? zoneOf(g) : (_loc(g)[f] || ''); }

// Gateways matching every set filter.
function pickGateways(gateways, filters) {
  return gateways.filter(g => PICK_FILTERS.every(([f]) => !filters[f] || pickField(g, f) === filters[f]));
}

// The values a filter can take, given the other filters set: sorted, without ''.
function pickValues(gateways, filters, field) {
  const others = { ...filters, [field]: '' };
  const vals = new Set(pickGateways(gateways, others).map(g => pickField(g, field)).filter(Boolean));
  return [...vals].sort((a, b) => a.localeCompare(b));
}

// -- card health (#168) ----------------------------------------------------------------------
// 'fault' (red): an alarm, a tripped main breaker, or an injected fault on either listener.
// 'offline' (orange): rebooting, an episode with the listeners down, or a listener set offline.
// Offline wins: a gateway that is down says so first. ``why`` is the card's tooltip.
function injected(f) {
  return Object.keys(f || {}).filter(k => f[k] && k !== 'offline');
}
function cardHealth(g) {
  const off = [], bad = [];
  if (g.rebooting) off.push('rebooting');
  if (g.episode_phase === 'down') off.push('episode: listeners down');
  if (g.faults && g.faults.offline) off.push('Modbus TCP offline');
  if (g.dc_faults && g.dc_faults.offline) off.push('Direct Connect offline');
  (g.alarm_names || []).forEach(a => bad.push('alarm ' + a));
  if (g.service && g.service.open) bad.push('main breaker tripped');
  injected(g.faults).forEach(k => bad.push('Modbus TCP fault ' + k));
  injected(g.dc_faults).forEach(k => bad.push('Direct Connect fault ' + k));
  const state = off.length ? 'offline' : bad.length ? 'fault' : '';
  return { state, why: off.concat(bad).join(' · ') };
}

// -- hardware model (#180, #191) ------------------------------------------------------------------
// "aGate X-01-AU (AGT-R1V1-AU) · 2× aPower X-01-AU (APR-05K13V1-AU) · 13.6 kWh, 5 kW". The SKU
// carries the country code and is what FranklinWH documents name; the first one is the current.
function _withSku(m) { return m.name + (m.skus && m.skus.length ? ' (' + m.skus[0] + ')' : ''); }
function modelText(g) {
  const m = g.model, p = m.apower;
  return _withSku(m.agate) + ' · ' + p.units + '× ' + _withSku(p) + ' · ' + p.kwh + ' kWh, ' + p.kw + ' kW';
}
function modelTitle(g) {
  const m = g.model;
  return 'aGate code ' + m.agate.code + ' (' + m.agate.skus.join(' / ') + ', ' + m.agate.status + '); aPower code ' +
         m.apower.code + ' (' + m.apower.skus.join(' / ') + ', ' + m.apower.status + '); region ' + m.region;
}

// -- action results and Activity (#167) -----------------------------------------------------------
// One clock: the gateway's local time, as its card and Events show it (not the browser's, not UTC).
function appliedText(state, body) {
  return 'Applied to ' + state.id + ' at ' + (state.local_time || '').slice(11, 19) + ': ' + JSON.stringify(body);
}
function logLine(e) {
  return (e.site_time || e.at).slice(11, 19) + ' · ' + e.gateway + ' · ' + e.action + ' ' + JSON.stringify(e.body) +
         (e.source !== 'api' ? ' · ' + e.source : '') + (e.ok ? '' : ' · ' + e.error);
}

// -- solar: forced, capped by the sun, curtailed (#176) ---------------------------------------------
// The sun caps a forced value at solar_available_w; anything below that is curtailment, with
// its cause (off-grid: nowhere to put it, or the full-battery 62.2 Hz cut-off; on-grid: an
// export limit or grid support). The two used to be one "capped by the sun" tag.
function _kw(w) { return (Math.abs(w) / 1000).toFixed(2) + ' kW'; }
function forcedSolarText(g) {
  const forced = g.overrides.solar_w;
  const capped = g.cap_forced_solar && g.solar_available_w < forced - 1;
  return 'solar forced ' + _kw(forced) + (capped ? ', capped at ' + _kw(g.solar_available_w) + ' by the sun' : '');
}
function curtailText(g) {
  if (!(g.solar_clipped_w > 1)) return '';
  const why = !g.grid_connected
    ? (g.pv_cutoff ? 'off-grid, battery full: 62.2 Hz cut-off'
      : g.droop_hz ? 'off-grid, frequency droop at ' + g.droop_hz.toFixed(2) + ' Hz'
      : 'off-grid, nowhere to put it')
    : 'export limit or grid support';
  return 'PV curtailed ' + _kw(g.solar_clipped_w) + ': ' + why;
}
