# Web UI phase 2, PR B: energy diagram (#22)

**Changed:** new `ui/static/diagram.js` (SVG built from the control state; text escaped; dash
animation phase kept across the 1 s redraws), styles (reduced motion stops the animation;
narrow screens scroll the diagram sideways at a 720 px minimum), `index.html` loads it in the
detail panel. Control state: `pv_inputs`, `apower_units`, `apower_rating`. App: no "-0.00 kW".

**Tests:** `tests/test_ui.py` now 12 cases: page files include `diagram.js`; the field contract
covers `diagram.js`; new `test_state_carries_what_the_diagram_draws`.

Full suite: **417 passed** (Python 3.12, local).

**Break checks** (each temporary, then restored):
1. `pv_inputs` dropped from the state → field contract and state tests failed.
2. `diagram.js` not loaded by the page → page-files test failed.

**Rendered (local only, not in CI):** headless Chrome, a two-gateway fleet on a fixed clock
(2026-01-15 14:00 Sydney): one with aGate PV 0.6 + remote1 0.25 + remote2 0.15 (aPbox drawn),
2 aPowers, sydney-family loads, a fitted generator; one plain. Flows: PV 3.34 + 1.39 + 0.83 kW
into the aGate, battery charging 5.14 kW, home 0.43 kW (base 0.29 + fridge 0.14), grid idle.
No console errors at 1280 px or 500 px.
