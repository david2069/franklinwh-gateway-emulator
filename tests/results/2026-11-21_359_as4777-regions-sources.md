# #359 AS/NZS 4777.2:2020 regions Australia B, Australia C and New Zealand: public sources, 2026-11-21

**Question:** can the emulator add the standard's Australia B, Australia C and New Zealand region
settings from public sources, as it did for Australia A (Jemena's settings summary, effective
18 Dec 2021, reproduces the Australia A tables)? Which region do WA, the NT and the New Zealand
networks require?

**Result:** no grid code added. No public document found reproduces the B, C or New Zealand tables
(trip limits and times, enter-service, volt-var, volt-watt, frequency response, sustained
overvoltage). The operators that require B or C refer to the standard's tables (3.6, 3.7, 3.8,
4.2, 4.3), which are paywalled. A partial code would be a guess, so none is added. The pirated
copies of the standard that circulate were not used.

## Which region each network requires

| Network | Region | Source (tier: vendor/operator documentation) |
|---|---|---|
| Western Power (WA, SWIS) | Australia B | Western Power, "Transition requirements for new standards AS/NZS 4777.2:2020", 30 Nov 2021: compliant inverters "must be configured to 'Australia Region B' settings" (its two tables are for 2015-standard inverters, not Region B). ENA "AS/NZS 4777.2 FAQ" (20 Feb 2025), p. 2 region table. SMA "AS/NZS 4777.2:2020 Technical Information" v2.0 (4 Jul 2023), p. 11: "Western Power [AU] AS/NZS 4777.2:2020 Generator Region B" |
| Horizon Power (WA, regional) | Australia C | Horizon Power, "Low Voltage EG Connection Technical Requirements" (draft, created 4 Apr 2025), section 4 and Table 17: "the Proponent shall set the region to 'Australia C', with exception to the upper IES reconnect frequency which is to be set 50.5Hz"; settings "in accordance with Table 3.7 / 3.8 / 4.2 of AS/NZS 4777.2.2020" (values not reproduced). ENA FAQ; SMA p. 11 |
| TasNetworks (TAS) | Australia C | ENA FAQ p. 2; SMA p. 11 |
| Power and Water (NT) | **not established** | ENA FAQ p. 2 lists Power & Water under Australia A **and** under Australia C. Power and Water's own pages (PV class requirements; "Embedded generation commissioning sheet 2024") are reported by search results to specify Region A, but both return HTTP 403 to automated requests, so they were not read here |
| Vector (Auckland, NZ) | Australia A | Vector "ESP009 Technical Requirements for Small-Scale Inverter Connected DG", v2.0, May 2026, 4.3: "Inverters must be configured in accordance with AS/NZS 4777.2 2020 Australia A (AUS-A) settings"; 4.4 tables 1-2 reproduce the A trip settings |
| Other NZ networks (WEL, Orion, Aurora) | **not established** | the standard has a New Zealand region (SMA p. 11: "New Zealand DNSPs ... Region NZ"). EEA "Voltage Change Guidance" (after the 13 Nov 2025 voltage-limit change): the Electricity Authority "has consulted on using the AS/NZS 4777.2:2020 + A2:2024 Region A settings profile" as NZ's default; "We are awaiting the Electricity Authority's final decision". The networks' own documents were not found |

All other Australian networks in the fleet's places are Australia A (ENA FAQ p. 2: Ergon/Energex,
Evoenergy, SA Power Networks among them).

## What the example fleet does

`examples/fleet-au-nz.yaml` keeps `as4777-au-a` everywhere (the only AU/NZ code). That is right for
Devonport (Vector), Townsville (Ergon), Canberra (Evoenergy) and Adelaide (SA Power Networks).
Perth (Western Power: B) and Hobart (TasNetworks: C) need regions the emulator doesn't have;
Darwin's and Hamilton's, Christchurch's and Queenstown's are not established. The file's header
and docs/configuration.md now say so; docs/grid.md has the table above.

## What is missing to add B, C or New Zealand

Every value of the region's column in the standard's tables 3.6 (volt-watt), 3.7 (volt-var),
3.8 (volt-watt charging), 4.1/4.2 (passive anti-islanding), 4.3 (sustained overvoltage), the
enter-service window and the frequency-response set points, from a public document that
reproduces them (an operator's settings summary like Jemena's), or the standard itself if the
owner has a licensed copy.

## Searches made

WebSearch (standard and extended) for region tables, Western Power, Horizon Power, Power and Water,
TasNetworks, Vector, Orion, WEL Networks, Aurora Energy and the EEA; WebFetch of the ENA page and
FAQ, the SMA and Horizon PDFs, Western Power's notice, the EA's 2021 decision (about the 2015
standard; no region), Vector ESP009 and the EEA guidance. PDFs read with pypdf in a scratch venv.

## Checks (worktree source, PYTHONPATH=src)
- full suite: 886 passed, 44 warnings
- mkdocs build --strict: Documentation built
- check_pii --scan: SUCCESS, no PII leaks
- validate examples/fleet-au-nz.yaml: fleet ok (30 gateways)
- Break check: not applicable (docs, an example's comments and a findings record; no behaviour changed)
