# Changelog

## Unreleased

### Web UI: version, actions on the right, active gateway pinned (2026-10-30, #34)
- The header shows the emulator's version and, from a git checkout, the short commit
  (`GET /version`, new).
- On wide screens the actions sit in their own scrolling column on the right; a header switch
  puts them back below (remembered in the browser). Narrow screens stack.
- The actions column pins "Actions on [gateway]" at its top, with a picker to change the active
  gateway.
- `vpp.watts` says it is used when `pct_hw` is also sent.

### Web UI: aGate status, day or night, temperatures (2026-10-30, #32)
- The aGate box shows the mode, run status with battery kW (Charging, Discharging, Standby and
  the off-grid versions), SoC, inverter state, cabinet temperature, and "Modbus VPP" (amber)
  while an M704 dispatch is active.
- The diagram header has a sun or moon icon with the sun's elevation at the gateway's location,
  the sky in words ("broken cloud", not "broken"), irradiance, and ambient and cabinet
  temperatures (cabinet is the emulator's model, and says so).
- Remote (cloud) VPP mode and the network connection type join the "not modelled" list.
- Control state gains `run_status_name`, `inverter_state_name` and `sun`
  (`elevation_deg`, `up`; null without a location).

### Every on/off field says what it does (2026-10-30, #30)
- `offgrid.on`: true = island now; false = end the request and reconnect when the grid code
  allows. The other true/false fields (restore, enforced, flicker, circuit on, installed,
  modbus, csip, enable, server_up, clear) say what each value does too; the UI shows it.

### Mode numbers say which numbering they use (2026-10-30, #28)
- The `mode` action and `native_mode` use the Modbus 15507 numbering (1 Emergency Backup,
  2 Self-Consumption, 3 TOU). Direct Connect and the cloud number modes differently (1 TOU,
  2 Self-Consumption, 3 Emergency Backup). The schema now labels each value and states both;
  the UI shows "1 · Emergency Backup".
- Fixed stale text: `ctl` help said `mode <1-4>` and the fleet example listed `4 manual`;
  15507 = 4 was never read and isn't accepted. `ctl status` prints the mode's name.

### Web UI testing guide (2026-10-30)
- `docs/UI_TESTING.md`: start the UI on the example fleet, then check the cards, the energy
  diagram and the actions step by step, with what each should show; with a bridge; undoing.

### Web UI: actions panel (2026-10-30, #25)
- Every control action as a form on the active gateway, built from `GET /schema`: fields by
  type, required ones marked, units and docs shown, "fill from example". Only filled fields are
  sent; the emulator's answer stays in the form (no toasts).
- `schema.GROUPS`: each action's group, served by `GET /schema` (`groups`) and set on each
  action (`group`).

### Web UI: energy diagram (2026-10-30, #22)
- The selected gateway's live energy diagram, after the owner's design: grid, meter, aGate,
  main panel and home, PV per input (aPbox for remote inputs), battery (one combined unit),
  generator and smart circuits with their appliances. Animated lines follow the power's
  direction; still with reduced motion. Unmodelled elements are listed, not drawn.
- Control state gains `pv_inputs`, `apower_units` and `apower_rating` (per aPower, the
  emulator's constants).

### Web UI: gateway cards (2026-10-30, #22)
- `franklinwh-emulator ui FLEET.yaml`: validates the fleet, starts it, and serves a read-only
  page with live gateway cards and a detail panel for the selected (active) gateway, plus the
  control API, on one loopback port (default 8502). `Fleet.start(..., ui=True)` does the same
  in-process.
- Alpine.js 3.14.1 vendored (identical to upstream and to the Modbus bridge's copy); system
  fonts; no network.
- Control state gains `native_mode_name`; the control server can return pages and files.

### Check fleet and scenario files without running them (2026-10-30, #18)
- `validate.py`: `validate_fleet` and `validate_scenario` build everything a run would build
  except the servers, and return every problem as `{path, error}`: unknown keys, bad values,
  duplicate gateway ids, port clashes, unknown step actions and body keys, steps aimed at a
  missing gateway.
- `config_schema()` describes every gateway key (type and default from `GatewayConfig` and
  `SiteConfig`, plus a one-line meaning). `GET /schema` serves it as `gateway_config`.

### One schema for the control actions (2026-10-30, #18)
- `schema.py` describes every control action: fields, types, units, allowed values, signs,
  required fields and examples. `GET /schema` serves it.
- **Changed:** the control API (and scenario steps) now refuse unknown body keys and missing
  required ones with 400. They used to be ignored, so `{"hz": 51, "phse": "L2"}` changed every
  leg.
- The `control.py` docstring no longer lists the actions; it had drifted (`generator` and
  `dc-faults` missing, `mode` 4 listed but not accepted). README and SCENARIOS.md point to the
  schema.

### UI plan (2026-10-30)
- `docs/UI_PLAN.md`: a plan, not yet built, for a local page to configure fleets, run scenarios
  and steer and watch emulated gateways. YAML stays the file format and JSON the wire format.
- `docs/UI_PLAN.md`: the owner's answers (any user; the bridge's look with a live energy
  diagram, location and weather; first release here with no JavaScript build), the diagram's
  settings against what the emulator models, and what it doesn't model yet.

### Profiles checked against firmware_quirks.json (2026-10-30)
- `tests/test_quirks_drift.py`: an `agate-*` profile may assert only what franklinwh-modbus
  firmware_quirks.json (pinned v0.11.0) records as documented or measured for its firmware.
  `QUIRK_BASIS` maps each firmware-specific setting to its behaviours; `QUIRK_ASSUMED` lists the
  V10 sign assumptions, and the test fails once the file measures them.
- V12 candidate: WSetPct limit raw +100 and charge refusal (negative WSet / WSetPct, exception 3)
  are now on by default (`m704_wsetpct_accepted_range`, `m704_wset_negative_refused`, measured).
  New profile field `refuse_charge`.
- V12 candidate: 15016 = 1 while islanded is now opt-in (`quirks: {islanded_15016: 1}`); the file
  grades it reported (`ext_15016_status`).
- The Emergency Backup 16001 = 100 citation is franklinwh-modbus-async #16 G2, not
  franklinwh-modbus #63.

### WSet sign; franklinwh-modbus 0.11.0 (2026-10-29)
- M704 `WSet` (WSetMod = 1) is now positive = discharge in every profile, as measured on
  V12R02B60D00 (franklinwh-modbus firmware_quirks `m704_wset_sign`, #104) and as SunSpec defines.
  It was positive = charge, with no recorded basis. Not measured on V10R01B04D00.
- Test dependency pinned to franklinwh-modbus v0.11.0, which sends watts through `WSet` with
  that sign and ships firmware_quirks.json.

### `span_unlock` removed (2026-10-29, #10)
- There is no SPAN unlock: writes to 15507–15509 are always acknowledged and not applied, as
  franklinwh-modbus's firmware_quirks.json records (`ext_15507_15509_writes`). Removed from
  `GatewayConfig`, `AGateDevice`, `serve --span-unlock`, the control-API state, the example
  fleet and the docs. A config that still passes it fails at load. Set mode and reserves from
  the test side (`Site.set_native_mode`, `set_reserves`, site config, control API).

- History squashed for publication (2026-10-13). Earlier commit hashes cited in issues and results refer to the maintainer's private archive.

### WSetPct limit opt-in (2026-10-21)
- The v12-candidate profile no longer refuses `|WSetPct raw| > 100`: the V12 limit is not
  concluded. Opt in with `quirks={"wsetpct_max_abs_raw": 100}`.

### Write rejection (2026-10-21)
- `write-reject {model, point, abs_gt|gt|lt, code}` (control API, `ctl`, `AGateDevice.reject_writes`).
- Profile `agate-v12r02b60d00-candidate` refuses `|704.WSetPct raw| > 100` with exception 3
  (owner's test 2026-10-21, discharge side; charge side assumed symmetric, unverified).

### V12 reserves (2026-10-21)
- Profile `agate-v12r02b60d00-candidate`: 15509 is the TOU reserve, and 16001 the active mode's
  reserve (100 in Emergency Backup). Read with the app in each mode, one site (franklinwh-modbus #63).

### V12 addressing (2026-10-21)
- Profile `agate-v12r02b60d00-candidate` serves the SunSpec map at 0 and 40000 (measured
  2026-10-20, one site). New `write_bases` quirk; `addressing` control action and `ctl` verb
  switch the mirror and the write bases at run time.

### Slow replies (2026-10-21)
- Faults `slow_p` / `slow_ms`: a seeded fraction of Modbus replies is delayed, so a reply can land
  after the client's timeout on the same socket.

### Range faults (2026-10-21)
- `range-fault {start, end, mode, code, for_s}` (control API, `ctl`, `Faults.fail_range`): an
  exception, no reply, or a short reply only for requests touching that range, optionally timed.

### 15507: modes 1–3 only (2026-10-21)
- Removed mode 4 (Manual), following franklinwh-modbus #88 (#75): 15507 has only read 1, 2 and 3.
  Mode 4 is refused by the control API, site config and Direct Connect; an extension write of 0
  or 4 is acknowledged and not applied, even with `span_unlock`.

### Drop-and-return-islanded episode (2026-10-21)
- `episode {down_s, offgrid_s}` (control API, `ctl`, `Gateway.episode`): listeners down with RST,
  back islanded, grid restored. Candidate profile `agate-v12r02b60d00-candidate` (15016 = 1
  while islanded, observed twice on one site).

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
