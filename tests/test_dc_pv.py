"""aPower S DC PV on its MPPT inputs (#180), modelled from the datasheet ratings."""

import pytest

from conftest import make_gateway
from franklinwh_emulator.control import apply

S = {"ac_type": 1, "model": {"agate": 103, "apower": 4}}


def _site(clock, dc, **kw):
    gw = make_gateway(clock, site={**S, "dc_pv": dc, "cap_forced_solar": False, **kw})
    gw.site.solar_override_w = 0                                      # AC PV off: DC PV only
    return gw


def test_config_needs_an_apower_s_and_respects_the_mppt_limit(clock):
    with pytest.raises(ValueError, match="needs an aPower S"):
        make_gateway(clock, site={"ac_type": 1, "dc_pv": {"rated_w": 5000}})
    with pytest.raises(ValueError, match="15000 W"):
        make_gateway(clock, site={**S, "dc_pv": {"rated_w": 16000}})
    two = make_gateway(clock, site={"ac_type": 1, "model": {"agate": 103, "apower": [4, 4]},
                                    "dc_pv": {"rated_w": 25000}})
    assert two.site.dc_pv["inverter_w"] == 23000


def test_inverter_shared_by_dc_pv_and_discharge(clock, monkeypatch):
    gw = _site(clock, {"rated_w": 15000}, initial_soc=100, utility={"battery_export": True})
    s = gw.site
    monkeypatch.setattr(s, "_dc_pv_w", lambda ts: 14000.0)
    s.load_override_w = 1000
    clock.advance(10)
    snap = s.step()                          # full battery: 11.5 kW through the inverter
    assert snap.solar_w == pytest.approx(11500, abs=5) and snap.solar_clipped_w == pytest.approx(2500, abs=5)
    assert snap.pv_by_input["mppt"] == pytest.approx(11500, abs=5)
    monkeypatch.setattr(s, "_dc_pv_w", lambda ts: 8000.0)
    s.soc = 50
    apply(gw, "vpp", {"watts": -11500})      # ask for a full discharge
    clock.advance(10)
    assert s.step().battery_w == pytest.approx(3500, abs=5)          # 11.5 kW less the DC PV


def test_no_dc_export_serves_only_house_and_battery(clock, monkeypatch):
    gw = _site(clock, {"rated_w": 10000, "export": False}, initial_soc=100)
    s = gw.site
    monkeypatch.setattr(s, "_dc_pv_w", lambda ts: 9000.0)
    s.load_override_w = 2000
    clock.advance(10)
    snap = s.step()
    assert snap.solar_w == pytest.approx(2000, abs=5) and snap.grid_w == pytest.approx(0, abs=5)
