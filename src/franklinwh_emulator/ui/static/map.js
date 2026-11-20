/* Map of where the fleet's gateways are installed (#352): an optional view, after the Direct
 * Connect bridge's CloudFront PoP map. Leaflet 1.9.4 (vendored, loaded on first use), one circle
 * marker per place with a tooltip. The base is Natural Earth's 1:110m outline shipped with the UI
 * (world-110m.json: land, country borders, state and province lines), not online tiles: the UI
 * makes no network requests, so the map works offline.
 *
 * mapPoints, mapTooltip and mapEsc are pure (no DOM), so they can be tested without a browser.
 */
const MAP_FIT_MAX_ZOOM = 6;

// One point per place: gateways at the same spot (a group's aGates, one per phase, or separate
// sites in one city) share one; gateways without a location are left out (mapMissing counts them).
function mapPoints(gateways) {
  const pts = new Map();
  for (const g of gateways) {
    const l = (g && g.location) || {};
    if (typeof l.latitude !== 'number' || typeof l.longitude !== 'number') continue;
    const key = l.latitude.toFixed(3) + ',' + l.longitude.toFixed(3);
    if (!pts.has(key)) pts.set(key, { key, lat: l.latitude, lon: l.longitude, city: l.city || '', items: [] });
    pts.get(key).items.push(g);
  }
  return [...pts.values()];
}

function mapMissing(gateways) {
  return gateways.filter(g => !mapPoints([g]).length).length;
}

function mapEsc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// The tooltip: the place, then one line per gateway with its SoC.
function mapTooltip(p) {
  const head = p.city ? '<b>' + mapEsc(p.city) + '</b><br>' : '';
  return head + p.items.map(g => mapEsc(g.id) + ' · ' + Math.round(g.soc) + '%').join('<br>');
}

// -- Leaflet (kept out of Alpine's reactive data: its proxy breaks Leaflet objects) ----------------
let _leafletP = null, _map = null, _layer = null, _fitKey = '';
const _markers = new Map();   // point key -> its circle marker

function ensureLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (_leafletP) return _leafletP;
  _leafletP = new Promise((resolve, reject) => {
    const css = document.createElement('link');
    css.rel = 'stylesheet'; css.href = '/static/leaflet-1.9.4.css';
    document.head.appendChild(css);
    const s = document.createElement('script');
    s.src = '/static/leaflet-1.9.4.min.js';
    s.onload = () => resolve(window.L);
    s.onerror = () => { _leafletP = null; reject(new Error('Leaflet did not load')); };
    document.head.appendChild(s);
  });
  return _leafletP;
}

async function openFleetMap(el) {
  const L = await ensureLeaflet();
  if (!_map) {
    _map = L.map(el, { worldCopyJump: true, minZoom: 0, maxZoom: 8, zoomSnap: 0.25 }).setView([20, 0], 1);
    _map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    const r = await fetch('/static/world-110m.json');
    if (!r.ok) throw new Error('the outline answered ' + r.status);
    L.geoJSON(await r.json(), {
      interactive: false,
      style: f => ({ className: 'map-' + f.properties.k, weight: f.properties.k === 'state' ? 0.5 : 1 }),
      attribution: 'Outline: <a href="https://www.naturalearthdata.com/">Natural Earth</a>',
    }).addTo(_map);
  }
  setTimeout(() => _map && _map.invalidateSize(), 50);
}

// Leaflet measures its box only when told: after pinning or unpinning (#365) changes its height.
function resizeFleetMap() {
  if (_map) _map.invalidateSize();
}

// The markers, updated in place on each poll (a redraw would close an open tooltip every second);
// the view fits the points when they change.
function drawFleetMap(points, activeId, onPick, onDrawer) {
  const L = window.L;
  if (!L || !_map) return;
  if (!_layer) _layer = L.layerGroup().addTo(_map);
  const seen = new Set();
  for (const p of points) {
    seen.add(p.key);
    const on = p.items.some(g => g.id === activeId);
    let m = _markers.get(p.key);
    if (!m) {
      m = L.circleMarker([p.lat, p.lon], { weight: 2, className: 'map-pt' }).bindTooltip('').addTo(_layer);
      _markers.set(p.key, m);
    }
    m.setLatLng([p.lat, p.lon]);
    m.setRadius(7 + 2 * Math.min(p.items.length - 1, 4));
    m.setStyle({ fillOpacity: on ? 0.55 : 0.35 });
    m.setTooltipContent(mapTooltip(p));
    // A click selects the place's first gateway, or the next one when one of them is selected.
    const i = p.items.findIndex(g => g.id === activeId);
    m.off('click').on('click', () => onPick(p.items[(i + 1) % p.items.length].id));
    // A right-click (a long press on touch) opens the actions drawer on the place's selected
    // gateway, or its first (#361).
    m.off('contextmenu').on('contextmenu', e => {
      if (e.originalEvent) e.originalEvent.preventDefault();
      if (onDrawer) onDrawer(p.items[Math.max(i, 0)].id);
    });
    const el = m.getElement();
    if (el) el.classList.toggle('on', on);
  }
  for (const [k, m] of _markers) if (!seen.has(k)) { m.remove(); _markers.delete(k); }
  const key = points.map(p => p.key).join('|');
  if (key !== _fitKey) {
    _fitKey = key;
    if (points.length) _map.fitBounds(points.map(p => [p.lat, p.lon]),
                                     { paddingTopLeft: [56, 24], paddingBottomRight: [24, 24], maxZoom: MAP_FIT_MAX_ZOOM });
  }
}
