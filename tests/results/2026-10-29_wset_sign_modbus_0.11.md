# WSet sign and franklinwh-modbus v0.11.0, 2026-10-29

**Change:** test dependency pinned to franklinwh-modbus v0.11.0 (38a80f8). M704 `WSet` is
positive = discharge in every profile (`wset_positive_charge=False`), per firmware_quirks
`m704_wset_sign` (V12R02B60D00, measured, site_count 1) and SunSpec. V10R01B04D00 has no
measurement in the file; the same sign is assumed there.

**Why now:** v0.11.0 sends watts through `WSet` with the hardware sign (BatteryCommand +3000 W
charge → WSet −3000). With the old sign, `test_send_command_and_reset` read +3000 (discharge).

**Full suite:** 314 passed.

**Break check:** with the old sign restored, 2 fail (`test_send_command_and_reset`,
`test_wset_watts_mode`); with the fix, all pass.
