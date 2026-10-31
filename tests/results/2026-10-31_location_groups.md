# #86: group gateways by location, compact cards, local time and timezone, 2026-10-31

Base: main (after #84) + branch feat/location-groups.

## Full suite
```
511 passed in 31.43s
```

## Break check
groups.js with the state label lacking the country: test_groups_by_location failed (AU/US "NSW" merged).

## Headless Chrome, example fleet (ports 0), 1280 and 500 px
- Group by state: AU · NSW (2), AU · SA (1), AU · VIC (1), US · AZ (1), no state / province (2).
- Card times: "11:55 · Australia/Sydney", "11:25 · Australia/Adelaide"; no location: "13:55 · UTC+13:00".
- Collapse AU · NSW: 5 cards visible. Compact on. Reload: still state, compact, 5 visible.
- No horizontal overflow at 500 px.
