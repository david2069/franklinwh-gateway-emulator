# aPower physical switch (#71), 2026-10-31

Base: main b720510 + branch feat/apower-switch.

## Full suite
```
503 passed in 30.89s
```

## Break check
set_apower without the rating update: test_switching_one_off_halves_the_power and test_all_off_no_battery_power_soc_kept failed.

## Running emulator (example fleet, ports 0)
- POST /gateways/site/apower {"n": 2, "on": false} -> apower_off [2], apower_units 2.
- {"n": 3, ...} -> 400 "apower: aPower 3: this site has aPower 1–2".
- Headless Chrome: site card tag "aPower 2 switched off"; form listed under Battery and modes.

## Scope (model, not measured)
Off: the unit adds no charge/discharge rating, apparent power or standby drain; the combined SoC and capacity are kept; Direct Connect device lists unchanged. The DC 1823 lead stays blocked on TEST-1823-APOWER-POWER (#71).
