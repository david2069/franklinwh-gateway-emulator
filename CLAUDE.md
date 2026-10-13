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

## Layout

| Path | Purpose |
|---|---|
| `src/franklinwh_emulator/` | The emulator |
| `tools/` | Capture and test tools for the reference aGate (`paired_collect`, `redact_capture`, `charge_test`, `bms_collect`, `agate_scan`) |
| `tests/` | pytest suite; `tests/results/` holds retained results |
| `docs/` | `CALIBRATION.md`, `FINDINGS.md`, `DIRECT_CONNECT.md`, `DESIGN.md` |
| `scripts/check_pii.py` | PII scanner (terms from `~/.config/pii_terms`); `scripts/hooks/pre-push` |

## Commands

```bash
.venv/bin/python -m pytest -q          # full suite
python3 scripts/check_pii.py --scan     # before every commit
```

Use a login shell (`zsh -lc`) for Homebrew's `gh` and `python3.12`.
