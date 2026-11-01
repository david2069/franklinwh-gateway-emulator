# Checking files and startup output

Check a file before running it, and read what the emulator prints when it starts.

## `validate`

```bash
franklinwh-emulator validate my-fleet.yaml
franklinwh-emulator validate my-fleet.yaml ~/metrics_30d.csv emulator-workspace/sessions/*.yaml
```

`validate` checks each file and starts nothing: no listener, no control API. It finds every
problem in a file, not only the first. Exit status: 0 when every file is clean, 2 when any file
has a problem.

The file type comes from the suffix and the top-level keys:

| File | Recognised by | What is checked |
|---|---|---|
| Fleet | `gateways:` | every key, every value, models, profiles, grid codes, duplicate ids, ports, seeds and serials, each gateway's `replay:` recording; each site is built as starting it would |
| Scenario | `steps:` or `fleet:` | its fleet (as above), the start time, each step's gateway, action and fields |
| Session | `actions:` (a saved session, `emulator-workspace/sessions/<name>.yaml`) | each action's gateway (it must be in the session's `gateways:`), action name and fields |
| Recording | `.csv`, `.db`, `.sqlite`, `.sqlite3` | that it opens and has a timestamp and at least two rows; what it holds |

`ui` and `fleet` run the same check before starting. If a fleet file has a problem, they print
every problem and exit 2 without starting any gateway.

### Output

A clean file prints one line with what it holds:

```text
my-fleet.yaml: fleet ok (7 gateways)
outage.yaml: scenario ok (11 steps)
session-20261101.yaml: session ok (14 actions)
rec.csv: recording ok (3 rows, 2026-10-01T00:00:00+00:00 to 2026-10-01T02:05:00+00:00 (2.1 h), largest gap 7200 s (1 over max_gap), columns solar_w, home_w)
```

A file with problems prints a count, then one line per problem: `file: path: what is wrong`,
followed by what is allowed where that helps:

```text
my-fleet.yaml: fleet: 6 problems
my-fleet.yaml: gateways[0].colour: unknown key (every key: docs/configuration.md, or GET /schema gateway_config)
my-fleet.yaml: gateways[1]: unknown profile 'agate-v9'; choose from ['agate-v10r01b04d00', 'agate-v12r02b60d00-candidate', 'spec']
my-fleet.yaml: gateways[1].id: duplicate of gateways[0]
my-fleet.yaml: gateways[1].seed: gives serial MOCK0001EFC63F0CD0E8, as gateways[0]; clients keyed by serial would collide
my-fleet.yaml: gateways[2].replay.use: 'ambient' plays ['ambient_temp_c'], which has no values in rec.csv; columns with values: ['solar_w', 'home_w']
my-fleet.yaml: gateways[2].directconnect: port 5020 already used by shed (modbus)
```

The path names the entry: `gateways[1].seed` is the second gateway's `seed`; `steps[3].with`
is the fourth step's fields; `actions[1].gateway` is the second action's gateway. A YAML syntax
error gives the line and column instead (`line 3, column 1: not valid YAML: …`). An unknown key
suggests the closest known key when one is close (`unknown key; did you mean 'seed'?`).

### Recordings

A recording is a replay source ([Replaying recorded data](replay.md)): the Modbus bridge's
CSV export or its SQLite store. It is checked on its own (`validate rec.csv`), or as part of a
fleet through each gateway's `replay:`. A relative `source` is read from the current
directory.

| Problem | Message |
|---|---|
| The file is missing | `replay.source: no such file: …` |
| No timestamp | `replay.source: …: no timestamp column ('timestamp' or 'ts')` |
| Fewer than two rows | `replay.source: replay source … has fewer than two rows` |
| `use:` plays a column with no values | `replay.use: 'ambient' plays ['ambient_temp_c'], which has no values in …; columns with values: […]` |
| Unknown `use:` entry or `align:` | `replay: replay.use: unknown ['wind'] (ambient, battery, dispatch, load, mode, solar)` |
| `columns:` maps an unknown field | `replay.columns.<name>: unknown column; known […]` |

The columns each `use:` entry needs: `solar` → `solar_w`; `load` → `home_w`; `ambient` →
`ambient_temp_c`; `battery` → `battery_w`; `dispatch` → `battery_w` and `soc`; `mode` →
`mode_name`.

A clean recording reports its rows, its first and last time (UTC) and span, the largest gap
between rows, and how many gaps are longer than `max_gap` (10 min by default; the emulator's
own weather and load models fill those). It also lists the columns that have values.

From Python: `validate_file(path)` returns `(kind, problems, info)`. `validate_fleet`,
`validate_scenario`, `validate_session` and `check_recording` check one kind each
(`franklinwh_emulator.validate`).

## What is printed at start

`serve`, `fleet` and `ui` print one or two lines per gateway, then the control API (and the UI)
address. The examples were printed by `serve --port 0 --dc-port 0 --control-port 0`, which picks
free ports.

A factory gateway (`setup: factory`, the default for `serve`):

```text
agate: Modbus TCP off (factory; enable with Direct Connect 1205 sunsMdEn)  serial=MOCK00073B1612DD272D  profile=agate-v12r02b60d00-candidate  grid=as4777-au-a  legs=L1  modbus_max_connections=1
agate: Direct Connect on 127.0.0.1:58622  dc_max_connections=None
control API on http://127.0.0.1:58625/gateways
```

An in-service US split-phase gateway (`--setup in-service --ac-type 1`):

```text
agate: Modbus TCP on 127.0.0.1:58623  serial=MOCK0008476C0EFECF6C  profile=agate-v12r02b60d00-candidate  grid=ieee1547-cat2  legs=L1,L2  modbus_max_connections=1
agate: Direct Connect on 127.0.0.1:58624  dc_max_connections=None
control API on http://127.0.0.1:58626/gateways
```

| Field | Meaning |
|---|---|
| `agate:` | the gateway's id (`--id`, or the fleet entry's `id`) |
| `Modbus TCP on HOST:PORT` | Modbus TCP is listening there |
| `Modbus TCP off (factory; …)` | not listening yet: a factory gateway serves only Direct Connect until a 1205 `sunsMdEn` write turns Modbus TCP on, about 3 s later |
| `serial=` | the Modbus Model 1 `SN` and Direct Connect `IBG_SN`; `MOCK` plus 16 characters, from `seed` unless `serial` is set |
| `profile=` | the firmware profile ([What is emulated](emulated.md)) |
| `grid=` | the grid code (`grid_code`), e.g. `as4777-au-a`, `ieee1547-cat2` |
| `legs=` | the energised legs or phases: `L1` single phase, `L1,L2` split phase, `L1,L2,L3` three phase |
| `modbus_max_connections=` | Modbus TCP clients served at once; a further client is refused (`None` = no limit) |
| `Direct Connect on HOST:PORT` | the Direct Connect listener (every aGate profile runs one; `spec` only when a port is given) |
| `dc_max_connections=` | Direct Connect clients served at once (`None` = no limit, the default; the aGate's limit is unmeasured) |
| `control API on http://…/gateways` | the control API ([Control API and ctl](control-api.md)); absent with `--no-control` |
| `UI on http://…/` | `ui` only: the web UI, on the same port as the control API |
| `restored session NAME: N actions applied, M failed` | `ui … --restore NAME` only, followed by one line per failed action: `GATEWAY ACTION {fields}: error` |

Log lines follow as `time LEVEL logger: message`; `-v` adds debug detail.
A fleet file with problems prints them as `validate` does, and nothing starts.
