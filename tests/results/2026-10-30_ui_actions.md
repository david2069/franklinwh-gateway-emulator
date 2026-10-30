# Web UI phase 3: actions panel (#25)

**Changed:** `schema.GROUPS` (+ `group` on each action, `groups` in `GET /schema`); new
`ui/static/actions.js` (pure form ↔ body conversion: `fieldKind`, `choiceOptions`,
`buildBody`, `formFromBody`); `app.js` loads the schema, keeps per-action form values and
per-gateway/action results, POSTs to `/gateways/<active>/<action>`; `index.html` renders the
grouped forms; styles. README, CHANGELOG.

**Tests:** `tests/test_ui.py` now 15 cases (+3): every action in exactly one group; every field
type the schema uses is handled by the page; under JavaScriptCore, every schema example (49)
entered as form values rebuilds the identical body, bad number / non-object / bad JSON are
refused with their messages, a blank form builds `{}` (skipped where `jsc` is absent, e.g. CI;
ran locally). Server side of every example: `tests/test_schema.py`.

Full suite: **420 passed** (Python 3.12, local).

**Browser run (local, not in CI):** headless Chrome driven over the DevTools protocol (scratch
script, not committed) against a 2-gateway fleet: 24 forms, 83 fields rendered;
- `load w=4321` → "Applied to family …: {"w":4321}", card Home 4.32 kW;
- `load w=abc` → "w: not a number" (page-side), not sent;
- `circuit n=2` without `on` → "circuit: missing ['on']" (server 400 shown as is);
- `circuit n=2 on=false` → applied, SW2 off in the diagram;
- `alarms set=OVER_TEMP latched=true` → applied, OVER_TEMP tag on the card;
- results stay in their forms after other forms are used;
- csip "fill from example" #2 → control `{"opModExpLimW":1500}`, duration_s `1800`.

**Break checks** (each temporary, then restored):
1. List fields never send the string form (`clear: "all"`) → JavaScriptCore round trip failed.
2. `null` not converted → JavaScriptCore round trip failed.
3. `appliance` left out of `GROUPS` → group test failed.
