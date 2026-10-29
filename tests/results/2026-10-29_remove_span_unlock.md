# Remove `span_unlock` (#10), 2026-10-29

**Change:** the `span_unlock` option is removed from GatewayConfig, AGateDevice, `serve`, the control-API
state, examples/fleet.yaml and the docs. Writes to 15507–15509 are always acknowledged and not applied,
as franklinwh-modbus firmware_quirks.json records (`ext_15507_15509_writes`, franklinwh-modbus#118).

**Tests:** `test_extension_writes_never_applied`, `test_span_unlock_option_is_gone`,
`test_native_mode_writes_not_applied` (the unmodified FranklinWHController), plus the updated
15507-modes, fleet and addressing tests.

**Full suite:** 314 passed (`.venv/bin/python -m pytest -q`).

**Break check:** with `_write_ext` changed to apply 15507 writes, 4 tests fail
(`test_extension_writes_never_applied`, `test_15507_only_modes_read_on_hardware`,
`test_extension_probe_sees_read_only`, `test_native_mode_writes_not_applied`); restored, all pass.
