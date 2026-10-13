# franklinwh-modbus#18 cross-check, 2026-10-12

Question: mtnears' 48 h capture (issue comment 6112488024, US split-phase, 2× aPower 2, generator) reports register meanings. Which hold on the reference site?

Data: ~/franklinwh-captures/paired_2026-10-08_redacted.jsonl (outside git). Reference site: AU aGate X, single phase, 1× aPower, PV1 6.6 kW, no generator, Modbus V10R01B04D00, DC V12R02B30D06. Thu 2026-10-08 18:52 → Fri 2026-10-09 16:00 AEDT, 10 s. Raw vregs come from the bridge's /points; the bridge reads them on alternate polls, so 4,815 of 7,598 paired samples have them. Mode Self-Consumption, reserves 5/5 throughout; no mode or reserve change, no generator, no islanding.

Script: `tests/results/2026-10-12_issue18_crosscheck.py`

```
samples with both sources: 7598  (Thu Oct  8 18:52:16 2026 -> Fri Oct  9 15:59:53 2026, local time)

## Their questions
15035 vs 1301 soc*10: n=4815 slope=0.9998 intercept=-0.5 r=1.0000 rms=0.56 x[87.3684..1000] y[87..1000]
15040: not in this capture (bridge /points exposes vreg_15000..15039 only).

## Their 'new ones'
15022 vs p_uti: n=4815 slope=0.9985 intercept=-2.3 r=0.9984 rms=96.67 x[-4883..236] y[-4888..145]
15013 vs p_fhp: n=4815 slope=1.0000 intercept=0.3 r=0.9985 rms=91.32 x[-4658..4951] y[-4660..4959]
15003 vs p_sun: n=4815 slope=0.9899 intercept=-5.9 r=0.9999 rms=23.39 x[0..5141] y[0..5100]
15003 values mod 100: 1 distinct; top [(0, 4815)]
15016: 2 distinct; top [(2, 4659), (6, 156)]
15026: 1 distinct; top [(1, 4815)]
(15016,15026,1301 offgridreason,elecnet_state): 3 distinct; top [((2, 1, 0, 0), 4659), ((None, None, 0, 0), 2783), ((6, 1, 0, 0), 156)]
15507/15508/15509/16001 via bridge (ongrid_mode, self_reserve, tou_reserve, vreg_16001): 3 distinct; top [((2, 5, 5, 5), 4793), ((2, 5, 5, None), 2804), ((None, None, None, None), 1)]
pair 15006:15007 (hi word, sign of lo): {(0, 'nonneg'): 2885, (-1, 'neg'): 1930}
pair 15010:15011 (hi word, sign of lo): {(0, 'nonneg'): 4815}
pair 15012:15013 (hi word, sign of lo): {(0, 'nonneg'): 2827, (-1, 'neg'): 1988}
pair 15014:15015 (hi word, sign of lo): {(-1, 'neg'): 3531, (0, 'nonneg'): 1284}

## Their 'matches' and 'differs'
15020: 1 distinct; top [(13600, 4815)]
15024 (unsigned, mHz): 14 distinct; top [(50010, 1458), (49969, 961), (49980, 759), (50020, 509), (50000, 410), (49989, 258), (49960, 184), (50030, 145)]
15024/1000 vs bridge frequency_hz: n=4815 slope=0.9574 intercept=2.1 r=0.9758 rms=0.00 x[49.92..50.05] y[49.93..50.059]
15025/10 vs bridge voltage_v: n=4815 slope=0.9996 intercept=0.1 r=0.9996 rms=0.06 x[239.1..248] y[239.1..248.1]
15036: 1 distinct; top [(948, 4815)]
16002: 1 distinct; top [(20, 4793)]
15017: 1 distinct; top [(20, 4815)]
15017 vs t_amb: n=4815 slope=0.0000 intercept=20.0 r=nan rms=0.00 x[13.3..29.6] y[20..20]
15011 vs p_load: n=4815 slope=0.9921 intercept=1.8 r=0.9917 rms=53.59 x[158..3374] y[158..3374]
16000 vs p_load: n=4793 slope=0.9920 intercept=1.8 r=0.9916 rms=53.71 x[158..3374] y[158..3374]
15011 values mod 1000: 259 distinct; top [(216, 136), (217, 131), (218, 121), (219, 106)]
16000 values mod 1000: 260 distinct; top [(216, 140), (217, 128), (218, 120), (219, 104)]

## Per-day counter deltas (Fri 2026-10-09, from the 1301 daily reset to the last sample)
TotWhAbs 15033:15034: register 0.026 kWh vs 1301 kwh_uti_in 0.026 kWh (00:00->15:59)
TotWhInj 15029:15030: register 17.030 kWh vs 1301 kwh_uti_out 17.029 kWh (00:00->15:59)
15510:15511: register 33.643 kWh vs 1301 kwh_sun 33.643 kWh (00:00->15:59)
15512:15513: register 33.643 kWh vs 1301 kwh_sun 33.643 kWh (00:00->15:59)

## Still unknown on their site
15007: 916 distinct; top [(274, 348), (275, 338), (273, 292), (271, 272), (270, 257), (272, 235)]
samples without raw vregs (bridge /points lacked them): 2783
15007 vs t_amb: n=4815 slope=-109.3309 intercept=1863.2 r=-0.8551 rms=327.42 x[13.3..29.6] y[-1518..277]
15015 vs p_fhp: n=4815 slope=-0.0115 intercept=-79.8 r=-0.0959 rms=199.46 x[-4658..4951] y[-1184..92]
15015 vs p_sun: n=4815 slope=-0.0120 intercept=-58.3 r=-0.1215 rms=198.90 x[0..5141] y[-1184..92]
15015 vs p_load: n=4815 slope=0.0095 intercept=-79.5 r=0.0198 rms=200.34 x[158..3374] y[-1184..92]
15015 vs p_uti: n=4815 slope=0.0278 intercept=-51.0 r=0.2401 rms=194.53 x[-4883..236] y[-1184..92]
15015: 475 distinct; top [(-2, 704), (-1, 620), (1, 444), (0, 318), (2, 316), (-3, 293)]

# 15016 transitions (one event)
Thu 18:52:16 15016=6 during the bridge's M704 force discharge (WSetEna 1, p_fhp ~4.9 kW)
Thu 19:30:20 15016=2, the same sample in which wset_enabled went 1 -> 0 (release)

# 15006:15007 as s32 vs M701.Var: n 4816, slope -0.849, r -0.936, rms 223 (not an identity)
# 15007 < 0 (hi word 15006 = -1) from Fri 06:58 to the end of the capture (solar hours)
# 15014:15015 as s32: -1184..-812 during the force discharge, -14..20 at night, -611..7 in solar hours
# 15014:15015 vs 704.WSetEna: |r| 0.847 (top match in a scan of all numeric mb./dc. fields)

# Direct Connect generator / backup settings on this site (255 slow reads, constant)
1405.genStartSoc 0, 1405.genStopSoc 0, 1405.BBBackupSoc 20, 1901.genEn 0, 1901.genStartElec 20, 1901.genCloseElec 80
```
