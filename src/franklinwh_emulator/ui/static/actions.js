/* Action forms: turning a schema field and what the user typed into a request body, and back.
 *
 * Pure functions (no DOM), so they can be tested without a browser. Every form value is a
 * string; '' means "not set" and the key is left out. The server is the only validator: these
 * only convert, and report what can't be converted (a bad number, bad JSON).
 */
const FIELD_KINDS = ['boolean', 'number', 'integer', 'string', 'array', 'object'];

function typesOf(spec) { return [].concat(spec.type); }

// How a field is entered: 'choice' (a select), 'number', 'text', 'list' (comma separated) or 'json'.
function fieldKind(spec) {
  const t = typesOf(spec);
  if (t.includes('array')) return 'list';
  if (t.includes('object')) return 'json';
  if (t.includes('boolean') || spec.enum) return 'choice';
  if (t.includes('number') || t.includes('integer')) return 'number';
  return 'text';
}

// The options of a 'choice' field, as [value, label] pairs; value '' = not set.
function choiceOptions(spec) {
  const opts = [['', '— not set —']];
  if (typesOf(spec).includes('boolean')) opts.push(['true', 'true'], ['false', 'false']);
  const labels = spec.labels || {};
  for (const v of spec.enum || []) opts.push([String(v), labels[v] ? v + ' · ' + labels[v] : String(v)]);
  if (spec.nullable) opts.push(['null', 'null']);
  return opts;
}

// Strings an action's examples send for a field whose type also allows a list ("all").
function exampleStrings(action, field) {
  return (action.examples || []).map(e => e[field]).filter(v => typeof v === 'string');
}

function convert(action, field, raw) {
  const spec = action.fields[field];
  const t = typesOf(spec);
  if (raw === 'null' && spec.nullable) return null;
  switch (fieldKind(spec)) {
    case 'choice':
      if (raw === 'true') return true;
      if (raw === 'false') return false;
      if (raw === 'null') return null;
      return t.includes('integer') || t.includes('number') ? Number(raw) : raw;
    case 'number': {
      const n = Number(raw);
      if (!Number.isFinite(n)) throw new Error(field + ': not a number');
      return n;
    }
    case 'list': {
      if (t.includes('string') && !raw.includes(',') && exampleStrings(action, field).includes(raw)) return raw;
      return raw.split(',').map(s => s.trim()).filter(Boolean)
        .map(s => (/^-?\d+(\.\d+)?$/.test(s) ? Number(s) : s));
    }
    case 'json': {
      let v;
      try { v = JSON.parse(raw); } catch (e) { throw new Error(field + ': not valid JSON'); }
      if (v === null || typeof v !== 'object' || Array.isArray(v)) throw new Error(field + ': needs a JSON object');
      return v;
    }
    default:
      return raw;
  }
}

// The request body for an action from its form values (only the fields that are set).
function buildBody(action, raw) {
  const body = {};
  for (const field of Object.keys(action.fields)) {
    const v = raw[field];
    if (v === undefined || v === '') continue;
    body[field] = convert(action, field, String(v).trim());
  }
  return body;
}

// Form values that build ``body`` again (used for "fill from example").
function formFromBody(action, body) {
  const raw = {};
  for (const field of Object.keys(action.fields)) raw[field] = '';
  for (const [field, v] of Object.entries(body)) {
    if (v === null) raw[field] = 'null';
    else if (Array.isArray(v)) raw[field] = v.join(', ');
    else if (typeof v === 'object') raw[field] = JSON.stringify(v);
    else raw[field] = String(v);
  }
  return raw;
}
