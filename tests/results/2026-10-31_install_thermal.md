# #110: installation placement and cabinet temperature, 2026-10-31

Base: main ee7ffa9 + feat/install-placement.

## Full suite
```
532 passed in 33.66s
```

## Break check
tests/test_install_thermal.py without the cabinet lag integration: 1 failed.

## Running emulator (example fleet)
- site (outdoor, afternoon sun, roof) 15:01 AEDT: ambient 28.6 C, surroundings 31.0 C, cabinet 36.1 C (charging).
- au-3phase (indoor, unheated): ambient 24.6 C, surroundings 28.6 C, cabinet 36.5 C (charging).
- us-split (no install): cabinet 28.8 C, the original estimate.
