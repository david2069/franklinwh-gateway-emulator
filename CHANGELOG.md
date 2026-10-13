# Changelog

## Unreleased

- History squashed for publication (2026-10-13). Earlier commit hashes cited in issues and results refer to the maintainer's private archive.

### Direct Connect 1207 and charge calibration (2026-10-08)
- The emulator stores Direct Connect 1207 writes with no effect. 222 tests.
- Morning force-charge staircase (1 / 2.5 / 5 kW): charging converter loss ≈ 70 W +
  11.6 W/kW² · P², pack resistance ≈ 7 mΩ, SoC scale = SoH-derated 13.03 kWh
  (docs/CALIBRATION.md).

### Modbus reference scan (2026-10-07)
- Full read-only scan of the real aGate over Tailscale, with the bridge's poller paused for 42 s
  (`tools/agate_scan.py`). The emulator's faithful profile now matches all 589 static points
  (`tests/data/reference_scan_static_2026-10-07.json`).
- Baseline: the real 705–712 curve groups and 704 PFWInj (Australia A codes; unimplemented for
  IEEE) and `1.Pad` 0x8000. Profile flags `m714_port_zero` (714.Prt[0] all zero) and
  `throtpct_unimplemented`.
- Response timing preset `agate-declared` (the 0–0.1 s declared in 705/706/711); named presets
  `as4777-envelope`/`ieee1547` via `grid_overrides: {response: <name>}`. 219 tests.

### Generator and AGS
- `generator:` per site, with the Direct Connect 1901 fields and the reference aGate's defaults
  (feature off, AGS 20/80 %, 1800 s start delay).
- SoC-based auto start while islanded, after the start delay and inside the operating windows;
  stop after the minimum run. Manual mode, the exercise run, crank/warm-up/cooldown, and
  loading at `genOptiPPoint`; the battery covers anything above rated. It is never paralleled
  with the grid and hands back when the grid returns.
- Faults: fail-to-start, trip, out of fuel.
- Direct Connect: 1902 readable and writable with a full block (`franklinwh_local.set_generator*`
  verifies); `p_gen`/`kwh_gen`/`genStat`/`main_sw[1]` in 1302; `p_gen` in 1304; `genRelayOpen`
  in 1710; `genpowerGen` in 1412.
- Control action and `ctl generator`; recordings add `generator_w`/`gen_state`, and the
  energy-balance check includes the generator.
- Scenario `offgrid-generator-ags`. 215 tests.

### Grid response timing
- Grid-support responses are no longer instant (`gridcodes.ResponseTiming`, per grid code):
  - a dead time (AU 1 s, IEEE 0.2 s), then a first-order lag *on the delivered output*;
  - freq-watt, volt-watt and volt-var settle to 95 % in 10 s (AU), or to 90 % in 5/10/5 s
    (IEEE open-loop response times);
  - soft export limits are met within 15 s;
  - over- and under-frequency responses release only after 0.1 Hz of hysteresis (AU);
  - after a reconnect, discharge and PV ramp over `es_ramp_s` (360 s AU, matching the scanned
    703 `ESRmpTms`; 300 s IEEE).
- Configure with `grid_overrides: {response: {...}}`; `response: null` restores instant
  behaviour. The `export_limit` check allows the response envelope.
- New scenario `au-response-timing`; built-in scenarios and tests updated to let responses
  settle. 202 tests.

### Calibrated against the reference aGate (2026-10-07)
- **Battery loss model** (`battery:` per site; presets `agate-measured` (default), `legacy`,
  `ideal`). It was fitted on 30 days of real 10 s data: discharge 0.915 and charge 0.971
  against 13.6 kWh, and 72 W of overhead while discharging. Replaying real dispatch, SoC MAE
  is 0.5–0.7 %, against 3.0–5.4 % with the old model. See `docs/CALIBRATION.md`.
- **Replay `use: battery`:** follow the recorded battery power, so the recording's dispatches
  happen. SoC re-anchors to 100 % when the recording reports full (BMS top-of-charge).
- **Faithful profile:** `713.SoC` in whole percent (`soc_whole_percent`) and `714.DCW` in 100 W
  steps (`quantize_w`), as in the real exports.
- **Direct Connect payloads** match a read-only capture of 20 commands on the real device: all
  312 top-level keys. Changes:
  - `reason: 0` on normal replies (none on 1726);
  - 1302 `electricity_type`/`ent`/`pro_load` and an `infi_status` list, without `devNum`;
  - 1304 `date`, `sectionEngy` and `sno` counted from commissioning;
  - 1708 without `energyMode`, with `dspRunstatus`, leg currents and `gridVol2`;
  - single phase reported as two half-voltage legs in 1704/1708;
  - 1706 integer-mV cells on an LFP curve;
  - 1202 standard `timezone` with a separate `DST` flag, an empty `timezoneStr` (the IANA name
    on `spec`) and string coordinates;
  - 1410/1412 extra fields, with `freq` as Hz×10;
  - 1832 without `isOver`;
  - 1904 `opt/result/reason`.
- Tests pin the captured key sets and the new battery model (188 tests).
- **Replay `use: dispatch` and `use: mode`:** infer the recording's dispatches (more than 500 W
  from native for at least 2 min, median setpoint) and replay them as commands, while the
  native mode runs between them; step the recorded mode and reserves. `replay_compare` adds
  `native`/`dispatched` scores and the report lists `replay_dispatches`. Native battery MAE on
  the reference site is 53–63 W, down from about 550 W.
- **Documented the aPower's idle standing draw** (about −27 W from AC while idle; cells at 0 A;
  converter online), with data provenance and firmware for every dataset
  (`docs/CALIBRATION.md`). Added `tools/bms_collect.py`, the read-only BMS and power-flow
  collector used to gather it. Filed firmware tracking (modbus-bridge#10, local-bridge#8) and
  the Local Bridge export mixing in mocks (local-bridge#9).
- **`full_snap_soc` (98 in `agate-measured`):** the BMS declares full from about 98 %, as in
  all 30 real arrivals at 100 %. 194 tests.

### Added
- **Replay** (`replay.py`): recorded solar/load/ambient from the Modbus bridge's metrics CSV
  export or its SQLite store (read-only), day-aligned (DST-aware) or absolute, looping, gap
  fallback to the models, and scaling. Battery, grid and SoC stay simulated. Recordings add
  `rec_battery_w/rec_grid_w/rec_soc`, and reports a `replay_compare` (MAE and bias). Helper
  `to_bridge_csv` and `scenario run --bridge-csv`. Round trip verified to MAE 0.9 W.
- **House load components** (`loads.py`): `base`, `daily`, `schedule`, `cycle`, `events` (seeded
  per day), `hvac` (temperature-driven) and `ev` (immediate/offpeak/solar divert,
  kWh-per-session, stateful). Presets `sydney-family` and `small-flat`, with add/remove/set.
  Smart circuits now live in the site: switching one removes its load (control `circuit`,
  `ctl circuit`, Direct Connect `1409` writes), `1410`/`1708` report state, and the new `1411`
  meter reports power and energy. Off-grid the aGate sheds smart circuits first; per-circuit
  `soc_low_cutoff`. `appliance` force on/off/auto. Recordings add `appl_<name>` and
  `circuitN_w`. Scenarios `sydney-family-day`, `offgrid-circuit-shedding` and
  `ev-solar-divert`.
- YAML loading maps unquoted `on:`/`off:` keys (parsed as booleans by YAML 1.1) back to strings.
- **Location per gateway**: `location: {latitude, longitude, timezone (IANA, DST-aware), postcode}`.
  The wall clock (TOU, load, daily totals, history, `1202`, naive scenario starts) follows real
  DST; weather inherits the coordinates; `1202` reports the gateway's own location
  (previously hardcoded to Sydney for 50 Hz codes) and its DST flag. CLI
  `--timezone/--lat/--lon/--postcode`. State and recordings carry `local_time`,
  `utc_offset_hours`, `dst` and `today_kwh`. Built-in scenarios use `Australia/Sydney` and
  `America/Phoenix`. New `sydney-dst-end` scenario (with a fixed-offset negative control in tests).
- **Weather** (`weather.py`): `synthetic` (sun position + clear-sky on the array plane + seeded
  cloud edges; skies clear/broken/overcast/storm) and `open-meteo` (real 15-minute GTI and
  temperature, the bridge's GTI→kW formula, mean-preserving cloud-edge flicker capped at
  clear sky, per-day disk cache, two Sydney days bundled). `weather` control action,
  `ctl sky`. Scenarios `sydney-real-day` and `cloud-edge-export-limit`.
- **Scenarios** (`scenario.py`, `franklinwh-emulator scenario list|show|run`): timed steps in the
  control-API vocabulary, `expect` checks with comparison operators, six invariants, CSV and
  JSON/text reports, and offline (deterministic) and live (scaled clock, servers up) modes.
  9 built-ins cover outage during VPP, CSIP zero export and comms loss, the latent export
  breach, brown-out ride-through, over-frequency, phase loss and US ride-through.
  `docs/SCENARIOS.md`.
- Control actions `vpp`, `release`, `mode`, `reserve`, `soc` (also `ctl vpp|release|mode|soc`).
- Site relay event log (open/close, reason, stage, healthy-grid time before reclose).
- `Fleet(..., clock=)` for a shared clock.

### Fixed
- Integration now lands exactly on the requested time; float accumulation over 0.1 s sub-steps
  could miss an exact 60 s reconnect boundary.
- Open-Meteo flicker could amplify clear moments in heavily clouded windows (11 kW from 6.6 kWp).
  It is now bounded by clear sky, with the shaded fraction set per window to keep its mean.
- Direct Connect `1202` reported Sydney's coordinates for every 50 Hz gateway; it now reports the
  gateway's own location.
- Recording columns for appliances are `appl_<name>`; `load_<name>` collided with `load_w` and
  `load_shed_w`.

### Added (earlier)
- **Direct Connect frontend** (TCP/9000, optional `[dc]` extra): 23 cmdTypes rendered from the
  same `Site` as Modbus. Login, power_flow with daily kWh, quarter-hour energy_history,
  per-aPower reads, modes (list/set), off-grid (read/write), relays, solar_pv
  (`grid_feed_max`), `der_comms` (sunsMdEn + CSIP-AUS client), time, network switches, and
  reboot (both ports drop and return). Real-device error semantics. `--dc-port`,
  `directconnect:` in fleet YAML, `ctl dc-fault`. Docs in `docs/DIRECT_CONNECT.md`.
- **CSIP-AUS client model** (`csip.py`): enable plus registration, LFDI/SFDI per the IEEE 2030.5
  algorithm, utility DERControl events with durations, and the DefaultDERControl when no event
  is active or after 300 s of lost comms. `opModExpLimW`, `opModImpLimW`, `opModGenLimW`,
  `opModLoadLimW`, `opModConnect` and `opModEnergize` act on real power flow. Control API
  `/csip` and `ctl csip …`.
- **Export compliance:** grid-code export limits are declared and breaches tracked
  (`export_over_limit_w`, `export_breach_s` / `_wh`); enforcement is optional
  (`export_limit_enforced`, `ctl export-limit enforce`).
- **`1205 sunsMdEn`:** `ctl modbus off|on` closes or reopens the Modbus listener after about 3 s.
- **`ausgrid` grid code:** Australia A plus a 10 kW single-phase export limit (5 kW per phase
  multi-phase, unconfirmed).
- **Australia A responses**, checked against the published distributor summary: storage
  frequency-watt with the f_transition (50.75 Hz) and f_stop-ch (49 Hz) breakpoints, charging
  volt-watt, and volt-var. `701.Var` / `VA` / `PF` are now live.
- **Grid codes** `as4777-au-a`, `ieee1547-cat2` and `ieee1547-cat3`: multi-stage trips,
  sustained over-voltage, enter-service, and `grid_overrides`. Model 703 follows the code.
- **Phases:** single (any leg), split, two-phase and three-phase layouts. Per-leg 701 points,
  phasor L-L voltages, unbalanced load/generation splits, per-leg metering, per-leg utility
  voltage and availability (phase loss trips), and an unused-leg register option.
- **Grid events:** black-out, brown-out and swell, frequency excursions, restore with
  enter-service delay, requested off-grid. Islanded operation: the battery forms the grid, sheds
  load, curtails PV and goes dark at the floor.
- **PV inputs** (proximal / remote1 / remote2) with relays, on 15503–15505.
- **Alarms:** forced `701.Alrm` bits (latched or persistent), tripping alarms fault the
  inverter, `715.AlarmReset` clears latched ones, and `714.PrtAlrms` raw bits.
- **HTTP control API** (stdlib, loopback) and the `ctl` subcommand.
- Docs: `docs/DESIGN.md`, `docs/FINDINGS.md`, `docs/BRIDGE_INTEGRATION.md`.

### Initial
- Modbus TCP emulator of the aGate:
  - the SunSpec chain (1, 701–715, 502) built from pysunspec2 definitions at the reference scan's
    lengths, extension registers 15000–15513, and the 16000 block;
  - the closed-loop site model;
  - `agate-v10r01b04d00` and `spec` quirk profiles;
  - a hand-rolled asyncio server with fault injection;
  - the `Gateway` / `Fleet` API and the `serve` / `fleet` CLI;
  - serials matching the Direct Connect `SyntheticSite`;
  - acceptance tests with the unmodified `FranklinWHController`.
