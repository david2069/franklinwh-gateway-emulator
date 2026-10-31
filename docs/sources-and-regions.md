# Sources and regions

FranklinWH sells different hardware in **North America** (the US and Canada) and in
**Australia and New Zealand**. The two regions have different SKUs, grid standards, grid
frequency and firmware variants. A FranklinWH document applies to the models and region it
was written for. This page lists the FranklinWH documents the emulator draws on, which
region each covers, and how the emulator tells the two regions apart.

Rule: **behaviour documented for North American models is not applied to AU/NZ models**,
and the reverse. Where a region has no evidence, the emulator keeps its current model and
says so. See [Model baseline](model-baseline.md) for how values are marked (measured,
documented, plausible).

## FranklinWH documents

The PDFs are FranklinWH's copyright and are not in this repository. They were supplied by the
owner; get them from FranklinWH.

| Document | Date | Region | Standards it cites | Used for |
|---|---|---|---|---|
| *PV Curtailment White Paper* (6 pp.) | 2025-05-14 | North America (60 Hz) | IEEE 1547-2018 §5.4.3, UL 1741 SA/SB | Off-grid PV curtailment ([#178](https://github.com/david2069/franklinwh-gateway-emulator/issues/178)) |
| *Power Control System Technical Brief* (12 pp.) | 2024-04-07 | North America | NFPA 70 (NEC 2020) 705.12, 705.13; UL 1741 PCS | PCS export and import settings ([#123](https://github.com/david2069/franklinwh-gateway-emulator/issues/123)), generator settings ([#179](https://github.com/david2069/franklinwh-gateway-emulator/issues/179)) |
| Support article [*Why is the system showing PV curtailment?*](https://www.franklinwh.com/support/articles/detail/why-is-the-system-showing-pv-curtailment?product_type=15) | — | North America (cites HECO and California NEM programmes) | — | Curtailment causes and labels ([#176](https://github.com/david2069/franklinwh-gateway-emulator/issues/176)) |
| Warranties (AU & NZ; US & CA) and the Backwards Compatibility Statement (US) | 2026 | per document | — | Products and compatibility ([Model baseline](model-baseline.md)) |

### What they document (North America)

PV Curtailment White Paper:

- Off-grid, the aGate curtails AC-coupled PV by raising the frequency it forms. IEEE 1547-2018
  inverters reduce output (frequency-watt). Relay control stays in use as well (a hybrid
  scheme, since aPower 2).
- Droop starts above 60 Hz + 0.036 Hz (dbOF). The droop is kOF 0.05, about 33.33 % of rated
  PV per Hz, and the frequency ramps at 0.2 Hz/s, about 6.67 % of rated PV per second.
- **Full battery:** the frequency goes to 62.2 Hz and PV is fully curtailed. PV resumes when
  SoC drops below 97 %.
- Inverters that are not IEEE 1547-2018 compliant don't droop: the PV relay toggles instead.
  The relay also opens if PV exceeds the aPower's charging capacity.
- Inverter trip defaults:

  | Trip | Frequency | Clearing time |
  |---|---|---|
  | OF2 | 62.0 Hz | 0.16 s |
  | OF1 | 61.2 Hz | 300 s |
  | UF1 | 58.5 Hz | 300 s |
  | UF2 | 56.5 Hz | 0.16 s |

- Charging capacity and recommended AC-coupled PV per aPower:

  | aPower | Charging capacity | Peak | Recommended AC-coupled PV |
  |---|---|---|---|
  | aPower 2 | 8.0 kW | 13 kW for 30 s | 12 kW |
  | aPower X | 5.0 kW | 7.6 kW for 30 min | 7.6 kW |

PCS Technical Brief:

- **ESS Import-Only Mode** is factory preset: battery discharge never exceeds home
  consumption.
- App settings: *Allow solar feed to grid?*, *Maximum feed-in power*, *Reserved battery*,
  *Max grid import*, *Grid charge*, *Max charge power*.
- Over the export (PEL) limit, the aGate **opens the PV relay**.
- NEC 120 % rule: ((busbar × 1.2) − main breaker) / 1.25. Example: a 200 A busbar with a
  200 A main breaker gives 32 A, which is 7.68 kW.
- aGate busbar 280 A: aPower discharge + grid + solar ≤ 280 A.
- Overcurrent protection inside the aGate: aPower circuit 100 A, PV input 80 A, grid entrance
  200 A.
- Generator: *Best power duty* 70 %, *Alarm delay after starting fails* 1800 s, *SOC lower
  limit* 20 %, *SOC upper limit* 80 %.

## Regions

| | North America (US, Canada) | Australia, New Zealand |
|---|---|---|
| Grid | 120/240 V split phase, 60 Hz | 230 V single phase or 400 V three phase, 50 Hz |
| Interconnection | IEEE 1547-2018, UL 1741 SA/SB, NEC | AS/NZS 4777.2 (and the network's rules, e.g. Ausgrid) |
| aGate | AGT-R1V1-US, AGT-R1V2-US, AGT-R1V3-US (aGate X 1.0–1.3.1); MAC 1 | AGT-R1V1-AU (aGate X-01-AU) |
| aPower | aPower X (APR-05K…-US), aPower 2, aPower S | aPower X-01-AU, X-02-AU |
| FranklinWH documents above | apply | **don't apply**: no AU/NZ equivalent yet |
| Emulator evidence | documents only, no US site read | the reference AU aGate (measured) |

The documents name US standards only (NEC, IEEE 1547). Canada is grouped with the US because
FranklinWH sells the same models there and covers them under one US & CA warranty. Canadian
code differences (CSA, CEC) have not been checked.

For AU/NZ, 50 Hz equivalents of the curtailment values (deadband, 62.2 Hz, trip settings) are
**unknown**. AU gateways keep the current off-grid model until AU evidence exists.

## Telling the models apart

The cloud API's `sysHdVersion` and Direct Connect 1102 `SyHdVersion` give the aGate model. The
cloud's `peHwVersion` gives each aPower's (Direct Connect `PE_HW_VER` may be the same scheme; see below). The
tables below come from franklinwh-cloud's device catalogue, whose origin is not traced, so
**every entry is inferred** unless marked otherwise ([#180](https://github.com/david2069/franklinwh-gateway-emulator/issues/180)).

| aGate code | SKU | Name | Region |
|---|---|---|---|
| 100 | AGT-R1V1-US | aGate X-10 | NA |
| 101 | AGT-R1V2-US | aGate X-20 | NA |
| 102 | AGT-R1V1-AU | aGate X-01-AU | AU/NZ: **known** (the reference aGate) |
| 103, 104 | AGT-R1V3-US | aGate X 20 (US), gen 2 | NA |

| aPower code | SKU | Name | Region |
|---|---|---|---|
| 0 | APR-05K1V1-US | aPower X-10 | NA |
| 1 | APR-05K11V1-US | aPower X-10 | NA |
| 2 | APR-05K13V1-AU | aPower X-01-AU | AU/NZ |
| 3 | APR-10K15V2-US | aPower X-20 (aPower 2) | NA |
| 4 | APRS-10K15V1-US | aPower S-10 | NA |
| 5 | APRS-11K15V2-US | aPower S-10 | NA |
| 6 | APR-05K15V1-US | aPower X-10 | NA |
| 7 | APR-05K13V2-AU | aPower X-02-AU | AU/NZ |

MAC 1 has no code in the catalogue. The cloud flags it by `msaModel` / `msaSn`.

Over Direct Connect, `SyHdVersion` and `deviceType` read the same on the reference aGate on two
firmwares. `PE_HW_VER` and `BMS_*_HW_VER` appear only from V12R02B60D00: a firmware update added
them, not a hardware change (franklinwh-direct-connect-api, two 1102 reads of one aGate). So
`PE_HW_VER` can't be the model key on older firmware. The meaning of every code is unknown from
Direct Connect itself.

**Today every emulated gateway reports the reference AU codes** (`SyHdVersion` 102, `PE_HW_VER`
[2]), including the US ones. Until #180 gives each gateway a model, nothing built from the North
American documents is switched on.
