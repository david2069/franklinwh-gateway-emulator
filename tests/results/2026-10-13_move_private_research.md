# Move reference-site research to the private repo, 2026-10-13

The owner approved this on 2026-10-13: write-unlock and Debug Mode research, and the site's operating notes, go to david2069/franklinwh-gateway-emulator-private (main @ c727a6e). The safety rules stay public.

Moved, and removed from this repo:
- `docs/HANDOFF.md` (whole file) → private `docs/HANDOFF.md`
- `tools/span_probe.py` → private `tools/span_probe.py`
- FINDINGS D3's original text, the SPAN-toggle test plan, the Debug Mode paragraph, the 1207 write test and the connect-time-writes note, plus BACKLOG `SUPPORT-DEBUG-MODE` → private `docs/WRITE_PLANE_RESEARCH.md`

Trimmed in place, keeping the facts the emulator encodes: CHANGELOG 1207 entry, DIRECT_CONNECT 1207–1825 row and run_status note, FINDINGS SPAN app-path row, CALIBRATION scan note, `dc_payloads.py` and `site.py` comments. Emulator behaviour is unchanged.

Kept public, as safety: never write Direct Connect 1821/1823/1825 (FINDINGS, DIRECT_CONNECT, CLAUDE.md, hardware_test_protocol), and that writes to 15507–15509 are discarded on the reference site.

Result:
```
pytest -q: 293 passed
check_pii.py --scan: SUCCESS
```
