# What is emulated

Each emulated gateway is a **working FranklinWH site**: an **aGate** with **one or more aPowers**
and its accessories, in front of a closed-loop model. Control commands move the battery, the grid
balances, and Modbus TCP and Direct Connect always agree. Run one gateway or a fleet of them.
You describe each site in a fleet file: see [Configuration](configuration.md).

## What you get

| Part | Emulated |
|---|---|
| **aGate** | Model "aGate X", serial, firmware profile (Modbus `Vr`, Direct Connect versions), factory or in-service setup, commissioning date, operating modes (Self-Consumption, TOU, Emergency Backup), reserves, off-grid floor, TOU schedule, alarms, reboot, Debug Mode |
| **aPower** (1 or more) | One combined battery: 13.6 kWh and 5 kW per aPower, state of charge, charge/discharge losses, standby drain, temperature limits, each switchable off. Per-aPower serials and firmware in Direct Connect |
| **Solar PV** | Up to three inputs: on the aGate (proximal) and two remote inputs through an **aPbox**, each with a relay; output from a weather model (bell curve, synthetic sky or real Open-Meteo data), PV temperature loss, or replayed recordings |
| **Grid** | Single, split or three phase, per-leg voltage and frequency, a grid code (AS/NZS 4777 Australia A, Ausgrid, IEEE 1547 Cat II/III) with its trips, ride-through, reconnect delays and responses, export limits and CSIP-AUS |
| **Generator** | On the aGate's generator port: automatic start by SoC during an outage (AGS), up to three operating windows, exercise runs, manual start, rating and warm-up/cool-down |
| **Smart circuits** | SW1–SW3 with names, on/off and off-grid SoC cut-off, metering on circuits 1 and 2; house appliances (hot water, EV, aircon, …) can sit on them |
| **House load** | A built-in curve, appliance presets or your own components, or recordings |
| **Environment** | Location and timezone (with DST), sun position, weather, ambient temperature, installation placement and cabinet temperature |
| **Metrics** | Power flows (solar, home, battery, grid, generator, per leg and per circuit), SoC, lifetime and daily energy, 15-minute history, temperatures, voltages, frequency, alarms and run status: over Modbus TCP (SunSpec models and FranklinWH extension registers) and Direct Connect, as the aGate serves them |
| **Controls** | What a client can do to a real aGate: Modbus M704 setpoints (FORCE), Direct Connect writes (mode, off-grid, smart circuits, generator, Modbus enable, reboot, Debug Mode, …), plus the emulator's own control API and web UI for things a client can't do (cut the grid, set the weather, inject faults) |

**Not modelled** (each gateway's diagram lists these too): per-aPower SoC and power (one
combined battery), aPower S (DC hybrid), aHub, V2L, a non-backup panel, EV battery SoC, hot-water
temperature, the FranklinWH Cloud API, network connectivity (Ethernet/Wi-Fi/4G). See the
[backlog](https://github.com/david2069/franklinwh-gateway-emulator/blob/main/BACKLOG.md).

## Protocol detail

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
modes are 1 Backup (charges to 100 % from solar and grid at the rating, never discharges;
inferred), 2 Self-Consumption, and 3 TOU (discharges 17–21 h); no other
value has been read from 15507 (franklinwh-modbus#75). Discharge
stops at the active mode's reserve, under VPP control too. The clock can be real, scaled
(`time_scale`) or manual (tests).

**Profiles.** The default is **`agate-v12r02b60d00-candidate`**, the firmware the reference aGate
runs since 2026-10-20 ("candidate": measured on one site). It serves the 40000 mirror,
answers mirror writes with the base-0 address, holds the TOU reserve in 15509 and the active
mode's reserve in 16001, and refuses charge setpoints and WSetPct above +100 % with exception 3.
`agate-v10r01b04d00` is the earlier firmware (the May and October scans), described below.
Every flag can be overridden via `quirks:`.

**Quirk profile `agate-v10r01b04d00`**.

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
[docs/FINDINGS.md](FINDINGS.md).

## Telling the emulator from a real aGate

- Modbus TCP: SunSpec Model 1 `Opt` reads `Emulator <version>` (the real aGate reads
  "Option Name"). `Mn`, `Md` and `Vr` are the aGate's, so model and firmware detection work as
  on hardware.
- Serials (Model 1 `SN`, Direct Connect `IBG_SN`) start with `MOCK`.
- Direct Connect has no free-text identity field yet; the serial is the marker there.
