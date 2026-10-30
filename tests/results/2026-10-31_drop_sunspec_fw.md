# #74: drop SUNSPEC_FW from DC 1102, 2026-10-31

Base: main fcd2b2a + fix/drop-sunspec-fw.

## Full suite
```
503 passed in 30.38s
```

## Break check
test_login_manifest with the old dc_payloads.py (SUNSPEC_FW sent): failed.

## Source check
No source: added 2026-10-07 (pre-public 6ff2130) without citation; not in docs/CALIBRATION.md capture fields; franklinwh-direct-connect-api main e8e5ee0 and franklinwh-modbus develop 4fa2b06 have no record (both sessions, 2026-10-31).
