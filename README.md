# franklinwh-gateway-emulator

An emulator of the FranklinWH **aGate** for testing without hardware. It serves the **Direct
Connect** local protocol (TCP/9000) and **Modbus TCP** (SunSpec chain plus FranklinWH
extension registers), both in front of one **closed-loop site model**, so control commands
actually move the battery and both ports always agree. It can run one gateway or a **fleet** of
them.

**As on a real aGate, Modbus TCP starts off.** Every aGate runs a Direct Connect listener; Modbus
TCP comes on when a Direct Connect `1205` `sunsMdEn` write enables it (about 3 s later). That is
`setup: factory`, the default. A gateway that is already set up is `setup: in-service`: Modbus TCP
on from the start. `modbus_enabled: true|false` overrides either.

Built for [franklinwh-modbus](https://github.com/david2069/franklinwh-modbus) and
[franklinwh-modbus-bridge](https://github.com/david2069/franklinwh-modbus-bridge). The
unmodified `FranklinWHController` connects, scans, reads and dispatches against it
(see `tests/test_franklinwh_modbus.py`).

> **Unofficial, and an emulation: it can never be 100% accurate.** See the
> [Disclaimer](#disclaimer). It emulates documented and observed behaviour of the reference
> aGate's firmware. Where the sources disagree, the choices are in
> [What is emulated](docs/emulated.md).

## Disclaimer
<!-- --8<-- [start:disclaimer] -->

> **UNOFFICIAL SOFTWARE — NOT AFFILIATED WITH OR ENDORSED BY FRANKLINWH.**
>
> This is an **emulator**, not an aGate. It imitates FranklinWH's undocumented **Direct Connect**
> local API (reverse-engineered by third parties) and its **SunSpec Modbus TCP** API (with
> undocumented FranklinWH extensions), from incomplete documentation and observations of a few
> real devices. It **can never be 100% accurate**: it may behave differently from a real aGate,
> now or in the future, and may have bugs, errors or omissions a real aGate doesn't have.
> Software that works here may still fail, or do harm, on real hardware. **Verify on real
> hardware before relying on any result.**
>
> Provided **AS-IS**, without warranty of any kind. Use is entirely at your own risk; by using it
> you accept these limitations unreservedly. The authors accept no liability for any consequence,
> including anything done to real hardware by software developed or tested with it.
>
> **Do NOT contact FranklinWH support** about this emulator. Raise issues at
> <https://github.com/david2069/franklinwh-gateway-emulator/issues>.
>
> **MIT License**, with the additional notices in [LICENSE](https://github.com/david2069/franklinwh-gateway-emulator/blob/main/LICENSE).

### Compliance & anti-circumvention

> The emulator doesn't connect to a real aGate, but what you build with it may. Keep your gateway
> and battery settings, controls and operation compliant with the configuration set through the
> official FranklinWH mobile app and the installer/support portal. FranklinWH-managed settings
> may exist to protect the battery and gateway, apply software updates, enforce your local grid
> profile and import/export limits, and enforce incentive, subsidy or VPP/grid-programme
> restrictions. Bypassing or overriding any of them, deliberately or inadvertently, is entirely
> at your own risk, and is neither condoned nor encouraged by the authors.
<!-- --8<-- [end:disclaimer] -->

## Quick start

```bash
git clone https://github.com/david2069/franklinwh-gateway-emulator.git
cd franklinwh-gateway-emulator
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Python 3.10 or later
.venv/bin/franklinwh-emulator ui examples/fleet.yaml      # web UI: open http://127.0.0.1:8502/
.venv/bin/franklinwh-emulator serve --port 5020 --dc-port 9000 --setup in-service   # one gateway
.venv/bin/python -m pytest
```

![The emulator's web UI](docs/assets/ui/overview.png)

## Documentation

**The full docs:** <https://david2069.github.io/franklinwh-gateway-emulator/>. They are built
from [`docs/`](docs/), so these links also work on GitHub:

| | |
|---|---|
| [Getting started](docs/getting-started.md) | install, run one gateway or a fleet, use it from Python |
| [Model baseline](docs/model-baseline.md) | the hardware, and how location, weather, placement and controls drive the model, with a diagram |
| [Configuration](docs/configuration.md) | describe each site: aGate, aPowers, PV, grid, accessories, location; where it's saved |
| [Web UI](docs/ui.md) | the optional web page, with screenshots |
| [What is emulated](docs/emulated.md) | protocols, profiles and the choices where sources disagree |
| [Grid, grid codes, export limits and phases](docs/grid.md) | outages, trips, CSIP-AUS, phases |
| [The site model](docs/site-model.md) | TOU, energy, location and time, weather, loads, generator |
| [Control API and `ctl`](docs/control-api.md) | drive the emulator from scripts and tests |
| [Scenarios](docs/SCENARIOS.md) · [Replay](docs/replay.md) | timed, checked test runs; recorded data |
| [Direct Connect](docs/DIRECT_CONNECT.md) · [Design](docs/DESIGN.md) · [Calibration](docs/CALIBRATION.md) · [Findings](docs/FINDINGS.md) | reference |
| [Changelog](CHANGELOG.md) · [Backlog](BACKLOG.md) | |

Local preview: `pip install mkdocs-material && mkdocs serve` (live reload at http://127.0.0.1:8000/).
