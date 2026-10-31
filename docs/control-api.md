# Control API and `ctl`

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
