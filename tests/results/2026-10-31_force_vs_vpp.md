# #91: FORCE vs VPP, 2026-10-31

Base: main ac6c235 + branch fix/force-vs-vpp.

## Source
fwh-direct-connect-bridge (a DC API client), main 76c07b6, docs/RUN_STATUS_AND_MODE.md §4: live reads on the reference aGate 2026-10-12 during a Modbus WSet force discharge (V12R02B30D06 per battery-dispatch.md:45-52): 1707 ibgRunStatus 85232 -> 9; 1301 run_status 2 unchanged; 1301/1707 name "Self-Consumption" -> "". Owner ruling (bridge #333): FORCE = an M704 setpoint; VPP = 1707 programme 9. Owner: the Cloud API also shows no programme name while VPP is active. Not modelled (no evidence): 1301 mode during a force, return timing, a FranklinWH-server VPP.

## Full suite
```
516 passed in 32.00s
```

## Break check
test_force_reports_programme_9_and_empty_name with the old dc_payloads.py: 4 failed.

## Running emulator (example fleet, ports 0) and headless Chrome
POST /gateways/site/vpp {"watts": -3000} -> force {direction: discharge, via: WSetPct, target_w: 3000}; card tags: grid connected, Self-Consumption, Discharging, "FORCE · discharge".
