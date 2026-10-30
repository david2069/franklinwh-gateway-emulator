"""aPower switched off at its physical switch (#71): no power from that unit."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.control import ApiError, apply, gateway_state
from franklinwh_emulator.gateway import Gateway, GatewayConfig

NIGHT = 1_767_225_600 + 2 * 3600          # 02:00 UTC, no sun


def gw(units=2):
    return Gateway(GatewayConfig.from_dict({"id": "g", "seed": 7, "setup": "in-service", "tz_offset_hours": 0,
                                            "apower_units": units, "initial_soc": 80}),
                   clock=ManualClock(NIGHT))


def discharge(g, load=12_000):
    s = g.site
    s.solar_override_w, s.load_override_w = 0, load
    s.clock.advance(120)
    return s.step().battery_w


def test_switching_one_off_halves_the_power():
    g = gw()
    assert discharge(g) == pytest.approx(10_000, abs=100)
    apply(g, "apower", {"n": 2, "on": False})
    assert discharge(g) == pytest.approx(5_000, abs=100)
    st = gateway_state(g)
    assert st["apower_off"] == [2] and st["apower_units"] == 2
    apply(g, "apower", {"n": 2, "on": True})
    assert discharge(g) == pytest.approx(10_000, abs=100)


def test_all_off_no_battery_power_soc_kept():
    g = gw(units=1)
    apply(g, "apower", {"n": 1, "on": False})
    soc = g.site.soc
    assert discharge(g) == pytest.approx(0, abs=1)
    assert g.site.soc == pytest.approx(soc, abs=0.01)      # no standby drain either


@pytest.mark.parametrize("n", [0, 3])
def test_unknown_unit_refused(n):
    with pytest.raises(ApiError, match="this site has aPower 1–2"):
        apply(gw(), "apower", {"n": n, "on": False})
