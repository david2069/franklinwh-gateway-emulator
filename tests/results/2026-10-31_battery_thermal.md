# #111: battery temperature limits, 2026-10-31

Base: main 8ed41f5 + feat/battery-thermal-limits.

## Full suite
```
542 passed
```

## Break check
tests/test_battery_thermal.py without the factors applied in _limits: failed.

Plausible LFP-style limits, not measured; all overridable.
