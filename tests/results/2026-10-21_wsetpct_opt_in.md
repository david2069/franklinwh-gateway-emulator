# WSetPct limit made opt-in, 2026-10-21

franklinwh-modbus (owner of the Modbus semantics) reports the V12R02B60D00 limit is not concluded: one test per value, discharge side only; charge side, raw 101, repeatability and after-reboot untested; a test plan is drafted. So no profile applies the limit; `wsetpct_max_abs_raw` stays as an opt-in quirk and `write-reject` rules are unchanged.

**Test:** `test_wsetpct_limit_is_opt_in`: v12-candidate accepts raw 500; with the quirk, 101/110/500/-101/-500 get exception 3.

```
314 passed in 17.33s
```
