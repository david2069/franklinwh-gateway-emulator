# #4: 15003, 15012:15013, 15022 and 15035 served live, 2026-10-14

**Evidence:** `tests/results/2026-10-12_issue18_crosscheck.md` (paired capture of the reference aGate, 4,815 samples with raw registers): 15003 always a multiple of 100, slope 0.99 against DC `p_sun`; 15013 slope 1.000, r 0.998 against `p_fhp`; 15022 slope 0.9985, r 0.998 against `p_uti`; 15035 slope 0.9998, RMS 0.56 tenths. The franklinwh-modbus read-only run of 2026-10-14 (897 samples, charging) closes 15040 + 701.W + 15013 − 16000 at RMS 5 W.

**Change (`device.py` `refresh`):** 15003 = quantized solar (= 15502); 15012:15013 = signed 32-bit battery W, + discharge; 15022 = signed grid W, + import (clipped to int16); 15035 = round(SoC × 10). README updated.

**Test first:** `test_live_diagnostic_registers[charging|discharging]` checks each register against the site snapshot, the battery sign in both directions, and the balance 15040 + 701.W + 15013 − 16000 within 3 W.
```
before the fix: 2 failed, 12 deselected in 0.04s   (15013 read the static −4998)
after the fix:  296 passed in 14.06s
```
**Scope:** observed on one site. The balance was measured while charging only; the discharging case is the emulator's own model, not a hardware observation.
