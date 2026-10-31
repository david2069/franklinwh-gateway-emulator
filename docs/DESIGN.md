# Design

How the emulator is put together, and why. The README covers usage; this document is the
reasoning. [FINDINGS.md](FINDINGS.md) records what was learned about the real devices and
libraries, and [BRIDGE_INTEGRATION.md](BRIDGE_INTEGRATION.md) is the plan for the bridge.

## Starting point (2026-10-07)

Four existing repos were evaluated:

| Repo | What it had | Relevance |
|---|---|---|
| [franklinwh-direct-connect-api](https://github.com/david2069/franklinwh-direct-connect-api) | `emulator.py`: a threaded socket server for the port-9000 protocol. `synthetic.py`: `SyntheticSite`, a per-seed site (serial, solar/load curves, multi-aPower, round-tripped writes). | Good site *ideas*; the server speaks only the DC framing. |
| [franklinwh-local-bridge](https://github.com/david2069/franklinwh-local-bridge) | `mockgw.py` runs the DC `Emulator` in-process on `127.0.0.1:<ephemeral>`. | The integration pattern to copy: mocks go through the real client. |
| [franklinwh-modbus-bridge](https://github.com/david2069/franklinwh-modbus-bridge) | `gateway/mock_gateway.py`: `MockPoller` emits `Sample`s straight onto the bus. | **Never touches the wire**, so the poller, catalog, library and command path go untested for mocks. |
| [franklinwh-modbus](https://github.com/david2069/franklinwh-modbus) | No emulator; `conftest.py` mocks the client. It has the full scan, the quirks doc and the PICS cross-reference. | The material a faithful Modbus emulator needs. |

`SyntheticSite` and the Modbus bridge's `synthetic_points` were already two copies of one model
(the former says it is a port of the latter). A third copy was the outcome to avoid, so this repo
owns the canonical, stateful model and the protocol frontends sit on top of it.

### Reuse or start from scratch?

The answer was a hybrid:

- **Reused:** the ideas in `SyntheticSite`: per-seed determinism, the solar bell, the load
  gaussians, the serial scheme (serials are identical per seed, see `mock_serial`) and multi-aPower.
- **Not reused as a base:** the DC `Emulator` server, since it is protocol-specific. Adding Modbus
  to the DC repo would also have pulled pymodbus/pysunspec2 into a small client library.
- **Rewritten:** the site model is integrated rather than time-of-day curves. SoC moves with power,
  power is bounded by SoC, and the lifetime counters only grow. Modbus control is closed-loop: a
  VPP charge has to raise SoC and pull from the grid.

### Multi-protocol

A real aGate serves Modbus TCP on 502 **and** Direct Connect on 9000
(`AGATE_NETWORK_PROFILE.md` lists 9000 as "unknown"; it is the DC broker). Each frontend
therefore renders the **same `Site`**, which keeps cross-protocol behaviour consistent:
for example a `1205 sunsMdEn` write closing :502, or a CSIP control curtailing a Modbus VPP
dispatch.

### Multi-gateway

Once the site is an object, more gateways are cheap. `Fleet` runs N gateways on one event-loop
thread, each with its own `Site` (no shared state) and its own port. Port-per-gateway works on
macOS and Linux, and both bridges accept `host:port`. Per-gateway IPs (Docker macvlan) remain a
roadmap item for LAN-discovery tests.

Model detail for solar and load lives in [WEATHER_AND_LOADS.md](WEATHER_AND_LOADS.md).

## Components

```
site.py         Site: closed-loop physics, grid, protection, grid-support, CSIP client hookup
gridcodes.py    GridCode: trip stages, enter-service, freq-watt, volt-watt, volt-var, export limits
csip.py         CsipClient: 2030.5 client state, DERControl events, default + comms-loss fallback
sunspec.py      SunSpecImage: the 'SunS' chain built from pysunspec2 model definitions
device.py       AGateDevice: register space bound to the Site; write semantics per profile
profiles.py     Profile: quirk switches (faithful aGate vs spec SunSpec DER)
server.py       ModbusServer: asyncio Modbus TCP + fault injection
directconnect.py DirectConnectServer: TCP/9000 framing via franklinwh_direct_connect_api.protocol
dc_payloads.py  DcPayloads: one builder per cmdType, rendered from the Site
gateway.py      Gateway / Fleet: in-process API, threads, sunsMdEn toggle
control.py      ControlApi: HTTP JSON control plane (loopback); `apply()` is the shared action vocabulary
loads.py        LoadModel: appliance components, presets, smart circuits
replay.py       Replay: recorded solar/load/ambient from bridge CSV or SQLite; compare()
weather.py      Weather: bell / synthetic sky / Open-Meteo
scenario.py     Scenario/Runner: timed steps, expectations, invariants, recording (offline + live)
scenarios/      built-in scenario library (YAML)
cli.py          serve / fleet / ctl
tools/gen_baseline.py   scan text → baselines/*.json
```

### Register image from pysunspec2 definitions

Every model is a real `sunspec2.device.Model` built at the **exact length and repeating-group
counts of the reference scan**. 705–712 need `NPt`/`NCrv`/`NCrvSet`/`NCtl` seeded, and 714 needs
`NPrt`. Offsets are therefore correct by construction: WSetEna = 318 and ControllerHb = 1092,
the addresses the quirks doc cites, and the chain ends at 1127. Static values come from a
committed baseline generated from the scan (`tools/gen_baseline.py`), which divides scaled
values back by their scale factors. Points the scan does not mention are served as SunSpec
"unimplemented".

Writes are decoded back to `(model, point)`. A partial write to a multi-register point is
merged with the point's current registers first. The device layer then decides whether each
write is applied, discarded (acknowledged but ignored) or rejected.

### Why a hand-rolled Modbus server

pymodbus 3.15 (current at the time) was midway through replacing its datastore API: the old
classes were deprecated in favour of `SimDevice`, which has a single action hook and in-place
write semantics. The emulator's job is exact control over the wire:

- writes acknowledged and then silently discarded;
- one client at a time, with "close" or "hang" for the next;
- any unit id answered the same;
- latency, dropped responses and connection resets;
- single-segment replies, because the library's raw-socket paths do one `recv()`.

About 150 lines of asyncio covering FC 3/4/6/16 do this with no server dependency. pymodbus
remains a *test* dependency for the client side.

### Site model

- Sign conventions: battery + = discharge (matches the aGate's `714.DCW`); grid + = import
  (matches the library's reading of `701.W`); load = solar + grid + battery, exactly, per leg too.
- Integration: sub-steps of at most 60 s, refined to 0.1 s while a protection trip or
  reconnect timer is running.
- Clocks: system, scaled (fast-forward) or manual (tests). All physics runs on UTC epoch
  seconds; the wall clock (TOU, load shape, midnights, `1202`) comes from the gateway's IANA
  `location.timezone` via `zoneinfo`, so DST transitions are real.
- Native modes 1–4. Discharge stops at the active mode's reserve, including under VPP. Off-grid
  it stops at the off-grid floor (5 %).
- Phases: SunSpec ACType 0/1/2 plus a list of wired legs. L-L voltages come from phasors
  (120° or 180°). Islanded, each leg's output follows its own load.
- Order of grid support when connected: storage window (frequency-watt, volt-watt) →
  SoC limits → PV curtailment (over-frequency, volt-watt) → CSIP controls → enforced export
  limit (battery discharge first, then PV).

### Quirk profiles

`agate-v10r01b04d00` reproduces the documented firmware behaviour (see the README). `spec` is a
well-behaved SunSpec DER, so clients can be tested against both. Every flag can be overridden per
gateway.

### Direct Connect frontend

Uses `franklinwh_direct_connect_api.protocol` for framing (an optional dependency, `[dc]`) and the DC
library's `SyntheticSite` only for static identities. Every live value is rendered from the
emulator's `Site`, so a DC read and a Modbus read at the same instant agree. The site gained
daily totals and quarter-hour history for `1301`/`1303`. Error semantics copy the real device:
no reply to wrong opts, `reason:-1/-2/4`, and a dropped connection for out-of-band codes.
Details are in [DIRECT_CONNECT.md](DIRECT_CONNECT.md).

## Verification done

- 180 pytest tests, CI on Python 3.10, 3.12 and 3.13, including all 15 built-in scenarios.
- Replay round trip: a recorded `sydney-family` day replayed onto another date tracks it to
  battery MAE 0.9 W and SoC MAE 0.0 %.
- Weather: Open-Meteo replay energy 32.0 vs 31.9 kWh raw, peak 5.72 kW vs 5.70 kW observed on
  the reference site, and window-mean error median 0.8 % (see
  [WEATHER_AND_LOADS.md](WEATHER_AND_LOADS.md)).
- DST: `sydney-dst-end` passes with `Australia/Sydney` and fails with a fixed +11 offset (a
  negative control in tests).
- Loads: the library's `set_smart_circuit` write over Direct Connect switches a circuit and the
  bound load disappears from the `1411` meter.
- The **unmodified `franklinwh_local.LocalClient`** logs in and drives power_flow, energy_history,
  mode list/set, off-grid, der_comms (incl. 2030.5 registration and `sunsMdEn` closing :502),
  solar_pv, the per-aPower reads, relays and reboot. Cross-checked against Modbus registers at
  the same instant. The `franklinwh-local` CLI was also run live against the emulator.
- The **unmodified `FranklinWHController`** connects, scans all 17 models, reads the nameplate,
  battery, grid, solar and native mode, concludes its extension-write probe is "read-only",
  sends `+3000 W` (WSetPct −600 on the wire, battery −3000 W) and resets. Its mode and reserve
  writes are acknowledged and not applied.
- Live CLI runs: fleet of 6 layouts; outage, alarm and reset via `ctl`; Ausgrid export clipping
  and breach tracking; CSIP registration and a 1.5 kW event curtailing a Modbus VPP dispatch;
  `modbus off` closing the port.
- The SFDI algorithm is checked against the IEEE 2030.5 worked example
  (LFDI `3E4F45AB…23E5` → SFDI 167261211391).
- Serial parity with `SyntheticSite` is checked for several seeds.

## Known limitations

- Islanded, alarm, three-phase and US register behaviour are **unverified** on real hardware
  (the only scans are from a single-phase Australian unit, on-grid, with `Alrm` = 0).
- Grid-support response timing uses the standards' envelopes, not the aGate's own settings:
  - AU: a 1 s dead time and 10 s settling, a 15 s soft export limit and 0.1 Hz hysteresis;
  - IEEE: 1547 open-loop response times.

  The aGate declares 0–0.1 s in its curve registers (the `agate-declared` preset); the real
  dynamic response is unmeasured. Ordinary setpoint changes (VPP) still apply without a ramp.
- IEEE 1547 values are from memory of the default tables; Australia B/C and NZ are not modelled.
- The emulator does not speak IEEE 2030.5; it models the client's behaviour only.
- Curve contents of 705–712 are not captured in the scan and are left zero/unimplemented.
- Solar-divert EV charging takes PV surplus ahead of the battery; export-only divert is not
  modelled.
- Gateways without `location.timezone` use the host's zone. The development Mac reported
  UTC+13 (New Zealand daylight time) on 2026-10-07, so always set a location for realistic
  local-time behaviour.
- Smart-circuit schedules (`SwXTime*`) are acknowledged and discarded, as the real device does
  for local writes.
