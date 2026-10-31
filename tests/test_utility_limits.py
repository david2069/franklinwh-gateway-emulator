"""The grid operator's rules and the main breaker (#123)."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.control import ApiError, apply, gateway_state
from franklinwh_emulator.gateway import Gateway, GatewayConfig
from franklinwh_emulator.utility import Utility, trip_seconds
from franklinwh_emulator.validate import validate_fleet

NIGHT = 1_767_225_600 + 2 * 3600        # 02:00 UTC, no sun


def gw(**kw):
    d = {"id": "g", "seed": 7, "setup": "in-service", "tz_offset_hours": 0, "initial_soc": 80,
         "cap_forced_solar": False, **kw}
    return Gateway(GatewayConfig.from_dict(d), clock=ManualClock(NIGHT))


def run(g, solar, load, seconds=120):
    s = g.site
    s.solar_override_w, s.load_override_w = solar, load
    s.clock.advance(seconds)
    return s.step()


def test_aliases_name_the_same_block():
    for key in ("utility", "dnsp", "lines_company"):
        assert gw(**{key: {"export": "prohibited"}}).site.utility_rules.export == "prohibited"
    with pytest.raises(ValueError, match="give one of"):
        gw(utility={"export": "allowed"}, dnsp={"export": "allowed"})
    assert validate_fleet({"gateways": [{"id": "a", "lines_company": {"export": "prohibited"}}]}) == []


def test_operator_defaults_and_bad_values():
    u = Utility.from_dict({"operator": "ausgrid"})
    assert (u.export, u.export_limit(1), u.export_limit(3)) == ("limited", 10000.0, 15000.0)
    for bad, msg in [({"export": "some"}, "export must be one of"), ({"export": "limited"}, "needs export_limit_w"),
                     ({"import_limit_w": -1}, "must be ≥ 0"), ({"colour": 1}, "unknown keys")]:
        with pytest.raises(ValueError, match=msg):
            Utility.from_dict(bad)


def test_zero_export_curtails_solar_and_keeps_the_battery_home():
    g = gw(utility={"export": "prohibited", "export_rule_source": "dnsp"}, initial_soc=100)
    snap = run(g, 6000, 1000)
    assert snap.grid_w > -50                                   # nothing (beyond settling) exported
    assert snap.solar_w < 1500
    assert gateway_state(g)["utility_rules"]["export_limit_in_force_w"] == 0


def test_no_battery_export_caps_discharge_at_the_home_load():
    g = gw(utility={"export": "allowed", "battery_export": False, "export_rule_source": "scheme"})
    apply(g, "vpp", {"watts": -5000})                           # a 5 kW discharge force
    snap = run(g, 0, 1200)
    assert snap.battery_w == pytest.approx(1200, abs=60) and snap.grid_w == pytest.approx(0, abs=60)
    free = gw()
    apply(free, "vpp", {"watts": -5000})
    assert run(free, 0, 1200).grid_w < -3000                    # without the rule it exports


def test_import_limit_trims_grid_charging_then_discharges():
    g = gw(utility={"import_limit_w": 6000}, initial_soc=50)
    apply(g, "vpp", {"watts": 5000})                            # charge 5 kW from the grid
    snap = run(g, 0, 4000)
    assert snap.grid_w == pytest.approx(6000, abs=60) and snap.battery_w == pytest.approx(-2000, abs=60)
    assert gateway_state(g)["import_clipped_w"] == pytest.approx(3000, abs=60)
    apply(g, "vpp", {"watts": 0})                               # standby: the house alone is 9 kW
    snap = run(g, 0, 9000)
    assert snap.grid_w == pytest.approx(6000, abs=60) and snap.battery_w == pytest.approx(3000, abs=60)


def test_trip_curve():
    assert trip_seconds(1.0) is None and trip_seconds(5.0) == 0.0
    assert trip_seconds(2.0) == pytest.approx(40.0)


def test_main_breaker_trips_on_overload_and_resets():
    g = gw(service={"main_breaker_a": 20}, initial_soc=20, self_reserve=20)   # 20 A × 230 V ≈ 4.6 kW
    st = gateway_state(g)["service"]
    assert st["rating_w"] == pytest.approx(20 * g.site.grid.nominal_v_ln, abs=1) and not st["open"]
    s = g.site
    for _ in range(12):                                         # 8 kW, battery at its reserve: ~1.7×
        snap = run(g, 0, 8000, seconds=10)
    st = gateway_state(g)
    assert st["service"]["open"] and not st["grid_connected"] and st["trip_reason"] == "main-breaker"
    run(g, 0, 500, seconds=600)
    assert not gateway_state(g)["grid_connected"]               # stays off until reset
    apply(g, "service", {"reset": True})
    run(g, 0, 500, seconds=600)
    st = gateway_state(g)
    assert st["grid_connected"] and not st["service"]["open"]


def test_actions_set_and_clear_rules():
    g = gw()
    apply(g, "utility", {"export": "prohibited"})
    assert gateway_state(g)["utility_rules"]["export"] == "prohibited"
    apply(g, "utility", {"clear": True})
    assert gateway_state(g)["utility_rules"] is None
    with pytest.raises(ApiError, match="export must be one of"):
        apply(g, "utility", {"export": "lots"})


def test_north_american_models_default_to_no_battery_export():
    """PCS Technical Brief: ESS Import-Only Mode is factory preset on NA models; AU/NZ unchanged;
    a PCS setting overrides either way (#180 phase 2)."""
    def forced(**kw):
        g = gw(**kw)
        g.site.update_remote(enabled=True, mode=1, watts=-5000)      # discharge 5 kW on command
        return g, run(g, 0, 1000)
    us, snap = forced(ac_type=1)                                      # 60 Hz → North America
    assert us.site.model.region == "NA" and gateway_state(us)["battery_export"] is False
    assert snap.grid_w > -50 and snap.battery_w < 1100                # the home only
    au, snap = forced()
    assert gateway_state(au)["battery_export"] is True and snap.grid_w < -3500
    us_on, snap = forced(ac_type=1, utility={"battery_export": True})
    assert gateway_state(us_on)["battery_export"] is True and snap.grid_w < -3500
    au_off, snap = forced(utility={"battery_export": False})
    assert snap.grid_w > -50
