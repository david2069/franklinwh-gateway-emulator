# Configuration

Every emulated site is described in a **fleet file**: a YAML file you write and keep. It sets the
aGate, how many aPowers, the solar PV, the grid connection, the house load, accessories such as a
generator, and where the site is. The emulator reads it at start; it doesn't write it.

## Where it lives, and what is saved

| What | Where | Who writes it |
|---|---|---|
| **The setup**: gateways, aPowers, PV, grid, location, … | your fleet file, e.g. `my-fleet.yaml` (start from [`examples/fleet.yaml`](https://github.com/david2069/franklinwh-gateway-emulator/blob/main/examples/fleet.yaml)) | you |
| **What you changed while it runs** (actions from the web UI, `ctl` or the control API: grid cut, solar forced, TOU schedule, …) | `<workspace>/sessions/<name>.yaml` when you press **Save** (default workspace: `./emulator-workspace`) | the emulator |
| **Test runs** (timed steps and checks) | scenario files and their recordings: see [Scenarios](SCENARIOS.md) | you / the emulator |

Changes made while it runs are not written back to the fleet file. Save them as a session and
restore it after a restart (`ui my-fleet.yaml --restore NAME`), or copy the values into the fleet
file to make them permanent.

## How to set it up

1. Copy `examples/fleet.yaml` and edit it: one entry under `gateways:` per site.
2. Start it: `franklinwh-emulator ui my-fleet.yaml` (web UI on http://127.0.0.1:8502/) or
   `franklinwh-emulator fleet my-fleet.yaml` (no UI). It checks the file first and prints every
   problem with its path (e.g. `gateways[1].modbus: port 5020 already used`), then exits.
   `franklinwh-emulator validate my-fleet.yaml` checks it without starting anything; see
   [Checking files and startup output](validate.md), which also explains what is printed at start.
3. Point your client at each gateway's Modbus TCP and Direct Connect ports.

From Python: `validate_fleet(load_yaml("my-fleet.yaml"))` returns the same problems. Every key,
with type, default and meaning, is also served at `GET /schema` under `gateway_config`.

`defaults:` applies keys to every gateway; a gateway's own keys win.

## A complete example

```yaml
defaults:
  host: 127.0.0.1
  setup: in-service              # already set up: Modbus TCP on from the start

gateways:
  - id: home                     # the control API's and UI's name for it
    seed: 7                      # seeds the serial and derived values (repeatable runs)
    profile: agate-v12r02b60d00-candidate   # firmware to emulate
    apower_units: 2              # two aPowers: 27.2 kWh, 10 kW
    initial_soc: 60
    native_mode: 2               # Self-Consumption
    self_reserve: 20
    ac_type: 0                   # single phase
    grid_code: ausgrid
    location: {latitude: -33.87, longitude: 151.21, timezone: Australia/Sydney,
               country: AU, state: NSW, city: Sydney}
    weather: {model: synthetic, kwp: 6.6, tilt: 20, sky: broken}   # solar from the sky
    pv_inputs: {proximal: 0.7, remote1: 0.3}                      # 30 % through an aPbox
    install: {outdoor: {sun: afternoon, cover: roof}}             # cabinet temperature
    loads: sydney-family         # appliances, some on smart circuits
    circuits: {2: {name: EV Charger}}
    generator: {installed: true, rated_w: 7000}
    modbus: {port: 5020}
    directconnect: {port: 9000}
```

## Every key

The tables below come from `validate.config_schema()`; a test checks every key is listed.

### Identity and interfaces

| Key | Type | Default | What it does |
|---|---|---|---|
| `id` | string | `agate` | gateway id, unique in the fleet; the control API's <id> |
| `seed` | integer | `0` | seeds the serial and the site's derived values; give each gateway its own: `validate` and `ui` refuse two gateways that set the same seed (same serial) |
| `serial` | string | — | 20-character serial; default derived from the seed |
| `host` | string | `127.0.0.1` | address the Modbus TCP Server binds to |
| `port` | integer | `0` | Modbus TCP Server port; 0 = ephemeral |
| `modbus` | object | — | shorthand for host/port: {port: 5020, host: ...} |
| `dc_port` | integer | — | Direct Connect port; 0 = ephemeral. aGate profiles always run one (see directconnect) |
| `directconnect` | object | — | Direct Connect (TCP 9000) frontend: {port: 9020}; 0 = ephemeral |
| `setup` | string | `factory` | factory (default: Modbus TCP off until a Direct Connect 1205 write), commissioning (as factory, the installer part-way through; the `commission` action) or in-service (already set up: Modbus TCP on) |
| `modbus_enabled` | boolean | — | start with the Modbus TCP Server on or off, whatever the setup |
| `events` | any | — | off \| normal (default) \| verbose, or {level, file}: what the gateway did (mode, battery status, trips, alarms, clients); file appends JSON lines |

### aGate firmware and behaviour

| Key | Type | Default | What it does |
|---|---|---|---|
| `profile` | string | `agate-v12r02b60d00-candidate` | firmware profile (profiles.py): agate-v12r02b60d00-candidate (default), agate-v10r01b04d00, spec |
| `quirks` | object | `{}` | overrides of the profile's switches |
| `native_mode` | integer | `2` | Modbus 15507 numbering: 1 Emergency Backup, 2 Self-Consumption, 3 TOU (Direct Connect and the cloud number them differently) |
| `self_reserve` | integer | `20` | Self-Consumption reserve, % |
| `tou_reserve` | integer | `20` | TOU reserve, % |
| `offgrid_floor_soc` | number | `5.0` | islanded discharge floor, % |
| `tou_schedule` | array | — | TOU blocks (franklinwh-cloud format): name, startHourTime, endHourTime, waveType, dispatchId; default: solar-charge off-peak, self-consumption 17-21 |
| `tou_rates` | object | — | prices per tariff period, {waveType: {buy, sell}} in $/kWh (display only) |
| `export_limit_enforced` | boolean | `False` | enforce the grid code's export limit (default: tracked only) |

### aPower (battery)

| Key | Type | Default | What it does |
|---|---|---|---|
| `apower_units` | integer | `1` | number of aPowers |
| `initial_soc` | number | — | starting state of charge, %; default derived from the seed |
| `battery` | any | `agate-measured` | loss model: agate-measured, ideal, legacy, or {preset, ...} |
| `battery_temperature` | object | — | battery temperature limits (overrides of thermal.BATTERY_THERMAL: charge/discharge taper, hot derate, heater, fan, OVER_TEMP); false = off; default on when install is set |

### Solar PV

| Key | Type | Default | What it does |
|---|---|---|---|
| `pv_inputs` | object | `{'proximal': 1.0}` | share of solar per PV input: proximal (aGate), remote1/remote2 (aPbox) |
| `pv_non_1547` | list | `[]` | PV inputs whose inverters don't follow IEEE 1547-2018 (no Freq/Watt droop), e.g. `[remote1]`; off-grid, a North American aGate opens their relay instead, for 300 s at a time |
| `weather` | object | — | {model: bell\|synthetic\|open-meteo, kwp, tilt, azimuth, sky}; inherits lat/lon |
| `solar_peak_w` | number | — | bell-curve solar peak, W; default derived from the seed |
| `pv_temperature` | object | — | PV output falls as panels heat up: {temp_coeff (per °C, -0.004), noct (°C, 45), ref_cell_c (°C, 45: the 0.85 efficiency's typical warm day)}; false = off |
| `cap_forced_solar` | boolean | `True` | forced solar (pv solar_w) can't exceed the solar model's value now, 0 at night (default true); false = use it as given (lab tests) |
| `replay` | any | — | recorded solar/load to replay: a path, or {source, use, align, loop} |

### Grid connection

| Key | Type | Default | What it does |
|---|---|---|---|
| `ac_type` | integer | `0` | 0 single phase, 1 split phase, 2 three phase |
| `phases` | array | — | legs wired, e.g. [L1, L2]; default from ac_type |
| `grid_code` | string | — | as4777-au-a, ieee1547-cat2, ieee1547-cat3, ausgrid; default from ac_type |
| `grid_overrides` | object | `{}` | grid-code values to change, e.g. {es_delay_s: 5} |
| `load_split` | object | — | share of home load per leg, e.g. {L1: 2, L2: 1}; default even |
| `gen_split` | object | — | share of aGate generation per leg; default even |
| `utility` | object | — | the grid operator's rules: {operator, export: allowed\|limited\|prohibited, export_limit_w, export_per_phase_w, import_limit_w, grid_charge, grid_charge_max_w, battery_export, export_rule_source}; enforced (PCS settings). Also accepted as dnsp (AU) or lines_company (NZ) |
| `dnsp` | object | — | AU name for utility (the same block) |
| `lines_company` | object | — | NZ name for utility (the same block) |
| `service` | object | — | the main breaker and busbar: {main_breaker_a, busbar_a}; import above the breaker trips the site off the grid (inverse-time) until reset; the site also holds grid import below it. North American models: `busbar_a` sets the PCS export limit by the 120 % rule, ((busbar × 1.2) − main) / 1.25 A, when `utility` sets none |

### House load and smart circuits

| Key | Type | Default | What it does |
|---|---|---|---|
| `loads` | any | — | house load: a preset name, a list of components, or {preset, add, remove, set} |
| `home_base_w` | number | — | base house load, W; default derived from the seed |
| `circuits` | object | — | smart circuits: 1–2 on AU aGates, 1–3 on North American: {1: {name, on, soc_low_cutoff}} |

### Generator

| Key | Type | Default | What it does |
|---|---|---|---|
| `generator` | object | — | {installed, rated_w, ...} plus 1901 fields; null = none fitted; AU/NZ: `rated_w` at most 80 A × line-to-neutral V (18.4 kW at 230 V) |
| `dc_pv` | object | — | aPower S DC PV on its MPPT inputs: {rated_w, export: true}; needs an aPower S, rated_w up to 15 kW per aPower S (datasheet MPPT limit). It follows the site's solar shape, shares the aPower S inverter with battery discharge (11.5 kW each), charges the battery directly, and with export: false only serves the house and battery. The app's MPPT page (Commissioning Guide p. 22) |
| `power_limits` | string | `settable` | settable (default) or locked: locked = the user's power-rating settings can't be changed, as FranklinWH has done in some US areas (owner's statement; regions not evidenced, so never derived). Direct Connect 1701 limit writes are then acknowledged and not stored |
| `pv` | object | — | PV model: {model: builtin (default) | pvlib, transposition: haydavies | perez | isotropic, temperature_model: sapm | faiman | pvsyst, wind_m_s: 1}; pvlib (pip install '.[pvlib]') computes the array irradiance for synthetic weather (Erbs split + transposition) and the cell temperature |
| `components` | object | — | component age and warranty: {agate, apower, pv_inverter, pv_panels}, each {installed: YYYY-MM-DD (default: commissioned), warranty_years}; apower also throughput_mwh, pv_panels degradation_pct_per_year (0.5). Defaults: FranklinWH's warranty (aPower X 12 years / 43 MWh, X-02-AU 12 / 60, aPower 2 and S 15 / 60, aGate 12; 70 % retained), PV inverter 10 and panels 25 years (plausible). aPower SoH falls linearly to 70 % at the end of the term by age or throughput and scales the usable capacity; panels lose output with age |
| `pcs_derate_w` | number | — | PCS Permanent Derate: the installer's cap on each aPower's charge and discharge power, W, on- and off-grid; the app offers 5.0, 7.6, 9.6, 10.0 and 11.5 kW (Commissioning Guide, MAC 1 systems); above the unit's rating it has no effect; stacks with temperature derating |
| `accessories` | object | all fitted | accessory modules fitted {smart_circuits, generator_module, apbox}, each true or false; left out = fitted. Without smart_circuits there are no circuits (Direct Connect keeps the keys, zeroed); generator.installed needs generator_module; remote1/remote2 PV inputs need apbox |
| `model` | object | — | hardware models (#180): {agate, apower}, each a code, SKU or name, e.g. {agate: 102, apower: 2}; the aPower model sets kWh, kW and charge kW per unit, Direct Connect 1102 serves the codes; default by region: AU/NZ 102 + 2, North America 103 + 0; apower may be a list, one model per aPower (mixed, e.g. [0, 3]: aPower X + aPower 2), checked against the Backwards Compatibility Statement (15 at most; aGate X 1.1 with X and 2 mixed, 8; up to 8 aPower S) ([Sources and regions](sources-and-regions.md)) |

### Location, time and placement

| Key | Type | Default | What it does |
|---|---|---|---|
| `location` | object | — | {latitude, longitude, timezone (IANA, DST-aware), postcode, name, country (ISO alpha-2), state, city, site_id}; country/state/city/site_id only group gateways in the UI |
| `tz_offset_hours` | number | — | fixed UTC offset, no DST; location.timezone wins |
| `install` | object | — | where the aGate/aPower sits: {indoor: heated\|unheated\|uninsulated} or {outdoor: {sun: morning\|afternoon\|all-day\|shade, cover: none\|roof\|enclosure}}, plus cabinet_lag_h, self_heat_c_per_kw, room_c; drives the cabinet temperature. Default: ambient + 8 °C |
| `time_scale` | number | `1.0` | site clock speed; > 1 fast-forwards |

### Energy history

| Key | Type | Default | What it does |
|---|---|---|---|
| `pto_date` | string | — | the utility's permission to operate, YYYY-MM-DD (the app's PTO Date); recorded and shown only, as no behaviour before or after it is documented |
| `commissioned` | string | — | commissioning date, YYYY-MM-DD (Direct Connect sno); default: the start day (factory) or the reference site's 2024-07-25 (in-service), so in-service gateways read the same sno unless each sets its own |
| `history_days` | number | `0` | in-service: run the model over this many days before start (max 105) to fill the history, today's totals and the lifetime counters; ~0.2 s per day |
| `grid_import_wh` | number | — | lifetime grid import counter at start, Wh; default 0 (factory) or the reference aGate's (in-service) |
| `grid_export_wh` | number | — | lifetime grid export counter at start, Wh; default 0 (factory) or the reference aGate's (in-service) |
| `pv_wh` | number | — | lifetime PV counter at start, Wh; default 0 (factory) or the reference aGate's (in-service) |
| `batt_discharge_wh` | number | — | lifetime battery discharge counter at start, Wh; default 0 (factory) or the reference aGate's (in-service) |
| `batt_charge_wh` | number | — | lifetime battery charge counter at start, Wh; default 0 (factory) or the reference aGate's (in-service) |

