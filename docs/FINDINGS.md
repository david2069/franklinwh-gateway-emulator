# Findings

Things learned while building the emulator about the real aGate, the sibling libraries and
their docs. Each item says where it belongs; fixes to other repos are theirs to make.

## franklinwh-modbus (library)

| # | Finding | Evidence | Suggested fix |
|---|---|---|---|
| M1 | **`read_alarms()` never refreshes.** It reads `701.Alrm`, `714.PrtAlrms` and `713.Sta` without calling `.read()`, so `check_blocking_alarms()` returns whatever the last unrelated read cached. It can still report an alarm after `clear_alarms()`, or miss a new one. This affects real hardware too. | Live run: OVER_TEMP forced, `read_grid_status()` cached it, `clear_alarms()` cleared it on the device (write log shows `715.AlarmReset=1/0 applied`), yet `check_blocking_alarms()` still reported `0x90`. A fresh `m701.read()` shows `16`. | Call `m701.read()`, `m714.read()`, `m713.read()` inside `read_alarms()`. |
| M2 | **`ac_type` is guessed from voltage.** A US split-phase gateway (120 V L-N) is reported as "Single-Phase (120V Nominal)", and a deep sag reports "Unknown", even though `701.ACType` says split phase. | `read_grid_status()` against the `us-split` fleet gateway. | Read `701.ACType` (0 single, 1 split, 2 three). |
| M3 | `FRANKLINWH_SUNSPEC_QUIRKS.md` says **M714.DCW positive = charging**; `controller.py` says positive = discharging. | The scan has DCW = +500 W at night with 500 W load and no PV, i.e. discharging. | Correct the doc. |
| M4 | `FRANKLINWH_SUNSPEC_QUIRKS.md` lists **15507 as 0 Emergency / 1 TOU / 2 Self / 3 Manual**; the controller and the extensions manifest use 1 Backup / 2 Self / 3 TOU / 4 Manual. | `controller.NATIVE_MODES`; the manifest notes "verified 1-indexed". | Correct the doc. |
| M5 | **16001/16002** are described as reserve mirrors (QUIRKS correlation table) and as "NPt mirror / TmpAmb mirror" (manifest). | 2026-10-07 scan: 16001 = 5 (both reserves 5 %); **16002 = 20** with TmpAmb 17.5 °C and TOU reserve 15 %, so neither the TOU reserve nor TmpAmb. It matches 1406 `BBBackupSoc` 20 (candidate). | Filed as franklinwh-modbus#18 (cross-check with Direct Connect). |
| M8 | **15000–15043 include live values the manifest treats as static.** 15029/15030 and 15033/15034 are 32-bit `701.TotWhInj`/`TotWhAbs`, 15024 is frequency in mHz, 15040 is `701.PF`, 15036 is SoH, 15020 is WHRtg; 15011 = 16000 (home load, candidate). The manifest's "15011 = ModelID 707" read 583. | Exact equality within one scan (2026-10-07). | franklinwh-modbus#18. |
| M6 | `WSetRvrtTms`: the quirks doc says reversion is unverified; `send_command` says the countdown is cosmetic (PICS Issue 4). | Both are in the code and docs. | The emulator follows "cosmetic"; make the doc consistent. |
| M7 | `AGATE_NETWORK_PROFILE.md` publishes what appears to be a real aGate serial and its WiFi MAC, while the scan files redact the serial. | The doc header. | Redact, if the repo is public. |

## franklinwh-direct-connect-api (library)

| # | Finding | Evidence | Suggested fix |
|---|---|---|---|
| D2 | The catalog, `client.ibg_run_status` docstring and `SyntheticSite` give **1708 an `energyMode` field**. The reference aGate (V12R02B30D06) does not send it. It sends `electricity_type`, `dspRunstatus`, grid/load currents, `gridVol2`, `genVoltage` and `isTripPhs*En`, which the synthetic site lacks. Other synthetic gaps: 1202 `timezone` is the *standard* offset with a separate `DST` flag, and `laitude`/`longitude` are strings; 1302 has no `devNum` and an `infi_status` list; 1726 has no `reason`. | Read-only capture of 20 commands on 2026-10-07 ([CALIBRATION.md](CALIBRATION.md#direct-connect-payloads)). | Mark `energyMode` as cloud-only; align `SyntheticSite` with the captured key sets. |
| D3 | **Unconfirmed Direct Connect cmdTypes 1207/1209/1821/1823/1825**, read on the reference site. 1821/1823/1825 are the app's `debugMode`/`powerOn`/`chargeDevice`: one-shot maintenance fields that ignore `id`. **Never write them** on a live site. | Read-only `opt:0` via the Local Bridge, 2026-10-07. | Mark 1821–1825 `dangerous_write` in the client. |
| D1 | `franklinwh-local health` probes Modbus on **port 502 literally**, not on the `port` that `1205 der_comms` reports. It reports "DOWN" plus a spurious "config says ON but :502 is DOWN" warning whenever Modbus is on another port. | Live run against the emulator with Modbus on 5034: `der_comms` said `tcp 127.0.0.1:5034`, `health` said `:502 DOWN`. | Probe `der_comms["port"]` (default 502). |

## franklinwh-local-bridge

| # | Finding | Evidence | Suggested fix |
|---|---|---|---|
| L1 | **`pv_azimuth` defaults to 0.0**, which in Open-Meteo's convention means **south-facing**. A Sydney roof facing north is 180. With defaults the solar forecast assumes panels facing away from the sun, which lowers the forecast, most of all in winter. | `config.py`: `pv_azimuth: float = 0.0` with the comment "0=S, -90=E, 90=W". The emulator's `clear_sky_poa` gives north > south at Sydney noon (test). | Default to 180 when latitude < 0, or require it to be set. |

| L2 | **The Local Bridge's API is unauthenticated, including control routes.** On :8101 every route answers without a login: `POST /api/mode`, `/api/offgrid`, `/api/battery/command`, `/api/reboot`, `/api/call/{cmd}` and `PUT /api/modbus` among them. It is reachable from anywhere on the LAN and, here, over Tailscale. The Modbus bridge, in contrast, requires a session. | 2026-10-07: `GET /openapi.json` and `GET /api/cmd/*` answered without credentials over Tailscale. Only GETs were made. | Add auth (at least for writes), or bind to loopback behind a proxy; document the exposure. |

| L3 | **The metrics export mixes mock gateways in by default.** `GET /api/admin/metrics/export` without `gateway=` returns the real and mock gateways' rows interleaved, with no gateway column (a quarter of the rows repeat a timestamp). Analyses of "the site" are silently contaminated. | 2026-10-07: 177,841 rows unfiltered vs 81,511 with `gateway=<real id>`. | Add a `gateway_id` column, and default to the default gateway (or refuse without `gateway=` when mocks exist). |

## Where Modbus and SPAN live in the FranklinWH app (2026-10-08)

Sources:
- SPAN's public app note, <https://techportal.span.io/s/app-note-integration-with-franklinwh>;
- the FranklinWH AU/NZ *Quick Installation Guide*;
- the US *NEM-MT System Configuration and Commissioning Guide* (Aug 2026).

Neither installation guide mentions Modbus. It is not part of commissioning: the commission
menu is Network Settings, Device Discovery, Location, System Parameters, Utility Plan, Grid
Profile, Bind/Unbind and Summary.

| App path | Local cmdType | Notes |
|---|---|---|
| Settings → 2030.5 Resource Address | 1205 (`resourceAddress`) | cloud 317 `paraType` 3 |
| Settings → Modbus → Sunspec Modbus | 1205 (`sunspecModbus`, same opcode) | `sunsMdEn` + IP/port |
| Settings → Modbus → **SPAN Panel → "Connect to SPAN Panel?"** | **none in the app's local enum** | It shows Sunspec Modbus IP and port 502, then "connect aGate **ETH2** to SPAN **AUX Comms (ETH-1)**", then "The FHP is ready for SPAN Panel integration." Not shown in the owner view. |

**Writes to 15507–15509 are acknowledged and discarded on the reference site** (SunSpec 704
control works). Never write Direct Connect 1821/1823/1825 on a live site.

**The AU guide's EMS screenshot labels 1708:**
- "Mains voltage L1/L2 119.0 V, line L1_L2 237.8 V" confirms the two half-voltage legs on
  single phase.
- Its relay list in field order (Mains, **Genset**, Photovoltaic, Load 1/2, EV, **Mains relay
  2**) makes `mainRelay2Status` the genset relay and `gridRelay2` "Mains relay 2". The emulator
  now follows that.
- The screenshot also shows alarm codes (3019, 4129, 4228, 4229), the likely content of 1829
  when `num` > 0.
- AU aGates ship with a **4G SIM** and commission over the aGate's Wi-Fi AP and then 4G, so
  cloud reachability does not depend on home internet.

**The NEM-MT guide labels 1903:** "Is aPbox installed?" = `remoteSolarEn`, Access mode
Load-/Line-side = `remoteSolarMode`, rated powers = `PV*RatedPower`. Its "Allow … solar export
to the grid" toggles (per source) have no obvious 1904 field yet.

## Requests to the integrators: firmware tracking

The aGate and aPower firmware is upgraded silently, by cloud OTA. Several behaviours are
firmware-specific:
- `sunsMdEn` apply delay;
- DEF-1303 `p_load`;
- 1708 `energyMode`;
- the Modbus quirk set;
- the battery and converter calibration in [CALIBRATION.md](CALIBRATION.md).

Neither bridge records firmware history, so a change in behaviour can't be tied to an upgrade,
and calibration data can't be split by firmware. Both bridges should record firmware **at
start-up, on every reconnect and every 24 h**; keep a timestamped history; flag differences;
and stamp exports with it.

- **Modbus bridge:** SunSpec model 1 (`Mn`, `Md`, `Opt`, `Vr`, `SN`) per gateway. A change logs
  an event and can raise an HA notification and/or an MQTT attribute. Add a `firmware` column, or
  a firmware-epoch id, to the metrics export.
- **Local Bridge:**
  - 1101/1102 login (`IBG_VER`, `APP_VER`, `AWS_VER`, `SL_VER`, `METER_VER`, `protocolVer`,
    `SyHdVersion`, per-aPower `FPGA/DCDC/INV/BMS/BL/TH_VER`), plus 1833/1834 per aPower;
  - its own version and asset;
  - optionally, the read-only OTA status reads 1501/1503, to see an upgrade in progress;
  - change notifications through its existing notify triggers, and the history in
    `support-info` and the exports.

Filed as david2069/franklinwh-modbus-bridge#10 and david2069/franklinwh-local-bridge#8; the export mixing (L3) as franklinwh-local-bridge#9.

## Reference-device observations (2026-10-07)

From 30 days of 10 s metrics and a read-only Direct Connect capture of the real aGate.
Details and method: [CALIBRATION.md](CALIBRATION.md).

- **Full Modbus scan (21:50):** the 705–712 curve groups are populated with exactly the
  Australia A settings, and the declared response times are 0 s (volt-var) and 0.1 s (volt-watt,
  frequency droop). 714.Prt[0] is all zeros; 701.ThrotPct is unimplemented; SoH 95.6 → 94.8 %
  since May. The May scan never captured the groups. See
  [CALIBRATION.md](CALIBRATION.md#modbus-register-scan-2026-10-07).
- **Register resolution:** `713.SoC` moves in whole percent and `714.DCW` in 100 W steps (99 %
  of samples). Grid, solar and home power are not quantised.
- **Battery energy:** 12.45 kWh discharged and 14.0 kWh charged per 100 % SoC, with about 72 W
  of overhead while discharging. A full, idle battery holds 100 % for hours.
  - SoC jumps from about 96 % to 100 % at the end of charging, which looks like a BMS
    recalibration.
  - Lifetime `714.DCWhInj`/`DCWhAbs` imply a 98 % ratio, which is inconsistent with the
    roughly 89 % round trip from SoC. The counters may be measured on another side of the
    converter.
- **Single phase as two half-voltage legs:** Direct Connect 1704/1708 report a 242 V supply as
  `gridVol1` = `gridVol2` = 121.2 V, with `gridLineVol` 2424 and equal leg currents. The aGate
  X power stage is split phase internally.
- **1202 time:** `timezone` 10 + `DST` 1 under AEDT, `timezoneStr` "".
- **1302 `mode` changes during the evening schedule:** 29287 "Solar & Battery Plan" (the
  owner-named TOU programme), then 9 with an empty name, matching `ibgRunStatus` 9. Undecoded.
- **Firmware labels differ by interface:** Modbus 1.Vr reports V10R01B04D00; Direct Connect
  reports `IBG_VER` V12R02B30D06_260304.
- **Bridge-driven evening dispatch:** a discharge of up to 4.9 kW to the grid from about 18:00
  daily, and a 5 kW force charge on 2 Oct at 20:00. Native-mode comparisons must account for it.
- **The aPower draws about 27 W from the AC side while idle.** 1301 `p_fhp` reads −26 to
  −27 W at full and at reserve, with 1705 `currGrp` 0 A and the DC bus held at about 343 V. The
  converter stays online, and the grid or PV pays for it. Modbus rounds it to 0. See
  [CALIBRATION.md](CALIBRATION.md#standing-consumption-the-apower-is-kept-alive-from-the-ac-side).
- **Islanding tests (20 and 25 Sep):** "PV Clipped" for about 50 s, then "Grid Forming" for
  1.5–2.5 min, with the battery flat at 200–400 W.

## Second-site extension samples (franklinwh-modbus#18, 2026-10-07): unconfirmed

A community user (mtnears) ran the proposed extension map on their own aGate and attached 13
overnight samples (20 s apart, about 4 minutes) plus one paired Direct Connect + Modbus read. This
is **one short night-time window from one site**, so nothing below is confirmed. It shows where the
two sites agree or differ, and what to ask for next. The emulator's mapping is unchanged.

**Site context, from their post and their public repo (mtnears/FranklinWH-Automation):**

| | Their site | Reference site (this repo) |
|---|---|---|
| Region / grid | US, split-phase 240 V, 60 Hz (PG&E, NEM2) | AU, single phase 230 V, 50 Hz |
| Batteries | 2× aPower 2 (repo: "27.2 kWh total"; 713.WHRtg 30000) | 1× aPower (13.6 kWh) |
| Gateway | "2023+ retail aGate" (repo); the owner calls it a newer model than ours; exact model not stated | aGate X-01-AU |
| Firmware | **unknown for this capture**. An anonymous telemetry record in mtnears/franklin-telemetry (Feb 2026, 2 batteries, 28.26 kW solar, "aGate X", probably this site) gives V10R01B04D00 | Modbus V10R01B04D00, DC V12R02B30D06 |
| Solar | 28.26 kW: 16-panel Enphase IQ8MC house array + 60-panel SolarEdge barn array, "dual-meter"; how each is wired to the aGate is not stated | aGate PV inputs |
| Generator | 1301 shows `genStat 1` and `kwh_gen 161` with `p_gen 0`: a generator input or accessory may be configured; not stated | none (feature off) |
| Mode at capture | TOU ("TOU-B"), batteries idle at 20 % reserve, night, grid import 1.4–1.9 kW | various |

**Readings, side by side (no conclusions):**

| Register | Their site | Ours | Note |
|---|---|---|---|
| 15024, 15025, 15029/15030, 15033/15034, 15036 | track 701.Hz, LLV, TotWhInj/TotWhAbs, SoH as on ours | same | agree in both captures |
| 15012/15013 (as int32) | −63 vs 1301 `p_fhp` −67 | 256 in the 2026-10-07 scan (battery carrying the house, not read at the same instant) | May 2026 dump on ours: −4998 while "discharging ~500 W" does not fit; needs a dispatch step |
| 15022 | 1928 = 701.W 1928 in the paired read | 65535 (−1) with grid ≈ 0 | needs a non-zero grid on ours |
| 15020 | 27200 with 713.WHRtg 30000 | 13600 = WHRtg | could be usable vs nameplate, or an aPower 2 difference |
| 15011 = 16000 | 1000 throughout while load moved 1.3–1.9 kW | tracks load at 1 W (Modbus bridge, 24 h, 713 distinct values) | differs: firmware, model or setup |
| 16002 | 10 (1405 `genStartSoc` 10, `BBBackupSoc` 20) | 20 (1405 `genStartSoc` 0, `BBBackupSoc` 20; 1901 `genStartElec` 20) | either generator start SoC or something else; their generator setup unknown |
| 15017 | 20 | 20 | constant on both |
| 15040 | −2 to −4, PF 0.713 | −9 | not PF on theirs |
| 15507 | 3 in TOU | 2 in Self-Consumption | their repo documents 1 Backup / 2 Self / 3 TOU from ~300k readings across mode switches (relevant to franklinwh-modbus#7) |
| 1301 `t_amb` | 195 (× 10?) | 15.8 (°C) | firmware or model difference |

**What would settle it:** the exact gateway model and both firmware versions (Modbus and Direct
Connect) at capture time, how the solar and any generator are wired, and longer captures from both
sites covering a dispatch step, daytime solar, a mode change and a reserve change.

## Control-strategy observations (from the emulator's models)

- **The home battery drains into an EV in Self-Consumption.** A 7 kW overnight session takes the
  battery to its reserve; the aGate has no EV awareness. Relevant to the bridges' strategy and
  scheduler work.
- **The latent 10 kW Ausgrid breach** needs only a midday VPP or export block coinciding with
  peak solar: 5.7 + 5 = 10.7 kW (`latent-export-breach` scenario).
- **CSIP-AUS limits would also curtail Modbus VPP dispatches** in the emulator. Whether a real
  aGate prioritises CSIP over Modbus VPP is unverified and worth testing once CSIP is enabled.

## SunSpec addressing (for clients and the bridge's LAN probe)

- The chain is at **PDU address 0** (`SunS` = 0x5375 0x6E53). The scan's "Base Address 40000" is a
  display offset: its addresses are PDU 0–1127 plus 40000. Base 40000 returned ILLEGAL_DATA_ADDRESS
  in the 2026-03-15 test, but the UID-aliasing table marks it "varies".
- **Recommended probe order:** PDU 0, then 40000, then 50000; stop at the first `SunS`. Any unit
  id gets the same answer.
- Extension registers sit at literal PDU addresses: 15000–15039 and 15500–15513 (the 15000:514
  block read succeeded, so 15000–15513 is readable) and 16000–16002 (the 16000:500 block read
  succeeded).

## Device behaviour (decisions the emulator encodes)

- **715.LocRemCtl is always 1 (Local), even during VPP control.** The Modbus Bridge's mock
  had reported "Remote" while dispatching; that was invented, not observed, and the bridge has
  since changed its mock to report Local.
- **Single Modbus session.** The aGate serves one client at a time; the bridge keeps one
  persistent connection per gateway and serialises I/O. A stale session can wedge the device
  after a fast restart.
- **Export limit on the reference Ausgrid site is declared, not enforced:** local
  `1903 grid_feed_max` = −1, cloud `gridFeedMax` = 10 kW, `notControlExportSolar: true`.
  5.7 kW solar plus 5 kW of battery discharge = 10.7 kW, which is a latent breach if a midday
  VPP or export block ever coincides with peak solar (franklinwh-direct-connect-api `BACKLOG.md`).
- **`1205 sunsMdEn`** writes are delayed-apply (~seconds, both directions) on FW V12R02B30D06;
  older firmware needed a reboot to turn Modbus back on.
- **IEEE 2030.5 / CSIP-AUS** is configured over Direct Connect `1205` and is invisible on Modbus;
  it becomes live on *registration*, not on the `enable` flag. It is currently off on the
  reference site.

## Grid connection (Sydney / Ausgrid)

- Ausgrid is an AS/NZS 4777.2:2020 **Australia A** network
  ([ENA](https://energynetworks.com.au/miscellaneous/power-quality-response-mode-settings)).
- The Australia A settings were checked against the distributor summary that reproduces the
  standard's tables ([Jemena, eff. 18 Dec 2021](https://jemena.com.au/siteassets/asset-folder/documents/electricity/embedded-generation/eg-inverter-requirements-settings-asnzs-4777-2_2020v2.pdf)):
  - V< 180 V 10 s, V<< 70 V 1 s, V> 265 V 1 s, V>> 275 V (0.2 s max), sustained 10-min > 258 V
    (3 s max);
  - F< 47 Hz 1 s, F> 52 Hz (0.2 s max); reconnection 60 s;
  - frequency response f_LLCO 49.75, f_stop-ch 49, f_Pmax 48, f_ULCO 50.25, f_transition 50.75,
    f_Pmin 52 Hz; response starts within 1 s and completes within 10 s; f_hyst 0.1 Hz;
  - volt-watt 253 V 100 % → 260 V 20 %; charging 207 V 20 % → 215 V 100 %;
  - volt-var 207 V 44 % supplying, 220/240 V 0, 258 V 60 % absorbing;
  - ROCOF 4 Hz/s and vector shift 60°/20° (not modelled).
- The reference aGate's 703 enter-service values (205–253 V, 47.5–50.15 Hz, 60 s) are exactly
  Australia A.
- **Export: 10 kW on a single-phase supply** (Ausgrid
  [NS194](https://www.ausgrid.com.au/asp-and-contractors/technical-document-library/ns194),
  confirmed by the site owner). The multi-phase figure used (5 kW per phase, the generic basic
  micro-EG rule) is unconfirmed for Ausgrid. The NS194 PDF itself could not be retrieved.
- **CSIP-AUS** (SA TS 5573:2025) compliance for inverter communication clients is required from
  late 2026; the Clean Energy Council lists compliant products; Ausgrid does not separately
  certify against its own utility server.
- AEMO / NEMWeb / Open Electricity / GridIQ are **market** data (prices, demand, generation
  mix), not inverter connection settings; they belong with the bridge's tariff and VPP logic.

## pymodbus

- 3.15 deprecates `ModbusDeviceContext` / `ModbusSequentialDataBlock` / `ModbusServerContext`
  in favour of `SimData`/`SimDevice` (removal planned for v4). That is the reason for the
  emulator's own server; see [DESIGN.md](DESIGN.md).
