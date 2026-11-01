# Control API and `ctl`

`serve` and `fleet` start an HTTP control API on `127.0.0.1:8502` (`--control-port`,
`--no-control`). It is unauthenticated, so keep it on loopback.

Commands name a gateway by its id. `serve` names its one gateway `agate` (`--id`); a fleet uses
each entry's `id`. The examples below use `agate`, as `serve` starts it; with
`examples/fleet.yaml`, use `site` (or any id from `GET /gateways`).

```bash
franklinwh-emulator ctl agate status
franklinwh-emulator ctl agate outage
franklinwh-emulator ctl agate restore
franklinwh-emulator ctl agate solar 6000          # or: solar auto
franklinwh-emulator ctl agate fault session_held=true
franklinwh-emulator ctl --json agate status       # full state

curl -s -X POST localhost:8502/gateways/agate/grid -d '{"hz": 51.3}'
curl -s -X POST localhost:8502/gateways/agate/grid -d '{"voltage": 170, "phase": "L3"}'
```

Every action, with its fields, units, allowed values and examples, is in
`src/franklinwh_emulator/schema.py`, and a running emulator serves the same table at
`GET /schema`. Unknown actions get 404; unknown or missing required body keys get 400. In-process code calls the
same methods on `gw.site`, and `Fleet.start(control_port=0)` starts the API alongside a fleet.

**Monitor.** `GET /monitor` shows who is connected and how the process is coping: for each
gateway, the Modbus TCP and Direct Connect clients connected now (address and port, seconds
connected, requests), refusals in the last hour (a second Modbus client while one holds the
session, or the `offline` fault) and requests per second; overall, the gateways, those with
clients, TCP connections, refusals and requests per second; and the process: CPU % of one core
since the previous read, memory now and at peak, threads, asyncio tasks, event-loop lag and
uptime. Memory now comes from psutil when installed, else `/proc` or `ps`.

**Events.** `GET /gateways/<id>/events` is what that gateway did on its own (the action log,
`GET /log`, is what was asked): `{level, dropped, events: [{seq, rev, at, site_time, kind, text,
count, last_at, last_site_time}]}`, oldest first. `?since=<rev>` returns only entries added or
folded into since that revision; `?limit=` caps the count (default 200; the log keeps 1000).

**Groups.** aGates in a fleet group (one site and grid connection, one per phase; see
[Configuration](configuration.md), #241) share the grid: a `grid` action on any member reaches
them all, `phase` picks the member on that leg, and frequency is common to all. Each member's
state has `group: {id, phase, members: {L1: <id>, …}}`. `GET /groups` (or `/groups/<id>`) is
the site as one: per phase the aGate, `grid_w`, `load_w`, `solar_w`, `battery_w`, `soc`,
`grid_available` and `main_breaker_open`; `totals` (the service meter: their sums); and
`service: {main_breaker_a, phases, v_ll, label}`, e.g. `3 × 63 A · 400 V`.

**Sign conventions on the wire:** `701.W` positive = grid import; `714.DCW` positive =
battery discharging.
