# hAr1x/franklinwh-local-api against the emulator, 2026-10-13

**Client:** https://github.com/hAr1x/franklinwh-local-api at c4c59f4 (2026-09-06), GPL-3.0. It was run from a clone in an isolated venv (Python 3.12, pymodbus 3.16.1) and is not vendored here. Only its read methods were called: `async_get_device_info`, `async_get_status`, `async_get_battery_limits` and `async_get_battery_command_status`. A code read confirmed none of them writes.
**Server:** emulator default profile (`agate-v10r01b04d00`), unit 1.

## Run 1: unmodified client, logging harness (`run_fla.py`, `server_log_run1.jsonl`)
One connection, 7 fc3 requests at fixed addresses (no SunSpec scan): 4×66, 72×121 (M701), 1035×7 (M713), 1042×20 (M714), 15500×17, 227×44 (M702), 318×35 (M704). 0 writes.

| Method | Result |
|---|---|
| `async_get_device_info` | OK: FranklinWH Technologies Co., Ltd, aGate X, V10R01B04D00, emulator serial |
| `async_get_status` | **Fails**: 15500×17 → exception 2, because the emulator's map ends at 15513 (the client reads 15514–15516 for a "TOU dispatch state") |
| `async_get_battery_limits` | OK: 5000 / 5000 W |
| `async_get_battery_command_status` | OK: WSetEna off, pct 0 |

The client's fixed addresses (M701 at 72, M713 at 1035, M714 at 1042, M702 at 227, M704 at 318) all match the emulator's chain.

## Run 2: status decode vs emulator truth (`run_fla2.py`, `run2_result.json`)
In-process gateway, clock frozen at 2026-01-15 12:00 AEST, SoC 60, Self-Consumption, self reserve 20, TOU reserve 35. The patch is client-side only: 15500×17 becomes 15500×14 plus [0, 0, 0] padding, so `tou_dispatch_*` is padding, not data.

| Field | Emulator | Client | |
|---|---|---|---|
| SoC % | 60 | 60.0 | match |
| Battery W (+discharge) | −3478.6 | −3500 | match at 100 W resolution |
| Grid W (+import) | 0.0 | 0 | match |
| Solar W | 3922.9 | 3900 | match at 100 W resolution |
| Home load W | 444.3 | 444 | match (16000, 1 W) |
| Voltage L-N / frequency | 241.8 V / 49.99 Hz | 241.8 / 49.99 | match |
| Mode | 2 Self-Consumption | SELF_CONSUMPTION | match |
| Self reserve % | 20 | 20 | match |
| **TOU reserve %** | **35** | **20** | **differs**: the client reads 15509, which carries the active mode's reserve (15508 = 15509 = 20 here, as on the reference site; franklinwh-modbus-bridge#34) |
| Ambient / cabinet °C | 28.9 / 40.4 | 28.9 / 40.4 | match |

**Finding:** the client reports the active mode's reserve as the TOU reserve whenever TOU is not the active mode. On a single-phase site, `grid_voltage_ll_v` equals the L-N value (241.8).
