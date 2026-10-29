# franklinwh-modbus-bridge integration plan

**Status:** waiting. Agreed with the "Modbus Bridge HA" session on 2026-10-07: no edits to
franklinwh-modbus-bridge or its worktrees until its branches have merged. Those branches are
`fix/no-phantom-default-gateway`, `feat/honest-mock-gateways`,
`feat/ha-supervisor-instance`, `feat/mqtt-optional-broker` and PR #1. The swap is then done on
a fresh worktree from `main`, either by the emulator side or by the bridge side; the owner
decides.

## The change

Replace the bridge's wire-less `MockPoller` / `synthetic_points` with an in-process emulator,
following the pattern franklinwh-local-bridge's `mockgw.py` uses for Direct Connect. Mocks then
exercise the real poller, catalog, `franklinwh-modbus`, command handler and MQTT path, and the
bridge's copy of the synthetic model can be deleted.

```python
from franklinwh_emulator import Gateway, GatewayConfig, SiteConfig

gw = Gateway(GatewayConfig(
    id=gw_id, seed=n,
    quirks={"tou_reserve_mirrors_self": False},          # 15509 holds the TOU reserve
    site=SiteConfig(ac_type=ac_type, apower_units=units)))
host, port = gw.start()          # register 127.0.0.1:<port> as the gateway host
...
gw.stop()
# inside the bridge's event loop: await gw.start_async() / await gw.stop_async()
```

Files on the bridge side (theirs): `gateway/mock_gateway.py`, `gateway/instance.py`,
`gateway/registry.py`, and possibly `api/gateways_api.py` and `main.py`.

## Acceptance checks (behaviour the bridge's mock has today, which must be kept)

1. HA select/number controls for mode and reserves: the emulator acknowledges and discards the
   15507–15509 writes, as the aGate does (franklinwh-modbus firmware_quirks
   `ext_15507_15509_writes`), so the bridge must show the setting as not applied. To test a
   mode or reserve change, set it from the test side (`gw.site.set_native_mode`, `set_reserves`).
2. Force Charge reaches −5 kW at max power (WSetPct −1000 → `714.DCW` −5000 → bridge
   `battery_power_w` −5000).
3. Force Release results in no dispatch: WSetEna 0, and the battery returns to native behaviour.
4. Discharge stops at the active mode's reserve, including under VPP.
5. `loc_rem_ctl_name` reads Local.
6. MQTT `franklinwh/<sid>/control/<slug>/set` is routed per device.
7. The Controls tab and Force Release post to `/api/gateways/<viewed>/command` and hit the
   viewed gateway, not the default one.
8. One client per gateway: the default profile serves `max_connections=1`. If anything other than
   the poller probes a mock, use `quirks={"max_connections": None}`.

## Semantics the two sides agreed

- `send_command(BatteryCommand(power_watts=W))`: positive = charge. The bridge then writes
  `WSetRvrtTms` to hold VPP. Release = `reset_control_state(handshake_wait_s=0)`, then
  `WSetRvrtTms=0`, `WSetEnaRvrt=0`.
- Native modes: 1 Emergency Backup, 2 Self-Consumption, 3 TOU. No other 15507 value has been read (franklinwh-modbus#75).
- Bridge sample convention: `battery_power_w` negative = charging.
- `ac_type`: 0 single (230 V / 50 Hz), 1 split L1+L2 (240 V / 60 Hz, US aGate), 2 three phase.
- The bridge's planned LAN setup wizard should probe PDU 0, then 40000, then 50000, and cope
  with "port open but session held". The emulator's `quirks={"busy_behaviour": "hang"}` plus
  `faults.session_held` reproduces that.

## Known consumers of the emulator's behaviour (keep stable)

- **franklinwh-modbus `discovery` (probe/scan), PR david2069/franklinwh-modbus#15.** It was
  verified against this emulator on 2026-10-07 (10/10 checks), by the Modbus Bridge HA session
  in a scratch venv, without changes to this repo. The checks were:
  - base 0, and a re-probe straight afterwards (the session is released);
  - a second client while the session is held → no response;
  - the `session_held` and `offline` faults;
  - `busy_behaviour: hang` (the probe returns within 1 s);
  - `base_aliases: [40000]`;
  - a closed port.
  
  The probe holds the session for two reads (the marker, then one 64-register nameplate
  read). The bridge's setup wizard will use it, so these fault modes are its test fixtures.
  **Changes to session handling, busy behaviour, aliases or fault semantics must be
  coordinated.**
