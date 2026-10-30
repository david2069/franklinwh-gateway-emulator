# #66: session restore and a bare on: key, 2026-10-31

Base: main 0e8a037 + fix/session-on-key.

## Full suite
```
491 passed in 28.63s
```

## Break check
test_restore_reads_a_bare_on_key with the old restore (plain yaml.safe_load): failed.
