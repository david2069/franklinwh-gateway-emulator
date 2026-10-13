# Backlog

Purpose: **synthetic testing of variable controls and flows**. That means making the emulator
produce the conditions control logic has to cope with (variability, events, failures) in a
form that is repeatable and assertable. Items are ranked by that yardstick.

Status: `todo` · `next` · `doing` · `done` · `parked`. Owner is this repo unless stated.

---

## P1: the core of synthetic testing

### SCENARIO-SCRIPTS: timed, repeatable scenarios · `done`
Shipped: `scenario.py`, `franklinwh-emulator scenario list|show|run`, offline (deterministic) and
live (scaled clock, servers up) modes, and 9 built-in scenarios. See
[docs/SCENARIOS.md](docs/SCENARIOS.md). Not yet: local-time `at:` values ("17:30"), and
multi-day canned scenarios such as a winter week.
**Why:** every event (outage, sag, CSIP control, alarm, cloud, load step) is currently driven
by hand with `ctl`. Control logic can only be regression-tested if the same scenario replays
identically.
**Scope:**
- A `scenario:` list in fleet YAML or a separate file, each step being `at` (s, or local time),
  an action (any control-API action), and args. Example: `at: 600, grid: {available: false}`
  then `at: 900, restore`.
- Runs on the site clock, so with `time_scale` a 24 h scenario plays out in minutes.
- `franklinwh-emulator run-scenario`, plus a pytest helper.
- A library of canned scenarios: Sydney summer day, winter evening peak, outage during a VPP
  dispatch, CSIP zero-export event, comms loss to the CSIP default, brown-out ride-through,
  phase loss on a 3-phase site, and the latent 10.7 kW export breach.

**Done when** a scenario run twice with the same seed produces identical time series.

### RECORD-AND-ASSERT: time-series capture and invariants · `done`
Shipped: `timeseries.csv`, `report.json`/`.txt`, six invariants, `expect` steps with comparison
operators, and per-gateway summaries (kWh, breach, relay events, islanded time, time per mode).
Not yet: Parquet output, and percentile/statistics assertions (e.g. "export > 9.5 kW for less
than 1 % of the day").
**Why:** a scenario is only useful if its outcome can be checked.
**Scope:**
- Record every site step (or every N s) to CSV/Parquet. Columns: power flows, SoC, mode, VPP
  state, CSIP source and limits, grid state, trips, alarms, per-leg values, and export breach.
- Built-in invariants that can be asserted: energy balance; SoC within bounds; export ≤ the
  enforced limit; discharge ≥ the reserve; no export while CSIP is at zero; reconnect only after
  the enter-service delay.
- A summary report per run: kWh by channel, breach seconds/Wh, trips, and the time spent in
  each mode.

### WEATHER-AND-SOLAR: realistic PV variability · `done`
Shipped (`weather.py`):
- `synthetic`: sun position, clear-sky irradiance on the array plane, and seeded skies with
  second-scale cloud edges.
- `open-meteo`: real 15-minute GTI and temperature, using the same conversion as the bridge's
  forecast. Flicker preserves each window's mean and never exceeds clear sky. Cached per day,
  with two Sydney days bundled.
- `ctl sky`, plus the `sydney-real-day` and `cloud-edge-export-limit` scenarios.

There was no richer mock in franklinwh-local-bridge; its contribution is the Open-Meteo forecast
integration, which this matches.

Not yet:
- multi-array sites (east/west split);
- PV clipping at the aGate's 6.6 kW AC input limit;
- Open-Meteo `cloud_cover_low/mid/high` to shape the edges;
- a "forecast vs actual" pairing mode, where the bridge reads the forecast API while the
  emulator plays the historical day.

### LOAD-PROFILES: appliance-level house load · `done`
Shipped (`loads.py`):
- components `base`, `daily`, `schedule`, `cycle`, `events`, `hvac` and `ev`
  (immediate/offpeak/solar);
- presets, smart circuits owned by the site (1409/1410/1411/1708 and `ctl circuit`), off-grid
  circuit shedding and per-circuit SoC cutoffs, leg placement, `ctl appliance`;
- three scenarios.

Not yet:
- appliance power factor (var) and inrush;
- EV "after battery" divert priority (today the EV takes surplus ahead of the battery);
- smart-circuit schedules (the real aGate discards local writes to them anyway);
- V2L.

Original intent:
**Why:** the load is two gaussians plus jitter. Controls need steps and spikes.
**Scope:**
- Composable loads: base, kettle/oven steps, a heat-pump or AC cycle, a pool pump schedule,
  and an EV charger (7 kW, 32 A, scheduled or opportunistic).
- **Smart-circuit coupling:** switching a circuit (1409) actually removes or adds its load. The
  toggle round-trips today but has no physical effect.
- Seeded randomness and per-leg assignment for 3-phase sites.

### REPLAY-REAL-DATA: drive the site from recorded samples · `done`
Shipped (`replay.py`):
- reads the bridge CSV export or its SQLite store (read-only, archive merged, `gateway_id`
  filter) or any CSV with a mapping;
- replays solar, load and ambient with day alignment, loop and `absolute` modes, gap fallback
  and scaling;
- recordings carry `rec_*` columns and a `replay_compare` score; `--bridge-csv` export;
- round trip verified (MAE 0.9 W).

Waiting on: a real export from the reference site to calibrate the model (see
WEATHER_AND_LOADS.md).

Original intent:
**Why:** the bridge store holds about 30 days of 30-second real samples (84,923 per
franklinwh-direct-connect-api BACKLOG), which is the most realistic input there is.
**Scope:**
- Import solar and load series from a bridge export (CSV/SQLite) as site inputs. Battery, grid
  and SoC stay simulated, so the controls under test still act.
- Time-scaled playback; loops or stops at the end.

### GRID-RESPONSE-TIMING: ramps instead of instant responses · `done`
**Done (2026-10-07):** `gridcodes.ResponseTiming` per grid code:
- a dead time, then a first-order lag on the output for freq-watt, volt-watt and volt-var;
- a 15 s soft export limit;
- frequency release hysteresis;
- the 703 `ESRmpTms` ramp after reconnects;
- the `export_limit` check allows the envelope;
- the `au-response-timing` scenario.

**Open:**
- Verify the AU and IEEE timing values against the standards' text and, if possible, the
  aGate's own curve `RspTms` (not in the scan).
- Ramp rate limits on ordinary setpoint changes (VPP `WSet`): `WRmp` is unimplemented on the
  device.

**Why:** AS/NZS 4777.2 responses start within 1 s, complete within 10 s and use 0.1 Hz
hysteresis; IEEE 1547 has open-loop response times. Instant responses hide oscillation and
overshoot in the control loops above.
**Scope:** first-order or ramped response per function (freq-watt, volt-watt, volt-var, export
limit), hysteresis on release, and ramp-up after enter-service (703 `ESRmpTms`, 360 s on the
reference).

### REPLAY-DISPATCH-AS-COMMANDS: score the native model around real dispatches · `done`
**Done (2026-10-07):** `use: dispatch` (inferred, replayed as setpoints) and `use: mode`;
`replay_compare.native` / `.dispatched`. Native battery MAE is 53–63 W on the reference site
(docs/CALIBRATION.md). **Open:** TOU's local behaviour is still modelled as Self-Consumption
in off-peak (undecoded), and dispatch inference is a heuristic, not the bridge's command log.

**Why:** following the recorded battery power (`use: battery`) validates the energy model
(SoC MAE 0.5–0.7 %). It bypasses the native mode, though. Native replays of the reference site
are dominated by the bridge's evening dispatches, which the emulator does not know about
([CALIBRATION.md](docs/CALIBRATION.md#native-behaviour)).
**Scope:** infer dispatch windows from the recording (battery power that departs from what the
native mode would do, or mode/reserve columns), replay them as M704 commands, and let the
native model run in between. Then score native flows separately from dispatched ones. Also
replay `mode_name`/reserve changes from the export.

### BATTERY-ECM: physical battery and converter loss model · `todo`
**Why:** the `agate-measured` preset uses flat efficiencies. Real losses depend on power and
temperature. From the reference site:
- charge efficiency falls from about 0.996 at 1 kW to 0.951 at 5 kW;
- one cell snapshot at −101 A implies about 11 mΩ of pack resistance, i.e. about 2 % I²R at
  5 kW, so most of the loss is in the converter and the overhead;
- the cabinet reaches 59 °C against 40 °C ambient.

The 30-day export can't separate discharge losses: there are only two operating points, and
temperature is confounded with time of day.

Also the **idle standing draw**: about −27 W per aPower from AC while idle at full or at
reserve (cells at 0 A), a 0.3 %/h drain while held at reserve, and the cells supplying it
off-grid (docs/CALIBRATION.md).

**Measured 2026-10-07 (evening dispatch, docs/CALIBRATION.md):**
- pack resistance 5.4 mΩ ohmic, 10.2 mΩ with polarisation;
- converter 94.3 % at 4.9 kW and 81.7 % at 245 W;
- the SoC scale is SoH-derated (13.0 kWh);
- +3.1 °C in 89 min at 100 A.

Still needed: low-power and charge-direction points (overnight and morning captures), and a
cold morning.

**Scope:**
- An equivalent-circuit cell model: OCV(SoC, T) on the LFP curve plus R(T, SoC) with an
  Arrhenius rise below 25 °C.
- A converter efficiency curve: fixed + linear + quadratic in power.
- Standing overhead.
- A first-order cell/cabinet thermal model driven by ambient temperature, I²R and sun.
- BMS limits: cold-charge derate below about 5 °C, hot derate.

The model drives 1704/1706, so cells sag under load and warm. `tools/calibrate_battery.py`
fits it from a BMS log joined with the Modbus metrics.

**Data:**
- a read-only collector polling Local Bridge `GET /api/battery` and `/api/power` at 10 s,
  started on 2026-10-07 around the evening dispatch;
- longer term, david2069/franklinwh-local-bridge#1 (BMS telemetry logging) and
  david2069/franklinwh-modbus-bridge#9 (`gateway_id` in the export, and DC points).

### MODBUS-REFERENCE-DIFF: register-level comparison with the real aGate · `done`
**Done (2026-10-07):** full read-only scan in a 42 s window with the bridge's poller paused
(`tools/agate_scan.py`). The emulator now matches all 589 static points (test fixture). Added
the 705–712 curve groups, zeroed 714.Prt[0], and left ThrotPct unimplemented. **Open:** decode
the live 150xx diagnostics (15024 = mHz); a US scan for the IEEE codes; *measure* the dynamic
response (a frequency step needs utility cooperation, so likely never on a real site).

**Why:** the Direct Connect payloads are now pinned to a real capture; Modbus is pinned only to
the May 2026 scan. **Constraint:** the aGate serves one Modbus client, which the bridge's poller
holds. Do this through the bridge (a raw-register read endpoint or a scan export), or in a
window agreed with the bridge's owner, never by competing for the session.

---

## P2: useful simulators

### GENERATOR-SIM: generator and AGS · `done`
**Done (2026-10-07):** `generator.py`:
- the 1901 block with the real defaults and key set;
- SoC-based AGS while islanded, after `startDelTime` and within the operating windows;
- manual mode and the exercise run;
- crank, warm-up, run and cooldown;
- the optimal-point loading;
- faults: fail-start, trip and fuel;
- 1302/1304/1902/1710/1412 fields, control `generator`, `ctl generator`;
- the `offgrid-generator-ags` scenario.

`franklinwh_local.set_generator*` verifies against it.

**Open:**
- Confirm `startDelTime` semantics, `manoDate`, `genRelayOpen` with a generator fitted, the raw
  1902 `mode` (2), and whether partial 1901 writes are refused, on a site with a generator.
- V2L via the CarSW port (one port, mutually exclusive uses: vendor-documented, not
  firmware-enforced).
- "PV drops then returns → start" is bridge-side orchestration (OFFGRID_DESIGN §7), not device
  behaviour.

**Why:** the off-grid design in franklinwh-local-bridge (`docs/OFFGRID_DESIGN.md`) wants
"start the generator if installed" logic. `p_gen` / `kwh_gen` are always 0 today.
**Scope:**
- Direct Connect `1901` (read and full-block write: `genEn`, `manuSw`,
  `genStartElec`/`genCloseElec` SoC thresholds, charge windows, exercise schedule), with the
  field list from franklinwh-direct-connect-api's `GENERATOR_WRITABLE`.
- Physics: rated kW, start delay, warm-up, minimum run time and fuel. Start on low SoC while
  islanded, or on manual or exercise triggers.
- Effects: `p_gen` / `kwh_gen`, `genStat`, `genRelayOpen` (1709), `main_sw[1]`, and the 1302
  generator fields. On Modbus the effect is power only.
- Faults: fails to start, trips under load, runs out of fuel.
- Open question: V2L via smart circuits in lieu of a generator (only one can be active).

### CONNECTIVITY-SIM: network paths and their failures · `todo`
**Why:** the real aGate fails over Ethernet → WiFi → 4G and can strand the LAN after a reboot
(DEF-WIFI-RECONNECT). Bridges must survive the aGate disappearing, changing IP, or answering
only one protocol.
**Scope:**
- Direct Connect `1113` connectivity (`wifiConnectRouterStatus`, `EthConnectRouterStatus`,
  `4GConnectBSStatus`, signal strengths, `netStatus`, `awsStatus`), `1117` interface
  config (nested `commSetPara`), and `1119` switches with real effect.
- State machine: the active interface, failover when Ethernet or WiFi drops, and the
  4G-after-reboot behaviour where the LAN listeners stay down until "someone is on-site"
  (`ctl reconnect`).
- Cloud reachability (`awsStatus`), which also feeds the CSIP `server_up` (a shared uplink).
- **IP change on reboot / DHCP renewal:** come back on a new port or address so the bridges'
  re-discovery gets tested. Depends on DOCKER-IPS for real addresses.
- Flaky-link modes: periodic stalls, partial reads, half-open sockets.

### DOCKER-IPS: one IP per gateway · `todo`
**Why:** LAN discovery (the bridge's planned setup wizard, `franklinwh-local scan`) needs
real addresses on :502/:9000, not port offsets.
**Scope:** a Compose file with macvlan/ipvlan, one container per gateway, and an mDNS stub
(`_ssh._tcp` with the NXP hostname, as the real device advertises).

---

## P3: only if a need appears

| Item | Note | Status |
|---|---|---|
| 1701 install profile | Mostly static config (`electricSupply`, `gridExportEnable`, `gridSoft/HardLimit`). Add only if a client starts relying on it. | `parked` |
| More DC cmdTypes (1109/1111/1115/1121–1229, 1253–1277, 1801, 1827/1829, 1501/1503) | Currently answer `result:1 reason:4`. Add each when a client calls it. | `parked` |
| Australia B/C, New Zealand grid codes | Not needed for Sydney/Ausgrid. | `parked` |
| ROCOF / vector-shift trips | Listed in Australia A; matters only for anti-islanding test rigs. | `parked` |
| Real IEEE 2030.5 server/client (TLS, resource tree) | Large. The behavioural CSIP model covers control-logic testing. | `parked` |
| 705–712 curve contents | Not in the scan; read-only interest. | `parked` |

---

## Owned elsewhere (tracked for context)

| Item | Owner | Note |
|---|---|---|
| **Tariff model** (TOU tiers, `sharp/peak/flat/valley`, EA11 TOU) | **franklinwh-local-bridge** | The emulator books all energy to `flat`. If the bridge's rate model becomes importable, the emulator could take tier boundaries from it rather than define its own. |
| AEMO / NEM prices, demand | franklinwh-local-bridge | Market data, not device behaviour. |
| Point local-bridge `mockgw.py` at this emulator's DC frontend | franklinwh-local-bridge | Would give its mocks grid events, CSIP, phases and the shared Site. |
| Modbus bridge `MockPoller` → `Gateway` | franklinwh-modbus-bridge | Waits for the in-flight branches; see [docs/BRIDGE_INTEGRATION.md](docs/BRIDGE_INTEGRATION.md). |
| Library fixes (read_alarms staleness, ac_type heuristic, health :502) | franklinwh-modbus / franklinwh-direct-connect-api | See [docs/FINDINGS.md](docs/FINDINGS.md). |
