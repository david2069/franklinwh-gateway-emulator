# CI fix: pin franklinwh-modbus and test the opt-in writability probe, 2026-10-13

Problem: CI failed on every push from 9fc73ca (2026-10-09 06:49 UTC). It installs franklinwh-modbus unpinned from the default branch (develop). franklinwh-modbus PR #25 (cb1d27d, merged 2026-10-09 02:40 UTC) made `connect()` never write and moved the 15507–15509 writability test to an explicit `test_extension_writability()`. Two tests here assumed the old connect-time probe:
`test_extension_probe_sees_read_only` and `test_native_mode_with_span_unlock`. Locally they passed only because the .venv had v0.9.5, which still has the old behaviour.

Reproduced: a fresh venv with franklinwh-modbus@develop gave 2 failed, 3 passed in tests/test_franklinwh_modbus.py.

Change:
- pyproject.toml pins franklinwh-modbus to develop@8af8301 (after #23 and #25). The latest release, v0.9.5, still writes on connect.
- Both tests call `test_extension_writability()` explicitly.
- New `test_connect_does_not_write`: counts emulator device writes during `connect()`, expecting 0, and checks `tested` is False.

Result:
```
fresh venv (Python 3.12.13, franklinwh-modbus@8af8301): 292 passed
local .venv after `pip install -e .`:                    3 failed, 289 passed  (pip kept the old 0.9.5 build:
                                                          the pinned commit reports the same version string)
local .venv after --force-reinstall of the pinned commit: 292 passed
```

Note for other clones: `pip install -e .` does not replace an installed franklinwh-modbus 0.9.5 with the pinned commit, because both report version 0.9.5. Run
`pip install --force-reinstall --no-deps "franklinwh-modbus @ git+https://github.com/david2069/franklinwh-modbus.git@8af83011755a538fb5c0020817d19569c8e48278"`.
