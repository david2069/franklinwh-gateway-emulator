"""Profiles may assert only what franklinwh-modbus firmware_quirks.json records as documented or
measured for their firmware (franklinwh-modbus#91/#118, read at the pinned version). "Reported"
behaviour stays opt-in. Settings with no measurement are listed in QUIRK_ASSUMED with a reason."""

import pytest
from franklinwh_modbus.firmware_quirks import load_firmware_quirks, quirks_for

from franklinwh_emulator.profiles import ALWAYS, PROFILES, QUIRK_ASSUMED, QUIRK_BASIS

ACTIONABLE = {"documented", "measured"}
FAITHFUL = sorted(n for n in PROFILES if n.startswith("agate-"))


def _asserts(profile, field):
    neutral, _ = QUIRK_BASIS[field]
    return neutral is ALWAYS or getattr(profile, field) != neutral


def test_basis_ids_exist_in_the_file():
    known = set(load_firmware_quirks()["behaviours"])
    missing = {b for _, ids in QUIRK_BASIS.values() for b in ids} - known
    assert not missing


@pytest.mark.parametrize("name", FAITHFUL)
def test_profile_asserts_only_documented_or_measured(name):
    p = PROFILES[name]
    quirks = quirks_for(p.firmware)
    assert quirks is not None, f"{p.firmware} is not in firmware_quirks.json"
    unbacked = []
    for field in QUIRK_BASIS:
        if not _asserts(p, field) or (name, field) in QUIRK_ASSUMED:
            continue
        for bid in QUIRK_BASIS[field][1]:
            if not any(o["status"] in ACTIONABLE for o in quirks.get(bid, [])):
                unbacked.append((field, bid))
    assert unbacked == []


def test_assumptions_are_still_unmeasured():
    """An assumption must go once the file measures it, so the profile follows the file."""
    stale = []
    for (name, field), _why in QUIRK_ASSUMED.items():
        quirks = quirks_for(PROFILES[name].firmware) or {}
        for bid in QUIRK_BASIS[field][1]:
            if any(o["status"] in ACTIONABLE for o in quirks.get(bid, [])):
                stale.append((name, field, bid))
    assert stale == []


def test_drift_check_catches_a_reported_only_setting():
    """Break check: a V12 profile with 15016 = 1 (reported only) must fail the rule."""
    from dataclasses import replace
    bad = replace(PROFILES["agate-v12r02b60d00-candidate"], islanded_15016=1)
    quirks = quirks_for(bad.firmware)
    assert _asserts(bad, "islanded_15016")
    assert not any(o["status"] in ACTIONABLE for o in quirks["ext_15016_status"])
