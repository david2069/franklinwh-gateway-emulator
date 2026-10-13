# fwh-modbus correlate_extensions.py dry run against the emulator, 2026-10-13

**Requested by:** the franklinwh-modbus session, before its owner-approved read-only run on the reference aGate (Wed 2026-10-14 09:30–10:10 AEDT).
**Server:** default profile `agate-v10r01b04d00` on 127.0.0.1:5020, under the logging harness from `2026-10-13_fwh_modbus_c1_c6/logged_emulator.py`.
**Client:** franklinwh-modbus `tools/correlate_extensions.py` (local, not yet pushed), unit 1, 2 s interval.

| Run (AEDT) | Connection | Requests | Extension blocks (fc3), each sample | Model reads | Exceptions |
|---|---|---|---|---|---|
| 1, 22:29:06–22:32:06 | #1 scan, 22:29:06.598–.634 | 65 | — | 64 | 1: the 40000×3 base probe (exception 2) |
| | #2 run, 22:29:06.634–22:32:06.967 | 900 | 15000×44 ✓ ×90 · 15500×17 → exception 2 ×90 · 16000×3 ✓ ×90 | 630 (7 per sample) | 90, all 15500×17 |
| 2, 22:32:20–22:33:20 | #3 scan | 65 | — | 64 | 1: the 40000 probe |
| | #4 run, 22:32:20.739–22:33:20.861 | 330 | 15000×44 ✓ · 15500×14 ✓ · 15514×3 → exception 2 · 16000×3 ✓, ×30 each | 210 | 30, all 15514×3 |

**Totals:** 4 connections, 0 refused, 0 writes (fc3 only). Each run is one scan connection followed by one connection held for the whole run, as the client intends.

**Not shown by this log:** Modbus transaction ids. The harness logs function code, address and count, but not the MBAP header. The reply echoes the request's id (`server.py` `_serve`), but this run does not prove that from the server side.

**Emulator scope:** the emulator's extension map ends at 15513, so reads of 15514–15516 raise exception 2. Nothing measured on the reference site covers 15514–15516.
