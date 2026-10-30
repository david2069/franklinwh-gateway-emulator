# franklinwh-gateway-emulator

An emulator of the FranklinWH **aGate** for testing without hardware. It serves **Modbus TCP**
(SunSpec chain plus FranklinWH extension registers) and, optionally, the **Direct Connect**
local protocol (TCP/9000), both in front of one **closed-loop site model**, so control
commands actually move the battery and both ports always agree. It can run one gateway or a
**fleet** of them.

Built for [franklinwh-modbus](https://github.com/david2069/franklinwh-modbus) and
[franklinwh-modbus-bridge](https://github.com/david2069/franklinwh-modbus-bridge). The
unmodified `FranklinWHController` connects, scans, reads and dispatches against it
(see `tests/test_franklinwh_modbus.py`).

> Unofficial software, not affiliated with FranklinWH. It emulates documented and observed
> behaviour of firmware `V10R01B04D00`. Where the docs disagree, the choices are listed below.

**Docs:** [Design and rationale](docs/DESIGN.md) · [Scenarios](docs/SCENARIOS.md) ·
[Direct Connect frontend](docs/DIRECT_CONNECT.md) · [Weather and loads](docs/WEATHER_AND_LOADS.md) · [Calibration against the real aGate](docs/CALIBRATION.md) · [Findings about the real devices and
libraries](docs/FINDINGS.md) · [Bridge integration plan](docs/BRIDGE_INTEGRATION.md) ·
[Backlog](BACKLOG.md) · [Changelog](CHANGELOG.md)

## Quick start

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/franklinwh-emulator serve --port 5020 --seed 7
.venv/bin/franklinwh-emulator serve --port 5020 --dc-port 9000 --grid-code ausgrid   # + Direct Connect
.venv/bin/franklinwh-emulator fleet examples/fleet.yaml
.venv/bin/franklinwh-emulator scenario run outage-during-vpp --out results/   # timed + checked
.venv/bin/python -m pytest
```

In-process (the same pattern franklinwh-local-bridge uses for its Direct Connect mocks):

```python
from franklinwh_emulator import Gateway, GatewayConfig, Fleet

gw = Gateway(GatewayConfig(id="site", seed=7))
host, port = gw.start()                 # background thread, ephemeral port
gw.site.load_override_w = 4000          # steer the scenario
gw.faults.session_held = True           # inject faults
gw.stop()

with Fleet.from_yaml("examples/fleet.yaml") as fleet:
    fleet["site"].address
```

Inside an asyncio app, use `await gw.start_async()` / `await gw.stop_async()` to serve on
your own loop.

Runnable examples (tested in `tests/test_examples.py`, standard library plus the emulator):

| File | Shows |
|---|---|
| `examples/single_gateway.py` | one aGate: start, read SoC, battery, grid, solar and load over Modbus TCP, stop |
| `examples/fleet_with_control.py` | every gateway in `examples/fleet.yaml`, plus the control API cutting the grid on one |
| `examples/conftest_for_clients.py` | a pytest fixture for client repos that records the emulator version and profile |

What an emulator run does and does not verify is listed in `CLAUDE.md` ("Critical changes need
real verification").

## What is emulated

**Register space (PDU addresses, any unit id):**

| Range | Contents |
|---|---|
| 0–1127 | `SunS`, then models 1, 701–715, 502 and the end marker. Each model is built from pysunspec2's definitions at the **exact lengths and repeating-group counts of the real scan**, so every offset matches (e.g. WSetEna = 318, ControllerHb = 1092). |
| 15000–15516 | Extension registers: 15500–15513 (PV, home load, mode, reserves, PV Wh), 15514–15516 (read 0 on the reference aGate, charging; meaning unknown) and the 15000–15043 diagnostics dump, with live values (15003 solar, 15011 load, 15012:15013 battery W, 15017, 15020, 15022 grid W, 15024 Hz, 15025 V, 15029:15030 and 15033:15034 energy, 15035 SoC, 15036 SoH, 15040 solar term). |
| 16000–16499 | 16000 high-resolution home load, 16001 self reserve, 16002 = 20 (1406 `BBBackupSoc`, candidate; franklinwh-modbus#18). |
| anything else | ILLEGAL DATA ADDRESS (as the real aGate does at 40000+ on V10R01B04D00). `base_aliases: [40000]` also serves the chain there, as V12R02B60D00 does (profile `agate-v12r02b60d00-candidate`; measured 2026-10-20, one site). `write_bases: [0]` makes the other bases refuse SunSpec writes (exception 2); whether V12 accepts writes at 40000 is unverified. Both switch at run time: `ctl <gw> addressing alias=40000 write=0` or `gw.set_addressing(...)`. |

**Profiles follow franklinwh-modbus firmware_quirks.json**: `tests/test_quirks_drift.py` fails if an `agate-*` profile asserts a behaviour that the file doesn't record as documented or measured for its firmware (`QUIRK_BASIS` in `profiles.py`); "reported" behaviour is opt-in, and the few unmeasured assumptions are listed in `QUIRK_ASSUMED` with reasons.

Static values come from `baselines/agate_v10r01b04d00.json`, which `tools/gen_baseline.py`
generates from `AGATE_FULL_SCAN_2026-05-14.txt`.

**Site model** (`site.py`): solar bell and load profile ported from the Direct Connect emulator's
`SyntheticSite`. The battery is **integrated**: SoC moves with power, power is limited by SoC,
reserves and 5 kW per aPower, and grid balances exactly. Lifetime counters only grow. Native
modes are 1 Backup, 2 Self-Consumption, and 3 TOU (discharges 17–21 h); no other
value has been read from 15507 (franklinwh-modbus#75). Discharge
stops at the active mode's reserve, under VPP control too. The clock can be real, scaled
(`time_scale`) or manual (tests).

**Quirk profile `agate-v10r01b04d00`** (default). Every flag can be overridden via `quirks:`.

- One Modbus client at a time. An extra client is closed, or with `busy_behaviour: hang` it is
  left open and never answered.
- M704 `WSetPct`: hardware sign, **positive = discharge**, as a percentage of the 5 kW rating
  (`WSetPct_SF = -1`), saturating beyond ±100 %. `WSet` (WSetMod = 1): positive = discharge too
  (measured on V12R02B60D00, franklinwh-modbus `m704_wset_sign`; assumed on V10R01B04D00).
- `WSetRvrtTms` counts down in `WSetRvrtRem`, but expiry does **not** release control (PICS Issue 4).
- Accepted but discarded: `715.ControllerHb`, `715.LocRemCtl` (always 1 = Local, even during
  VPP), M702 rate settings (stay 0xFFFF), `WMaxLimPctEna`, `VarSetEna`, and extension writes.
- Extension 15507–15509 writes are acknowledged and never applied. Change mode and reserves from
  the test side (`gw.site.set_native_mode`, `set_reserves`, site config or the control API).
  15509 **mirrors 15508** unless `tou_reserve_mirrors_self: false`.
- `713.Sta` is always 0, `714.DCA` is always 0, `702.WMaxRtg` is 0, and 502.OutPw / 15506 are
  quantised to 100 W.

**Profile `spec`**: a well-behaved SunSpec DER for comparison testing. It has reversion that
works, LocRemCtl that follows VPP, writes that stick, read-only writes that raise exceptions,
and multiple clients.

**Fault injection** (`gw.faults`, live): `latency_s`, `offline`, `session_held`,
`drop_responses`, `reset_after`, `exception`, and `slow_p` / `slow_ms` (Modbus only: delay a
seeded fraction of replies, e.g. past a client's timeout so the late reply lands on its next read;
`ctl <gw> fault slow_p=0.1 slow_ms=3000`). **Range faults** hit only requests that touch one
address range, e.g. the extension block while SunSpec keeps answering:
`gw.faults.fail_range(15500, 15509, "silent", for_s=30)` or `ctl <gw> range-fault 15500 15509 silent for=30`.
**Write rejection**: `gw.device.reject_writes(704, "WSetPct", abs_gt=100, code=3)` or
`ctl <gw> write-reject 704.WSetPct abs_gt=100` refuses matching SunSpec writes (no limit = every
write; `gt`/`lt` for one side; `write-reject clear`). Profile
`agate-v12r02b60d00-candidate` refuses WSetPct beyond raw +100 and every negative WSetPct or WSet
(charge) with exception 3 (`wsetpct_max_abs_raw: 100`, `refuse_charge: true`), as franklinwh-modbus
firmware_quirks.json measures for V12R02B60D00 (one site).
Range-fault modes: `exception` (`code=N`, default 4), `silent` (no reply), `short` (a well-formed reply with half
the registers asked for); `range-fault clear` ends it.

## Grid events, PV relays and alarms

The site has a utility you can break, plus the aGate's protection around it:

| Scenario | How | What the aGate does |
|---|---|---|
| Black-out | `ctl <gw> outage` / `site.set_grid(available=False)` | Grid relay opens at once (`701.ConnSt` 0, `DERMode` grid-forming, `GRID_DISCONNECT`). The battery carries the house down to the off-grid floor (5 %), ignoring VPP and the mode reserve. It sheds the load it can't carry and curtails the PV it can't absorb (`PV_CLIPPED`). At the floor the house goes dark (`InvSt` STANDBY, 0 V). |
| Loss of one phase | `ctl <gw> outage L2` | The relay opens at once (`trip_reason` phase-loss). Islanded, each leg's output follows its own load. |
| Brown-out / swell | `ctl <gw> voltage 195 [L3]` or `0.85pu` | `701.LNV`/`V<leg>` sag. Each trip stage of the grid code fires after its own delay, with `AC_UNDER/OVER_VOLT` raised while the condition lasts. Shorter excursions ride through. |
| Frequency excursion | `ctl <gw> hz 51.3` | Frequency-watt: over-frequency curtails generation and moves storage towards charging, and under-frequency does the reverse (`ThrotPct`, `InvSt` THROTTLED). It trips per the grid code's frequency stages. |
| Restore | `ctl <gw> restore` | The relay recloses only after the grid has spent the code's enter-service delay inside its window. |
| Drop and return islanded | `ctl <gw> episode 60 120` / `gw.episode(down_s, offgrid_s)` | Utility fails; Modbus and Direct Connect go away for `down_s` (open Modbus sessions get a TCP RST, connects are refused), then come back on the same ports with the site islanded (`ConnSt` 0, `GRID_DISCONNECT`). The utility returns `offgrid_s` later and the relay recloses after the enter-service delay. `episode_phase` in the status shows `down` / `offgrid`. 15016 stays 2 unless you opt in with `quirks: {islanded_15016: 1}`: 1 was seen during the two 2026-10-21 episodes on one V12R02B60D00 site, which firmware_quirks.json grades "reported" (`ext_15016_status`). |
| Requested off-grid | `ctl <gw> offgrid on` | Islands without `GRID_DISCONNECT`, like Direct Connect `1723 offgridSet`. |
| PV relays | `ctl <gw> pv remote1 off` | That input drops to 0 W (15503 proximal, 15504/15505 remote). Split solar across inputs with `pv_inputs: {proximal: 0.6, remote1: 0.4}`. |
| Alarms | `ctl <gw> alarm OVER_TEMP [--persistent]` | Sets the `701.Alrm` bit. GROUND_FAULT, DC_OVER_VOLT, MANUAL_SHUTDOWN, OVER_TEMP and HW_TEST_FAILURE fault the inverter (battery 0 W; dark if islanded). Latched alarms clear on a `715.AlarmReset` write (`controller.clear_alarms()`). Persistent ones need `clear-alarm`. Raw DC-port bits are set with `dc_bits` (`714.PrtAlrms`). |

### Grid codes

`grid_code` selects the protection settings and grid-support responses. Split phase defaults
to IEEE, everything else to generic Australia A. Model 703's enter-service registers report the
selected code.

| | `as4777-au-a` / `ausgrid` (AS/NZS 4777.2:2020 Australia A) | `ieee1547-cat2` (IEEE 1547-2018 Cat II) | `ieee1547-cat3` (Cat III, Rule 21-style) |
|---|---|---|---|
| Nominal | 230 V, 50 Hz (starts at the scanned 241.8 V / 49.99 Hz) | 120 V L-N, 60 Hz | same |
| Under-voltage | V< 180 V 10 s · V<< 70 V 1 s | UV1 0.70 pu 10 s · UV2 0.45 pu 0.16 s | UV1 0.88 pu 21 s · UV2 0.50 pu 2 s |
| Over-voltage | V> 265 V 1 s · V>> 275 V 0.2 s · 10-min average > 258 V | OV1 1.10 pu 2 s · OV2 1.20 pu 0.16 s | OV1 1.10 pu 13 s · OV2 1.20 pu 0.16 s |
| Frequency trips | F< 47 Hz 1 s · F> 52 Hz 0.2 s | UF1 58.5 Hz 300 s · UF2 56.5 Hz 0.16 s · OF1 61.2 Hz 300 s · OF2 62 Hz 0.16 s | same |
| Freq-watt, generation | 100 % at 50.25 Hz → 0 at 52 Hz | 0.036 Hz deadband, 5 % droop | same |
| Freq-watt, storage | over: discharge → 0 by 50.75 Hz, then charge → rated by 52 Hz · under: charge → 0 by 49 Hz, then discharge → rated by 48 Hz | droop through zero (1 pu per 3 Hz) | same |
| Volt-watt | generation 253 V 100 % → 260 V 20 % · charging 207 V 20 % → 215 V 100 % | off (1547 default) | off |
| Volt-var | 207 V +44 % (supplying) · 220–240 V 0 · 258 V −60 % (absorbing) of 5.8 kVA per aPower | off (unity PF default) | off |
| Export limit | `ausgrid`: **10 kW single phase**, 5 kW/phase multi-phase · `as4777-au-a`: none | none | none |
| Enter service | 205–253 V, 47.5–50.15 Hz, 60 s, then a **360 s ramp** (exactly the scanned 703) | 0.917–1.05 pu, 59.5–60.1 Hz, 300 s, 300 s ramp | same |
| Response timing | 1 s dead time; freq-watt, volt-watt and volt-var 95 % within 10 s; soft export limit within 15 s; 0.1 Hz frequency hysteresis | 0.2 s; freq droop 5 s, volt-var 5 s, volt-watt 10 s (90 %, open-loop response time); no hysteresis | same |

The Australia A values come from the distributor settings summary that reproduces the
standard's tables ([Jemena, effective 18 Dec 2021](https://jemena.com.au/siteassets/asset-folder/documents/electricity/embedded-generation/eg-inverter-requirements-settings-asnzs-4777-2_2020v2.pdf)).
Ausgrid is an Australia A network
([ENA](https://energynetworks.com.au/miscellaneous/power-quality-response-mode-settings)).
Ausgrid's 10 kW single-phase export limit comes from its
[NS194](https://www.ausgrid.com.au/asp-and-contractors/technical-document-library/ns194),
as confirmed for this Sydney site. The 5 kW/phase used for multi-phase `ausgrid` sites is the
generic basic micro-EG rule and is unconfirmed for Ausgrid. The IEEE values are from memory of
the 1547-2018 default tables and have not been checked against its text.

**Response timing.** Each function waits out a dead time and then follows a first-order lag
*from the power actually being delivered*, so a cap tightening from 5 kW doesn't hide a 500 W
output. The time constant is set so the response reaches 95 % (AU) or 90 % (IEEE) at the
settle time. Over- and under-frequency responses see the frequency through the hysteresis
backlash: a response is released only once the frequency has come back by 0.1 Hz.
- After a reconnect, battery discharge and PV ramp linearly from zero over `es_ramp_s`.
- A soft export limit (an enforced static limit, or CSIP `opModExpLimW`) is met within 15 s,
  so fast cloud edges can briefly breach it. The `export_limit` scenario check allows that
  envelope.
- The emulator uses the slowest compliant response, so tests see the worst case.
- Tune it with `grid_overrides: {response: {freq_settle_s: 5, start_s: 0.5, hysteresis_hz: 0}}`,
  pick a preset (`as4777-envelope`, `ieee1547`, or `agate-declared`, the 0–0.1 s response
  times the real aGate declares in 705/706/711), or restore instant responses with
  `response: null`.

The AU timing values are the commonly cited AS/NZS 4777.2 envelope and AS/NZS 4777.1 soft
limit. The real aGate's curve registers (scan of 2026-10-07) declare 0 s for volt-var and
0.1 s for volt-watt and frequency droop. Those are declared open-loop times, not measured
behaviour, so they are a preset, not the default.

With volt-var on, `701.Var` is live, for example −348 var at 241.8 V (the
scanned aGate read −715). Australia B/C and New Zealand are not modelled.

Override any field per gateway with `grid_overrides`, for example
`{export_limit_single_phase_w: 5000, es_delay_s: 5, freq_watt: null,
trips: [{name: "V<", kind: v_lo, threshold: 0.8, delay_s: 3}]}`.

**AEMO and market data** (NEMWeb, the AEMO dashboard JSON, Open Electricity) cover prices,
demand and the generation mix, not what an inverter reports, so the emulator doesn't use them.
They belong with the bridge's tariff and VPP logic.

### Export limits and CSIP-AUS

**Static limit: declared and tracked, not enforced, by default.** On the reference Ausgrid
site the aGate does not apply its 10 kW limit: local `1903 grid_feed_max` is −1 and the cloud
reports `notControlExportSolar: true` (see franklinwh-direct-connect-api `BACKLOG.md`). So the
emulator exports what physics allows and records breaches (`export_over_limit_w`, running
`export_breach_s` / `export_breach_wh`). That makes the documented latent exposure testable:
5.7 kW solar plus a 5 kW midday VPP discharge exports 10.7 kW, 700 W over the limit.
`ctl <gw> export-limit enforce` (or `export_limit_enforced: true`) clips at the limit instead,
cutting battery discharge first and then PV.

**CSIP-AUS** (SA TS 5573, mandatory from late 2026). The aGate's IEEE 2030.5 client is
configured over Direct Connect `1205 der_comms` (`enable`, `uri`, `lfdi`, `sfdi`, `pin`,
`status2030_5`) and is invisible on Modbus. The emulator models what the client does, and
the control API plays the utility server:

```bash
ctl site csip on https://csip.example/dcap   # registers after 5 s if the server is reachable
ctl site csip export 1500 1800               # DERControl opModExpLimW 1.5 kW for 30 min
ctl site csip default export=5000            # DefaultDERControl
ctl site csip server down                    # after 300 s of lost comms → default control
ctl site csip connect off                    # opModConnect false: DER output stops, house stays on grid
ctl site csip import 2000 | gen 7000 | load 1500 | energize off | clear
```

- Controls apply only once the client is **registered** (enable alone is not enough, as on
  the real device).
- With no active event, or after `comms_loss_s` (300 s) of lost comms, the DefaultDERControl
  applies.
- `opModExpLimW` overrides the static limit and is always enforced.
- `opModImpLimW` and `opModLoadLimW` stop battery charging; `opModGenLimW` caps PV plus
  discharge.
- CSIP limits also curtail Modbus VPP commands. Whether a real aGate prioritises them like this
  is unverified.
- With the Direct Connect frontend on, `1205 der_comms` reads and writes this client
  (`enable`, `uri`, `pin`, `lfdi`, `sfdi`, `status2030_5`).
- LFDI/SFDI are derived from the serial with the IEEE 2030.5 SFDI algorithm (checked against
  the spec's worked example).
- The emulator does **not** speak 2030.5 (no TLS, no resource tree), and the encoding of
  `status2030_5` is unknown.

**`sunsMdEn`.** `ctl <gw> modbus off|on` is what a `1205` write does: the Modbus listener
closes or reopens about 3 s later (delayed-apply in both directions on FW V12R02B30D06).

### Phases

| Layout | Config | 701 registers |
|---|---|---|
| Single phase | `ac_type: 0` (default `phases: [L1]`; `[L2]` puts the leg elsewhere) | That leg's W/VA/A/V/TotWh points. `VL1L2` and `LLV` carry the L-N voltage, as on the scanned aGate. |
| Split phase (US) | `ac_type: 1` → L1 + L2, 180° | `VL1L2` = 240 V |
| Two-phase | `ac_type: 2, phases: [L1, L2]` | `VL1L2` = √3 × V, L3 points unused |
| Three phase | `ac_type: 2` → L1 + L2 + L3, 120° | `VL1L2/VL2L3/VL3L1` from the phasors |

Unwired legs read 0, as on the single-phase scan, or the SunSpec unimplemented value with
`quirks: {unused_phase_registers: unimplemented}` (the `spec` profile's default).
`load_split` and `gen_split` (e.g. `{L1: 3, L2: 1, L3: 1}`) make the legs unbalanced, so
per-leg `W` differs and each leg meters its own import and export. Voltage and availability
can be set per leg, which is how phase loss and single-leg sags work.

> **Unverified.** Nobody has watched a real aGate's Modbus registers during an outage, and
> the reference scans show `Alrm` = 0 throughout and come from a single-phase AU unit. The
> islanded and multi-phase pictures follow SunSpec semantics. Treat them as "what a correct
> DER would show" until real captures exist.

### Control API and `ctl`

`serve` and `fleet` start an HTTP control API on `127.0.0.1:8502` (`--control-port`,
`--no-control`). It is unauthenticated, so keep it on loopback.

```bash
franklinwh-emulator ctl site status
franklinwh-emulator ctl site outage
franklinwh-emulator ctl site restore
franklinwh-emulator ctl site solar 6000          # or: solar auto
franklinwh-emulator ctl site fault session_held=true
franklinwh-emulator ctl --json site status       # full state

curl -s -X POST localhost:8502/gateways/site/grid -d '{"hz": 51.3}'
curl -s -X POST localhost:8502/gateways/site/grid -d '{"voltage": 170, "phase": "L3"}'
```

Every action, with its fields, units, allowed values and examples, is in
`src/franklinwh_emulator/schema.py`, and a running emulator serves the same table at
`GET /schema`. Unknown actions get 404; unknown or missing required body keys get 400. In-process code calls the
same methods on `gw.site`, and `Fleet.start(control_port=0)` starts the API alongside a fleet.

**Sign conventions on the wire:** `701.W` positive = grid import; `714.DCW` positive =
battery discharging.

## Decisions where the sources disagree

| Topic | Docs say | Emulator follows | Why |
|---|---|---|---|
| 714.DCW sign | QUIRKS.md: positive = charging | positive = **discharging** | `controller.py` and the scan (DCW +500 W at night with 500 W load, no PV) |
| 15507 values | older QUIRKS.md: 0 Emergency, 1 TOU, 2 Self, 3 Manual | **1 Backup, 2 Self, 3 TOU** only | each read with the app in that mode, V12R02B60D00, 2026-10-21, one site (franklinwh-modbus#75, #88); 0 and 4 were unsourced and removed |
| 16001/16002 | manifest: 16002 = TmpAmb | 16001 self reserve (on `agate-v12r02b60d00-candidate`: the active mode's reserve, 100 in Emergency Backup; firmware_quirks `ext_16001_reserve`, one site: franklinwh-modbus #63 for SC/TOU, franklinwh-modbus-async #16 G2 for EB); 16002 = 20 (`BBBackupSoc`) | 2026-10-07 scan: 16002 = 20 with TmpAmb 17.5 °C and TOU reserve 15 % |
| FC 4 | unknown | same data as FC 3 | — |
| 714 per-aPower ports | unknown | one aggregate port (`NPrt = 1`) | only single-aPower scans exist |
| 713.WHRtg with 2+ aPowers | unknown | scales (13600 × units) | M702 rates are known *not* to scale |
| Islanded / alarm registers | never observed | SunSpec semantics | see "Grid events" |

| 715.LocRemCtl during VPP | fixed Local (QUIRKS.md) | **always 1 (Local)** | documented as read-only; the bridge's earlier "Remote" mock was invented |
| Ausgrid export limit | cloud `gridFeedMax` 10 kW | **declared, not enforced** | local `1903 grid_feed_max` = −1, `notControlExportSolar` |

**Found with the emulator:** franklinwh-modbus bugs, including `read_alarms()` never
refreshing (stale `check_blocking_alarms()`) and `ac_type` being guessed from voltage. See
[docs/FINDINGS.md](docs/FINDINGS.md).

## Location and time

Each gateway can have its own location:

```yaml
location: {latitude: -33.87, longitude: 151.21, timezone: Australia/Sydney, postcode: "2000"}
```

`timezone` is an **IANA name**, so daylight saving is handled, including 23- and 25-hour days.
Everything on the site's wall clock follows it: TOU windows, load shape, daily totals and
midnights, quarter-hour history, Direct Connect `1202` (local time, offset, `DST` flag,
lat/lon, postcode) and naive scenario `start` times. Weather inherits the coordinates.
Precedence is `location.timezone`, then `tz_offset_hours` (a fixed offset with no DST, kept for
compatibility), then the host's zone. Don't rely on the host's zone: it is whatever the machine
running the emulator uses. CLI: `serve --timezone Australia/Sydney --lat -33.87 --lon 151.21`.

## Weather

Solar comes from one of three models (`weather:` per gateway):

| Model | Source | Use |
|---|---|---|
| `bell` (default) | the original half-sine day | quick tests, no location |
| `synthetic` | sun position + clear-sky irradiance on the array plane, and a seeded cloud field (`sky: clear \| broken \| overcast \| storm`) with second-scale edges | offline, deterministic variability |
| `open-meteo` | real 15-minute tilted irradiance and temperature for the location and day ([Open-Meteo](https://open-meteo.com/), free, CC BY 4.0), cached per day | realistic days, past or forecast |

```yaml
weather: {model: open-meteo, kwp: 6.6, tilt: 20}      # lat/lon from `location`
# (or give latitude/longitude here to override)
#        azimuth: Open-Meteo convention, 0 = south, ±180 = north (default 180 south of the equator)
```

Open-Meteo PV uses **the same conversion as franklinwh-local-bridge's solar forecast**
(GTI ÷ 1000 × kWp × 0.85). The emulator's "actual" therefore matches the bridge's "forecast"
for the same day, which makes forecast-vs-actual logic testable. Seeded cloud-edge flicker
follows the real cloud cover *within* each 15-minute value: it never exceeds clear sky and keeps
the window's mean. On 15 Jan 2026 Sydney gives 32.0 kWh emulated against 31.9 kWh raw, with a
5.72 kW peak (the reference site's highest observed output is 5.70 kW). Two real Sydney days are
bundled (`cache_dir: builtin`) for offline tests; other days are fetched once and cached in
`~/.cache/franklinwh-emulator`. Change the sky live with `ctl <gw> sky storm`.

## House load

`loads:` builds the house from appliance components. Without it the original
breakfast/dinner curve is used.

```yaml
loads: sydney-family                       # a preset, or a list, or:
loads:
  preset: sydney-family
  remove: [pool_pump]
  set: {ev: {mode: solar, kwh: 25}}
  add: [{name: spa, type: schedule, w: 2000, on: ["19:00-21:00"], circuit: 1}]
```

| Type | Behaviour |
|---|---|
| `base` | always on, slow drift |
| `daily` | the original morning/evening curve |
| `schedule` | on in time windows (wall clock, optional weekdays) |
| `cycle` | compressor duty cycle (fridge) |
| `events` | seeded random uses per day in windows (kettle, oven, dishwasher). Repeatable per seed and date. |
| `hvac` | AC/heating with duty growing as the ambient temperature passes the set points |
| `ev` | charger with arrive/depart, kWh per session, mode `immediate`, `offpeak` (from `start`) or `solar` (PV surplus only, 6–32 A, ahead of the battery) |

- **Smart circuits.** Any component can be bound to a smart circuit (`circuit: 1–3`).
  Switching the circuit off really removes its load, whether by `ctl <gw> circuit 2 off`, a
  scenario step, or a Direct Connect `1409` write. `1410` and `1708` report the state, and the
  new `1411` meter reports circuit power and energy.
- **Off-grid shedding.** When islanded and short of battery, the aGate sheds smart circuits
  before anything else. Per-circuit `soc_low_cutoff` (1410 `SwXSocLowSet`) drops a circuit
  below that SoC.
- **Placement and overrides.** Components can sit on a specific `leg` of a 3-phase supply.
  `ctl <gw> appliance kettle on|off|auto` forces one.
- **Presets:** `sydney-family`, `small-flat`.

## Generator (AGS)

A generator on the aGate's generator input, configured with the real Direct Connect `1901`
fields. The defaults are the reference aGate's own block: feature off, AGS 20 % / 80 %,
`startDelTime` 1800 s, operating window 1 11:00–23:59, exercise off.

```yaml
generator: {installed: true, rated_w: 7000, genStartElec: 20, genCloseElec: 80,
            startDelTime: 1800, charge1En: 0, fuel_l: 40, min_run_s: 900}
```

- **Automatic start is SoC-based only, as on the device:**
  - it starts while islanded, once the outage has lasted `startDelTime` and SoC is at or below
    `genStartElec`, inside an enabled operating window (`charge1..3`), or at any time if none
    is enabled;
  - it stops at `genCloseElec` after `min_run_s`.
- **Manual mode** (`manuSw` 2, or `ctl <gw> generator start|stop`) and the **exercise run**
  (`oilmanoEn`, every `manoFre` days at `manoStartTime` for `manoTime` minutes).
- **Never in parallel with the grid.** On-grid it runs unloaded with its relay open. When the
  grid returns it hands back and cools down.
- **Start sequence:** cranking, then warm-up (relay open), then running, then cooldown. Loaded,
  it carries the house and charges the battery so it runs at `genOptiPPoint` (70 %) of rated;
  above rated the battery covers the rest.
- **Faults:** `ctl <gw> generator fault fail-start|trip`, plus running out of `fuel_l`
  (Willans-line burn). Clear them with `generator clear` and `generator refuel <L>`.
- **Where it shows:**
  - 1302: `p_gen`, `kwh_gen`, `genStat`, `main_sw[1]`;
  - 1304: the `p_gen` series;
  - 1902: `genStat`, `genpowerGen`, and the block itself, readable and writable with a full
    frame; `franklinwh_local.set_generator*` verifies;
  - 1710: `genRelayOpen`;
  - 1412: `genpowerGen`.
  
  On Modbus only the power balance changes. Recordings add `generator_w` and `gen_state`, and
  the energy-balance check includes the generator. See the `offgrid-generator-ags` scenario.
- **Interpretations, not observed on hardware:** what `startDelTime` measures, `manoDate`,
  `genRelayOpen` with a generator fitted, and refusing partial 1901 writes. The reference site
  has no generator.

## Replaying recorded data

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
  MAE, against 3.0–5.4 % before; see [docs/CALIBRATION.md](docs/CALIBRATION.md).
- **Scoring the native model:** `use: [solar, load, ambient, dispatch, mode]` infers the
  recording's dispatches (VPP, schedules, force charge), replays them as commands, steps the
  recorded mode and reserves, and scores native samples separately. On the reference site the
  native battery error is 53–63 W MAE.
- **Exporting:** `scenario run … --out DIR --bridge-csv` writes emulator runs in the bridge's
  format, for replay or for `POST /api/metrics/import` into the bridge.

## Scenarios

Timed, repeatable test runs with recording and checks. They run offline (deterministic, fast)
or live (servers up on a scaled clock so a bridge can take part). Nine built-ins cover outages,
CSIP events and comms loss, the latent Ausgrid export breach, Australia A brown-out and
frequency behaviour, phase loss and US ride-through, a real Sydney day (Open-Meteo), an export
limit held under cloud edges, DST end, a family day with an EV, off-grid circuit shedding and
solar-divert EV charging. See [docs/SCENARIOS.md](docs/SCENARIOS.md).

## Roadmap

See [BACKLOG.md](BACKLOG.md). Next up are ramped grid responses, then the generator and
connectivity simulators. Tariffs belong
to franklinwh-local-bridge.
