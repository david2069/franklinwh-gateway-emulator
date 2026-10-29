# Profiles checked against firmware_quirks.json, 2026-10-30

**Against:** franklinwh-modbus v0.11.0 (38a80f8), `firmware_quirks.json` read with
`load_firmware_quirks()` / `quirks_for()` (list form).

**Change:**
- `QUIRK_BASIS` (profiles.py) maps 8 firmware-specific profile fields to file behaviours.
- `tests/test_quirks_drift.py`:
  - every behaviour id exists;
  - each `agate-*` profile asserts only documented or measured behaviour for its firmware;
  - `QUIRK_ASSUMED` entries (the two V10 sign assumptions) fail once the file measures them.
- V12 candidate:
  - `wsetpct_max_abs_raw=100` and the new `refuse_charge=True` are on (measured);
  - `islanded_15016=1` is removed and now opt-in (reported);
  - the EB 16001 citation is now franklinwh-modbus-async #16 G2.

**Full suite:** 320 passed.

**Break checks, one at a time, each restored after:**
- `islanded_15016=1` put back in the V12 profile → the drift test fails for the V12 profile (and the episode test's opt-out case).
- a V12 assumption for a measured behaviour (`tou_reserve_mirrors_self`) → `test_assumptions_are_still_unmeasured` fails.
- the `refuse_charge` check disabled in `_rejected` → `test_v12_wsetpct_range_and_charge_refusal` fails.
