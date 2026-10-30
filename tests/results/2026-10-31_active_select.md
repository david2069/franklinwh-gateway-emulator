# #62: the "Actions on" select after a reload, 2026-10-31

Base: main 8aa6d4d + branch fix/active-select.

## Full suite
```
490 passed in 29.21s
```

## Headless Chrome, example fleet (ports 0)
- Before (main cdd3726, earlier today): select au-2phase, reload: card au-2phase pressed, select "site".
- After, 1280 and 500 px: card au-2phase pressed, select "au-2phase".
- Choosing spec in the select: card spec pressed; after a reload both still spec.

## Break check
test_active_gateway_select_marks_the_option with the old index.html: failed.
