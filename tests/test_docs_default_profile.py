"""#166: the docs and docstrings name the current default profile and its firmware."""

import re
from pathlib import Path

from franklinwh_emulator import profiles
from franklinwh_emulator.profiles import DEFAULT_PROFILE, PROFILES

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_direct_connect_doc_names_the_default_firmware():
    text = (DOCS / "DIRECT_CONNECT.md").read_text()
    m = re.search(r"Default profile \(`([^`]+)`\): (V\w+)", text)
    assert m, "DIRECT_CONNECT.md must say which firmware the default profile serves"
    assert m.groups() == (DEFAULT_PROFILE, PROFILES[DEFAULT_PROFILE].dc_firmware)


def test_profile_docstrings_name_the_default():
    assert f"``{DEFAULT_PROFILE}`` (the default)" in profiles.__doc__
    assert f"`{DEFAULT_PROFILE}` (the default)" in (DOCS / "DESIGN.md").read_text()


def test_bridge_check_2_matches_the_default_profile():
    """Check 2 can only promise −5 kW if the default profile accepts charge setpoints."""
    text = (DOCS / "BRIDGE_INTEGRATION.md").read_text()
    check2 = text.split("\n2. ", 1)[1].split("\n3. ", 1)[0]
    if PROFILES[DEFAULT_PROFILE].refuse_charge:
        assert "exception 3" in check2 and DEFAULT_PROFILE in check2
