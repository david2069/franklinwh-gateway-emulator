# FranklinWH Cloud API

The emulator can also serve the FranklinWH Cloud API: the REST API the FranklinWH app and
[franklinwh-cloud](https://github.com/david2069/franklinwh-cloud) use (#295). It is served from
the same gateways as Modbus TCP and Direct Connect, so a cloud client, a Modbus client and a
Direct Connect client all see one site. The fleet is one cloud account holding every gateway.

It never calls or forwards to the real cloud, and it listens on 127.0.0.1 only unless you choose another address with `--cloud-host`.

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

It listens on loopback (127.0.0.1) only, unless `--cloud-host` names another address (#316),
for example the Mac's Tailscale address so that HTTP Toolkit on another machine can forward
the official app to it. `Fleet.start(cloud_host=…)` does the same from Python. On any address
other than loopback the start-up output warns:

```text
warning: the Cloud API listens on 0.0.0.0, not loopback: anyone who can reach it and knows the account can read and change the emulated gateways
```

The account in `cloud:` is the only protection (there is no HTTPS), so use it only on a
network you trust.

## Point a client at it

Change the base URL and nothing else. For a client with no base URL setting, or the official
app, see [Cloud API clients](cloud-clients.md). With franklinwh-cloud:

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

## Seeing it in the UI

#314. The Cloud API shows the way Modbus TCP and Direct Connect do:

* **Card and detail:** `Cloud API 127.0.0.1:8503` under the other two listeners, or `Cloud API
  off` without `--cloud-port`. A cloud fault adds a `cloud: …` badge and colours the card's
  border, as the other listeners' faults do.
* **Monitor:** a "Cloud API clients" column. Each HTTP request is its own connection, so a client
  here is a login session (a token). It connects with its first request for the gateway and
  leaves after 5 minutes without one. It is listed by host and by the app version it claims (its
  `softwareversion` header; franklinwh-cloud sends `APP2.4.1` unless told otherwise). A 429
  counts as refused. Login and the gateway list name no gateway, so they count only in the
  totals line (`Cloud API N logins, M requests`). `franklinwh-emulator monitor` prints the same.
* **Events:** `Cloud API client <host> connected`, `left` and `refused`, in the gateway's event
  log.
* **Calls not emulated yet** (#316): each path the emulator doesn't serve (HTTP 404) and each
  `sendMqtt` command it doesn't handle (`code 400`) is counted. The Monitor lists them under the
  table, most-called first, and each gateway's column says how many. A gateway's event log
  shows `Cloud API call not emulated: GET /hes-gateway/terminal/…`, with repeats folded.
  `franklinwh-emulator monitor` lists them too. Point the official app at the emulator and this
  list is what the app asked for that isn't served yet.

The emulator answers every app version the same way, since differences between versions aren't
established.

## The account

The fleet file's `cloud:` sets it:

```yaml
cloud: {account: owner@example.com, password: emulator}   # these are the defaults
```

Login takes the account and the password's MD5, as the app sends them. A wrong account or
password gets `code 401`. Tokens don't expire unless the fleet file sets `cloud: {token_ttl_s:
N}`. Every other call needs the token in the
`loginToken` header; without a valid one the reply is `code 401`, `Please enter a valid user
authorization token!` and no `result`, as the app's captures show (#323). franklinwh-cloud and
richo's franklinwh library answer it by logging in again.

### richo's franklinwh library (homeassistant-franklinwh)

richo's [franklinwh](https://github.com/richo/franklinwh-python) library, the client of the
[homeassistant-franklinwh](https://github.com/richo/homeassistant-franklinwh) integration, works
against the emulator too (#322). Every call franklinwh 2026.3 makes is served. But its `Client`
takes a `url_base`, and login, `sendMqtt`, `getHomeGatewayList` and `updateTouMode` ignore it:
they always go to `https://energy.franklinwh.com/`. Point those at the emulator as well:

```python
from franklinwh import client as richo

richo.DEFAULT_URL_BASE = "http://127.0.0.1:8503/"     # before the first call
c = richo.Client(richo.TokenFetcher("owner@example.com", "emulator"), "<serial>",
                 url_base=richo.DEFAULT_URL_BASE)
stats = await c.get_stats()
```

The Home Assistant integration has no base URL setting: it reaches the emulator through DNS and
a TLS front instead (not tried yet); see [Cloud API clients](cloud-clients.md). `get_controllable_loads` (`selectTerGatewayControlLoadByGatewayId`),
which richo's library lists among its unknown methods, isn't served: it isn't in the app's
captures.

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
| `sendMqtt` 311 | the smart circuits' names and modes (`Sw1Name`, `Sw1Mode`, `SwMerge`…) and the cloud's own keys (#322): `opt`, `order` (the serial), `result` 0, `modeChoose` 14, `runingMode` (the active mode's id, as `currendId`), `touMinSoc` 0, `selfMinSoc` 5, `backupMaxSoc` 100, `BBBackupSoc` 20, `GridChargeEn`, `genStartSoc`, `genStopSoc`, `stopMode`, `stromEn`, `custom`. Those values are the app's captures' (one site, never changing there), not this site's reserves |
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
| `sendMqtt` 311 with `dataArea.opt` 1 | the smart-circuit write: `Sw{n}Mode` switches a circuit, `Sw{n}SocLowSet`, `Sw{n}Name`. The reply is the whole block as it now stands, as the cloud's is |
| `GET hes-gateway/terminal/weather/getStormSetting?equipNo=` | the Storm Hedge settings (2c below); `set_mode` reads `enableStorm` |
| `GET hes-gateway/terminal/selectTerPushMessageUnreadCount` | 0 |

The emergency-backup reserve is kept and read back but changes nothing: the site charges to
100 % in that mode. A fixed-time emergency backup switches to `nextWorkMode` at the first cloud
call after it ends, not by itself.

Phase 2b (#305), TOU schedules:

| Call | What it does |
|---|---|
| `GET hes-gateway/terminal/tou/getTouDispatchDetail` | the schedule: `template`, `strategyList` (seasons by `month`, then `dayTypeVoList`: `dayType` 3 every day, or 1 weekdays and 2 weekends, each with its `detailVoList` blocks and its `eleticRate*`/`eleticSell*` prices), and `detailDefaultVo.touDispatchList` (the dispatch codes) |
| `POST hes-gateway/terminal/tou/saveTouDispatch` | replaces the schedule (`{template, strategyList, …}`) and returns `result.id`. Each month in one season at most, each season `dayType` 3 or both 1 and 2, blocks as for the control API's `tou` (`code 400` otherwise). It doesn't switch the mode: switch to TOU with `updateTouModeV2`, as with current firmware |
| `POST hes-gateway/terminal/tou/getPcsHintInfo` | an empty hint; franklinwh-cloud's `set_tou_schedule` asks for it before saving |

The site runs one 24-hour schedule, so the emulator gives it the blocks and prices of the
season and day type for the site's date: at the save, and at a later cloud call once the date
moves into another one. A schedule set through the control API reads back as one everyday
season. Prices map by `waveType`: 0 `Valley`, 1 `Shoulder`, 2 `Peak`, 4 `SuperOffPeak`;
`Sharp` has no `waveType`, so it is kept for the read-back but not used.

Phase 2c (#307), settings:

| Call | What it does |
|---|---|
| `POST hes-gateway/terminal/weather/switchStorm`, `setAdvanceBackupTime`, `setStormNotice` (`{equipNo, …}`) | Storm Hedge on or off, its lead time (30–300 min), the notice (`stormNoticeEn`, `advanceTime` 5–30 min); read back by `getStormSetting`. `getStormList` and `getProgressingStormList` are empty: no storms are emulated, so Storm Hedge never starts a backup |
| `GET hes-gateway/terminal/selectIotGenerator` | the generator block and its live state, as Direct Connect 1902 |
| `POST hes-gateway/terminal/updateIotGenerator` | sets the keys it carries (`charge{n}En`, `charge{n}StartTime`/`EndTime`, `manuSw`, `genStartElec`…) in that block, so the generator runs on them |
| `GET hes-gateway/terminal/tou/getPowerControlSetting` | `globalGridChargeMax` and `globalGridDischargeMax`, kW (-1 unlimited, 0 off); the export mode `gridFeedMaxFlag` (1 solar only, 2 solar and aPower, 3 no export), `gridFeedMax` (the export limit, kW, -1 none), `gridMax` (the import limit, kW, -1 none) and `gridMaxFlag` (1 none, 2 limited), from the site's `utility` settings (#322) |
| `POST hes-gateway/terminal/tou/setPowerControl` | grid charging: 0 stops it, a kW value caps it (the site's `utility` `grid_charge` and `grid_charge_max_w`). Battery to grid: 0 stops it (`battery_export`); a kW cap is kept and read back but not enforced |

On `manuSw`: the emulator's generator treats it as Direct Connect 1902's mode (1 auto,
2 manual). franklinwh-cloud saw the cloud treat it as a start/stop command; neither is
established.

Phase 2d (#322), the calls richo's franklinwh library makes. The app makes the first three too:

| Call | What it does |
|---|---|
| `GET hes-gateway/common/getAccessoryList?gatewayId=` | the accessory modules fitted (`accessories:` in the fleet file): `accessoryType` 3 generator module, 4 Smart Circuits (richo's numbering; the app's captures show 4 on a site with Smart Circuits), `id`, `sn`, `createTime` (the `commissioned` date), `loadOption`, `gerOption`, and `total`. Without `gatewayId`, as richo's library sends it, every gateway's. `id` and `sn` are made up: their real formats aren't known |
| `POST hes-gateway/terminal/tou/setPowerControlV2` (`{gatewayId, gridFeedMaxFlag, gridFeedMax, gridMax, gridMaxFlag}`) | sets the export mode and limits in the site's `utility` settings: flag 1 `export: allowed` without the battery, 2 with it, 3 `prohibited`; a `gridFeedMax` of 0 or more makes it `limited`; `gridMax` is the import limit. `globalGridDischargeMax` and `globalGridChargeMax`, when sent too (richo's library posts back the whole setting), apply as for `setPowerControl`, before the export mode |
| `POST hes-gateway/terminal/tou/updateTouMode` | as `updateTouModeV2`, with the parameters in the query (the app) or a form body (richo's library); `result` null |
| `GET hes-gateway/manage/getEquipmentList?gatewayId=`, `GET hes-gateway/terminal/getIotAccessoryList?gatewayId=` | the same list as `getAccessoryList`. Neither is in the app's captures, so their real shape isn't known |

The official app's start-up calls (#333, #316 step 3): the 17 the app makes in 34 or more of
the 42 captures that weren't served. Field names, types, message text and envelope keys are the
captures', merged over all of them; values are the emulator's own:

| Call | What comes back |
|---|---|
| `GET hes-gateway/terminal/getDeviceInfoV2?gatewayId=` | the aGate and its aPowers from the site's models: `sysHdVersion`/`sysHdVersionInt` (the aGate code), `realSysHdVersion` (`FHP1.2` for code 102, the reference aGate's; empty otherwise), `protocolVer` (Direct Connect's), `zoneInfo`, `countryId`, `date`/`deviceTime` (site local), `fhpSn`, `peHwVerList`, `apowerList` (`ratedPwr` W, `rateBatCap` Wh), `totalCap` kWh, `fixedPowerTotal`/`fixedPowerAverage` kW, `mpptEnFlag` (an aPower S), `genEn`, `v2lModeEnable`, `offGirdFlag` (the cloud's spelling) |
| `GET hes-gateway/terminal/chargePowerDetails?gatewayId=` | `batterySoc` (the displayed SoC, as the composite's) and `touMinSoc` (the active mode's reserve), as [Findings](FINDINGS.md) records; the duration and consumption figures are null |
| `GET hes-gateway/terminal/tou/getEntranceInfo?gatewayId=` | the grid limits as `getPowerControlSetting` has them; every settings entrance off (`sgipEntrance`, `bbEntrance`, `pcsEntrance`, `ja12Entrance` 0) |
| `GET hes-gateway/terminal/weather/getCurrentBriefWeather?equipNo=` | the site's sky (a weather event's, else the model's: `description`, `thunderFlag` and `rainFlag` for `storm`), ambient `tempC`/`tempF`, and `isDayTime` from the sun; icons and the forecast null |
| `GET hes-gateway/terminal/bill/electric/selectBenefitInfo?gatewayId=&dayTime=` | the day's solar and aPower energy to the home and the grid (kWh, the split above); no prices, so the earnings are 0 and `priceFlag` false |
| `GET hes-gateway/common/selectDeviceRunLogList` | the emulator's alarm names (`alarmCode` its bit) |
| `GET hes-gateway/common/getPowerCapConfigList` | the aPower models the emulator knows: `peHwVersion`, `modelName`, `ratedCap` Wh, `chargePower`/`dischargePower` W |
| `GET hes-gateway/terminal/selectProgramFlag`, `nps/getNpsShowTip`, `getShowTip`, `feedback/whetherPopUp`, `getAppGlobalConfig` | no VPP programme, survey, tip, feedback prompt or feature entrance (their keys aren't known) |
| `GET hes-gateway/terminal/message/selectMessage`, `appObtainAdvertiseLst`, `common/getPageByTypeList` | no messages (null), adverts or help pages (empty lists) |
| `POST hes-gateway/terminal/updateTerUserFcmToken`, `tou/notify/ai/cache` | accepted (the phone's push token isn't kept) |

Then the next 14 (#337), made in 19 to 30 of the captures, and three more sendMqtt commands
(34 to 37):

| Call | What comes back |
|---|---|
| `GET hes-gateway/terminal/obtainAgateInfo?gatewayId=` | the aGate's versions as Direct Connect 1102 has them (`ibgVersion`, `protocolVer`, `slVersion`, `awsVersion`, `appVersion`, `meterVersion`), `sysHdVersion` (the aGate code), `connType` 3 (Wi-Fi) |
| `GET hes-gateway/terminal/obtainApowersInfo?gatewayId=` | each aPower: `ratedPower` kW, `ratedCapacity` and `remainingPower` kWh, `soc`, `peHwVer` (the model code) and its firmware versions (1102's) |
| `GET hes-gateway/terminal/selectDeviceOverallInfo?gatewayId=` | `apowerCount` and `totalPower` (the total capacity, kWh) |
| `GET hes-gateway/terminal/selectGatewayAlarm?gatewayId=` | the alarms active now, named as the run log names them, level 2; English only, explanations empty |
| `GET hes-gateway/terminal/backupHistorySummary?gatewayId=` | the grid relay's openings: `totalBackup`, `totalDuration` and `longestDuration` (seconds), and `lastRecord` (null before the first; its start and end SoC are null, as the site doesn't record them) |
| `GET hes-gateway/terminal/site/get/DeviceDetail?gatewayId=` | the fleet entry's id as `gatewayName`; country, state, city and postcode from `location` (no street); `alphaCode` the country code |
| `GET hes-gateway/terminal/tou/getBonusInfo?gatewayId=` | battery bonus off; `bbDefaultVo` as every capture has it (its start-time window is the emulator's); the capacity and aPower count from the site |
| `POST hes-gateway/terminal/tou/calculate/expected/earnings` | no prices, so no savings: `scene` 1, 0.0; the envelope has `traceId` and no `success`, as captured |
| `GET hes-gateway/terminal/getPersonalInfo` | the account's profile with nothing filled in (no names or address) |
| `GET hes-gateway/newApi/api-user/app/resource/getUserResources/v2` | no resources, in that service's own envelope (`msg`, `data`) |
| `GET hes-gateway/terminal/selectSimBtn?gatewayId=` | the SIM page's three buttons, as captured |
| `GET hes-gateway/terminal/sim/cardShow?gatewayId=` | HTTP 404 with `{"message": "", "status": 404}`, as 2,449 of 2,471 captured calls |
| `GET hes-gateway/terminal/recommend/recommendFlag` | no referral offer |
| `POST hes-gateway/terminal/v2/loginOut` | accepted; the token stays valid until it expires |
| `sendMqtt` 317 → 318 | `paraType` 6: the network interfaces (`commSetPara`: on Wi-Fi, addresses from the documentation ranges, 192.0.2.x and `AA:BB:CC:…`); `optType` 1 writes the keys sent. `paraType` 12: the install profile (`sysConfigPara`, Direct Connect 1702's values; `fhpRatePower` one aPower's rating, W) |
| `sendMqtt` 327 → 328 | the aPower LED: `lightStat` one per aPower, the timer off; `opt` 1 writes the keys sent |
| `sendMqtt` 337 → 338 | the aGate's Wi-Fi and access point (placeholder names and passwords); `opt` 1 writes the keys sent |

Then the rest of the user app's calls (#341), made in 1 to 18 of the 50 captures, the in-app
assistant and four more sendMqtt commands (cloud_more.py). The same rules apply, and where a
capture shows a value whose meaning isn't established, the emulator's choice is named:

| Call | What comes back |
|---|---|
| `GET hes-gateway/terminal/site/list/siteAndDeviceInfo`, `site/get/SiteDetail?siteId=` | one site per gateway, numbered 1.. in fleet order (`siteId`), named by its fleet id, with its city-level address, status, aGate code and aPower count |
| `GET hes-gateway/terminal/listAppUserDevice` | the account's gateways: name, status, firmware (`version`, `protocolVer`), address, timezone (`timeZone` the standard offset, `dst` whether daylight saving is on), `activeTime`/`createTime` the `commissioned` date |
| `GET hes-gateway/terminal/getEquipmentLocationDetail?gatewayId=` | the fleet entry's `location`: city-level `latitude`/`longitude`, `zoneInfo`, `timezone` (standard UTC offset, hours) and `dst`, country, state, city, postcode |
| `GET hes-gateway/common/obtainGeographyList`, `POST terminal/getProvincesListBYGatewayId?gatewayId=` | the countries whose cloud ids are known (2 US, 3 Australia); with `countryId`, that country's states among the fleet's gateways, numbered 1.. by name; the gateway's own country and state. `nameZh` repeats the English name |
| `GET hes-gateway/terminal/newCompliance/getComplianceNameList?gatewayId=`, `getComplianceDetailById?systemId=` | the emulator's grid codes at the site's frequency (numbered 1.. by name), `currentId` the site's; a code's numbers and trip stages (threshold, seconds) as one read-only catalog section |
| `GET hes-gateway/terminal/getCompliance?gatewayId=&paraType=` | every captured `gridConnFuntionPara` key, as placeholders (0, 0.0, empty): what each number means isn't established. `ComplianceRuleType` is the site's grid code id |
| `GET hes-gateway/terminal/getDeviceInfo?gatewayId=` | the older call: `getDeviceInfoV2`'s values, with its key set as captured |
| `GET hes-gateway/terminal/getGatewaySystemSetting`, `system/getSystemSetting` | the install profile as Direct Connect 1702 has it (`electricSupply` the service amps, the grid limits, -1 on firmware without them), the off-grid flag and aPower count |
| `GET hes-gateway/terminal/solar/selectAnyOneSolarOpen?gatewayId=` | which PV inputs are fitted (Direct Connect's solar block: remote PV through the aPbox, PV1 on the aGate) and `mpptEnFlag` (an aPower S) |
| `GET hes-gateway/terminal/getOffGridFlag`, `common/getDeviceRealTimeData` | the composite's off-grid flag; the displayed SoC |
| `GET hes-gateway/terminal/selectIotUserRuntimeDataLog?gatewayId=` | the composite's `runtimeData` with the captured key set, Direct Connect 1412's readings for the relays and temperatures it lacks |
| `GET hes-gateway/terminal/v2/warrantyInfo?gatewayId=` | the component warranties (`components:` in the fleet file): the aGate's and each aPower's expiry, the aPowers' throughput (MWh) and what is left; no installer or supplier contact |
| `GET hes-gateway/terminal/selectWorkingHours?day=`, `iotDayPowerStatistics?date=` | the day's power curve from the site's history (kW, a point per quarter-hour, and which flows are on; the colours 0); the second as strings with SoC and kWh stored, `mode` the site's mode now |
| `GET hes-gateway/terminal/electricStatistics?startDate=&endDate=`, `countLoad?date=&type=` | daily kWh from the history (at most 366 days); where the home's energy came from over a day, week, month or year, with each share as a percentage string |
| `GET hes-gateway/terminal/iotChargePowers`, `chargePowerTotal` | every backup (the grid relay's openings, newest first, paged) and their total time |
| `GET hes-gateway/terminal/listDeviceMessagesByType?eventTypes=` | the gateway's event log as device messages, newest first; `eventTypes` are the classes below |
| `GET hes-gateway/terminal/selectEventClassification`, `POST setEventPushToUser` | five event classes of the emulator's own (Grid, Battery, Alarms, Mode, System, over its event kinds); the notification settings sent are kept and read back. No pushes are sent |
| `GET hes-gateway/terminal/getHotSpotInfo/v2`, `getHotSpotInfo` | the aGate's access point as sendMqtt 337 keeps it; the older call its name |
| `POST hes-gateway/terminal/tou/getCustomEnergyDispatchList`, `getRecommendEnergyDispatchList` | the TOU editor's dispatches; for each block sent, Home recommended (the emulator's choice) |
| `POST hes-gateway/terminal/tou/getGatewayTouList` | the older call: `getGatewayTouListV2`'s list |
| `POST hes-gateway/terminal/tou/getTouCompanyListPageV2`, `GET tou/getTariffListByCompanyId`, `tou/getTariffDetailByIdV2` | no utility or tariff catalogue: empty lists, and code 400 for a tariff id |
| `GET hes-gateway/terminal/site/check/OverlapSite`, `POST site/update/DeviceDetail`, `site/update/SiteDetail` | as every capture: code 4091 (message null), and 4094 "Successful." with `success` false; nothing is changed |
| `GET hes-gateway/terminal/selectTerPushMessageListApp`, `selectTerPushMessageList`, `selectTerPushMessageUnread`, `selectTerPushMessageV2`, `POST updateTerPushMessageReadByType` | no push messages: empty lists, 0, and code 400 for a message id |
| `GET hes-gateway/terminal/span/getSpanSetting`, `getWarnFlagNotice`, `v2/queryTerminalUserInfo`, `common/v2/obtainUrlByKey?key=`, `common/getMaintenanceInfo`, `initialize/selectTerStartupPage`, `tou/getVppTipForUpdateTou` | no SPAN panel, no warning, the account's email, no web page for the key (an empty URL), no maintenance notice, start-up page or VPP tip |
| `GET hes-gateway/newApi/api-user/app/resource/getUserResources` | the older call: no resources, in that service's own envelope |
| `GET api-smart/terminal/recommendQuestion`, `POST askQuestion`, `feedback` | the in-app assistant, in its own envelope (`msg` "Request Success", `data`): no suggested questions, a fixed answer saying the emulator has none, feedback accepted |
| `sendMqtt` 315 → 316 | maintenance: `opt` 1 with `reboot` 1 reboots the emulated aGate (as Direct Connect 1721); `cleanAlarmFlag` 1 clears a forced alarm |
| `sendMqtt` 335 → 336 | a Wi-Fi scan: the aGate's own network (337's) and one placeholder neighbour, RSSI in dBm (the emulator's choice) |
| `sendMqtt` 339 → 340 | the connection: the interface 317 keeps, connected to the router and the cloud while 341 has it switched on, the composite's Wi-Fi signal |
| `sendMqtt` 341 → 342 | which interfaces are switched on (Ethernet and Wi-Fi on, 4G off); `opt` 1 writes the keys sent |

And the installer app's calls (#343), `hes-gateway/manage/…` in 1 to 13 of the captures
(cloud_installer.py). The emulator has one account, so the installer sees the fleet's gateways as
the owner does:

| Call | What comes back |
|---|---|
| `GET hes-gateway/manage/selectAppGatewayList?countryId=&stateId=` | the account's gateways, filtered by country and state ids when given, paged: status, firmware, city-level address, `runStatus` (the composite's `run_status`), aGate code; `emsModel` and `simCardStatus` 0 (their meanings aren't established) |
| `GET hes-gateway/manage/selectAppGatewayStatus` | how many gateways are offline, in fault (an alarm), off-grid, charging, discharging or standby (by the aPower power); the other buckets 0 |
| `GET hes-gateway/manage/selectCountriesList`, `selectStatesList?countryId=` | the countries and states the fleet's gateways are in, numbered as `obtainGeographyList` numbers them |
| `GET hes-gateway/manage/selectIotGatewayInstall?gatewayId=` | the aGate's versions; the address and install step null, as every capture |
| `GET hes-gateway/manage/getDeviceUpgradeResult?gatewayId=`, `listUpgradeSteps` | no upgrade running: the firmware the aGate has, and no steps |
| `GET hes-gateway/manage/getCommunicationOptimization?gatewayId=` | the smart-circuit and mode block, as sendMqtt 311 reads it |
| `GET hes-gateway/manage/getPersonalInfo` | the account's login; no name or phone number |
| `GET hes-gateway/manage/getDeviceInfoV2`, `getDeviceCompositeInfo`, `tou/getEntranceInfo`, `getEquipmentLocationDetail`, `getOffGridFlag`, `selectDeviceOverallInfo`, `obtainApowersInfo`, `obtainAgateInfo` (without `msaSn`), `getHotSpotInfo/v2`, `solar/selectAnyOneSolarOpen`, `message/selectMessage`, `selectSimBtn`, `POST tou/getGatewayTouListV2`, `v2/loginOut` | as the user app's `terminal/` call, in the envelope the installer app's captures show |
| `GET hes-gateway/manage/getAppGlobalConfig`, `selectProgramFlag`, `selectGatewayPowerAlarm`, `app/message/selectPushUnreadCount`, `tipForUploadingPictures`, `component/progressOrderCount`, `POST app/message/fcmToken/updateManage` | nothing: no feature entrance, VPP programme, power alarm, unread message, picture prompt or service order; the push token accepted and not kept |
| `POST hes-gateway/manage/sendMqtt` | the installer app's relay: the same commands as `terminal/sendMqtt` |

What the app shows for these values hasn't been seen yet: that is #316 step 4, the app pointed
at the emulator.

## Faults

Phase 3 (#309). Per gateway, the `cloud-faults` action (`ctl <gw> cloud-fault k=v`, or
`POST /gateways/<gw>/cloud-faults`) acts on the cloud requests that name that gateway, next to
`faults` (Modbus TCP) and `dc-faults` (Direct Connect):

| Fault | What the client sees |
|---|---|
| `offline=true` | `sendMqtt` answers `code 136`; the gateway list shows `status` 0 (the Modbus `offline` fault does both too). franklinwh-cloud raises `GatewayOfflineException` |
| `timeout=true` | `sendMqtt` answers `code 102`; franklinwh-cloud raises `DeviceTimeoutException` |
| `rate_limit_per_min=N` | past N requests in a minute, HTTP 429 with `code 429` and `Retry-After` (seconds until the oldest request leaves the minute) |
| `latency_s=S` | every reply waits S seconds |
| `empty_composite=true` | `getDeviceCompositeInfo` answers HTTP 200 with `result: null`; franklinwh-cloud's `get_stats` returns its last reading marked `is_stale` |

### Broken replies

#311. `bad_reply=<kind>` breaks the replies below the envelope. The request is still carried
out (a write still changes the site); what comes back is broken. `bad_reply_count=N` breaks
only the next N (then `bad_reply` clears; 0, the default, breaks every one) and
`bad_reply_p=P` breaks each with chance P (repeatable from run to run).

| `bad_reply` | The reply | franklinwh-cloud 0.6.5 |
|---|---|---|
| `hang` | none: the connection is held until the client gives up (or the emulator stops) | its 30 s timeout: `FranklinWHTimeoutError` |
| `reset` | the connection closes without a reply | `httpx.RemoteProtocolError` |
| `truncate` | the full `Content-Length`, half the body, then the connection closes | `httpx.RemoteProtocolError` |
| `not_json` | HTTP 200, a body that isn't JSON | `InvalidResponseError` (status 200) |
| `not_envelope` | HTTP 200, JSON without `code`, `message`, `result` or `success` | `sendMqtt`: `FranklinWHError` with code None; other reads return the body as it is |
| `html_500`, `html_502`, `html_503`, `html_504` | that status, an HTML error page (as a CDN would send) | `InvalidResponseError` with the status and the page |
| `success_false` | the usual reply with `success: false` | accepted: the client reads `code` only |
| `wrong_cmd_type` | `sendMqtt` only: `cmdType` 100 past the right one | accepted: not checked |
| `bad_crc` | `sendMqtt` only: `crc` 00000000 | accepted: not checked |
| `bad_data_area` | `sendMqtt` only: `dataArea` that isn't valid JSON | `json.JSONDecodeError` |

The three `sendMqtt` kinds leave other requests alone (and aren't counted on them). What the
real cloud sends in these cases is not established; each is a setting, not a claim.

Rate limits: `rate_limit_per_min` above counts one gateway's requests; `cloud:
{rate_limit_per_min: N}` in the fleet file counts every request the account makes after login,
for any gateway, with the same 429 reply.

Token expiry is account-wide: `cloud: {token_ttl_s: N}` in the fleet file. An expired token gets
`code 401`, as an unknown one does; franklinwh-cloud logs in again, or with `TokenAuth` raises
`TokenExpiredException`.

The codes are franklinwh-cloud's (its client.py). The real cloud's rate limits, `Retry-After`
values and timeouts are not known, so they are settings here.

Every reply has the cloud's envelope, `{code, message, result, success}`; `code` 200 is success
and errors come with HTTP 200, as the cloud sends them. A path the emulator doesn't serve gets
HTTP 404 with the envelope, and another `sendMqtt` command gets `code 400`.

Every phase in #295 is in. Older history comes with #296.

## Where the replies come from

Field names and the envelope follow franklinwh-cloud: its request code, its test fixtures and
its OpenAPI file (response shapes inferred from captured traffic). The values are the
emulator's own site model, not captured cloud data. `runtimeData` and the `sendMqtt` replies reuse
the Direct Connect payloads whose field names match the cloud's (1411 for 353, 1410 for 311,
1412 for 211 and `selectOffgrid`); whether the real cloud carries
the same values as Direct Connect is not established.
