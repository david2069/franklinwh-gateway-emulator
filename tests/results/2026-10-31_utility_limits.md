# #123 (model part): grid operator rules and the main breaker, 2026-10-31

Base: main b4f6c43 + feat/utility-limits.

## Full suite
```
559 passed
```

## Break checks
- import cap disabled: test_import_limit_trims_grid_charging_then_discharges failed.
- breaker integration disabled: test_main_breaker_trips_on_overload_and_resets failed.

DC 1701 (serve and store, unenforced) follows separately, per fwh-local 2026-10-31.
