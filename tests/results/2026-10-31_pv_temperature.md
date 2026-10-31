# #109: PV temperature loss, 2026-10-31

Base: main 8f432fa + feat/pv-temperature.

## Full suite
```
521 passed in 31.47s
```

## Break check
tests/test_pv_temperature.py with the factor not applied: 2 failed.

## Running emulator (example fleet)
site, 14:36 AEDT, broken sky, ambient 28.7 C: cell 35.4 C, factor 1.039 (relative to the 45 C reference), solar 1.23 kW.
