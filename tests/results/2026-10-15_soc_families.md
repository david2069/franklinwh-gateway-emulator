# Which state-of-charge readings agree, reference aGate, 2026-10-15

**Data:** paired capture Thu 2026-10-08 18:52 → Fri 2026-10-09 16:00 AEDT (`~/franklinwh-captures/paired_2026-10-08_redacted.jsonl`, outside git): Modbus through the bridge, and Direct Connect 1301 every 10 s plus 1705 every 5 min. Self-Consumption with reserve 5 % throughout; SoC 13.3–100 % (BMS).

| Comparison | n | mean diff | RMS | range |
|---|---|---|---|---|
| (1301 `fhpSoc` − 5) / 0.95 vs 1301 `soc` | 7,606 | +0.000 | 0.014 | ±0.11 |
| 1301 `fhpSoc` vs 1301 `soc` | 7,606 | +2.808 | 3.271 | 0 … +4.62 |
| 1705 `batSoc` vs 1301 `fhpSoc` | 255 | +0.001 | 0.018 | ±0.10 |
| M713 `SoC` / 10 vs 1301 `soc` | 7,596 | +0.021 | 0.258 | −1.00 … +1.21 |
| 15035 / 10 vs 1301 `soc` | 4,815 | −0.052 | 0.077 | −1.30 … +1.05 |
| M713 `WHAvail` / `WHRtg` × 100 vs 1301 `soc` | 7,596 | −0.006 | 0.047 | −1.26 … +1.05 |

**Observed (one site, one capture):**
- There are two families. The **BMS SoC** is 1301 `fhpSoc` = 1705 `batSoc`. The **displayed SoC** is 1301 `soc` = (BMS − 5) / 0.95, the BMS SoC rescaled so 5 % reads as 0.
- M713 `SoC` follows the displayed SoC in whole percent: the raw ×10 value is always a multiple of 10. 15035 follows it at 0.1 %. `WHAvail`/`WHRtg` follows it too.
- The ±1 % ranges are sample-timing differences between the two bridges (a few seconds), not offsets.

**Not established:** whether the 5 is the fixed 5 % floor or the Self-Consumption reserve, which was also 5 % for the whole capture. A capture with a different reserve would separate the two. Cloud SoC was not captured.
