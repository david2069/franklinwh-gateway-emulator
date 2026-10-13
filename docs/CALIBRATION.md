# Calibration against the reference aGate

The emulator's battery model and its Direct Connect payloads were checked on 2026-10-07 against
the real installation the emulator is modelled on:

- **Device:** an aGate X-01-AU with one aPower, on a 242 V single-phase Ausgrid supply in Sydney.
  The PV is 6.6 kWp (`PV1RatedPower` 66) and peaks at about 5.6 kW.
- **Firmware:** Direct Connect reports `IBG_VER` V12R02B30D06_260304 and protocol V1.11.03.
  Modbus 1.Vr reports V10R01B04D00.

The real aGate was never contacted directly. Its Modbus port serves one client at a time, and
the Modbus bridge's poller holds it. All data came over Tailscale from the integrators:

- **Modbus bridge (:8100):** `GET /api/metrics/export?range=30d&format=csv`, which needs a
  login. This gave 164,996 samples at about 10 s, from 2026-09-07 to 2026-10-07.
- **Local Bridge (:8101):** `GET /api/admin/metrics/export?fmt=csv&hours=720` (177,841 samples),
  and read-only `GET /api/cmd/<name>` for the Direct Connect payloads.

The raw exports and captures are **not** in this repository: they hold the site's serials,
location and household data. Each finding below records the method, so it can be repeated.

## Data provenance

| Dataset | Source (read-only) | Span (AEST/AEDT) | Rows / interval | Fields |
|---|---|---|---|---|
| Modbus bridge metrics | `GET :8100/api/metrics/export?range=30d&format=csv` (login) | 2026-09-07 16:07 → 10-07 17:07 | 164,996 at ~10 s | `timestamp, battery_w, grid_w, solar_w, home_w, soc, ambient_temp_c, cabinet_temp_c, mode_name, self_reserve_pct, tou_reserve_pct, grid_mode` (from SunSpec 701/713/714 and extensions 15xxx) |
| Local Bridge metrics, **real gateway only** | `GET :8101/api/admin/metrics/export?fmt=csv&hours=720&gateway=<real id>` | 2026-09-07 → 10-07 | 81,511 at ~30 s | `ts, soc, grid_w, solar_w, battery_w, load_w, generator_w, mode` (from Direct Connect 1301; battery **not quantised**) |
| Direct Connect snapshot | `GET :8101/api/cmd/<name>` for 20 commands | 2026-10-07 ~17:16 | one each | full payloads; credentials and personal commands not read |
| BMS + power flow, evening | `tools/bms_collect.py` → `GET /api/power` + `GET /api/battery?id=1` | 2026-10-07 17:53 → 18:38 (idle), then from 18:43 (dispatch, 5 s) | 269 at 10 s, then 5 s | 1301 power flow + 1705 cells (16 × mV, 16 × °C, pack V, `currGrp` A, SoC, SoH) + 1703 power electronics (`batVol`, bus voltages, inverter/load currents, `buckboostCurr`, `DCDCStatus`, `runMode`) + 1835 states |

| Modbus full scan | `tools/agate_scan.py` over Tailscale (subnet route to the aGate), function 3 only, one connection, with the bridge's poller stopped (`POST :8100/api/gateways/default/stop`, 30 s settle, scan, `…/start`) | 2026-10-07 21:50:43, 6.5 s; bridge down 42 s | 55 reads, median 63 ms | the SunSpec chain at base 0 (17 models, groups included), 15000–15513, 16000–16499, aliases 40000/50000, address 3000 |

**Caveat: the Local Bridge export without `gateway=` mixes in its mock gateways** (a quarter of
the rows repeat a timestamp). Always filter by the real gateway's id. The Modbus bridge export
has no gateway column (franklinwh-modbus-bridge#9). Its rows matched the real gateway here.

### Firmware during the captures

| Component | Version | Reported by |
|---|---|---|
| Modbus SunSpec 1.Vr | V10R01B04D00 | Modbus bridge (gateway record) |
| aGate IBG | V12R02B30D06_260304 | DC 1102 `IBG_VER`, 1834 `ibg_ver` |
| App / local | V12R00B07D04_260205 | `APP_VER`, `ibg_local` |
| IoT (AWS) | V12R00B06D04_260304 | `AWS_VER`, `ibg_iot` |
| SL | V12R02B07D81 | `SL_VER` |
| aPower FPGA / DC-DC / inverter | V12R01B00D00 / V12R02B07D01 / V12R02B09D03 | `FPGA_VER`, `DCDC_VER`, `INV_VER` |
| BMS / bootloader / TH | V11R50B03D00 / V11R06B00D00 / V11R02B01D00 | `BMS_VER`, `BL_VER`, `TH_VER` |
| Meter | V12R00B11D00 | `METER_VER` |
| Protocol / hardware | V1.11.03 / SyHdVersion 102 | 1102 |
| Local Bridge | 0.1.0 (asset d3166b5f95) | `GET /api/version` |

Neither bridge records firmware history, so there is no proof the firmware was unchanged across
the 30 days. See [FINDINGS.md](FINDINGS.md) (firmware tracking).

## Modbus register scan (2026-10-07)

The bridge's poller was paused for 42 s, then one read-only connection was made (no writes). The layout is identical to the May scan and to the emulator: 17 models,
`SunS` at 0, the end marker at 1126.

The scan surfaced findings the May scan didn't:

- **The curve groups are populated.** The May scan recorded only fixed points, so the emulator
  reported every curve as unimplemented. Real values:

  | Model | Real aGate | Scale |
  |---|---|---|
  | 705 volt-var (3 curves, `Ena` 1) | 207 V +44 % · 220 V 0 · 240 V 0 · 258 V −60 %; VRef 100 %; **RspTms 0 s** | V_SF −2 (V holds **volts**, not %), DeptRef_SF −2 |
  | 706 volt-watt (2 curves) | 253 V 100 % → 260 V 20 %; **RspTms 0.1 s** | V_SF 0, RspTms_SF −1 |
  | 707 LV must-trip (2 sets) | 70 V 1 s · 180 V 10 s (to 21 s); momentary cessation 50 V for 2 s | V_SF −1, Tms_SF −2 |
  | 708 HV must-trip | 275 V 0.2 s · 265 V 1 s (to 21 s); momentary cessation 110 V for 13 s | same |
  | 709 LF must-trip | 47 Hz 1 s (to 350 s) | Hz_SF −1, Tms_SF −2 |
  | 710 HF must-trip | 52 Hz 0.2 s (to 310 s) | same |
  | 711 frequency droop (2 controls) | DbOf 0.25 Hz, DbUf 2.0 Hz, KOf 4.9, KUf 5.075; **RspTms 0.1 s** | Db_SF/K_SF −3, RspTms_SF −1 |
  | 712 watt-var | `Ena` 0, all points 0 | — |
  | 704 PFWInj[0] | PF 1000, Ext 1 | — |

  Every trip and curve value matches the AS/NZS 4777.2 Australia A tables the emulator's grid
  code already used. `Crv[0].ReadOnly` = 1 marks the active curve.
- **Declared response times are 0–0.1 s,** much faster than the standard's envelope (start
  ≤ 1 s, complete ≤ 10 s). They are declared open-loop values, not measured behaviour, so the
  envelope stays the default; `grid_overrides: {response: agate-declared}` uses the declared
  values.
- **714.Prt[0] is all zeros** (`IDStr` "ID_String"), while the top-level `DCW`/`DCWh*` are live.
- **701.ThrotPct is unimplemented** (not 0).
- **1.Pad reads 0x8000.**
- **Aliases 40000/50000 and address 3000** answer exception 2 (illegal address), as emulated.
- **State of health fell from 95.6 % (May 14) to 94.8 % (Oct 7).** In the same period,
  `714.DCWhInj` rose 6,344,500 → 7,749,600 Wh, i.e. 1.41 MWh discharged.
- **15024 tracks frequency in mHz** (49,969 in May, 50,020 now). 15007, 15011–15015,
  15022, 15029–15030 and 15033–15035 also changed since May, so they are live, not static as
  the emulator had them. Meaning undecoded.

**Emulator changes:** the 705–712 groups (and 704 PFWInj) come from this scan, for the
Australia A codes. IEEE codes leave them unimplemented, because no US scan exists to copy. The
faithful profile zeros 714.Prt[0] and leaves ThrotPct unimplemented.
`tests/data/reference_scan_static_2026-10-07.json` holds the 589 static points (live values and
the serial removed), and a test requires the emulator to match every one; it does.

## Register resolution (Modbus)

Across the 30-day export:

| Point | Observed | Emulator |
|---|---|---|
| `713.SoC` | whole percent (163,265 of 164,996 samples on .0) | faithful profile: `soc_whole_percent` (raw 550, 560 …) |
| `714.DCW` (battery W) | 100 W steps (163,116 of 164,996) | faithful profile: `quantize_w` now also applies to 714.DCW |
| grid / solar / home W | not quantised (e.g. 17.8, −99.8, 185.8) | unchanged |

The `spec` profile keeps full precision.

## Battery energy model

### Method

The method was a least-squares fit over windows of 30, 60 and 120 minutes with no gaps longer
than 40 s, and with SoC between 7 % and 95 % at both ends. The model was:

    ΔSoC = k_c · E_charged − k_d · E_discharged − k_o · hours_discharging

The fit was compared with variants that drain at rest or while not charging. The
discharge-only overhead fitted best, and it is the only variant consistent with the battery
holding 100 % for hours at DCW = 0.

### Result

| | 30 min (n=656) | 60 min (n=314) | 120 min (n=148) |
|---|---|---|---|
| kWh discharged per 100 % SoC | 12.51 | 12.44 | 12.48 |
| kWh charged per 100 % SoC | 14.01 | 14.04 | 13.94 |
| Overhead while discharging | 73 W | 71 W | 74 W |
| Fit RMSE | 0.42 % | 0.51 % | 0.63 % |

The charge energy per 5 % SoC band is flat, between 12.9 and 14.4 kWh per 100 %, from 5 % to 95 %.
The 95–100 % band reads 22.6 kWh per 100 %. Modelling that as a taper made replays *worse*,
however. The real SoC also jumps from about 96 % to 100 % when charging ends, which looks like
the BMS recalibrating at the top of charge. Taken together, the display seems to hold 99 %
rather than the cells taking more energy.

This gives the preset `battery: agate-measured`, the new default:

| | `agate-measured` | `legacy` (before) | `ideal` |
|---|---|---|---|
| `discharge_efficiency` (vs 13.6 kWh) | 0.915 | 1.0 | 1.0 |
| `charge_efficiency` | 0.971 | 0.95 | 1.0 |
| `standby_w` (per aPower, while discharging) | 72 | 0 | 0 |
| `taper_above` / `taper_efficiency` | 100 / 1.0 (off) | off | off |
| `full_snap_soc` (BMS declares full) | 98 | 100 (off) | 100 (off) |

`full_snap_soc`: all 30 arrivals at 100 % in the export jump the last 1–4 % within one 10 s
sample while charging at 2–5 kW (from 98 % at the median). Charging energy can't do that; it is
the BMS declaring full.

Round trip is about 89 % at the AC/DCW measurement point, against 95 % before. Any field can be
overridden: `battery: {preset: agate-measured, standby_w: 50}`.

### End-to-end check (replay)

The check replays real solar, load and ambient, plus the **recorded battery power**
(`replay.use: [solar, load, ambient, battery]`). The battery then follows the real dispatch,
and the error in SoC is down to the energy model:

| Window (Self-Consumption, 5 % reserve) | Model | battery MAE | grid MAE | **SoC MAE** (bias) |
|---|---|---|---|---|
| 2–3 Oct 2026 (17,279 samples) | legacy | 47 W | 76 W | 3.0 % (+3.0) |
| | **agate-measured** | 9 W | 40 W | **0.5 % (+0.2)** |
| 29–30 Sep 2026 (17,253 samples) | legacy | 38 W | 69 W | 5.4 % (+5.4) |
| | **agate-measured** | 5 W | 37 W | **0.7 % (+0.4)** |

About 33 W of the grid residual is in the recording itself: the real `grid_w − (home_w −
solar_w − battery_w)` has a mean absolute value of 33 W, with a median of −22 W.

### Native behaviour

Without `battery` in `use`, the emulator decides for itself. Replaying the inputs alone gives
a whole-day battery MAE of about 550 W. Nearly all of it comes from **dispatches the native
model doesn't know about**: the bridge's schedules discharge the battery at 4.9 kW to the grid
from 18:00 every evening, and on 2 Oct they force-charged it at 5 kW from 19:30.

`replay.use: [solar, load, ambient, dispatch, mode]` therefore **infers the dispatches** and
replays them as setpoints, like the bridge's commands. A dispatch is a departure of more than
500 W, for at least 2 minutes, from what the native mode would do (cover `home − solar`, within
the rate, the reserve and full charge). The setpoint is the median recorded power. The recorded
mode and reserves are replayed too. Between dispatches the native model decides, and the
comparison is split:

| Window | Inferred dispatches | **native** battery MAE (bias) | native grid MAE | SoC MAE (bias) |
|---|---|---|---|---|
| 2–3 Oct (15,562 native samples) | 18:00 +4.9 kW 90 min ×2; 19:30 −5 kW 107 min | **53 W** (+14) | 40 W | 1.3 % (−1.2) |
| 29–30 Sep (16,181 native samples) | 18:00 +4.9 kW 90 min ×2 | **63 W** (+2) | 46 W | 1.9 % (−1.9) |

What remains is mostly in the recording:

- **Mornings:** the real battery charges 50–60 W more than `solar − home` allows. The export
  doesn't balance: `grid − (home − solar − battery)` has a median of −22 W. The emulator
  balances exactly, so it banks about 0.3 kWh less per morning, which is the 2–3 % SoC gap.
- **Nights:** the real battery reads a flat 200 W while home is 210–240 W, because of the 100 W
  quantisation of `714.DCW`.
- **Late afternoon at full:** the real battery idles until about 17:00. The emulator covers
  small deficits from about 16:00, which is about 100 W.

## Standing consumption: the aPower is kept alive from the AC side

**Observation.** When the battery is idle (full, or held at the reserve), Direct Connect 1301
`p_fhp` reads a steady **−26 to −27 W**: power flowing *into* the aPower. The cells are not
being charged, though. 1705 `currGrp` reads 0 A, and SoC holds. The aPower's power electronics
stay energised, and their idle consumption is paid from the grid or PV, not from the cells.
While discharging, there is no AC input to cover it, so it comes out of the cells, inside the
~72 W discharge overhead fitted above.

### Evidence

1. **Local Bridge metrics, real gateway, 30 days** (`battery_w` = `p_fhp`, not quantised):

   | Condition | Samples | `battery_w` distribution |
   |---|---|---|
   | SoC ≥ 99.5 %, \|battery\| < 200 W | 14,767 | −27 W ×9,109, −26 ×3,953, −28 ×782, −25 ×176 |
   | … of which solar > 500 W | 11,263 | −27 ×7,170, −26 ×3,334 |
   | SoC ≤ 6.5 % (reserve 5 %), \|battery\| < 200 W | 817 | −26 ×533, −25 ×112 |

2. **SoC holds while the 27 W flows.** There were 22 runs of more than 3 h at full with
   `battery_w` between −60 and 0 W (one a day, 09-09 to 10-07, typically 11:00 → 16:30). SoC
   went from 100.0 to 99.7–99.9 over 3.1–7.5 h. At reserve (the only episode, 09-28 01:35 →
   07:14) SoC drifted 4.9 → 4.0 % over 3 h at −26 W. Holding at the floor is not perfect:
   about 0.3 %/h, roughly 40 W net, comes from the cells.

3. **Energy balance: the grid pays at night.** Real rows at full, after dark:

   | Local time | SoC | grid_w | solar_w | battery_w | load_w |
   |---|---|---|---|---|---|
   | 2026-10-02 21:17:59 | 100.0 | 270 | 0 | −26 | 244 |
   | 21:18:30 | 100.0 | 272 | 0 | −26 | 246 |
   | 21:19:01 | 100.0 | 271 | 0 | −26 | 245 |
   | 21:19:31 | 100.0 | 275 | 0 | −26 | 249 |

   `grid = load − battery` (270 = 244 + 26). The house load excludes the 26 W, and the grid
   supplies it.

4. **Cell-level check (264 idle samples, 2026-10-07 17:53–18:38, SoC 99.8–99.9 %).** These come
   from 1705/1703 via `tools/bms_collect.py`:

   | Telemetry | Reading while idle |
   |---|---|
   | 1301 `p_fhp` | −27 W ×201, −28 ×57 |
   | 1705 `currGrp` (− = out of cells) | **0 A ×258**; −0.2 to −1.3 A ×6 |
   | 1703 `buckboostCurr` | 0.2 A |
   | 1703 `DCDCStatus` / `runMode` / `inverterStatus` | 4 (standby) / 8 / 8 |
   | 1835 `peState` / `bmsState` | 8 "Online / Running" / 5 "Standby" |
   | 1703 `middleBusVolt` | 342.4–343.1 V (DC bus held up) |
   | 1703 `inverterCurr1/2` | 0.6–0.7 A per leg (≈ 75–85 VA at 121 V, mostly reactive) |
   | 1704 `batVol` | 53.6–53.7 V |

   The power goes in at the AC port and never reaches the cells, so it is consumed by the
   converter, which stays online with the DC bus up.

5. **Modbus can't see it.** `714.DCW` is quantised to 100 W, so −27 W reads 0. Every Modbus
   consumer, including the bridge's `battery_w`, reports an idle battery as exactly 0 W.

### What it is not, or what is still unknown

- **The aGate's own controller** (relays, comms, IBG) is not in `p_fhp`. Whether it is fed from
  the aPower or the grid can't be told from this data: its draw is either inside `load_w` or
  unmetered. To find out, compare load across an aGate reboot, or meter its supply.
- **Per aPower or per site:** one aPower here. A two-aPower site should show about −54 W if it
  is per unit.
- **Off-grid:** without the grid, the standing draw must come from the cells (or PV). Not
  observed yet; the two islanding tests were short and under load.

### Emulator implications (not yet implemented)

- An idle battery should report `p_fhp` ≈ −27 W per aPower in Direct Connect (Modbus rounds to
  0), and the grid or PV should supply it. That is ≈ 0.65 kWh/day the emulator doesn't import
  today.
- At reserve, also a slow net drain of about 0.3 %/h.
- Off-grid, about 27 W from the cells.
- Planned as part of BACKLOG `BATTERY-ECM`, alongside the converter curve from the dispatch
  capture.

### Reproducing

```bash
# 30-day Local Bridge export for the real gateway only (find its id via GET /api/gateways)
curl "http://<local-bridge>:8101/api/admin/metrics/export?fmt=csv&hours=720&gateway=<id>" -o lb.csv
# idle telemetry with cells and power electronics (GET only)
python tools/bms_collect.py http://<local-bridge>:8101 idle.jsonl 2026-10-08T09:30:00 10
```

## Evening dispatch capture: cells, converter and capacity (2026-10-07)

**Data:** `tools/bms_collect.py` at 5 s from 18:43:00 to 20:21:37: 1,181 samples, no errors,
median read 0.79 s. It started after the dispatch had begun, so it caught the *end* step
(20:11:36) and not the onset. The dispatch was a 4.9 kW discharge to the grid from SoC 99.4 %
to 40.3 % (TOU programme, bridge schedule), followed by the house load (≈ 245 W) from the
battery.

| Quantity | Result | How |
|---|---|---|
| Pack resistance, ohmic | **5.4 mΩ** (0.34 mΩ per cell) | Cell-mean step 3221.1 → 3255.2 mV, 5 s after −101 A → −5 A |
| … with polarisation | 8.3 mΩ after 1 min, 10.2 mΩ after 10 min | Cell mean recovers to 3270.9 then 3281.7 mV |
| Cell I²R at 100 A | ≈ 55 W ohmic, ≈ 100 W including polarisation (1–2 %) | from the above |
| Converter AC/DC at 4.9 kW | **94.3 %** median (93.8–94.9 %); 296 W loss | `p_fhp` ÷ (`batTotalVolt` × −`currGrp`), 1,060 samples |
| Converter AC/DC at 245 W | **81.7 %**; 56 W loss | 110 samples after the dispatch |
| DC energy per 100 % SoC | **13.03 kWh** = 13.6 kWh × SoH 0.948 (12.89) | 7.70 kWh DC over 59.1 % |
| AC energy per 100 % SoC | 12.29 kWh (the 30-day fit gave 12.45) | 7.26 kWh AC over 59.1 % |
| Cell heating | +3.1 °C in 89 min at ~100 A (max 22.3 → 25.4 °C; ambient 18–19 °C) | 1705 `batTemp[]` |
| Cell balance | spread ≤ 10 mV under load, 3–4 mV at rest | 1705 `batVolt[]` |
| Current sign | `currGrp` −98.5 to −101.6 A while discharging | confirms − = discharge |

**What this says about the loss model:**
- The SoC scale is the **SoH-derated capacity** (about 13.0 kWh), not the 13.6 kWh nameplate.
  That accounts for ~4 % of the apparent "discharge efficiency".
- The **converter** is the main loss. Two points fit either 43 W + 5.1 % of P, or
  55 W + 9.9 W/kW² · P². The morning charge staircase (below) favours the quadratic form.
- The **cells** lose only ~1–2 % at full power.
- The flat preset's discharge factor (0.915) ≈ 0.958 (capacity) × ~0.955 (average converter),
  consistent with the 30-day fit.

## Morning force-charge staircase: charge direction (2026-10-08)

**Data:** `tools/bms_collect.py` at 5 s, 05:15 until full (754 samples; the Local Bridge stalled
for 1–17 min at a time, 51 gaps over 30 s). Grid-powered force charge through the Modbus
bridge's `battery_command` (SunSpec Model 704 `WSetEna`/`WSetPct`, which the locked site
obeys), before sunrise, ambient 15.8–15.9 °C, cells 16.9–18.1 °C. The controller's loop
waited on slow Local Bridge reads, so the steps ran 05:30 → 05:42 → 06:00 → release 06:03 instead of
10-minute steps; the 5 kW step has only four samples with cell data before a gap.

| Step (AC) | Held | AC → DC | Loss | SoC rise ÷ AC energy (at 13.03 kWh) |
|---|---|---|---|---|
| 1.0 kW | 11.5 min | **91.8 %** | 82 W | 0.92 |
| 2.5 kW | 17.3 min | **94.2 %** | 145 W | 0.95 |
| 5.0 kW | 3.6 min | **92.8 %** (4 samples) | ≈ 360 W | 0.95 |

| Quantity | Result | How |
|---|---|---|
| Converter loss, charging | **≈ 70 W + 11.6 W/kW² · P²** (predicts 142 W at 2.5 kW; measured 145 W) | fit to the 1 kW and 5 kW points |
| Pack resistance, ohmic | **≈ 7.3 mΩ** (±2.5 mΩ: `batTotalVolt` is 0.1 V) | 53.3 → 53.6 V for 44 → 85 A, and 52.6 → 52.8 V for 17 → 44 A, 5 s after each step |
| … with polarisation | 9–11 mΩ after 20 s; ≈ 13 mΩ from rest at 18.5 % SoC | |
| Coulombic | SoC rise ÷ DC energy ≈ 1.00 at 13.03 kWh | the SoC scale is the SoH-derated capacity, as when discharging |
| Grid import | 1.23 / 2.70 / 5.26 kW = step + house load | `p_uti` |

**What this says about the loss model:** the converter's losses fit the quadratic form (fixed loss
plus I²R) in both directions, and the linear form does not (it predicts 187 W at 2.5 kW). Charging
costs slightly more than discharging at the same power: about 70 W + 11.6 W/kW² · P² against
55 W + 9.9 W/kW² · P². Pack resistance is the same in both directions within the 0.1 V
resolution. The emulator's flat preset (charge 0.971 against 13.6 kWh) is consistent with
these figures: about 0.94 converter × 1.044 capacity ratio ≈ 0.98. Open: a longer 5 kW charge
step (15 min) to pin the high-power point.

## Direct Connect payloads

The method was to read 20 commands from the real aGate (via the Local Bridge's read-only
`/api/cmd/*`), then diff key sets and container types against `DcPayloads`. Commands that
expose credentials or personal details (`wifi_config`, `cloud_config`, `device_info`,
`network_interfaces`) were not read. After the fixes below, **all 312 top-level keys across
20 commands match**; `tests/test_directconnect.py::test_payload_shapes_match_reference_capture`
pins the key sets.

| Cmd | Real device | Was |
|---|---|---|
| all reads | `reason: 0` in every normal reply, **except** 1726, which has `current_id, result, opt (1), list` only | `reason` missing on most |
| 1302 | `electricity_type` 1, `ent`, `pro_load [0,0,0]`, `infi_status` **list** per aPower, `slaver_stat` 4; **no `devNum`** | `devNum`, `infi_status` int |
| 1302 `mode` | read 29287 "Solar & Battery Plan" at 16:05 local, then **9 with an empty name** at 17:16 (TOU, during the evening schedule). 9 matches `ibgRunStatus` and `ibgMainState`. | not modelled (see open questions) |
| 1304 | `date` echoed; `sectionEngy` = the latest quarter-hour's kWh in channel order; `sno` = days since commissioning (804) | missing; `sno` was the epoch day |
| 1708 | `dspRunstatus`, `electricity_type`, `gridVol2`, `gridCurr1/2`, `loadCurr1/2`, `genVoltage`, `isTripPhs*En`; **no `energyMode`**; `name` empty; `blackStartRelay` 1; `mainRelay2Status` 0 on single phase | `energyMode` present; 11 keys missing |
| 1704 / 1708 voltages | **single phase reads as two half-voltage legs**: `gridLineVol` 2424 (242.4 V), `gridVol1` = `gridVol2` = 121.2, equal leg currents | full voltage on leg 1 |
| 1706 | cell voltages are **integer mV**, about 3350–3370 at 99.9 % (flat LFP curve); `batSoh` 94.8 | linear 3200 + 4·SoC mV (3600 at full), floats |
| 1704 | `middleBusVolt` 342.7; `batVol` 53.7 V at 99.9 % | 400; 57.6 |
| 1202 | under AEDT: **`timezone` 10 with `DST` 1**, i.e. the standard offset plus a flag; `timezoneStr` **empty**; `laitude`/`longitude` are **strings** | 11, IANA name, floats |
| 1404 | `modeChoose` 14 and an empty `name` while TOU ran; `electricity_type` 1 | the scheduling type, the mode name |
| 1410 | `SwXFreq`, `genStat`, `gridVoltCheck` 3, `CarSwConsSupEnerge`, `CarSwConsSupStartTime` | missing |
| 1412 | `Sw2Volt`, `SW2*`, `power`, `curr`, `volt`, `genpowerGen`, `CarSwConsSupExpEnerge`; `freq` 500 (Hz×10 integer), `Sw1Volt` 1014 | missing; `freq` 50.0 |
| 1832 | no `isOver` (1106 has it) | `isOver` present |
| 1904 | `opt/result/reason` present | missing |

The `spec` profile keeps the IANA `timezoneStr` for tests that want it. Everything else above is
unconditional.

### Open questions from the captures

- What drives 1302 `mode`/`name` to 9/"" while a mode is in force. It may reflect a running
  schedule or dispatch rather than the selected mode.
- The 1404 `modeChoose` 14.
- The 1412 `Sw1Volt` 1014 on a 242 V site, and the integer energy encodings
  (`CarSWExpEnergy` 10150, `CarSWImpEnergy` 16384 = 0x4000).
- Why `714.DCWhInj`/`DCWhAbs` lifetime counters imply a 98 % ratio when the SoC fit implies
  about 89 % round trip. The counters may be measured on a different side of the converter.

## Observed grid events in the 30 days

Two short islanding episodes appear in the Modbus export. Both are probably off-grid tests:

| Local time | Sequence |
|---|---|
| 2026-09-20 22:27 AEST | 10 s before: battery +4.9 kW, grid −4.7 kW. Then `grid_mode` "PV Clipped" for about 50 s, then "Grid Forming" for about 2.5 min, with the battery at a flat 200 W, SoC 28 %, and grid 0 → 9 W on return. A TOU 15 % segment surrounds it (22:17–22:30). |
| 2026-09-25 18:28 AEST | "PV Clipped" for about 50 s, then "Grid Forming" for about 1.5 min, with the battery at 300–400 W and SoC 77 %. |

The "PV Clipped" state before islanding is the bridge's reading of the DER mode. It is a useful
expectation for `outage` scenarios.

## Repeating this

```bash
# Modbus bridge export (login first; LAN-only HTTP needs ALLOW_INSECURE_AUTH=1 on the bridge)
curl -c jar -H 'Content-Type: application/json' -d '{"username":"…","password":"…"}' \
     http://<bridge>:8100/api/auth/login
curl -b jar 'http://<bridge>:8100/api/metrics/export?range=30d&format=csv' -o metrics_30d.csv
```

```yaml
# Replay with the recorded dispatch, then read replay_compare in the report.
gateways:
  - id: home
    grid_code: ausgrid
    location: {latitude: -33.87, longitude: 151.21, timezone: Australia/Sydney}
    initial_soc: 32            # the recording's SoC at the first local midnight
    self_reserve: 5
    replay: {source: metrics_30d.csv, use: [solar, load, ambient, battery], align: day, loop: false}
```
