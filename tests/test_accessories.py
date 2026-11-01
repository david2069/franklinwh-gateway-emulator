"""Accessory modules a gateway has fitted (#189)."""

import pytest

from conftest import make_gateway
from franklinwh_emulator.control import ApiError, apply, gateway_state


def test_everything_is_fitted_by_default(clock):
    gw = make_gateway(clock)
    assert gateway_state(gw)["accessories"] == {"smart_circuits": True, "generator_module": True,
                                                "apbox": True}
    assert sorted(gw.site.circuits) == [1, 2]                      # AU aGate


def test_no_smart_circuits_module_keeps_the_keys_zeroed(clock):
    """Direct Connect sends a missing module's keys, zeroed (fwh-local capture, V12R02B30D06)."""
    gw = make_gateway(clock, site={"accessories": {"smart_circuits": False}})
    assert gw.site.circuits == {}
    p = gw.dc.payloads
    sw = p.smart_circuits({})
    for n in (1, 2, 3):
        assert sw[f"Sw{n}Name"] == f"Circuit {n}" and sw[f"Sw{n}Mode"] == 0
    rs = p.ibg_run_status({})
    assert [rs[f"smartRelay{n}Status"] for n in (1, 2, 3)] == [0, 0, 0]
    m = p.smart_circuit_meter({})
    assert m["SW1ExpEnergy"] == m["SW2ExpEnergy"] == m["CarSWPower"] == 0.0
    with pytest.raises(ValueError, match="accessories.smart_circuits is false"):
        make_gateway(clock, site={"accessories": {"smart_circuits": False},
                                  "circuits": {1: {"name": "Pool"}}})


def test_generator_needs_the_generator_module(clock):
    with pytest.raises(ValueError, match="generator_module is false"):
        make_gateway(clock, site={"accessories": {"generator_module": False},
                                  "generator": {"installed": True}})
    gw = make_gateway(clock, site={"accessories": {"generator_module": False}})
    with pytest.raises(ApiError, match="no Generator Module"):
        apply(gw, "generator", {"installed": True})


def test_remote_pv_inputs_need_an_apbox(clock):
    with pytest.raises(ValueError, match="need an aPbox"):
        make_gateway(clock, site={"accessories": {"apbox": False},
                                  "pv_inputs": {"proximal": 0.5, "remote1": 0.5}})
    make_gateway(clock, site={"accessories": {"apbox": False}})   # proximal only is fine


def test_bad_accessories_are_refused(clock):
    with pytest.raises(ValueError, match="unknown keys"):
        make_gateway(clock, site={"accessories": {"ahub": True}})
    with pytest.raises(ValueError, match="true or false"):
        make_gateway(clock, site={"accessories": {"apbox": "yes"}})
