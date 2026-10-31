/* Display units (#119). Everything stays metric inside the emulator and on its protocols; this
 * only converts what the page shows. Pure functions, tested under JavaScriptCore.
 */
const TEMP_UNITS = { C: '°C', F: '°F' };
// US English (and the US territories) defaults to °F; everywhere else °C.
const FAHRENHEIT_REGIONS = ['US', 'PR', 'GU', 'VI', 'AS', 'MP', 'UM'];

function defaultTempUnit(locale) {
  const region = String(locale || '').split(/[-_]/)[1];
  return region && FAHRENHEIT_REGIONS.includes(region.toUpperCase()) ? 'F' : 'C';
}

function toTempUnit(c, unit) { return unit === 'F' ? c * 9 / 5 + 32 : c; }

// "25.0 °C" / "77.0 °F"; "—" when there's no value.
function fmtTemp(c, unit, digits = 1) {
  if (c == null || Number.isNaN(c)) return '—';
  return toTempUnit(Number(c), unit).toFixed(digits) + ' ' + TEMP_UNITS[unit === 'F' ? 'F' : 'C'];
}

// The page's current unit, set by the header toggle; temp() is what every view calls.
let currentTempUnit = 'C';
function setTempUnit(unit) { currentTempUnit = unit === 'F' ? 'F' : 'C'; }
function temp(c, digits = 1) { return fmtTemp(c, currentTempUnit, digits); }
