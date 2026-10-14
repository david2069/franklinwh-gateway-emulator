# CLAUDE.md: franklinwh-gateway-emulator

> Auto-loaded by Claude Code. Read `.agents/policies/` before doing anything. Operating notes
> for the owner's reference aGate (`docs/HANDOFF.md`) live in the private repo
> david2069/franklinwh-gateway-emulator-private: read them before any work on that device.

## Critical rules

1. **Change management (AP-1):** queue → plan → execute, one fix at a time, through the full
   cycle (code → test → verify → commit). Log a defect before fixing it.
   `.agents/policies/change_management.md`.
2. **Save test results** to `tests/results/` (dated) and reference the file in the commit.
3. **Test before sharing.** No code, snippet or data goes to anyone else (issues, gists, other
   repos, other sessions) untested. Results go in `tests/results/` first.
4. **Evidence standard (AP-14):** claims carry a tier and a citation. One site or one dataset is a
   candidate, never confirmed. No guessed causes in issues, docs or replies.
   `.agents/policies/evidence_standard.md`.
5. **PII (AP-3):** the pre-commit hook runs `scripts/check_pii.py` (enable it with
   `git config core.hooksPath scripts/hooks` if unset). No serials, IPs, names, locations or
   home paths in tracked files, commit messages or issues. Clean fetched or captured data where
   it is written, never commit raw device or API output, and use the policy's fixed fake
   example values. `.agents/policies/pii_policy.md`.
6. **Hardware:** read-only by default; each write needs the owner's explicit go-ahead and a
   restore plan; one Modbus client; never write 1821/1823/1825.
   `.agents/policies/hardware_test_protocol.md`.
7. **The owner's schedules belong to the owner.** Diagnose read-only; never create, edit,
   disable or trigger them.
8. **Other repos:** defects found in another repo are filed there as GitHub issues, with
   evidence; code changes there belong to that repo's session.

## Working rules (shared across the FranklinWH repos, with this repo's additions)

### Ground every claim in evidence
Don't speculate. What a field means, what an API does or what the hardware reports is a claim and
needs a source:
1. Vendor documentation: FranklinWH commissioning guides, the Modbus and CSIP PICS, the SunSpec
   model definitions (copies in `~/Downloads` on the Mac mini; check the revision, since some are superseded).
2. The API cookbooks: franklinwh-cloud, franklinwh-modbus, franklinwh-direct-connect-api.
3. A live read: the paired capture (`tools/paired_collect.py`) or the Modbus bridge's
   `/api/gateways/default/points`, with the file in `tests/results/`.
4. The code, read, not inferred from a name, a neighbouring field or a comment.

Cite the source and say what is unverified. One observation of an enum is not a mapping; a field's
name is not its meaning. **Emulator values are not evidence of what a register means**: they are the
emulator's own.

### Test thoroughly
- Every code change ships with tests, and the full suite passes. Retain the result in `tests/results/`.
- A test that passes with the fix disabled proves nothing: break the change and watch it fail.
- Test the failure paths: unavailable, timeout, ambiguous, disabled, not measured.
- Prefer the emulator for device behaviour, within its scope (below).

### Critical changes need real verification
Critical means: anything that writes to hardware, changes dispatch, alters safety behaviour,
migrates a schema, pauses the bridge or opens a direct Modbus client. It needs a real test on the
reference aGate (coordinated with the owner, other clients quiesced), or an emulator run that genuinely
covers the path. A mock written for the test in hand proves only that the test agrees with itself.

**What an emulator run verifies.** It counts only for paths the emulator reproduces from observation
of the reference aGate:

| Area | Status | Basis |
|---|---|---|
| Modbus framing, function codes, exception codes for bad addresses and functions | substitute | hand-written server; layout matched the full scan of 2026-10-07 |
| Register layout and static values, profile `agate-v10r01b04d00` | substitute for that firmware | 589 static points, `tests/data/reference_scan_static_2026-10-07.json` |
| Writes: M704 obeyed; 15507–15509 acknowledged and discarded; DC 1207 stored | substitute, observed once each on one site | `tests/results/`, private research notes |
| Direct Connect read payloads | substitute for V12R02B30D06 | captured from the reference site |
| Live extension registers | partial model | 15003, 15011–15013, 15022, 15024/25, 15029–15036, 15040 and 16000 are live, following one site's evidence: candidates, not established (#2, #4); 15514–15516 served as 0, observed once while charging (#3); 15016, 16001/16002 and the rest static or candidate |
| `spec` profile, US split phase, three phase, generator | model | not from a scan |
| `span_unlock` | **withdrawn premise** | no evidence the state exists (owner, 2026-10-14) |
| Connection limits | model | one client, newest refused; oldest-vs-newest behaviour on the aGate not established |
| Timing: latency, ramps, settling, revert timers | model | answers instantly unless a fault is injected |
| Firmware fault modes (0xFFFF values, broken sockets, partial replies, reboot recovery) | not covered | fault injection exists but is not calibrated |
| Battery, solar and grid physics; grid-code responses | model | calibrated on one site; IEEE 1547 values from memory (`docs/DESIGN.md`) |
| Concurrency or stress | not covered | single-threaded asyncio server: a smoke test, not a stress test |

Anything marked model, withdrawn or not covered needs the reference aGate. Every emulator-verified
claim names the profile and the emulator commit it ran against.

### Say so when something is not done
Report outcomes plainly: what was verified, what was assumed, what was skipped and why.

## Layout

| Path | Purpose |
|---|---|
| `src/franklinwh_emulator/` | The emulator |
| `tools/` | Capture and test tools for the reference aGate (`paired_collect`, `redact_capture`, `charge_test`, `bms_collect`, `agate_scan`) |
| `tests/` | pytest suite; `tests/results/` holds retained results |
| `docs/` | `CALIBRATION.md`, `FINDINGS.md`, `DIRECT_CONNECT.md`, `DESIGN.md` |
| `scripts/check_pii.py` | PII scanner (terms from `~/.config/pii_terms`); `scripts/hooks/pre-commit` and `pre-push` |

## Commands

```bash
.venv/bin/python -m pytest -q          # full suite
python3 scripts/check_pii.py --scan     # before every commit
```

Use a login shell (`zsh -lc`) for Homebrew's `gh` and `python3.12`.
