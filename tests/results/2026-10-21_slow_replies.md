# FAULT-EPISODES item 5: slow replies, 2026-10-21

Asked by the Modbus bridge (late replies arriving on a shared socket). Emulator behaviour only; the aGate's real reply-time distribution is not measured.

**Tests:** `test_slow_reply_lands_on_the_next_read` (tid-1 reply arrives after the client timed out and sent tid 2), `test_slow_p_delays_only_a_fraction` (seeded, 0.3 → 4–20 of 40).

```
slow path disabled:  2 failed
308 passed in 17.72s
```
