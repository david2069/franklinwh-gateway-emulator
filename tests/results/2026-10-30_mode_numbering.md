# Mode numbering labelled (#28)

**Changed:** `schema.py`: `labels` on enum fields; mode field labelled from `MODE_NAMES` and
documented as Modbus 15507 numbering, with `DC_MODE_NUMBERS` (Direct Connect / cloud: 1 TOU,
2 Self-Consumption, 3 Emergency Backup) stated in the action doc. `actions.js`: options show
"value · label". `validate.DOCS["native_mode"]`, `examples/fleet.yaml` comment, `ctl` help
(`mode <1-4>` → `<1|2|3>` with names), `ctl status` prints the mode name.

**Basis:** 15507 values per `site.py` (V12R02B60D00, 2026-10-21, one site, franklinwh-modbus#75);
Direct Connect `scheduling_type` per `dc_payloads.PROGRAMMES` / docs/DIRECT_CONNECT.md (1726).

**Tests:** `test_schema.py::test_mode_labels_and_the_other_numbering` (labels = `MODE_NAMES`;
`DC_MODE_NUMBERS` = `PROGRAMMES`; both numberings in the doc); the JavaScriptCore test checks
the mode options read `1 · Emergency Backup`, `2 · Self-Consumption`, `3 · TOU`.

Full suite: **421 passed** (Python 3.12, local).

**Break check:** `DC_MODE_NUMBERS["Time-of-Use"]` set to 3 → `test_mode_labels_and_the_other_numbering` failed; restored.
