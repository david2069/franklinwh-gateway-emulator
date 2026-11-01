Refs #

## What changed

<!-- One or two sentences. Use "Refs #N", never "Fixes"/"Closes": the issue is labelled `fixed`
     at merge and closed only after a post-release check (docs/issue-policy in the bridges). -->

## Evidence

<!-- CLAUDE.md "Ground every claim in evidence": cite the source for every claim about what a
     field means or how the aGate behaves (vendor document with revision and date, a cookbook, a
     live read with time and firmware, or the code). Say what is unverified. -->

## Break check

<!-- Required. Disable the change, run the tests, and name the ones that failed. -->

Disabled the change; these tests failed:

## Verification

- [ ] Full suite passes (`.venv/bin/python -m pytest -q`); result saved in `tests/results/`
- [ ] UI change: checked in a browser (say which)
- [ ] Docs change: `mkdocs build --strict`

## Changelog and PII

- [ ] `CHANGELOG.md` updated under Unreleased (Keep a Changelog: Added, Changed, Deprecated,
      Removed, Fixed, Security), or not user-facing
- [ ] `python3 scripts/check_pii.py --scan`: no findings
