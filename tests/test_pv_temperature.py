"""PV output falls as the panels heat up (#109)."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.control import gateway_state
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.site import PV_TEMPERATURE, pv_cell_c

NOON_SYD = 1_768_438_800          # 2026-01-15 01:00 UTC = 12:00 AEDT
SYD = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}


def gw(**kw):
    d = {"id": "g", "seed": 7, "location": SYD, "weather": {"model": "synthetic", "kwp": 6.6, "sky": "clear"}, **kw}
    return Gateway(GatewayConfig.from_dict(d), clock=ManualClock(NOON_SYD))


def test_noct_cell_temperature():
    assert pv_cell_c(20.0, 800.0, 45.0) == pytest.approx(45.0)        # NOCT conditions
    assert pv_cell_c(30.0, 0.0, 45.0) == 30.0                         # dark: the air's temperature


def test_hot_day_gives_less_than_a_cold_one():
    g = gw()
    s = g.site
    s.ambient_override_c = 40.0
    hot = s.solar_model_w(NOON_SYD)
    cell_hot, f_hot = s.pv_temperature_factor(NOON_SYD)
    s.ambient_override_c = 5.0
    cold = s.solar_model_w(NOON_SYD)
    cell_cold, f_cold = s.pv_temperature_factor(NOON_SYD)
    assert cold > hot > 0
    assert f_hot < 1.0 < f_cold                               # relative to the 45 °C reference cell
    assert cold / hot == pytest.approx(f_cold / f_hot, rel=1e-6)
    assert cell_hot - cell_cold == pytest.approx(35.0)        # same irradiance, 35 °C apart
    assert f_hot == pytest.approx(1 + PV_TEMPERATURE["temp_coeff"] * (cell_hot - 45.0))


def test_off_and_tunable_and_in_state():
    off = gw(pv_temperature=False)
    off.site.ambient_override_c = 40.0
    on = gw()
    on.site.ambient_override_c = 40.0
    assert off.site.solar_model_w(NOON_SYD) > on.site.solar_model_w(NOON_SYD)
    assert gateway_state(off)["pv_temperature"] is None
    st = gateway_state(on)["pv_temperature"]
    assert st["factor"] < 1 and st["cell_c"] > 40 and st["temp_coeff"] == -0.004
    steep = gw(pv_temperature={"temp_coeff": -0.008})
    steep.site.ambient_override_c = 40.0
    assert steep.site.solar_model_w(NOON_SYD) < on.site.solar_model_w(NOON_SYD)
    with pytest.raises(ValueError, match="pv_temperature: unknown keys"):
        gw(pv_temperature={"gamma": -0.004})


def test_night_is_unaffected():
    g = Gateway(GatewayConfig.from_dict({"id": "g", "seed": 7, "location": SYD,
                                         "weather": {"model": "synthetic", "kwp": 6.6}}),
                clock=ManualClock(NOON_SYD + 12 * 3600))
    assert g.site.solar_model_w(g.site.clock.now()) == 0.0
    assert g.site.pv_temperature_factor(g.site.clock.now()) == (None, 1.0)
