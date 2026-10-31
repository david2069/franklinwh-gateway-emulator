"""Temperature limits on the battery, heater and fan, automatic OVER_TEMP (#111)."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.control import apply, gateway_state
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.thermal import BATTERY_THERMAL, battery_factors

NIGHT = 1_767_225_600 + 2 * 3600        # 02:00 UTC, no sun


def gw(**kw):
    d = {"id": "g", "seed": 7, "setup": "in-service", "tz_offset_hours": 0, "initial_soc": 50,
         "install": {"outdoor": {"sun": "shade"}}, **kw}
    return Gateway(GatewayConfig.from_dict(d), clock=ManualClock(NIGHT))


def settle(g, ambient, minutes=12 * 60, load=0, solar=0):
    s = g.site
    s.ambient_override_c, s.load_override_w, s.solar_override_w = ambient, load, solar
    for _ in range(minutes // 10):
        s.clock.advance(600)
        s.step()
    return s.step()


@pytest.mark.parametrize("t,expect", [(25, (1, 1)), (5, (1, 0.5)), (-1, (1, 0)), (-15, (0.5, 0)),
                                      (50, (0.5, 0.5)), (56, (0, 0))])
def test_factors(t, expect):
    assert battery_factors(t, BATTERY_THERMAL) == pytest.approx(expect)


def test_cold_blocks_charging_and_runs_the_heater():
    g = gw()
    settle(g, -5.0)
    s = g.site
    s.solar_override_w, s.load_override_w = 0, 0
    apply(g, "vpp", {"watts": 3000})                         # ask for a 3 kW charge
    snap = settle(g, -5.0, minutes=20)
    st = gateway_state(g)["battery_temperature"]
    assert st["derated"] == "cold" and st["charge_factor"] == 0
    assert snap.battery_w > -1                               # no charging below 0 °C
    assert st["aux_w"] == {"battery_heater": 300.0}
    assert snap.load_w == pytest.approx(300.0, abs=1)        # the heater is drawn as a load


def test_hot_derates_runs_the_fan_and_raises_over_temp():
    g = gw()
    settle(g, 50.0, load=6000)
    st = gateway_state(g)["battery_temperature"]
    assert st["derated"] == "hot" and 0 < st["discharge_factor"] < 1 and "battery_fan" in st["aux_w"]
    settle(g, 63.0)
    s = g.site
    assert "OVER_TEMP" in s.forced_alarms and s.over_temp_auto and s.inverter_faulted()
    settle(g, 30.0)
    assert "OVER_TEMP" not in s.forced_alarms and not s.over_temp_auto


def test_a_hand_set_over_temp_is_left_alone():
    g = gw()
    apply(g, "alarms", {"set": ["OVER_TEMP"]})
    settle(g, 20.0, minutes=60)
    assert "OVER_TEMP" in g.site.forced_alarms


def test_off_by_default_without_install_and_switchable():
    plain = Gateway(GatewayConfig.from_dict({"id": "g", "seed": 7}), clock=ManualClock(NIGHT))
    assert plain.site.batt_temp is None and gateway_state(plain)["battery_temperature"] is None
    on = Gateway(GatewayConfig.from_dict({"id": "g", "seed": 7, "battery_temperature": True}),
                 clock=ManualClock(NIGHT))
    assert on.site.batt_temp is not None
    assert gw(battery_temperature=False).site.batt_temp is None
    assert gw(battery_temperature={"charge_off_c": -5}).site.batt_temp["charge_off_c"] == -5
    with pytest.raises(ValueError, match="battery_temperature: unknown keys"):
        gw(battery_temperature={"freeze_c": 0})
