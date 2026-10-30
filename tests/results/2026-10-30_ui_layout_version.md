# Web UI: version, actions column, pinned active gateway (#34)

**Changed:** `control.py` `GET /version` (`version_info()`: `__version__` + `git rev-parse
--short HEAD` when run from a checkout; cached per server); `index.html` header version badge and
layout switch, two columns (`col-main`, `col-side`), "Actions on [select]" pinned; `app.js`
`version`, `layout` (localStorage `emulator.layout`, default right), `versionText()` (guarded
until loaded), `toggleLayout()`; styles (two columns from 1100 px, sticky scrolling actions
column, switch hidden below 1100 px). `schema.py` vpp.watts doc.

**Tests:** new `test_version_endpoint`; `test_schema_over_http` adjusted to the longer watts doc.
Full suite: **424 passed** (Python 3.12, local).

**Rendered (local, headless Chrome):** 1600 px: badge "v0.1.0 · 194989f", actions in the right
column with "Actions on [family]"; 900 px: stacked, switch hidden. First render found a console
error (version text read before `/version` answered); fixed by the guard; no console errors
after.
