# Replaying recorded data

Drive a site's solar, house load (and optionally ambient temperature) from real recorded data,
while the battery, grid and SoC stay simulated, so the controls under test still act. Add
`battery` to `use` to also follow the recorded battery power (dispatches included), so SoC
tests only the energy model:

```yaml
replay: {source: ~/metrics_30d.csv}            # Modbus bridge: GET /api/metrics/export?range=30d&format=csv
replay: {source: ~/bridge.db, gateway_id: default, use: [solar, load, ambient]}   # its SQLite store, read-only
```

- **Day alignment:** with `align: day` (the default), the recording's first whole local day plays
  on the site's first day; `loop` repeats it in whole days. `align: absolute` uses the recorded
  timestamps as they are.
- **Gaps:** gaps longer than `max_gap` (10 min), and times outside the recording, fall back to
  the configured weather and load models.
- **Comparison:** recordings carry `rec_battery_w`, `rec_grid_w` and `rec_soc`, and the scenario
  report scores emulated against recorded (MAE and bias). Replaying in the site's real mode
  therefore measures how faithful the model is. A control strategy can also be tested against
  real weather and load.
- **Battery losses:** `battery: agate-measured` (the default) was fitted on 30 days of the
  reference aGate. It discharges 12.45 kWh and charges 14.0 kWh per 100 % SoC, and adds about
  72 W of overhead while discharging. `legacy` (the previous 95 % charge, lossless discharge)
  and `ideal` remain. Replaying the real recorded dispatch, it tracks real SoC to 0.5–0.7 %
  MAE, against 3.0–5.4 % before; see [docs/CALIBRATION.md](CALIBRATION.md).
- **Scoring the native model:** `use: [solar, load, ambient, dispatch, mode]` infers the
  recording's dispatches (VPP, schedules, force charge), replays them as commands, steps the
  recorded mode and reserves, and scores native samples separately. On the reference site the
  native battery error is 53–63 W MAE.
- **Exporting:** `scenario run … --out DIR --bridge-csv` writes emulator runs in the bridge's
  format, for replay or for `POST /api/metrics/import` into the bridge.
