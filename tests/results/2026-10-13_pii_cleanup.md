# PII cleanup before making the repo public, 2026-10-13

The owner approved these on 2026-10-13. The scanner uses `~/.config/pii_terms`; terms are referred to by number only.

| Finding | Cause | Change |
|---|---|---|
| Terms #8 and #9 (location) in two `weather_data/open-meteo/*.json` files | Open-Meteo answers with its snapped grid cell (latitude/longitude fields), not the requested point (−33.87, 151.21); the raw response was cached and committed | `fetch_open_meteo_day` now stores the requested point and drops `elevation`; both cached files rewritten the same way; new test `test_open_meteo_cache_stores_requested_point`. The irradiance and temperature data are unchanged. The requested point does not match either term (checked) |
| Term #3 in `LICENSE` line 3 | Copyright line used a real name | Now `david2069` |
| The example postcode in 18 files (examples, scenarios, docs, a test) | A value copied from the owner's setup | `2000` (Sydney CBD) |
| aGate LAN IP in `docs/HANDOFF.md` | Copied from the owner's setup | `<agate-ip>` |
| Scanner ran only before push, and the hook wasn't enabled | — | New `scripts/hooks/pre-commit`; `core.hooksPath` set in this clone; policy and CLAUDE.md now require the commit-time hook, clean-at-write, and the fixed fake example values |

Result:
```
check_pii.py --scan: SUCCESS, no PII leaks (98 files)
pytest -q: 293 passed
```
History still holds the old values; the history rewrite is a separate step.
