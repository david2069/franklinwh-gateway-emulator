# #94: 1102 on the V12 candidate answers result 0, as the live read showed, 2026-10-31

Base: main a52cf9a + fix/dc-login-result-live.

## Source
fwh-local, owner-approved read-only live login 2026-10-31 (roll call cleared): IBG V12R02B60D00_260924, request {opt:0, minProtocolVer:"V1.00.00"}, reply opt 0, result 0, reason 0, protocolVer V1.12.02; shape 13 lists / 14 scalars, each list one element, no SUNSPEC_FW. Contradicts the relayed stored record (result 1, reason -2) that #81 followed.

## Full suite
```
511 passed in 32.39s
```

## Break check
tests/test_dc_login.py with the #81 code (login_status 1/-2): failed.
