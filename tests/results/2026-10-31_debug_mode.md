# Debug mode injection, DC 1301 run_status 8 (#70), 2026-10-31

Base: main 7bb0bd2 + branch feat/debug-mode.

## Full suite
```
497 passed in 30.39s
```

## Break check
test_debug_mode_injection_changes_only_1301_run_status with the old dc_payloads.py (1301 always the model's run_status): failed.

## Running emulator (example fleet, ports 0) and headless Chrome
- POST /gateways/site/debug-mode {"on": true} -> state debug_mode true.
- Card site tags: grid connected, Self-Consumption, Charging, "debug mode (injected, DC 1301)".
- The debug-mode form is listed under Alarms and faults.

## Evidence note
run_status 8 is from the cloud enum (franklinwh-direct-connect-api catalog.RUN_STATUS, docs/PROTOCOL.md); no capture shows the local channel sending it. This is a test switch, not a model.
