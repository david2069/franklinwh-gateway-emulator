/* Gateway cards grouped by location (#86): country, state, city, postcode, site ID or timezone;
 * and the detail card's gateway picker, filtered by the same fields (#142).
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
