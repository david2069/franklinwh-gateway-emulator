# Direct Connect frontend (TCP/9000)

The aGate's local broker protocol ("sendMqtt" cmdType frames), served from the **same `Site`**
as Modbus. Framing (cipher, len/crc) comes from `franklinwh_local.protocol`, so the emulator and
the client library cannot disagree about the wire format. It is a standard dependency: **every
aGate profile runs a Direct Connect listener** (a free port unless one is given). On a factory
aGate (`setup: factory`, the default) Modbus TCP is off until a `1205` `sunsMdEn` write turns it
on; `setup: in-service` starts with it on (#40).

```bash
franklinwh-emulator serve --port 5020 --dc-port 9000      # factory: Modbus TCP off
franklinwh-local --host 127.0.0.1 --port 9000 power_flow
```

```python
gw = Gateway(GatewayConfig(id="home", seed=7, dc_port=0))   # 0 = ephemeral; Modbus off until 1205
gw.start(); gw.dc_address                                   # ('127.0.0.1', 5xxxx)
```

```yaml
gateways:
  - id: home
    modbus: {port: 5020}
    directconnect: {port: 9020}
```

Field names, types and units follow real aGate captures. On 2026-10-07, 20 read commands were
diffed against the reference aGate (V12R02B30D06): all 312 top-level keys match, and a test pins
the key sets. See [CALIBRATION.md](CALIBRATION.md#direct-connect-payloads) for what changed.
Earlier sources: The sources are
franklinwh-direct-connect-api's test fixtures, `BACKLOG.md` and `docs/PROTOCOL.md`, and
franklinwh-local-bridge's parsers. Static per-aPower identities (serials, smart-circuit names,
programme ids) come from the DC library's `SyntheticSite`, so one seed gives the same device on
both emulators.

## Commands

| Request → reply | What it reports / does | Backed by |
|---|---|---|
| 1101 → 1102 login | `IBG_SN` (= the Modbus 701 serial), `FHP_NUM`, `FHP_SN[]`, `SyHdVersion` 102, `protocolVer` V1.11.03, `*_VER` | gateway |
| 1301 → 1302 power_flow | `p_uti/p_sun/p_fhp/p_load/p_gen` (W), `soc`, `t_amb`, `mode`/`name`, `electricity_type` 1, `run_status` (0/1/2, off-grid 5/6/7; 8 Debug Mode and 9 VPP are not emulated), the seven `kwh_*` today, tier arrays (`flat` carries everything, `sharp` 0), `main_sw`, `pro_load`, `fhpSn/fhpSoc/fhpPower`, `infi_status` (list per aPower), `slaver_stat` 4, `elecnet_state`, `offgridreason`. No `devNum` (as on the device). | site snapshot, daily totals |
| 1303 → 1304 energy_history | 96 quarter-hour points: `p_uti/p_fhp/p_load/p_gen`, `rec_time`, `pointId`, `kwh_*`, tiers, `date` (echoed), `sectionEngy` (the latest quarter-hour's kWh per channel), `sno` (days since commissioning). `p_load` repeats `p_fhp` (real firmware bug DEF-1303-PLOAD-DUP; `spec` profile fixes it). About 105 days are kept. | site history |
| 1105 → 1106, 1831 → 1832 | `devNum`, `devMap[{id, devSN[, checkResult]}]` per aPower; `isOver` on 1106 only | config |
| 1703 → 1704 | per aPower: `batVol` (16 × the cell voltage), `gridVol1/2` and `inverterVolt1/2` (**single phase: two half-voltage legs**, 121.2 + 121.2 on a 242 V supply), equal leg currents, `middleBusVolt` 342.7, `gridFreq`, `DCDCStatus` 4/6/7 | site |
| 1705 → 1706 | per aPower: 16 `batVolt` cells (**integer mV on a flat LFP curve**, about 3,365 at full), `batTemp`, `batSoc`, `batSoh` 94.8, `currGrp` | site |
| 1833 → 1834, 1835 → 1836 | per aPower firmware/serials; `bmsState` 5/6/7, `ibgMainState` = programme | site |
| 1707 → 1708 ibg_run_status | programme, `name` "" and `electricity_type` 1 (**no `energyMode`** on this firmware), `dspRunstatus`, relays (`solarRelayStatus` = proximal PV relay, `pvRelay2` = remote1, `mainRelay1Status` and `gridRelay2` = grid relay, `mainRelay2Status` only on split/three phase, `blackStartRelay` 1, `smartRelay1–3`), `gridLineVol` (line V×10), `gridVol1/2` and `gridCurr1/2` (half-voltage legs on single phase), `gridFreq`, `dspSettingFreq`, `genVoltage`, `isTripPhs*En` | site |
| 1709 → 1710 relay_status | `gridRelayOpen` 1 when islanded; `genRelayOpen` 1 while a fitted generator is disconnected (0 with none, as on the reference site) | site protection, generator |
| 1723 → 1724 offgrid | read `offgridSet` (requested) / `offgridState` (actual) / `offgridSoc`. Write `{opt:1, offgridSet, offgridSoc}` islands the site and sets the off-grid floor. | `request_offgrid` |
| 1725 → 1726 mode_list | **answers only `opt:1`**; the reply has **no `reason`** (`current_id, result, opt, list`). Self 85232 / TOU 29287 / Backup 47522, `scheduling_type` 2/1/3, `electricity_type` 1, `reserved_soc` = site reserves (read-only: cloud-owned on real devices). The reference site's TOU programme is named by its owner ("Solar & Battery Plan"). | site |
| 1727 → 1728 mode set | **only `opt:3`**, `current_id` → native mode. Applies immediately and shows on Modbus 15507. | `set_native_mode` |
| 1403 → 1404 | `runingMode` (sic), `modeChoose` (the scheduling type here; the device read 14 under TOU, undecoded), `name` "" | site |
| 1405 → 1406 | the 9-field SoC block (`BBBackupSoc` 20). Full-block writes are acknowledged and discarded (cloud-owned); partial writes get `reason:-2`. | — |
| 1409 → 1410 smart circuits | `SwXName/Mode/ProLoad/SocLowSet/...` from the site's circuits. A write (`opt:1`) switches them, so their bound load really goes; schedules are acknowledged and discarded. | site circuits |
| 1411 → 1412 circuit meter | `Sw1Volt` (V×10), `SW1Curr/ExpPower/ExpEnergy` (circuit 1), zeroed `Sw2Volt/SW2*`, `CarSW*` (circuit 2), `power/curr/volt/genpowerGen`, `freq` (Hz×10 integer), with the device's mixed `Sw`/`SW` casing | site circuits |
| 1901 → 1902 generator | The 1901 block (real key set and defaults from the reference aGate: `genEn` 0, `genStartElec` 20, `genCloseElec` 80, `startDelTime` 1800, `charge1` 11:00–23:59, exercise off) plus live `genStat` (0 off, 1 running, 2 cooldown, 3 fault) and `genpowerGen` (W). The trailing `power/curr/volt/freq` are gateway-level, as in 1412. A full-block write (`opt:1`) applies; partial frames get `reason:-2` (assumed, as for 1205/1405). `franklinwh_local.set_generator*` read-back verifies. | site generator |
| 1829 → 1830 deviceAlarms | `{num}`: the active-alarm count (the reference reads 0). The app's name, from the enum voidstarr/franklinwh_local recovered. The list shape when `num` > 0 is unknown. | site alarms |
| 1207, 1209, 1821, 1823, 1825 (unconfirmed) | Reads return the reference aGate's values: 1207 `enable 111, mode 222`, 1209 `enable 1`, 1821 `enable 0`, 1823 `powerOn 0, powerOff 0`, 1825 `power 0`. 1821–1825 are the app's **`debugMode` / `powerOn` / `chargeDevice`** (cloud cmdType 333), one-shot fields that ignore `id` on read: **treat writes as dangerous**. Writes to 1209/1821–1825 are not emulated (`reason:4`); 1207 writes are stored with no effect. | — |
| 1903 → 1904 solar_pv | `solarPower` (deciwatts), `solarPowerGen` (kWh×10 today), `PV1RatedPower` (×100 W), `remoteSolarEn`, `solarRelayStat`, **`grid_feed_max` −1 unless the export limit is enforced** (then kW, unit unverified), `threePhPvEnb` | site |
| 1205 → 1206 der_comms | `sunsMdEn`, `ip`, `port` (the Modbus port), `enable`, `uri`, `dcap2030_5`, `lfdi`/`sfdi` (once registered), `pin`, `status2030_5`. A full-block write toggles Modbus (delayed) and the CSIP client; partial writes get `reason:-2`. | gateway + CSIP client |
| 1201 → 1202 | local `time`; `timezone` = the **standard** offset in hours (10 in Sydney all year; float for half-hour zones) with `DST` 0/1 as a separate flag; `timezoneStr` "" (the IANA name on the `spec` profile); `laitude` (sic) / `longitude` as **strings**, `postcode`, from the gateway's `location` ("" when unset) | site clock + location |
| 1119 → 1120 | network switches (read/write, stored) | — |
| 1251 → 1252 | a minimal compliance block (`IEEE_2030_5_EVAL`, …) | — |
| 1721 → 1722 device_control | read zeros. A full-block write with `reboot:1` **drops the socket, and both :502 and :9000 go down** for `reboot_s` (20 s), then come back. Site state persists. | `Gateway.reboot` |

## Error behaviour (as on the real aGate)

- 1725 with `opt` ≠ 1, and 1727 with `opt` ≠ 3: **no reply** (the client times out).
- Reads needing `id` (1703/1705/1833/1835) or `date` (1303) without it: the empty key set
  with `result:1, reason:-1`.
- Partial full-block writes (1205, 1405, 1721): `result:1, reason:-2`.
- Normal replies carry `opt`, `result: 0` and `reason: 0` (except 1726 and 1728, which have no
  `reason`).
- Other in-band cmdTypes (1101–1998) not implemented here: `result:1, reason:4`, with the
  socket kept open.
- Out-of-band codes (e.g. the cloud relay codes 203/211): **the connection is closed.**

## Faults

`gw.dc_faults` (or `ctl <gw> dc-fault …`) are separate from the Modbus faults: `latency_s`,
`offline`, `drop_responses`, and `exception` (replies `result:1, reason:<n>`).

## Cross-protocol consistency

Both ports render one `Site`:

- `701.W` = `p_uti`, `714.DCW` = `p_fhp` (same sign), and `713.SoC` = `soc`×10.
- `1727` mode set → `15507`.
- `1723` off-grid → `701.ConnSt` 0 / `DERMode` grid-forming.
- An outage → `gridRelayOpen` 1 and `offgridState` 1 with `offgridSet` 0.
- `1205 sunsMdEn=0` → :502 closes.
- CSIP controls registered via `1205` act on power seen on both ports.

## Unknown or unverified

- The encoding of `status2030_5` (the emulator uses 0 disabled, 1 registering, 2 registered,
  3 comms-lost), and the types of `lfdi`/`sfdi` (strings here).
- The unit of `grid_feed_max` when a limit is applied (kW here, matching the cloud's
  `gridFeedMax`).
- `elecnet_state` (undecoded on real devices; 0 connected / 1 islanded here) and
  `offgridreason` values.
- Tier attribution (no tariff model: everything is booked to `flat`).
- Daily totals start when the emulator starts, not at a real midnight, on the first day.
