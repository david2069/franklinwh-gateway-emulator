# Grid, grid codes, export limits and phases

## Grid events, PV relays and alarms

The site has a utility you can break, plus the aGate's protection around it:

| Scenario | How | What the aGate does |
|---|---|---|
| Black-out | `ctl <gw> outage` / `site.set_grid(available=False)` | Grid relay opens at once (`701.ConnSt` 0, `DERMode` grid-forming, `GRID_DISCONNECT`). The battery carries the house down to the off-grid floor (5 %), ignoring VPP and the mode reserve. It sheds the load it can't carry and curtails the PV it can't absorb (`PV_CLIPPED`). At the floor the house goes dark (`InvSt` STANDBY, 0 V). |
| Loss of one phase | `ctl <gw> outage L2` | A multi-leg site: the relay opens at once (`trip_reason` phase-loss); islanded, each leg's output follows its own load. A grouped AU site: the aGate on L2 islands and the others stay on the grid. |
| Brown-out / swell | `ctl <gw> voltage 195 [L3]` or `0.85pu` | `701.LNV`/`V<leg>` sag. Each trip stage of the grid code fires after its own delay, with `AC_UNDER/OVER_VOLT` raised while the condition lasts. Shorter excursions ride through. |
| Frequency excursion | `ctl <gw> hz 51.3` | Frequency-watt: over-frequency curtails generation and moves storage towards charging, and under-frequency does the reverse (`ThrotPct`, `InvSt` THROTTLED). It trips per the grid code's frequency stages. |
| Restore | `ctl <gw> restore` | The relay recloses only after the grid has spent the code's enter-service delay inside its window. |
| Drop and return islanded | `ctl <gw> episode 60 120` / `gw.episode(down_s, offgrid_s)` | Utility fails; Modbus and Direct Connect go away for `down_s` (open Modbus sessions get a TCP RST, connects are refused), then come back on the same ports with the site islanded (`ConnSt` 0, `GRID_DISCONNECT`). The utility returns `offgrid_s` later and the relay recloses after the enter-service delay. `episode_phase` in the status shows `down` / `offgrid`. 15016 stays 2 unless you opt in with `quirks: {islanded_15016: 1}`: 1 was seen during the two 2026-10-21 episodes on one V12R02B60D00 site, which firmware_quirks.json grades "reported" (`ext_15016_status`). |
| Requested off-grid | `ctl <gw> offgrid on` | Islands without `GRID_DISCONNECT`, like Direct Connect `1723 offgridSet`. |
| PV relays | `ctl <gw> pv remote1 off` | That input drops to 0 W (15503 proximal, 15504/15505 remote). Split solar across inputs with `pv_inputs: {proximal: 0.6, remote1: 0.4}`. |
| Alarms | `ctl <gw> alarm OVER_TEMP [--persistent]` | Sets the `701.Alrm` bit. GROUND_FAULT, DC_OVER_VOLT, MANUAL_SHUTDOWN, OVER_TEMP and HW_TEST_FAILURE fault the inverter (battery 0 W; dark if islanded). Latched alarms clear on a `715.AlarmReset` write (`controller.clear_alarms()`). Persistent ones need `clear-alarm`. Raw DC-port bits are set with `dc_bits` (`714.PrtAlrms`). |

### Grid codes

`grid_code` selects the protection settings and grid-support responses. Split phase defaults
to IEEE, everything else to generic Australia A. Model 703's enter-service registers report the
selected code.

| | `as4777-au-a` / `ausgrid` (AS/NZS 4777.2:2020 Australia A) | `ieee1547-cat2` (IEEE 1547-2018 Cat II) | `ieee1547-cat3` (Cat III, Rule 21-style) |
|---|---|---|---|
| Nominal | 230 V, 50 Hz (starts at the scanned 241.8 V / 49.99 Hz) | 120 V L-N, 60 Hz | same |
| Under-voltage | V< 180 V 10 s · V<< 70 V 1 s | UV1 0.70 pu 10 s · UV2 0.45 pu 0.16 s | UV1 0.88 pu 21 s · UV2 0.50 pu 2 s |
| Over-voltage | V> 265 V 1 s · V>> 275 V 0.2 s · 10-min average > 258 V | OV1 1.10 pu 2 s · OV2 1.20 pu 0.16 s | OV1 1.10 pu 13 s · OV2 1.20 pu 0.16 s |
| Frequency trips | F< 47 Hz 1 s · F> 52 Hz 0.2 s | UF1 58.5 Hz 300 s · UF2 56.5 Hz 0.16 s · OF1 61.2 Hz 300 s · OF2 62 Hz 0.16 s | same |
| Freq-watt, generation | 100 % at 50.25 Hz → 0 at 52 Hz | 0.036 Hz deadband, 5 % droop | same |
| Freq-watt, storage | over: discharge → 0 by 50.75 Hz, then charge → rated by 52 Hz · under: charge → 0 by 49 Hz, then discharge → rated by 48 Hz | droop through zero (1 pu per 3 Hz) | same |
| Volt-watt | generation 253 V 100 % → 260 V 20 % · charging 207 V 20 % → 215 V 100 % | off (1547 default) | off |
| Volt-var | 207 V +44 % (supplying) · 220–240 V 0 · 258 V −60 % (absorbing) of 5.8 kVA per aPower | off (unity PF default) | off |
| Export limit | `ausgrid`: **10 kW single phase**, 5 kW/phase multi-phase · `as4777-au-a`: none | none | none |
| Enter service | 205–253 V, 47.5–50.15 Hz, 60 s, then a **360 s ramp** (exactly the scanned 703) | 0.917–1.05 pu, 59.5–60.1 Hz, 300 s, 300 s ramp | same |
| Response timing | 1 s dead time; freq-watt, volt-watt and volt-var 95 % within 10 s; soft export limit within 15 s; 0.1 Hz frequency hysteresis | 0.2 s; freq droop 5 s, volt-var 5 s, volt-watt 10 s (90 %, open-loop response time); no hysteresis | same |

The Australia A values come from the distributor settings summary that reproduces the
standard's tables ([Jemena, effective 18 Dec 2021](https://jemena.com.au/siteassets/asset-folder/documents/electricity/embedded-generation/eg-inverter-requirements-settings-asnzs-4777-2_2020v2.pdf)).
Ausgrid is an Australia A network
([ENA](https://energynetworks.com.au/miscellaneous/power-quality-response-mode-settings)).
Ausgrid's 10 kW single-phase export limit comes from its
[NS194](https://www.ausgrid.com.au/asp-and-contractors/technical-document-library/ns194),
as confirmed for this Sydney site. The 5 kW/phase used for multi-phase `ausgrid` sites is the
generic basic micro-EG rule and is unconfirmed for Ausgrid. The IEEE values are from memory of
the 1547-2018 default tables and have not been checked against its text.

**Response timing.** Each function waits out a dead time and then follows a first-order lag
*from the power actually being delivered*, so a cap tightening from 5 kW doesn't hide a 500 W
output. The time constant is set so the response reaches 95 % (AU) or 90 % (IEEE) at the
settle time. Over- and under-frequency responses see the frequency through the hysteresis
backlash: a response is released only once the frequency has come back by 0.1 Hz.
- After a reconnect, battery discharge and PV ramp linearly from zero over `es_ramp_s`.
- A soft export limit (an enforced static limit, or CSIP `opModExpLimW`) is met within 15 s,
  so fast cloud edges can briefly breach it. The `export_limit` scenario check allows that
  envelope.
- The emulator uses the slowest compliant response, so tests see the worst case.
- Tune it with `grid_overrides: {response: {freq_settle_s: 5, start_s: 0.5, hysteresis_hz: 0}}`,
  pick a preset (`as4777-envelope`, `ieee1547`, or `agate-declared`, the 0–0.1 s response
  times the real aGate declares in 705/706/711), or restore instant responses with
  `response: null`.

The AU timing values are the commonly cited AS/NZS 4777.2 envelope and AS/NZS 4777.1 soft
limit. The real aGate's curve registers (scan of 2026-10-07) declare 0 s for volt-var and
0.1 s for volt-watt and frequency droop. Those are declared open-loop times, not measured
behaviour, so they are a preset, not the default.

With volt-var on, `701.Var` is live, for example −348 var at 241.8 V (the
scanned aGate read −715). Australia B/C and New Zealand are not modelled.

Override any field per gateway with `grid_overrides`, for example
`{export_limit_single_phase_w: 5000, es_delay_s: 5, freq_watt: null,
trips: [{name: "V<", kind: v_lo, threshold: 0.8, delay_s: 3}]}`.

**AEMO and market data** (NEMWeb, the AEMO dashboard JSON, Open Electricity) cover prices,
demand and the generation mix, not what an inverter reports, so the emulator doesn't use them.
They belong with the bridge's tariff and VPP logic.

### Export limits and CSIP-AUS

**Static limit: declared and tracked, not enforced, by default.** On the reference Ausgrid
site the aGate does not apply its 10 kW limit: local `1903 grid_feed_max` is −1 and the cloud
reports `notControlExportSolar: true` (see franklinwh-direct-connect-api `BACKLOG.md`). So the
emulator exports what physics allows and records breaches (`export_over_limit_w`, running
`export_breach_s` / `export_breach_wh`). That makes the documented latent exposure testable:
5.7 kW solar plus a 5 kW midday VPP discharge exports 10.7 kW, 700 W over the limit.
`ctl <gw> export-limit enforce` (or `export_limit_enforced: true`) clips at the limit instead,
cutting battery discharge first and then PV.

**CSIP-AUS** (SA TS 5573, mandatory from late 2026). The aGate's IEEE 2030.5 client is
configured over Direct Connect `1205 der_comms` (`enable`, `uri`, `lfdi`, `sfdi`, `pin`,
`status2030_5`) and is invisible on Modbus. The emulator models what the client does, and
the control API plays the utility server:

```bash
ctl site csip on https://csip.example/dcap   # registers after 5 s if the server is reachable
ctl site csip export 1500 1800               # DERControl opModExpLimW 1.5 kW for 30 min
ctl site csip default export=5000            # DefaultDERControl
ctl site csip server down                    # after 300 s of lost comms → default control
ctl site csip connect off                    # opModConnect false: DER output stops, house stays on grid
ctl site csip import 2000 | gen 7000 | load 1500 | energize off | clear
```

- Controls apply only once the client is **registered** (enable alone is not enough, as on
  the real device).
- With no active event, or after `comms_loss_s` (300 s) of lost comms, the DefaultDERControl
  applies.
- `opModExpLimW` overrides the static limit and is always enforced.
- `opModImpLimW` and `opModLoadLimW` stop battery charging; `opModGenLimW` caps PV plus
  discharge.
- CSIP limits also curtail Modbus VPP commands. Whether a real aGate prioritises them like this
  is unverified.
- With the Direct Connect frontend on, `1205 der_comms` reads and writes this client
  (`enable`, `uri`, `pin`, `lfdi`, `sfdi`, `status2030_5`).
- LFDI/SFDI are derived from the serial with the IEEE 2030.5 SFDI algorithm (checked against
  the spec's worked example).
- The emulator does **not** speak 2030.5 (no TLS, no resource tree), and the encoding of
  `status2030_5` is unknown.

**`sunsMdEn`.** `ctl <gw> modbus off|on` is what a `1205` write does: the Modbus listener
closes or reopens about 3 s later (delayed-apply in both directions on FW V12R02B30D06).

### Phases

| Layout | Config | 701 registers |
|---|---|---|
| Single phase | `ac_type: 0` (default `phases: [L1]`; `[L2]` puts the leg elsewhere) | That leg's W/VA/A/V/TotWh points. `VL1L2` and `LLV` carry the L-N voltage, as on the scanned aGate. |
| Split phase (US) | `ac_type: 1` → L1 + L2, 180° | `VL1L2` = 240 V |
| Two-phase | `ac_type: 2, phases: [L1, L2]` | `VL1L2` = √3 × V, L3 points unused |
| Three phase | `ac_type: 2` → L1 + L2 + L3, 120° | `VL1L2/VL2L3/VL3L1` from the phasors |

AU/NZ aGates are single phase (owner, 2026-11-02; #242): an AU/NZ model is refused with more
than one leg. A two- or three-phase AU/NZ home is one aGate per phase in a fleet group
(`groups:`, [Configuration](configuration.md)). The multi-leg layouts above remain for North
American grid codes and the `spec` profile.

Unwired legs read 0, as on the single-phase scan, or the SunSpec unimplemented value with
`quirks: {unused_phase_registers: unimplemented}` (the `spec` profile's default).
`load_split` and `gen_split` (e.g. `{L1: 3, L2: 1, L3: 1}`) make the legs unbalanced, so
per-leg `W` differs and each leg meters its own import and export. Voltage and availability
can be set per leg, which is how phase loss and single-leg sags work.

> **Unverified.** Nobody has watched a real aGate's Modbus registers during an outage, and
> the reference scans show `Alrm` = 0 throughout and come from a single-phase AU unit. The
> islanded and multi-phase pictures follow SunSpec semantics. Treat them as "what a correct
> DER would show" until real captures exist.
