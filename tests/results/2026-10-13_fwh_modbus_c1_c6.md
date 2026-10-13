# fwh-modbus C1–C6 against the emulator, 2026-10-13

**Requested by:** the franklinwh-modbus session. It wanted to check six pysunspec2 and raw-socket claims against the emulator's reference-site map. Only the emulator was involved; nothing touched the aGate.

**Server:** `franklinwh-emulator`, default profile `agate-v10r01b04d00` (max_connections 1, busy_behaviour "close"), on 127.0.0.1:5020. It ran inside the test harness `2026-10-13_fwh_modbus_c1_c6/logged_emulator.py`, which wraps `ModbusServer._client`/`_handle` at run time to log every connection and request. The emulator code is unchanged. Before the run, the harness was self-tested on port 5021 at 19:19: a read was logged, a second connection was refused, and an fc6 write to 15508 was acknowledged and discarded.

**Client run:** 2026-10-13 19:21:14–19:21:16 AEDT from 127.0.0.1, unit 1. The log for that window is in `2026-10-13_fwh_modbus_c1_c6/harness_log.jsonl`.

| Claim | Predicted by fwh-modbus | Server log | Verdict |
|---|---|---|---|
| C1 scan | one connection, reads from 0/40000 to the end of the map, then disconnect | conn #1, 19:21:14.588–.625: 65 fc3 reads. The first is 40000×3, answered with exception 2 (illegal address), so the scan moved to base 0 and read up to 1126×1. Then disconnect | matches |
| C2 model reads | three separate connect/disconnect pairs, each reading model 1 (2×68 or 40002×68) | conns #2–#4, each one fc3 2×68, then disconnect | matches |
| C3 raw read | one connection, fc3 15500×14, then disconnect | conn #5: fc3 15500×14, then disconnect | matches |
| C4 socket reuse | one connection: fc3 15507×3, then three model-1 reads on the same connection | conn #6, 19:21:15.250–.459: 15507×3, then 2×68 three times, then disconnect | matches |
| C5 sequencer write | no write sent | no fc6/fc16 in the log | matches |
| C6 raw write with no socket | no request and no write | no request outside conns #1–#6 | matches |

**Totals:** 6 connections, 0 refused, 0 writes, 73 requests, 1 exception (the 40000 probe). After the run, 15507–15509 read [2, 20, 20], the same as at start. That read went through a separate connection after the client's window, so it is not in the extract.

**Limits:** these results show the emulator's behaviour, not the aGate's. On a busy session the emulator refuses the newest connection; whether the real aGate closes the oldest or the newest connection isn't established. The client-side behaviour (pysunspec2 1.3.3) is fwh-modbus's to record.
