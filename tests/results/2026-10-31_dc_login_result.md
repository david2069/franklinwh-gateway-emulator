# #80: 1102 result 1, reason -2 on the V12 candidate, 2026-10-31

Base: main 5585a76 + fix/dc-login-result.

## Source
The 1102 recorded by fwh-direct-connect-bridge (IBG V12R02B60D00_260924, ~2026-10-20T20:1xZ) carried result 1, reason -2 with the full manifest, in reply to franklinwh-direct-connect-api login {opt:0, minProtocolVer:"V1.00.00"}. Relayed 2026-10-31.

## Full suite
```
509 passed in 30.16s
```

## Break check
test_v12_b60_reply_matches_the_recorded_one with the old dc_payloads.py (result 0): failed.

The unmodified LocalClient logs in against both profiles (it does not check result).
