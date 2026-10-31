# Scenarios: timed, repeatable, checked

A scenario is a fleet, a start time, timed steps and checks. It runs **offline** (a manual
clock with no sockets: deterministic, and a 24 h day takes well under a second) or **live**
(servers up on a scaled clock, so an external controller such as a bridge takes part while the
scenario plays).

```bash
franklinwh-emulator scenario list
franklinwh-emulator scenario show outage-during-vpp
franklinwh-emulator scenario run outage-during-vpp latent-export-breach --out results/
franklinwh-emulator scenario run my-test.yaml --live --time-scale 60 --control-port 8502
```

The exit code is 1 if any scenario fails, so it can be used directly in CI.

```python
from franklinwh_emulator.scenario import run_scenario
res = run_scenario("csip-zero-export")          # or a path, or a dict
assert res.passed, res.text()
res.rows          # one dict per gateway per sample
res.summary       # kWh by channel, export breach, relay events, SoC, islanded time
res.write("out/") # timeseries.csv, report.json, report.txt
```

## File format

```yaml
name: outage-during-vpp
description: Grid fails mid-dispatch; the battery carries the house, then reconnects.
start: "2026-01-15T17:30:00"        # ISO 8601; naive = the first gateway's wall clock
                                     # (its location.timezone, DST-aware)
duration: 50m                        # 90, 90s, 10m, 2h, 1d, 1h30m, 2m9s
record_every: 5s
fleet:                               # same schema as a fleet file (defaults + gateways)
  gateways:
    - {id: home, seed: 7, grid_code: ausgrid, initial_soc: 60,
       location: {latitude: -33.87, longitude: 151.21, timezone: Australia/Sydney}}
steps:
  - {at: 0,   do: pv,   with: {solar_w: 0}}
  - {at: 1m,  do: vpp,  with: {watts: 3000}}                  # + = charge
  - {at: 10m, do: grid, with: {available: false}, label: blackout}
  - {at: 10m, expect: {grid_connected: false, trip_reason: outage, battery_w: "~1500"}}
  - {at: 30m, do: grid, with: {restore: true}}
  - {at: 31m, expect: {grid_connected: true}}
checks: [energy_balance, soc_bounds, export_limit, reserve_floor, islanded_no_flow,
         reconnect_delay]                                     # default: all of them
```

- **`do`** is any control-API action, the same vocabulary as `ctl` and `POST /gateways/<id>/<action>`
  (every action and field: `schema.py`, or `GET /schema`). A step with an unknown key in `with`
  is recorded as an action error. The actions:
  - grid and phases: `grid` (`available`, `voltage`, `v_pu`, `hz`, `phase`, `restore`), `offgrid`;
  - inputs: `pv` (`solar_w`, `input`/`relay`), `load` (`w`), `ambient` (`c`), `weather`
    (`sky`, `flicker`);
  - alarms and faults: `alarms`, `faults`, `dc-faults`, `range-fault`, `write-reject`,
    `addressing`, `episode`;
  - generator: `generator`;
  - CSIP and comms: `csip`, `der-comms`, `export`;
  - battery and modes: `vpp` (`watts`, + = charge, or `pct_hw`), `release`, `mode`, `reserve`,
    and `soc` (setup);
  - loads: `circuit` (`n`, `on`), `appliance` (`name`, `on`: true/false/null).
  
  Unquoted `on:`/`off:` keys are fine; YAML 1.1 would read them as booleans, and the loader
  maps them back. The same holds for fleet files and saved sessions.
  
  `null` hands an override back to the built-in curve (`{solar_w: null}`).
- **`gateway`** targets one gateway; by default a step applies to every gateway in the fleet.
- **`expect`** compares fields of the gateway's state, the same JSON as `ctl --json status`.
  Nested fields are dotted, e.g. `csip.status` or `utility.hz`. Values can be literals, or:
  - `">0"`, `"<=10000"`, `"==V<"`, `"!=0"`;
  - `"~5000"`: within ±1 %, minimum ±1.
- **Steps at the same `at`** run in file order; expectations see the state after the actions
  before them.

## Checks (invariants, evaluated at every recorded sample)

| Check | Fails when |
|---|---|
| `energy_balance` | load ≠ solar + grid + battery by more than 1 W |
| `soc_bounds` | SoC outside 0–100 % |
| `export_limit` | export exceeds the **enforced** limit (CSIP `opModExpLimW`, or the static limit when enforced) by more than 50 W for longer than the grid code's response envelope (2 × the 15 s soft-limit settle + the dead time). With `response: null`, any breach counts. Declared-only limits are tracked as breaches instead. |
| `reserve_floor` | SoC keeps falling below the active reserve while discharging on-grid |
| `islanded_no_flow` | grid power is non-zero while islanded |
| `reconnect_delay` | the relay reclosed after less than the grid code's enter-service delay of healthy grid (checked from the relay event log) |

## Recording

`timeseries.csv` has one row per gateway per `record_every`:

- `t`, `ts`, `local_time`, `gateway`;
- power: solar, load, battery and grid W;
- state: SoC, mode, VPP, grid connected, trip reason/stage, V, Hz, var;
- curtailment: available, clipped and shed watts;
- inverter state, run status, alarms, throttle;
- enforced and declared export limits, export over the limit, DER connected, CSIP source;
- per leg: `L1_v` and `L1_grid_w`, etc.;
- per appliance: `appl_<name>` (served W), and per smart circuit: `circuitN_w`;
- with `replay`: `rec_battery_w`, `rec_grid_w` and `rec_soc` (the recorded values at that
  moment). The report adds `replay_compare` (MAE and bias for battery, grid and SoC, plus grid
  import/export kWh, emulated against recorded).

`--bridge-csv` (with `--out`) also writes `metrics_<gateway>.csv` in the Modbus bridge's export
format.

`report.json` / `report.txt` hold, per gateway:
- kWh by channel;
- export breach seconds and Wh;
- relay events (open/close with reason, stage, and healthy-grid time before reclose);
- SoC start and end, islanded time, and time per mode;
- every expectation, invariant violation and action error.

## Built-in library

| Scenario | What it pins down |
|---|---|
| `sydney-summer-day` | A 24 h baseline on the Ausgrid reference site. |
| `outage-during-vpp` | Islanding mid-dispatch, VPP ignored while islanded, the 60 s reconnect, VPP resuming. |
| `csip-zero-export` | A CSIP 0 W event curtailing VPP discharge and then PV; expiry. |
| `csip-comms-loss` | Events persist until the 300 s timeout, then the DefaultDERControl; recovery. |
| `latent-export-breach` | 5.7 kW PV plus 5 kW VPP = 10.7 kW against the unenforced 10 kW Ausgrid limit. |
| `brownout-ride-through` | V< 175 V: 8 s rides through, 12 s trips; reconnect at 60 s. |
| `over-frequency-event` | Australia A freq-watt: discharge stops at 50.75 Hz, storage charges by 51.5 Hz, F> trips at 52.5 Hz. |
| `phase-loss-3ph` | Losing L2 on a three-phase site; legs carry their own load; reconnect. |
| `us-over-frequency-ride-through` | IEEE 1547 Cat II OF1 61.5 Hz: rides through 300 s, trips, 300 s reconnect. |
| `sydney-dst-end` | DST ends on 5 Apr 2026: offset 11→10, a 25-hour day, daily reset at local midnight, and the TOU peak at the right wall-clock hours. It fails with a fixed offset. |
| `sydney-family-day` | The `sydney-family` preset: overnight EV session to 18 kWh, hot water on circuit 1 at noon, switching circuit 1 off removes 3.6 kW, EV at 7 kW from 22:00. |
| `offgrid-circuit-shedding` | An evening blackout with a low battery: circuits (EV, aircon) shed first, the essentials carried, and circuits restored with the grid. |
| `ev-solar-divert` | EV charging only from PV surplus under broken cloud, between 1.4 and 7 kW; no export while it charges. |
| `sydney-real-day` | 15 Jan 2026 Sydney from bundled Open-Meteo data on the 6.6 kWp reference array. |
| `cloud-edge-export-limit` | Broken cumulus swinging PV by kW within seconds under a CSIP 1.5 kW limit plus VPP. Transient breaches on cloud edges are allowed, but the limit must be regained within the 15 s soft-limit envelope; then a storm. |
| `offgrid-generator-ags` | A night black-out on a low battery with a 7 kW generator on AGS: at the 20 % start threshold it cranks at once, then carries the house and charges at 70 % of rated until 30 %, stops after its minimum run, restarts on low SoC, and hands back to the returning grid. |
| `au-response-timing` | AS/NZS 4777.2 timing end to end: the 1 s dead time and 10 s settle on an over-frequency excursion, the 0.1 Hz release hysteresis, then a black-out, the 60 s enter-service delay and the 360 s output ramp. |

Each is also a pytest case (`tests/test_scenario.py`), so the library doubles as a regression
suite for the emulator itself.

## Live mode with an external controller

```bash
franklinwh-emulator scenario run my.yaml --live --time-scale 30 --control-port 8502
# prints each gateway's Modbus (and Direct Connect) address; point the bridge at them
```

The site clock runs `time_scale` times faster than wall time, so a bridge polling every 5 s sees
150 s of site time per poll at ×30. Pick a scale the controller's loop can keep up with. Inputs
and steps are identical between runs; timing relative to the controller is not. For
deterministic checks of the emulator itself, use offline mode.
