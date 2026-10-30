# #76: V12 candidate echoes the base-0 address on writes to the 40000 mirror, 2026-10-31

Base: main 368c016 + branch feat/write-echo-base0.

## Source
franklinwh-modbus firmware_quirks.json (0.11.0 as installed) `pdu_40000_write_response_address`, V12R02B60D00: "write applied; response echoes the base-0 address (40319 -> 319, 41092 -> 1092)", measured 2026-10-23, one site, franklinwh-modbus#107.

## Full suite
```
505 passed in 30.21s
```

## Break checks
- Candidate profile with write_echo_base0=False: test_mirror_write_reply_address[candidate] failed.
- V10 profile with write_echo_base0=True (no V10 observation in the file): test_quirks_drift failed.

## Wire check (test, raw frames)
Candidate: FC06 to 40000+WSetEna -> reply address WSetEna; FC16 the same; FC06 to base 0 -> request address. V10 profile with the alias enabled: the request address is echoed.
