/* TOU schedule: the 24 h timeline and the editor's draft (#58).
 *
 * Pure functions (no DOM), so they can be tested without a browser. A draft holds strings as
 * typed; touBody turns it into the ``tou`` action's body. The server is the only validator:
 * these only convert, and report what can't be converted (a bad price).
 */
const DAY_MIN = 1440;
const DERIVED = ['dispatch', 'wave', 'filled'];   // keys the state adds to a block

function hhmmToMin(s) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(s || '');
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
}

// Timeline segments, as percentages of the day.
function touSegments(schedule) {
  return (schedule || []).map(b => {
    const start = hhmmToMin(b.startHourTime), end = hhmmToMin(b.endHourTime);
    return { name: b.name, from: b.startHourTime, to: b.endHourTime, dispatchId: b.dispatchId,
             dispatch: b.dispatch, waveType: b.waveType, wave: b.wave,
             left: start / DAY_MIN * 100, width: (end - start) / DAY_MIN * 100 };
  });
}

// Where "now" is on the timeline (percent), from the active block's end and time left.
function touNowPct(active) {
  if (!active) return null;
  const now = hhmmToMin(active.endHourTime) - active.remaining_min;
  return Number.isFinite(now) ? Math.min(100, Math.max(0, now / DAY_MIN * 100)) : null;
}

// An editable copy of the state's schedule and rates. Gaps the server filled are left out (it
// fills them again), so sending a draft unchanged gives the same schedule. Limit fields ride
// along untouched.
function touDraft(tou) {
  const rows = ((tou && tou.schedule) || []).filter(b => !b.filled).map(b => {
    const extra = {};
    for (const [k, v] of Object.entries(b)) {
      if (!['name', 'startHourTime', 'endHourTime', 'dispatchId', 'waveType'].concat(DERIVED).includes(k)) extra[k] = v;
    }
    return { name: b.name, start: b.startHourTime, end: b.endHourTime,
             dispatchId: String(b.dispatchId), waveType: String(b.waveType), extra };
  });
  const rates = {};
  for (const [w, r] of Object.entries((tou && tou.rates) || {})) {
    rates[w] = { buy: r.buy == null ? '' : String(r.buy), sell: r.sell == null ? '' : String(r.sell) };
  }
  return { rows, rates };
}

function minToHhmm(m) { return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); }

// A new row after the last one: from its end to 24:00. If the last row already runs to 24:00,
// it gives up its second half (rounded to 15 minutes) to the new row. No rows: the whole day.
function touAddRow(draft) {
  const last = draft.rows[draft.rows.length - 1];
  let start = last ? last.end : '00:00';
  if (last && hhmmToMin(last.end) >= DAY_MIN) {
    const a = hhmmToMin(last.start);
    const mid = Number.isFinite(a) ? Math.floor((a + DAY_MIN) / 2 / 15) * 15 : NaN;
    if (Number.isFinite(mid) && mid > a) { start = minToHhmm(mid); last.end = start; }
  }
  draft.rows.push({ name: 'New', start, end: '24:00', dispatchId: '6', waveType: '0', extra: {} });
  return draft;
}

// The ``tou`` action's body from a draft: {schedule, rates}.
function touBody(draft) {
  const schedule = draft.rows.map(r => ({
    ...r.extra, name: r.name.trim(), startHourTime: r.start.trim(), endHourTime: r.end.trim(),
    dispatchId: Number(r.dispatchId), waveType: Number(r.waveType),
  }));
  const rates = {};
  for (const [w, r] of Object.entries(draft.rates)) {
    const out = {};
    for (const k of ['buy', 'sell']) {
      const raw = String(r[k] == null ? '' : r[k]).trim();
      if (raw === '') continue;
      const n = Number(raw);
      if (!Number.isFinite(n)) throw new Error('rates ' + w + ' ' + k + ': not a number');
      out[k] = n;
    }
    if (Object.keys(out).length) rates[w] = out;
  }
  return { schedule, rates };
}

// Limit fields of a row, for display ("maxChargeSoc 90 · gridFeedMax 100").
function touLimits(extra) {
  return Object.entries(extra || {}).filter(([k]) => !['id', 'strategyId'].includes(k))
    .map(([k, v]) => k + ' ' + v).join(' · ');
}
