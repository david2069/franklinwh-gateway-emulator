"""Installation placement and the cabinet temperature (#110)."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.control import gateway_state
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.thermal import Install

DAY = 1_768_395_600                  # 2026-01-15 00:00 AEDT (13:00 UTC on the 14th)
SYD = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}


def gw(install=None, hour=12.0, **kw):
    d = {"id": "g", "seed": 7, "location": SYD, "weather": {"model": "synthetic", "kwp": 6.6, "sky": "clear"},
         **({"install": install} if install is not None else {}), **kw}
    return Gateway(GatewayConfig.from_dict(d), clock=ManualClock(DAY + hour * 3600))


def run(g, minutes, ambient=None, battery_w=None):
    """Step the site for ``minutes`` with ambient forced; battery idle unless given."""
    s = g.site
    if ambient is not None:
        s.ambient_override_c = ambient
    s.load_override_w, s.solar_override_w = 0, 0
    for _ in range(int(minutes)):
        s.clock.advance(60)
        s.step()
    return s.step().cabinet_c


@pytest.mark.parametrize("d,msg", [
    ({"indoor": "attic"}, "install.indoor must be one of"),
    ({"outdoor": {"sun": "noon"}}, "install.outdoor.sun must be one of"),
    ({"outdoor": {"cover": "tarp"}}, "install.outdoor.cover must be one of"),
    ({"indoor": "heated", "outdoor": {}}, "indoor or outdoor, not both"),
    ({"room_c": 20}, "install needs indoor"),
    ({"indoor": "heated", "colour": "red"}, "install: unknown keys"),
])
def test_bad_install_refused(d, msg):
    with pytest.raises(ValueError, match=msg):
        Install.from_dict(d)


def test_heated_room_stays_near_20():
    g = gw({"indoor": "heated"})
    cab = run(g, 6 * 60, ambient=40.0)
    assert 20.0 < cab < 23.5                              # 20 + 0.1 × 20, not 40
    assert gateway_state(g)["install"]["label"] == "indoor, heated"


def test_sun_and_cover_outdoors_at_noon():
    exposed = run(gw({"outdoor": {"sun": "all-day", "cover": "none"}}), 5 * 60, ambient=25.0)
    roofed = run(gw({"outdoor": {"sun": "all-day", "cover": "roof"}}), 5 * 60, ambient=25.0)
    shaded = run(gw({"outdoor": {"sun": "shade"}}), 5 * 60, ambient=25.0)
    assert exposed > roofed > shaded >= 24.5
    assert exposed - shaded > 8                             # full summer sun on the box


def test_morning_sun_reaches_an_east_side_at_9_not_a_west_side():
    east = gw({"outdoor": {"sun": "morning"}}, hour=9.0).site.surroundings_c(DAY + 9 * 3600, 20.0)
    west = gw({"outdoor": {"sun": "afternoon"}}, hour=9.0).site.surroundings_c(DAY + 9 * 3600, 20.0)
    assert east > west + 3


def test_cabinet_lags_its_surroundings_and_heats_with_power():
    g = gw({"outdoor": {"sun": "shade"}})
    start = run(g, 60, ambient=10.0)
    after_30 = run(g, 30, ambient=40.0)
    assert start < after_30 < start + 15                 # τ 1.5 h: well short of the 30 °C step
    assert run(g, 10 * 60, ambient=40.0) == pytest.approx(40.0, abs=0.5)
    s = g.site
    s.ambient_override_c = 20.0
    hot = s.surroundings_c(s.clock.now()) + g.site.install.self_heat_c_per_kw * 5.0
    assert hot == pytest.approx(35.0, abs=0.5)             # 5 kW adds about 15 °C at steady state


def test_default_is_the_original_estimate_and_modbus_follows():
    g = gw()
    s = g.site
    s.ambient_override_c = 30.0
    snap = s.step()
    assert snap.cabinet_c == pytest.approx(30.0 + 8.0 + abs(snap.battery_w) / 1000.0)
    assert gateway_state(g)["install"] is None
    h = gw({"indoor": "heated"})
    run(h, 60, ambient=30.0)
    h.device.read(0, 2)
    assert h.device.image.get(701, "TmpCab") / 10 == pytest.approx(h.site.step().cabinet_c, abs=0.1)   # 0.1 °C
