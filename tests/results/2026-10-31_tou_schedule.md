# TOU schedule: dispatch codes, tariff periods, prices (#58)

**Basis:** franklinwh-cloud docs/TOU_SCHEDULE_GUIDE.md (local checkout, last changed f7fbaed
2026-06-04): block schema, dispatch code table (1/2/3/6/7/8), wave types (0/1/2/4), and its
caveats (`minDischargeSoc` ignored by the aGate; other limit fields unverified; saving a schedule
doesn't switch the mode in current firmware).

**Changed:** new `tou.py` (parse/validate, gap fill, `block_at`, `target_w`, `DEFAULT_SCHEDULE`);
`site.py` TOU mode follows the active block (`TOU_PEAK` removed; the default schedule reproduces
it), `set_tou_schedule`, `tou_now`, `SiteConfig.tou_schedule/tou_rates`; control action `tou`
(group Battery and modes), state `tou`; `validate.DOCS`; UI: arrays of objects edited as JSON
(`items: object`); README, CHANGELOG.

**Tests:** new `tests/test_tou.py` (22): each dispatch code's battery power (10 cases), the
default schedule reproduces the old behaviour, TOU only in TOU mode, reserve floor under
GRID_EXPORT, limit fields kept but not applied, gap fill and the active block with minutes left and
rates, six malformed schedules refused (also via `validate_fleet`), the `tou` action sets and
resets without changing the mode. The JavaScriptCore form round trip covers the `tou` examples.
Full suite: **480 passed** (local).

**Break check:** HOME made to absorb surplus → `test_each_dispatch_code[1-3000-1000-0]` failed;
restored.
