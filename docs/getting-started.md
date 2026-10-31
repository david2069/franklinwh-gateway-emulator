# Getting started

Install, run one gateway or a fleet, and use the emulator from Python.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/franklinwh-emulator serve --port 5020 --dc-port 9000 --seed 7   # factory: Modbus off until 1205
.venv/bin/franklinwh-emulator serve --port 5020 --dc-port 9000 --setup in-service --grid-code ausgrid
.venv/bin/franklinwh-emulator fleet examples/fleet.yaml
.venv/bin/franklinwh-emulator scenario run outage-during-vpp --out results/   # timed + checked
.venv/bin/python -m pytest
```

In-process (the same pattern franklinwh-local-bridge uses for its Direct Connect mocks):

```python
from franklinwh_emulator import Gateway, GatewayConfig, Fleet

gw = Gateway(GatewayConfig(id="site", seed=7, setup="in-service"))   # Modbus TCP on
host, port = gw.start()                 # background thread, ephemeral ports
gw.site.load_override_w = 4000          # steer the scenario
gw.faults.session_held = True           # inject faults
gw.stop()

with Fleet.from_yaml("examples/fleet.yaml") as fleet:
    fleet["site"].address
```

Inside an asyncio app, use `await gw.start_async()` / `await gw.stop_async()` to serve on
your own loop.

To check a fleet or scenario file without starting anything, `validate_fleet` and
`validate_scenario` return every problem with its path (`[]` when clean):

```python
from franklinwh_emulator.gateway import load_yaml
from franklinwh_emulator.validate import validate_fleet
validate_fleet(load_yaml("my-fleet.yaml"))
# [{'path': 'gateways[1].modbus', 'error': 'port 5020 already used by site (modbus)'}, ...]
```

Every gateway key, with its type, default and meaning, is in `validate.config_schema()` and in
`GET /schema` under `gateway_config`.

Runnable examples (tested in `tests/test_examples.py`, standard library plus the emulator):

| File | Shows |
|---|---|
| `examples/single_gateway.py` | one aGate: start, read SoC, battery, grid, solar and load over Modbus TCP, stop |
| `examples/fleet_with_control.py` | every gateway in `examples/fleet.yaml`, plus the control API cutting the grid on one |
| `examples/conftest_for_clients.py` | a pytest fixture for client repos that records the emulator version and profile |

What an emulator run does and does not verify is listed in `CLAUDE.md` ("Critical changes need
real verification").
