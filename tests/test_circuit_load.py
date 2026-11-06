"""#279: watts set on a smart circuit at runtime."""

import pytest

from conftest import make_gateway
from franklinwh_emulator.control import ApiError, apply, gateway_state


def test_watts_on_a_circuit_without_appliances(clock):
    gw = make_gateway(clock, id="s", seed=7, profile="spec")
    site = gw.site
    before = site.step().load_w
    apply(gw, "circuit", {"n": 2, "on": True, "w": 3000})
    clock.advance(1); snap = site.step()
    assert snap.circuit_w[2] == pytest.approx(3000) and snap.load_w == pytest.approx(before + 3000, rel=0.05)
    st = gateway_state(gw)["circuits"]["2"]
    assert st["set_w"] == 3000 and st["power_w"] == pytest.approx(3000)
    apply(gw, "circuit", {"n": 2, "on": False})                 # switched off: nothing drawn, kept
    clock.advance(1); snap = site.step()
    assert snap.circuit_w[2] == 0 and site.circuit_load_w[2] == 3000
    apply(gw, "circuit", {"n": 2, "on": True})
    apply(gw, "circuit", {"n": 2, "w": None})                   # back to its appliances (none)
    clock.advance(1); snap = site.step()
    assert snap.circuit_w[2] == 0 and gateway_state(gw)["circuits"]["2"]["set_w"] is None


def test_watts_replace_the_circuits_appliances(clock):
    gw = make_gateway(clock, id="s", seed=7, profile="spec",
                      site={"loads": [{"name": "kettle", "type": "base", "w": 2000, "circuit": 1}]})
    clock.advance(1)
    assert gw.site.step().circuit_w[1] == pytest.approx(2000, rel=0.2)
    apply(gw, "circuit", {"n": 1, "w": 500})
    clock.advance(1); snap = gw.site.step()
    assert snap.circuit_w[1] == pytest.approx(500) and snap.loads["kettle"] == 0


def test_bad_circuit_bodies(clock):
    gw = make_gateway(clock, id="s", seed=7, profile="spec")
    with pytest.raises((ApiError, ValueError), match="0 or more"):
        apply(gw, "circuit", {"n": 1, "w": -5})


def test_ctl_circuit_watts():
    from franklinwh_emulator.cli import _ctl_request
    assert _ctl_request("circuit", ["2", "on", "w=7000"]) == ("POST", "circuit", {"n": 2, "on": True, "w": 7000.0})
    assert _ctl_request("circuit", ["2", "w=auto"]) == ("POST", "circuit", {"n": 2, "w": None})
    assert _ctl_request("circuit", ["2", "off"]) == ("POST", "circuit", {"n": 2, "on": False})


def test_watts_on_a_circuit_are_shed_off_grid_like_any_circuit_load(clock):
    gw = make_gateway(clock, id="s", seed=7, profile="spec", site={"initial_soc": 60})
    apply(gw, "circuit", {"n": 1, "on": True, "w": 40000})       # far more than the battery can carry
    apply(gw, "grid", {"available": False})
    for _ in range(5):
        clock.advance(1); snap = gw.site.step()
    assert snap.circuits_shed and snap.circuit_w[1] == 0
