# FAULT-EPISODES item 3: write rejection, 2026-10-21

**Evidence (Modbus bridge, owner's site, V12R02B60D00, 2026-10-21, WSetPct_SF -1, discharge side):** raw +10 accepted; raw +100 accepted and delivered +500 W DCW in ~8 s; raw +110 and +200 never latched (WSetEna stayed 0; the exception code not confirmed); raw +500 refused with exception 3 (twice). V10R01B04D00 accepted raw 1000 (2026-10-20). Modelled: |raw| > 100 → exception 3 on the candidate profile only. ASSUMED: charge side (negative raw) and raw 101 behave the same; settled by a charge-side test.

**Tests:** `test_v12_candidate_refuses_wsetpct_beyond_100_raw`, `test_write_reject_rules`, `test_write_reject_over_control_api`.

```
rule check disabled:  2 failed (the API test only checks storage)
314 passed in 17.36s
```
