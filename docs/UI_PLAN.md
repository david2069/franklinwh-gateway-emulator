# UI plan: configure, run and watch emulated fleets

Status: **plan**, not built. Nothing here changes how the emulator behaves today.

## What it is for

The emulator already has everything a UI would drive. Fleet YAML describes the gateways. Scenario
YAML holds timed steps and checks ([SCENARIOS.md](SCENARIOS.md)). The control API steers running
gateways (`control.py`), and runs produce `timeseries.csv` and `report.json`. All of it is reached
through files, `curl` and `ctl`. The UI puts one local page on top of those, so that you can:

1. **Configure**: build or edit a fleet, with each gateway's profile, phases, grid code, location,
   weather, reserves and ports. Check it before running.
2. **Run**: start and stop a fleet, and run scenarios offline (deterministic) or live (a bridge
   connected to the emulated aGates).
3. **Watch and steer**: see each gateway's live state and fire any control action (outage, sag,
   VPP, alarms, faults, CSIP events) without remembering the JSON.
4. **Review**: open past runs, with the expectation results, invariant violations, a chart of the
   time series and the files to download.

It drives **emulated gateways only**. It never connects to a real aGate, so it is not subject to
the aGate exclusive-access rule.

## Where it lives

**In this repo**, as `src/franklinwh_emulator/ui/`, started with `franklinwh-emulator ui`.

- The UI is a front end for the control API and the scenario runner, which are still changing.
  Keeping them in one repo means one change and one test run update both, and there's no
  version pinning between repos.
- The emulator stays headless for its main users: the bridges' test suites install it and never
  load the UI.
- **When to split it out:** if the front end needs a JavaScript build (Node, npm, a bundler), or
  becomes a product of its own. It then moves to a new repo: a front end only, talking to the
  emulator over the UI server's documented HTTP routes. **Never a fork**: a fork copies the
  emulator and the copies drift.
- The first release ships from here (owner, 2026-10-30).

## Config format: YAML for files, JSON on the wire

| Where | Format | Why |
|---|---|---|
| Fleet and scenario files | **YAML** (as today) | People write them by hand. YAML carries comments, which explain why a gateway or step exists (see `examples/fleet.yaml`). The 17 built-in scenarios are YAML. |
| Control API, UI ↔ server | **JSON** (as today) | What the browser speaks. |
| Run results | JSON + CSV (as today) | `report.json` and `timeseries.csv`, unchanged. |

The two formats hold the same data. The loader uses `yaml.safe_load`, which also reads JSON, so a
`.json` fleet file works if a tool prefers to write one. No second format is introduced.

**Comments survive.** Writing YAML back out with PyYAML drops comments. So the UI never rewrites
a file it didn't create:
- A hand-written file (including the built-ins) opens read-only. "Save as" makes a UI-owned copy.
- A UI-owned file is written by the UI in full, with a header saying so.
- Editing YAML text directly (a text tab next to the form) is always possible. That text is saved
  as typed, comments included, after it validates.

## Architecture

```
browser  ──HTTP/JSON──>  ui server (one local process)
                           ├── workspace: fleets/, scenarios/, runs/
                           ├── sessions: a running Fleet, or a scenario run (offline / live)
                           │     each live session = Fleet in-process + its control API
                           └── existing modules: gateway.Fleet, scenario.run_scenario, control.apply
```

- **One process.** The UI server runs fleets in-process with the existing `Fleet` and
  `run_scenario`, as the tests and examples already do. No subprocess supervisor, no Docker.
- **Standard library on the server**, like `control.py`: asyncio HTTP, JSON. No web framework.
- **Front end without a build step, on the Modbus bridge's stack:** Alpine.js and Chart.js,
  vendored as single pinned files, plus plain JavaScript files shipped as package data. The
  bridge's web UI shows this carries an advanced UI with no JavaScript build (its only build step
  compiles one CSS file, committed). No Node for users.
- **Live updates by polling** `GET` state about once a second. It's local, few gateways, and
  needs no WebSocket code. Revisit only if it proves too slow.
- **Loopback only, no authentication**, as with the control API: it binds to `127.0.0.1`.
- **For any user.** The repo is to become public: the UI ships as an optional install extra, its
  HTTP routes are documented for others to script against, and the docs say it has no login.
- **Offline by default.** Fonts are vendored or the system's; nothing loads from the internet
  unless a feature says so (the map, below).

### Workspace

A directory (default `./emulator-workspace/`, or `--workspace PATH`) holding `fleets/*.yaml`,
`scenarios/*.yaml` and `runs/<timestamp>-<name>/`. It stays outside git: phase 2 adds the
default to `.gitignore`, since run output may hold whatever a user typed into a config.

## Screens

1. **Fleets.** List the fleet files. The editor has a form per gateway, generated from the
   config schema (below), plus a YAML tab. *Validate* reports errors per field without starting
   anything. Port clashes inside the fleet and with ports already in use are reported here.
2. **Running.** One card per gateway: the Modbus TCP Server and Direct Connect addresses to point
   a bridge at, SoC, battery/solar/load/grid power, grid state, mode, VPP, active alarms and
   injected faults, profile. Selecting a card makes it the **active gateway**; the action panel
   applies to it.
3. **Actions.** The control vocabulary as forms, grouped as SCENARIOS.md groups them (grid and
   phases, inputs, alarms and faults, CSIP and comms, battery and modes, loads). Each action's
   result (the returned state, or the API's error text) shows inside its form and stays until
   the next action. No toast notifications.
4. **Scenarios.** The built-in library (read-only) and the workspace's own. The editor shows the
   steps on a timeline by `at`, with `expect` steps marked, plus the YAML tab. *Run offline* gives
   a report in seconds. *Run live* asks for the time scale, then shows the gateway addresses and
   the step progress, and the Running screen applies.
5. **Runs.** A list of past runs with pass/fail. One run shows the expectations, the violations,
   the relay events, the per-gateway summaries and a chart of chosen `timeseries.csv` columns,
   with links to the files.

## Changes needed in the emulator first

Each is its own PR with tests. They're useful without the UI too.

1. **One description of the actions and the config.** Today the action list is a docstring in
   `control.py` and an `if`/`elif` chain, and the config keys are a comment in
   `examples/fleet.yaml`. Add a table that names each action, its fields, their types, units,
   allowed values and sign conventions (for example `vpp.watts`: + = charge). `apply()` validates
   against it, `GET /schema` serves it, and the UI builds its forms from it. The same for fleet
   and gateway config keys. Then the API, `ctl`, the docs and the UI cannot drift apart.
   (`generator` and `dc-faults` already exist in `apply()` but are missing from the docstring.)
2. **Validate without running:** `GatewayConfig.from_dict`, `Fleet.from_dict` and the scenario
   loader get a check-only path that returns every error with its field, instead of the first
   exception.
3. **Scenario progress:** `run_scenario` reports the current step, the site time and the
   expectations so far, through a callback, so a live run can be watched while it plays.
4. **Fleet lifecycle on the server:** start, stop and list sessions; the existing
   `POST /gateways/<id>/<action>` routes are reused as they are, under the session.

## Phases

| Phase | Delivers | Done when |
|---|---|---|
| 0 | This plan | Owner agrees on scope and the open questions |
| 1 | Prerequisites 1–2 (schema, validation) | Schema covers every action and config key; a test fails if `apply()` gains an action the schema lacks |
| 2 | UI server + Running screen, read-only | `franklinwh-emulator ui` starts a fleet file and shows live cards |
| 3 | Actions panel | Every action in the schema can be fired from the UI; errors shown in the form |
| 4 | Fleet editor | Create, validate and save a fleet; hand-written files are never rewritten |
| 5 | Scenarios + Runs (prerequisite 3) | Run a built-in scenario offline and live from the UI, open its report |
| later | Per-gateway IPs | Comes with DOCKER-IPS in BACKLOG.md; the UI would show the addresses, nothing more |

Each phase ships with tests: the server's routes through its HTTP interface, and the schema
check above. The browser pages get a smoke test that loads each screen against a test fleet.

## Energy diagram

The Running screen's centrepiece is a live energy diagram in the Modbus bridge's look, from a
design the owner has (a canvas of four presets, kept outside the repo). Grid → service meter →
aGate in the middle; the main panel and home loads above; PV top right; aPowers right; generator,
smart circuits and V2L below; an optional secondary panel on the aGate's non-backup port. Dashed
lines animate in the direction power flows.

Its four presets are one diagram with these settings:

| Design setting | Values | From the emulator |
|---|---|---|
| PV topology | AC ports on the aGate · remote PV via aPbox · aPower S DC | `pv_inputs` (`proximal` = 15503 on the aGate; `remote1`/`remote2` = 15504/15505 via aPbox). aPower S DC: not modelled |
| aPowers | 1–3 | `apower_units` |
| Phases, service rating | 1–3 phases; amps | `ac_type`, `phases`. Rating: not modelled |
| Grid, battery, home, solar flows | direction and kW | the gateway's state (`grid_w`, `battery_w`, `load_w`, `solar_w`) |
| Smart circuits | SW1–3 on/off, kW | `circuits` |
| Generator | status | `generator` |
| EV, hot water | kW | appliances (`ev`, a scheduled `hot_water`) |
| Header | location, timezone | `location`; weather from the gateway's `weather` |

**Map (optional).** A map of the fleet's gateway locations needs map tiles from the internet
(OpenStreetMap or similar), fetched by the viewer's browser. It is off unless switched on; the
location and weather panel needs no network.

**Not modelled; the diagram shows these as "not modelled", never invented values:**

- per-aPower SoC and power (one combined battery; only single-aPower scans exist);
- two PV inputs on the aGate itself (the emulator has one, 15503);
- aPower S DC-coupled hybrid with MPPT inputs, and its ratings;
- V2L;
- non-backup loads on a secondary panel via the aGate's non-backup port;
- the service meter's rating;
- the EV's battery SoC and the hot water tank temperature;
- tariff prices ($/MWh, c/kWh), which belong to the bridge's tariff logic.

Each becomes its own backlog item with a documented or measured source before the emulator
models it. Ratings shown on the cards (13.6 kWh, 5 kW per aPower) come from the emulator's
constants; the aPower S figures in the design need a FranklinWH source.

The design is a static mock-up: its numbers are fixed text and its flows don't balance. The UI
rebuilds it in plain SVG driven by the state above. Its header held a street address; the UI shows
only what the fleet config holds, and examples use city-level locations.

## Owner's answers (2026-10-30)

1. **Who uses it:** any user. The repo will become public.
2. **Look and feel:** the bridge's UI, with the energy diagram above, including location and
   weather.
3. **JavaScript:** an advanced UI is expected. The first release is built here without a
   JavaScript build; a new repo (not a fork) follows only if one becomes necessary.
