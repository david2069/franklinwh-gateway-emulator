/* Start-up screen (#329, the owner's design board F): a welcome, the active gateway's last 7
 * days (solar and home use, kWh a day, from GET /gateways/<id>/days) and our own mark. Shown on
 * the first load of a browser session only; it fades once the page has loaded, or on a click or
 * Esc.
 */
const SPLASH_MIN_MS = 2500;   // on screen at least this long, so it can be read
const SPLASH_KEY = 'emulator.splashShown';
const CHART = { x0: 64, x1: 820, y0: 280, top: 20 };
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function splashWanted() {
  try { return !sessionStorage.getItem(SPLASH_KEY); } catch (e) { return true; }   // private mode
}

function splashSeen() {
  try { sessionStorage.setItem(SPLASH_KEY, '1'); } catch (e) { /* private mode */ }
}

// The scale: a round step (1, 5, 10, 20… kWh) and a top that is a whole number of steps.
function chartScale(days) {
  const peak = Math.max(0, ...days.flatMap(d => [d.solar_kwh, d.home_kwh]));
  const step = peak <= 5 ? 1 : peak <= 20 ? 5 : 10 * Math.ceil(peak / 40);
  return { step, max: Math.max(step, Math.ceil(peak / step) * step) };
}

// Smooth line through the points (Catmull-Rom as cubic Béziers, as on the board).
function smoothPath(p) {
  if (!p.length) return '';
  let d = 'M ' + p[0][0].toFixed(1) + ' ' + p[0][1].toFixed(1);
  for (let i = 0; i < p.length - 1; i++) {
    const a = p[i - 1] || p[i], b = p[i], c = p[i + 1], e = p[i + 2] || c;
    const c1 = [b[0] + (c[0] - a[0]) / 6, b[1] + (c[1] - a[1]) / 6];
    const c2 = [c[0] - (e[0] - b[0]) / 6, c[1] - (e[1] - b[1]) / 6];
    d += ' C ' + c1.map(n => n.toFixed(1)).join(' ') + ' ' + c2.map(n => n.toFixed(1)).join(' ') +
         ' ' + c[0].toFixed(1) + ' ' + c[1].toFixed(1);
  }
  return d;
}

// The chart's geometry from GET /gateways/<id>/days: ticks, day labels, both lines and points.
function splashChart(days) {
  const { x0, x1, y0, top } = CHART;
  const { step, max } = chartScale(days);
  const n = days.length;
  const px = i => n === 1 ? (x0 + x1) / 2 : x0 + (x1 - x0) * i / (n - 1);
  const py = v => y0 - (y0 - top) * v / max;
  const pts = key => days.map((d, i) => [px(i), py(d[key])]);
  const ticks = [];
  for (let v = 0; v <= max; v += step) ticks.push({ v, y: py(v) });
  return {
    ticks,
    labels: days.map((d, i) => ({ x: px(i), text: WEEKDAYS[new Date(d.date + 'T12:00:00').getDay()], date: d.date })),
    solar: { path: smoothPath(pts('solar_kwh')), points: pts('solar_kwh') },
    home: { path: smoothPath(pts('home_kwh')), points: pts('home_kwh') },
  };
}

// The chart's SVG children. Alpine's x-for doesn't run inside <svg>, so the page sets them with
// x-html; every value is a number or a weekday name from WEEKDAYS.
function splashSvg(chart) {
  const { x0, x1, y0, top } = CHART;
  const dots = pts => pts.map(p => `<circle r="4" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}"></circle>`).join('');
  return chart.ticks.map(t => `<line class="grid" x1="${x0}" x2="${x1}" y1="${t.y.toFixed(1)}" y2="${t.y.toFixed(1)}"></line>` +
                          `<text class="tick" x="${x0 - 12}" y="${(t.y + 5).toFixed(1)}" text-anchor="end">${t.v}</text>`).join('') +
         chart.labels.map(l => `<text class="tick" x="${l.x.toFixed(1)}" y="${y0 + 36}" text-anchor="middle">${l.text}</text>`).join('') +
         `<line class="axis" x1="${x0}" x2="${x0}" y1="${top}" y2="${y0}"></line>` +
         `<line class="axis" x1="${x0}" x2="${x1}" y1="${y0}" y2="${y0}"></line>` +
         `<path class="solar" d="${chart.solar.path}"></path><path class="home" d="${chart.home.path}"></path>` +
         `<g class="solar">${dots(chart.solar.points)}</g><g class="home">${dots(chart.home.points)}</g>`;
}

function splashSummary(days) {
  if (!days.length) return 'No history yet';
  const sum = key => days.reduce((s, d) => s + d[key], 0).toFixed(1);
  return days.length + (days.length === 1 ? ' day' : ' days') + ': solar ' + sum('solar_kwh') +
         ' kWh, home use ' + sum('home_kwh') + ' kWh';
}
