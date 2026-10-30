# Web UI: aGate status, day/night, temperatures (#32)

**Changed:** `site.py` `RUN_NAMES`, `INV_NAMES` (SunSpec 701.InvSt names); control state
`run_status_name`, `inverter_state_name`, `sun` (from `weather.sun_position`); `diagram.js`:
aGate box status lines, amber when Modbus VPP is active, sun/moon icon, sky labels, ambient and
cabinet (modelled) temperatures, two more "not modelled" items; `app.js` detail line; styles.

**Tests:** new `test_state_has_agate_status_sun_and_temperatures` (names, temperatures, no sun
without a location, sun up at 14:00 and down at 22:00 Sydney on 2026-01-15). Field contract
covers the new fields. Full suite: **423 passed** (Python 3.12, local).

**Break check:** `sun.up` forced true → the new test failed; restored.

**Rendered (local, headless Chrome, fixed clocks):** 14:00 with a 3 kW Modbus VPP discharge:
sun icon, "Day · sun 72.3° · clear sky · 992.1 W/m² · ambient 28.9 °C · cabinet 39.9 °C
(modelled)"; aGate amber "Self-Consumption / Discharging · 3.00 kW / SoC 38% · inverter running /
cabinet 39.9 °C / Modbus VPP"; grid exporting 8.14 kW (5.57 PV + 3.00 battery − 0.43 home).
22:00: moon icon, "Night · sun -19.7°". No console errors.
