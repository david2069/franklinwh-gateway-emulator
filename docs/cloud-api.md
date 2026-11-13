# FranklinWH Cloud API

The emulator can also serve the FranklinWH Cloud API: the REST API the FranklinWH app and
[franklinwh-cloud](https://github.com/david2069/franklinwh-cloud) use (#295). It is served from
the same gateways as Modbus TCP and Direct Connect, so a cloud client, a Modbus client and a
Direct Connect client all see one site. The fleet is one cloud account holding every gateway.

It never calls or forwards to the real cloud, and it listens on 127.0.0.1 only.

## Start it

Add `--cloud-port` to `ui`, `fleet` or `serve`:

```bash
franklinwh-emulator ui examples/fleet.yaml --cloud-port 8503
```

The start-up output adds a line with the address and the account:

```text
FranklinWH Cloud API on http://127.0.0.1:8503/  account=owner@example.com  (franklinwh-cloud: url_base="http://127.0.0.1:8503/")
```

From Python: `Fleet.start(cloud_port=0)` (0 = any free port), then `fleet.cloud.port`.

## Point a client at it

Change the base URL and nothing else. With franklinwh-cloud:

```python
from franklinwh_cloud.auth import PasswordAuth
from franklinwh_cloud.client import Client

client = Client(PasswordAuth("owner@example.com", "emulator"), "<serial>",
                url_base="http://127.0.0.1:8503/")
stats = await client.get_stats()
```

`<serial>` is the gateway's serial, as printed at start-up and listed by
`get_home_gateway_list()`.

!!! warning "franklinwh-cloud 0.6.5 and earlier log in at the real cloud"
    In those versions login ignores `url_base` (franklinwh-cloud#39, fixed after 0.6.5).
    With them, get a token from the emulator yourself and use `TokenAuth(token)`; see
    `tests/test_cloud.py`.

## The account

The fleet file's `cloud:` sets it:

```yaml
cloud: {account: owner@example.com, password: emulator}   # these are the defaults
```

Login takes the account and the password's MD5, as the app sends them. A wrong account or
password gets `code 401`. Tokens don't expire. Every other call needs the token in the
`loginToken` header; without a valid one the reply is `code 10009`, which franklinwh-cloud
answers by logging in again.

## What it serves

Phase 1a (#297):

| Call | What comes back |
|---|---|
| `POST hes-gateway/terminal/initialize/appUserOrInstallerLogin` | the token, user id and email |
| `GET hes-gateway/terminal/getHomeGatewayList` | one entry per gateway: `id` (serial), `name` (the fleet id), `status` (0 while the gateway's `offline` fault is on), `version`, `zoneInfo`, `countryId` (2 US, 3 AU) |
| `GET hes-gateway/terminal/getDeviceCompositeInfo?gatewayId=` | `currentWorkMode` (1 TOU, 2 self-consumption, 3 backup), `solarHaveVo.offGridFlag`/`offGridReason`, and `runtimeData`: power flows, SoC, today's energy, relays (`main_sw`), aPower serials, SoC and power, smart-circuit switches (`pro_load`), generator and V2L flags |
| `POST hes-gateway/terminal/sendMqtt`, `cmdType` 353 | the smart-circuit loads (`SW1ExpPower`, `SW2ExpPower`, `CarSWPower` and their energies); franklinwh-cloud's `get_stats` asks for them whenever a circuit is on |

Phase 1b (#299):

| Call | What comes back |
|---|---|
| `GET hes-gateway/terminal/selectOffgrid?gatewayId=` | `offgridSet` (off-grid requested), `offgridState` (islanded), `offgridSoc`; `get_stats` asks for it when the grid relay is open, so a requested off-grid reads `SIMULATED_OFF_GRID` and an outage `OUTAGE` |
| `sendMqtt` 211, `dataArea {"type": 1}` | the electrical readings for `get_stats(include_electrical=True)` and `get_power_info()`: `gridLineVol` (tenths of a volt), `gridVol1`/`gridVol2` (half the line each on a 230 V site, as the aGate reports them), currents, `gridFreq`, `dspSetFreq`, relays and run states. Types 2 and 3 (BMS) get `code 400` |
| `sendMqtt` 311 | the smart circuits' names and modes (`Sw1Name`, `Sw1Mode`, `SwMerge`…) |
| `sendMqtt` 203 | the composite's `runtimeData`; the reply's `cmdType` is 201, not 204, as the cloud answers it |

Phase 1c (#301), energy history:

| Call | What comes back |
|---|---|
| `GET api-energy/power/getFhpPowerByDay?gatewayId=&dayTime=YYYY-MM-DD` | the day's samples: `deviceTimeArray`, `socArray`, `kwhTotalArray` (energy stored), `runStatusArray` (0 idle, 1 charging, 2 discharging) and the flows in kW (`powerSolarHomeArray`, `powerSolarGirdArray`, `powerGirdHomeArray`, `powerFhpHomeArray`…; "Gird" is the cloud's spelling). Every 15 minutes; the cloud's are every 5 |
| `GET api-energy/electric/getFhpElectricData?gatewayId=&type=` | kWh per bucket: `kwhSuArray`, `kwhUtiInArray`, `kwhUtiOutArray`, `kwhFhpChgArray`, `kwhFhpDiArray`, `kwhLoadArray`, `kwhGenArray` and the home-from and battery-from splits. Type 1 is one day (`dayTime`); 2 a week (`startDate`, its Monday); 3 a month and 4 a year (`startDate`, any day in it); 5 one bucket per year since `commissioned` |

As on the cloud, a week that doesn't start on a Monday, or the wrong date key (`startDate` for
a day, `dayTime` for the others), gives empty arrays.

The history is the site's own quarter-hour record, the one Direct Connect's energy history
reads: from when the emulator started, or `backfill`ed, up to about 105 days. Earlier days
read as zeros. A day's totals equal Direct Connect's for that day. How the cloud splits the
flows (how much of the solar went to the home, and so on) is not established; the emulator
gives solar to the home first, then the battery, then the grid, and the battery to the home
before the grid.

Phase 2a (#303), writes. Each changes the same site state as the Modbus, Direct Connect and
control-API writes, so every interface reads the change:

| Call | What it does |
|---|---|
| `POST hes-gateway/terminal/tou/getGatewayTouListV2` | reads the modes: `currendId` (the active one), `list[]` with each mode's `workMode`, `oldIndex`, `soc` (its reserve), `minSoc` 5, `maxSoc` 100; `backupForeverFlag`, `nextWorkMode`, `timerStartTime`/`timerEndTime`, `stromEn` |
| `POST hes-gateway/terminal/tou/updateTouModeV2?workMode=&soc=…` | switches the mode (1 TOU, 2 self-consumption, 3 emergency backup) and sets that mode's reserve. Emergency backup takes `backupForeverFlag` 1 (until changed) or 2 with `durationMinute` (30–4320) and `nextWorkMode` |
| `POST hes-gateway/terminal/tou/updateSocV2?workMode=&soc=` | sets a mode's reserve; outside 5–100 gets `code 400` |
| `POST hes-gateway/terminal/updateOffgrid` (`{offgridSet, offgridSoc}`) | requests off-grid (or ends it) with its SoC floor |
| `sendMqtt` 311 with `dataArea.opt` 1 | the smart-circuit write: `Sw{n}Mode` switches a circuit, `Sw{n}SocLowSet`, `Sw{n}Name` |
| `GET hes-gateway/terminal/weather/getStormSetting?equipNo=` | `enableStorm` (as `set_mode` last sent it); storm writes come in 2c |
| `GET hes-gateway/terminal/selectTerPushMessageUnreadCount` | 0 |

The emergency-backup reserve is kept and read back but changes nothing: the site charges to
100 % in that mode. A fixed-time emergency backup switches to `nextWorkMode` at the first cloud
call after it ends, not by itself.

Every reply has the cloud's envelope, `{code, message, result, success}`; `code` 200 is success
and errors come with HTTP 200, as the cloud sends them. A path the emulator doesn't serve gets
HTTP 404 with the envelope, and another `sendMqtt` command gets `code 400`.

Still to come, in order (#295): TOU schedules (2b), storm, generator and
grid-limit settings (2c), and faults (expired tokens, rate limits, an offline gateway).

## Where the replies come from

Field names and the envelope follow franklinwh-cloud: its request code, its test fixtures and
its OpenAPI file (response shapes inferred from captured traffic). The values are the
emulator's own site model, not captured cloud data. `runtimeData` and the `sendMqtt` replies reuse
the Direct Connect payloads whose field names match the cloud's (1411 for 353, 1410 for 311,
1412 for 211 and `selectOffgrid`); whether the real cloud carries
the same values as Direct Connect is not established.
