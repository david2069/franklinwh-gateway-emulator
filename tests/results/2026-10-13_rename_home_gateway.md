# Rename the home gateway in tests and README, 2026-10-13

Owner request: rename the `home` gateway id in the tests and README, because the PII scanner reads `/gateways/home/*` as a home-directory path.

Changed: README.md, examples/fleet.yaml (the README's fleet examples load it), tests/test_grid.py, test_replay.py, test_scenario.py (inline fleet only), test_server_and_fleet.py. Unchanged: the built-in scenarios and the docstrings in src/ and docs/ still use `home`; tests that run built-in scenarios keep `summary["home"]`.

```
pytest -q: 291 passed in 15.10s
check_pii.py --scan: ❌ FAILED: Found 5 PII leaks. (was 13; all 8 home-path hits are gone. The remaining 5: personal terms in LICENSE (1) and in two weather_data files (2 each))
```
