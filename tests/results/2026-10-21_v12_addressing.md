# FAULT-EPISODES item 4: V12 addressing, 2026-10-21

**Evidence:** franklinwh-modbus docs/SUNSPEC_SNAPSHOT_2026-10-20_V12R02B60D00.md (read-only re-scan 2026-10-20, one site): SunSpec map also at PDU 40000; 50000 → exception 2. Whether writes at 40000 are accepted is unverified, so `write_bases` is a test switch, default both.

**Tests:** `test_v12_candidate_mirrors_at_40000`, `test_addressing_switch_and_write_bases`.

```
write_bases check off:  1 failed, 15 passed
v12 alias removed:      1 failed, 15 passed
310 passed in 17.59s
```
