# FAULT-EPISODES item 2: range faults, 2026-10-21

Asked by the Modbus bridge (partial failure: SunSpec answers, 15500–15509 does not) and franklinwh-modbus-async (16001 refused with exception 2). Emulator behaviour only: no hardware observation of these failure shapes.

**Tests:** `test_range_fault_hits_only_that_range`, `test_range_fault_silent_short_and_expiry`, `test_range_fault_over_control_api`.

```
range check disabled:  2 failed, 1 passed
short mode disabled:   1 failed, 2 passed
306 passed in 16.11s
```
