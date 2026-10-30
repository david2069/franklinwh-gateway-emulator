# #78: DC 1102 login, the full reply per aGate firmware, 2026-10-31

Base: main fe75081 + branch feat/dc-login-versions.

## Sources
- IBG V12R02B30D06_260304: reference capture 2026-10-07, docs/CALIBRATION.md firmware table (values only).
- IBG V12R02B60D00_260924: full 1102 payload in fwh-direct-connect-bridge's private firmware sweep (C_known_reads.json codes/1101/data, read ~2026-10-20T20:1xZ, reference aGate, one aPower), relayed by that session 2026-10-31; agrees with its B_firmware.json (2026-10-20T20:09:02Z).

## Full suite
```
509 passed in 30.15s
```

## Break check
tests/test_dc_login.py with the old dc_payloads.py (placeholder 1102): 3 failed (the fourth tests profiles.py).

## What the tests check (unmodified franklinwh_local.LocalClient)
- V12 candidate: key order identical to the recorded reply; every version value; list shapes; serial lengths 20/22/22; no SUNSPEC_FW; 1834 ibg_ver/ibg_iot/ibg_local/bms_ver from the same set.
- Default profile: the 2026-10-07 values; V12R02B60D00-only keys absent.
- 3 aPowers: every list has 3 entries (inferred, labelled).
- Unknown dc_firmware: refused.
