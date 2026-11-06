"""#287: V2L, an EV supplying the home off-grid through the generator input."""

import pytest

from conftest import make_gateway
from franklinwh_emulator.control import ApiError, apply, gateway_state

V2L = {"circuit": 1, "ev_kwh": 60, "ev_soc": 80, "max_w": 2200, "min_soc": 20, "charge_apower": False}


def gateway(clock, **site):
    return make_gateway(clock, id="v", seed=7, site={"initial_soc": 60, "home_base_w": 1500,
                                                      "solar_peak_w": 0, "v2l": dict(V2L), **site})


def run(gw, clock, seconds):
    snap = None
    for _ in range(seconds):
        clock.advance(1); snap = gw.site.step()
    return snap


def test_ready_on_grid_supplying_off_grid(clock):
    gw = gateway(clock)
    snap = run(gw, clock, 3)
    st = gateway_state(gw)["v2l"]
    assert snap.v2l_w == 0 and st["state"] == "Standby"                  # the app's word (#289)
    assert st["voltage_v"] == 0 and st["frequency_hz"] == 0               # as the app shows in Standby
    apply(gw, "grid", {"available": False})
    snap = run(gw, clock, 5)
    st = gateway_state(gw)["v2l"]
    assert st["state"] == "Active" and 0 < snap.v2l_w <= 2200
    assert st["voltage_v"] > 200 and 49 < st["frequency_hz"] < 51          # the app's Vehicle Data
    soc0 = st["ev_soc"]
    run(gw, clock, 600)
    assert gateway_state(gw)["v2l"]["ev_soc"] < soc0 and gateway_state(gw)["v2l"]["energy_kwh"] > 0


def test_stops_at_min_soc_when_disabled_and_while_the_generator_runs(clock):
    gw = gateway(clock)
    apply(gw, "grid", {"available": False})
    apply(gw, "v2l", {"ev_soc": 20})
    assert run(gw, clock, 3).v2l_w == 0                       # at its floor
    apply(gw, "v2l", {"ev_soc": 70, "on": False})
    assert run(gw, clock, 3).v2l_w == 0 and gateway_state(gw)["v2l"]["state"] == "Disabled"
    apply(gw, "v2l", {"on": True})
    assert run(gw, clock, 3).v2l_w > 0
    gw.site.generator.relay_closed = True                     # a generator on the same input
    assert gw.site._v2l_w(3000, True) == 0


def test_ev_on_that_circuit_does_not_charge_while_supplying(clock):
    gw = gateway(clock, loads=[{"name": "house", "type": "base", "w": 1500},
                               {"name": "ev", "type": "base", "w": 3000, "circuit": 1}])
    assert run(gw, clock, 2).loads["ev"] > 0
    apply(gw, "grid", {"available": False})
    snap = run(gw, clock, 5)
    assert snap.v2l_w > 0 and snap.loads["ev"] == 0


def test_direct_connect_1411_reports_v2l_on_the_car_switch(clock):
    gw = gateway(clock)
    apply(gw, "grid", {"available": False})
    run(gw, clock, 120)
    body = gw.dc.payloads.smart_circuit_meter({})
    assert body["CarSWPower"] < 0 and body["CarSWExpEnergy"] > 0


def test_v2l_needs_the_modules_and_a_circuit(clock):
    with pytest.raises(ValueError, match="generator module"):
        gateway(clock, accessories={"generator_module": False})
    with pytest.raises(ValueError, match="circuit 3"):
        gateway(clock, v2l={**V2L, "circuit": 3})
    with pytest.raises(ValueError, match="unknown keys"):
        gateway(clock, v2l={**V2L, "colour": "red"})
    gw = make_gateway(clock, id="n", seed=7)
    with pytest.raises(ApiError, match="no V2L"):
        apply(gw, "v2l", {"on": True})


def test_ctl_v2l_verb():
    from franklinwh_emulator.cli import _ctl_request
    assert _ctl_request("v2l", ["on", "soc=55"]) == ("POST", "v2l", {"on": True, "ev_soc": 55.0})



def test_allow_charging_to_apower(clock):
    """#289: the app's "Allow charging to aPower?": on, V2L also charges the aPower within max_w;
    off, it covers the house only."""
    gw = gateway(clock, home_base_w=300)
    apply(gw, "grid", {"available": False})
    snap = run(gw, clock, 5)
    assert snap.v2l_w == pytest.approx(snap.load_w, rel=0.05) and snap.battery_w > -50   # house only
    apply(gw, "v2l", {"charge_apower": True})
    snap = run(gw, clock, 5)
    assert snap.v2l_w == pytest.approx(2200, abs=1) and snap.battery_w < -1000            # charging
    assert gateway_state(gw)["v2l"]["charge_apower"] is True


def test_charge_apower_defaults_on_as_the_app_shows(clock):
    from franklinwh_emulator.v2l import V2L as Model
    assert Model.from_dict({"circuit": 1}).charge_apower is True
