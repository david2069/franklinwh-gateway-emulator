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
- **When to split it out:** if the front end needs a JavaScript build (Node, npm, a bundler), it
  moves to its own repo that pins an emulator version. The design below avoids that.

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
- **Front end without a build step:** plain HTML, CSS and JavaScript files shipped as package data.
  A chart library, if needed, is vendored as one file. No Node.
- **Live updates by polling** `GET` state about once a second. It's local, few gateways, and
  needs no WebSocket code. Revisit only if it proves too slow.
- **Loopback only, no authentication**, as with the control API: it binds to `127.0.0.1`.

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

## Open questions for the owner

1. **Who uses it?** Only you, or also the bridge sessions to set up their test fleets? If the
   latter, the server's HTTP routes are the interface they would script against, and should be
   documented as such.
2. **Look and feel:** should it follow the bridges' UI patterns (layout, dialogs, terms), or is a
   plain page enough?
3. **No JavaScript build:** agreed? It is what keeps the UI in this repo.
