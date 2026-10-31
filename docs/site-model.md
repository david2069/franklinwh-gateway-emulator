# The site model

TOU schedule, energy totals and history, location and time, weather, house load and the generator.

## TOU schedule

In TOU mode (`mode` 3) the battery follows a schedule of time blocks in the FranklinWH cloud
format ([franklinwh-cloud TOU guide](https://david2069.github.io/franklinwh-cloud/TOU_SCHEDULE_GUIDE/)):

```yaml
    tou_schedule:
      - {name: Super Off-Peak, startHourTime: "00:00", endHourTime: "10:00", waveType: 4, dispatchId: 1}
      - {name: Solar, startHourTime: "10:00", endHourTime: "12:00", waveType: 4, dispatchId: 3}
      - {name: On-Peak, startHourTime: "12:00", endHourTime: "19:00", waveType: 2, dispatchId: 6}
      - {name: Export, startHourTime: "19:00", endHourTime: "20:00", waveType: 0, dispatchId: 7}
    tou_rates: {2: {buy: 0.54, sell: 0.03}}      # $/kWh per waveType, shown only
```

| dispatchId | Code | Battery |
|---|---|---|
| 1 | HOME | covers home loads; surplus solar goes to the grid |
| 2 | STANDBY | idle |
| 3 | SOLAR | charges from surplus solar only |
| 6 | SELF | self-consumption |
| 7 | GRID_EXPORT | discharges at its rating |
| 8 | GRID_CHARGE | charges at its rating from grid and solar |

`waveType`: 0 off-peak, 1 mid-peak, 2 on-peak, 4 super off-peak. Gaps are SELF, off-peak. The
active reserve still applies. The guide's limit fields (`maxChargeSoc`, `minDischargeSoc`,
`gridChargeMax`, …) are kept and shown but not applied: the guide says `minDischargeSoc` is ignored
by the aGate and the others aren't verified in all firmware. One schedule covers every day
(seasons and weekdays: not yet). Without one, TOU charges from surplus solar except
17:00–21:00, when it self-consumes (the emulator's model before schedules). The `tou` action
replaces it at run time; it never changes the mode.

## Energy totals and history

Lifetime totals are served as SunSpec 701 `TotWhAbs`/`TotWhInj` (grid import/export, split
across legs), 714 `DCWhAbs`/`DCWhInj` (battery charge/discharge), 502 `OutWh` and extension
15510–15513 (PV), and they keep counting as the model runs. Direct Connect 1301 has today's totals
and 1303 the quarter-hour history.

| | factory (default) | in-service |
|---|---|---|
| Lifetime totals at start | 0 | the reference aGate's (scan of 2026-10-07) |
| Commissioned (1303 `sno`) | the day it starts | 2024-07-25 (the reference site) |
| History | from start | from start, or `history_days: N` earlier |

Any of them can be set per gateway: `grid_import_wh`, `grid_export_wh`, `pv_wh`,
`batt_charge_wh`, `batt_discharge_wh` (Wh), and `commissioned: YYYY-MM-DD`. With
`history_days: N` (in-service, at most 105) the site model runs over the N days before start-up
(about 0.2 s per day per gateway), so the history, today's totals and the lifetime counters all
agree; the totals given are as of the start of that period, and the SoC is wherever the model
ends. An `open-meteo` gateway that isn't `offline` fetches those days.

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

## Installation placement and cabinet temperature

`install:` says where the aGate and aPower sit, and drives the cabinet temperature (Modbus
`701.TmpCab`, the UI):

```yaml
install: {indoor: heated}                                   # or unheated (garage), uninsulated (shed)
install: {outdoor: {sun: afternoon, cover: roof}}            # sun: morning | afternoon | all-day | shade
                                                             # cover: none | roof | enclosure
```

| Placement | Temperature around the cabinet |
|---|---|
| indoor, heated | about 20 °C (`room_c`), a little of the outside air |
| indoor, unheated | the outside air smoothed over ~8 h, plus 4 °C |
| indoor, uninsulated | the outside air smoothed over ~1 h, plus 1 °C |
| outdoor | the outside air plus sun on the cabinet: up to 15 °C uncovered, 6 °C in an enclosure (+2 °C shelter), 3 °C under a roof, scaled by the sun's height and the cloud, and by the side it faces (east = morning, west = afternoon) |

The cabinet follows its surroundings with a lag (`cabinet_lag_h`, 1.5 h) and runs warmer while
charging or discharging (`self_heat_c_per_kw`, 3 °C per kW). All values are plausible, not
measured; each can be set under `install:`. Without `install:` the cabinet is estimated as
before (ambient + 8 °C + 1 °C per kW). Rain isn't modelled. How the temperature limits the
battery comes next (#111).
