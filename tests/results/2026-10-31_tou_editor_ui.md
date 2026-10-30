# TOU schedule editor UI (#58 part 2), 2026-10-31

Base: main cdd3726 + branch feat/tou-editor.

## Full suite
```
482 passed in 24.69s
```

## Break checks
- touDraft without the gap filter: test_tou_editor_round_trips_the_schedule failed (gap blocks came back as real blocks).
- Before the touAddRow fix, headless Chrome showed the server refusing an added block (24:00-24:00) on a full day; now the test checks the split (17:00-20:30, 20:30-24:00).
- Before the control.py fix, the overlap error read "tou: tou: ..."; the test now checks a single prefix.

## Headless Chrome (example fleet, ports 0), 1280 and 500 px
- tou-reserve: the timeline showed SOLAR 0-70.8%, SELF 70.8-87.5%, SOLAR 87.5-100%; the table had 3 blocks.
- Edit: first block -> 7 GRID_EXPORT, Add block, On-Peak buy 0.55, Send -> "Schedule applied to tou-reserve"; table 00:00-17:00 GRID_EXPORT, 17:00-21:00 SELF 0.55, 21:00-22:30 SOLAR, 22:30-24:00 New SELF.
- Last start 10:00, Send -> "tou: 'Off-Peak' (00:00-17:00) overlaps 'New' (10:00-24:00)"; the editor stayed open.
- No horizontal overflow at 500 px.
- Seen, not from this change: after a reload the "Actions on" select shows the first gateway while another is active (separate issue).
