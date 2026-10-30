# Direct Connect always on; Modbus TCP off on a factory aGate (#40)

**Basis:** owner, 2026-10-30: "Every aGate must have DC Listener by default. Modbus TCP is NOT
ON by default - there needs be a DC API command issued to turn on", and "we need preconfigured
aGates where things ARE ENABLED ... default - ready to configure and existing setup".

**Changed:** `GatewayConfig.setup` (`factory` default | `in-service`), `modbus_enabled`
override; aGate profiles get `dc_port` 0 when unset; `_start_all` starts Modbus only when
enabled; `serve --setup`; start-up output says how to enable Modbus; Direct Connect library a
standard dependency; example fleet (in-service defaults, DC 9020–9026, factory `fresh`, `site`
loads); UI card "Modbus TCP off"; README, BRIDGE_INTEGRATION, DIRECT_CONNECT, UI_TESTING,
CHANGELOG. Tests and examples that talk Modbus from the start now say `setup: in-service`
(conftest default, 13 constructions, 4 fleet dicts, 2 examples).

**Tests:** new in `test_directconnect.py`: a factory aGate has a Direct Connect listener and no
:502 until the real `1205` write through `franklinwh_local.LocalClient`, then serves; the default
setup is factory; setup × override × profile (5 cases); an unknown setup is refused. Example
fleet test counts 7 gateways. Full suite: **433 passed** (Python 3.12, local).

**Break check:** default `setup` set back to `in-service` → `test_default_setup_is_factory`
failed; restored.

**Rendered (local, headless Chrome, example fleet on ephemeral ports):** 7 cards; `fresh` shows
"Modbus TCP off" with a Direct Connect address; `spec` "Direct Connect off"; no console errors.
