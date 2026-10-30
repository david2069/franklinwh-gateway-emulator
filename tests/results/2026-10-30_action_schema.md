# Control-action schema (#18, PR A)

**Changed:** new `src/franklinwh_emulator/schema.py` (every control action: fields, types, units,
allowed values, required, examples); `control.apply` dispatches only schema actions (404) and
refuses unknown or missing required body keys (400); `GET /schema`. Docstring action list
removed (it had drifted). README, SCENARIOS.md, CHANGELOG.

**Tests:** new `tests/test_schema.py` (59 cases): every example of every action through `apply()`
on a started gateway (location, synthetic weather, sydney-family loads, generator, Direct
Connect); handlers in `apply()` == schema actions (source scan); required and example keys are
declared fields; unknown action 404; bad bodies 400 and leave state unchanged; `GET /schema` and
a 400 over HTTP; fault fields follow the `Faults` dataclass; mode values follow `MODE_NAMES`.

Full suite: **379 passed** (Python 3.12, local).

**Break checks** (each temporary, then restored):
1. Body check removed from `apply()` → 5 failed (4 bad-body cases, HTTP 400 case).
2. Schema action `export` renamed `export-limit` → 2 failed (example 404, handler scan).
3. `grid.phase` removed from the schema → 2 failed (example refused, example keys check).

**Found, not fixed here:** stopping a gateway during an `episode` leaves its task pending
("Task was destroyed but it is pending"). The test runs the episode example with 0.05 s
durations so it finishes first. Logged on #18.
