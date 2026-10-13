# Weather, solar and house load

The inputs that make flows vary. The README has the configuration summary; this document
covers how the models work, how they were checked, and what they show.

## Solar

### Models

| `weather.model` | How it works |
|---|---|
| `bell` (default) | Half-sine from 06:00 to 20:00 local, with a ±12 % ripple. No location needed. |
| `synthetic` | Sun position (NOAA approximation), Haurwitz clear-sky GHI, a 15 % diffuse share, beam by angle of incidence on the array, isotropic diffuse. Then a deterministic cloud field (a ~6 min structure plus ~40 s edges, thresholded to the sky's cover). |
| `open-meteo` | Real 15-minute global tilted irradiance (GTI) and 2 m temperature for the location and UTC day, plus hourly cloud cover. PV = GTI ÷ 1000 × kWp × 0.85, **the same formula as franklinwh-local-bridge's `solar_forecast.py`**. |

Synthetic skies:

| `sky` | Cloud cover | Opacity | Haze |
|---|---|---|---|
| `clear` | 3 % | 0.6 | 1.00 |
| `broken` | 40 % | 0.75 | 0.95 |
| `overcast` | 95 % | 0.65 | 0.80 |
| `storm` | 85 % | 0.90 | 0.55 |

Array geometry uses Open-Meteo's azimuth convention (0 = south, −90 = east, 90 = west,
±180 = north). The default is 180 (north-facing) south of the equator and 0 north of it.
`kwp` defaults to the site's `solar_peak_w ÷ 0.85`.

### Open-Meteo

- **Endpoints:** `api.open-meteo.com/v1/forecast` serves about 92 days back to 16 days ahead.
  `historical-forecast-api.open-meteo.com/v1/forecast` serves older dates (2022 onwards, with
  15-minute data). The emulator picks by the date's age (more than 85 days → historical).
  Requests use `timezone=GMT` and `timeformat=unixtime`, so times are UTC epochs.
- **Fetching and caching:** each UTC day is fetched once and cached as JSON under
  `~/.cache/franklinwh-emulator/open-meteo/` (or `cache_dir`, or the
  `FRANKLINWH_EMULATOR_CACHE` environment variable). `offline: true` never fetches. A local day
  spans two UTC days, so both get fetched.
- **Bundled data:** Sydney (−33.87, 151.21, tilt 20, azimuth 180) for 2026-01-14 and
  2026-01-15, used with `cache_dir: builtin`.
- **Licence:** Open-Meteo data is CC BY 4.0, with attribution to https://open-meteo.com/.

### Cloud-edge flicker on Open-Meteo data

Open-Meteo's 15-minute values are model averages: smooth, with none of the second-scale edges
real PV shows. Within each 15-minute window the emulator switches between the clear-sky level
C and a shaded level S = 0.25 × C. The shaded fraction x = (C − G)/(C − S) is chosen so the
window's mean equals Open-Meteo's value G. The seeded cloud field decides *when* the window is
shaded, using a threshold at that window's own (1 − x) quantile. Windows with low cloud cover
(≤ 5 %), values at or above 95 % of clear sky, and values below the shaded level stay smooth.
`flicker: false` turns it off.

**Checked on the bundled 15 Jan 2026 Sydney day (6.6 kWp):**

| | Emulated | Reference |
|---|---|---|
| Daily energy | 32.0 kWh | 31.9 kWh from the raw Open-Meteo data with the bridge's formula |
| Peak | 5.72 kW | 5.70 kW, the reference site's highest observed output in 30 days |
| 15-minute window mean vs Open-Meteo | median 0.8 %, p90 3.5 %, worst 11 % | — |
| Largest 30-second change | 4.3 kW | — |

The first version rescaled the cloud factor to the window mean. That amplified the clear
moments of heavily clouded windows by up to 3×, giving **11 kW from 6.6 kWp**. It was replaced
by the clear-sky-bounded scheme above, and a test asserts output never exceeds clear sky
(+15 % margin).

### Sun position check

For Sydney on 15 Jan 2026 at noon AEST (02:00Z), the zenith is 12.8° and the azimuth −175°
(north). With a fixed +10 offset the sun sets just after 19:00 AEST (20:08 AEDT with DST).
North-facing beats south-facing at Sydney noon (test).

## House load

### Components

| Type | Fields | Notes |
|---|---|---|
| `base` | `w`, `drift` | smooth deterministic wander (±drift) on a 30-minute scale |
| `daily` | `w`, `morning_w` (08:00), `evening_w` (19:00) | the original curve as a component |
| `schedule` | `w`, `on: ["HH:MM-HH:MM"]`, `days` | windows may wrap midnight |
| `cycle` | `w`, `period`, `duty`, `on` | seeded phase |
| `events` | `w`, `duration`, `per_day`, `windows` | start times are a pure function of (site seed, name, local date), so offline and live runs agree |
| `hvac` | `w`, `cool_above`, `heat_below`, `span`, `on` | duty = how far the ambient temperature is past the set point ÷ span, on a 20-minute cycle; full power at ≥ 95 % duty |
| `ev` | `w`, `min_w`, `arrive`, `depart`, `kwh`, `mode`, `start` | sessions are named by the evening they began; stops when `kwh` is delivered. `solar` mode takes `solar − non-EV load`, between `min_w` and `w`. |

Every component accepts `circuit` (1–3) and `leg` (L1/L2/L3). Without a leg, a component is
spread by `load_split`.

### Presets

`sydney-family` (one simulated summer day, broken sky):

| Component | Spec | kWh/day |
|---|---|---|
| base | 280 W ±15 % | 6.6 |
| fridge | 140 W, 35 % duty over 40 min | 1.2 |
| kettle | 2.2 kW × 3 min, 5 per day (06:30–08:30, 15:00–21:30) | 0.6 |
| dishwasher | 1.9 kW × 50 min, 1 per day (19:30–21:30) | 1.6 |
| cooking | 1.8 kW 17:45–18:30 | 1.4 |
| evening | morning 350 W, evening 650 W gaussians | 2.9 |
| pool_pump | 1.1 kW 10:00–14:00 | 4.4 |
| hot_water (circuit 1) | 3.6 kW 11:00–13:30, soaking midday PV | 9.0 |
| aircon (circuit 3) | 2.4 kW, cooling above 26 °C, heating below 15 °C | 6.2 (synthetic ambient) |
| ev (circuit 2) | 7 kW off-peak from 22:00, 18 kWh per session, 18:00–07:30 | 18 per session |

That is about 34 kWh of house load plus the EV. `small-flat` has base, fridge, kettle and
cooking events only.

### Smart circuits

- **State lives in the site:** name, on/off, `soc_low_cutoff`, energy. All three circuits are
  on by default; set them with `circuits: {3: {on: false}}`.
- **Switching** removes the bound load. It can be done by:
  - the control action `circuit`;
  - `ctl circuit`;
  - a Direct Connect `1409` full-block write (as `franklinwh_local.set_smart_circuit` sends it).
- **Reporting:** `1410` reports state, `1708` the relay bits (open while shed), and `1411` the
  power and energy on circuit 1 and the car switch (circuit 2).
- **Off-grid:** when the battery cannot carry the house, every running circuit is shed before
  any other load. A circuit is also dropped while islanded with SoC below its `soc_low_cutoff`.

## Replay of recorded data

`replay.py` reads the Modbus bridge's metrics export CSV
(`timestamp, battery_w, grid_w, solar_w, home_w, soc, ambient_temp_c, …`; ISO UTC or epoch
timestamps), or its SQLite store directly. The store is opened read-only; `metrics_archive` and
`metrics` are merged with overlaps de-duplicated, and `gateway_id` filters it. Any other CSV
works with a `columns` mapping.

- **What's replayed:** the inputs (`use: [solar, load, ambient]`), linearly interpolated.
  Adding `battery` also drives the battery at the recorded power, so the recording's
  dispatches (VPP, schedules, force charge) happen. SoC then tests only the energy model, and
  re-anchors to 100 % whenever the recording reports full, as the real BMS recalibrates at
  top of charge. M704 remote control, when enabled, still wins.
- **Dispatch as commands:** `use: [..., dispatch, mode]` instead infers the recording's dispatches
  (more than 500 W from native, for at least 2 min; `dispatch_threshold_w` / `dispatch_min_s`)
  and replays them as setpoints with the emulator's own limits. It also steps the recorded mode
  and reserves. `replay_compare` then scores `native` and `dispatched` samples separately, and
  the report lists `replay_dispatches`. `battery` and `dispatch` are exclusive.
  `scale_solar` and `scale_load` resize them, for "what if we add 3 kW of panels".
- **Priority:** for solar, override → replay → weather → bell. For load, override → replay →
  `loads` → the curve. For ambient, override → replay → weather → the default.
- **Alignment:** `day` maps the recording's first whole local day, DST-aware, onto the site's
  first day, and loops in whole days. `absolute` uses the recorded timestamps as they are.
- **Gaps:** gaps over `max_gap`, and times outside the recording, fall back to the models.

**Round-trip check:** record a 24–48 h `sydney-family` day with a broken sky, export it with
`to_bridge_csv`, and replay it onto a different date with the same initial SoC. The emulated
battery tracks the "recording" to MAE 0.9 W and SoC to MAE 0.0 % over 5,761 samples. That
confirms the alignment, interpolation and sign conventions end to end.

**Real-data check:** see [CALIBRATION.md](CALIBRATION.md). With the recorded battery
followed, SoC tracks the reference aGate to 0.5–0.7 % MAE over two 48-hour windows.

With real data, replay in the site's actual mode and read `replay_compare`: the residual is the
model's error against the real aGate (dispatch logic, efficiency, reserve behaviour). That is a
direct way to calibrate the site model.

### Observations worth knowing (control strategy)

- **In Self-Consumption the battery discharges into the EV.** With the `sydney-family` preset,
  the overnight 7 kW EV session drains the home battery to its reserve, and the grid carries the
  rest. A real aGate has no EV awareness either; a bridge strategy that wants to protect the
  battery has to handle it, for example with a VPP hold or by switching the EV circuit.
- **Solar-divert EV takes surplus ahead of the battery.** In `ev-solar-divert`, under broken
  cloud the EV follows the surplus between 1.5 and 5.2 kW with zero export while charging.
  Chargers that divert only *export* (after the battery) are not modelled yet (backlog).
- **Hot water and pool pump at midday** mean the battery charges noticeably less on a typical
  day than with the bare curve. Expect lower evening SoC in strategy tests.
