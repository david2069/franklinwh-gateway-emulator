"""Switching firmware at run time, as an update (#130)."""

import pytest

from conftest import make_gateway
from franklinwh_emulator.control import ApiError, apply, gateway_state


def test_firmware_update_swaps_the_profile_and_keeps_the_site(clock):
    gw = make_gateway(clock, profile="agate-v12r02b60d00-candidate")
    gw.site.soc = 63.0
    apply(gw, "write-reject", {"model": 704, "point": "WSetPct", "abs_gt": 100, "code": 3})
    p = gw.dc.payloads
    assert p.login({})["IBG_VER"] == "V12R02B60D00_260924"
    apply(gw, "firmware", {"profile": "agate-v10r01b04d00"})
    assert gw.profile.name == gw.config.profile == "agate-v10r01b04d00"
    assert gw.server.device is gw.device and gw.device.image.get(1, "Vr") == "V10R01B04D00"
    assert p.login({})["IBG_VER"] == "V12R02B30D06_260304"           # Direct Connect follows
    assert gw.site.soc == 63.0 and len(gw.device.write_rejects) == 1  # site and test rules kept
    assert gateway_state(gw)["profile"] == "agate-v10r01b04d00"
    with pytest.raises(ApiError):
        apply(gw, "firmware", {"profile": "spec"})
    with pytest.raises(ApiError):
        apply(gw, "firmware", {"profile": "agate-v9"})
