"""Hardware models and their capabilities (#180)."""

import pytest

from conftest import make_gateway
from franklinwh_emulator import models
from franklinwh_emulator.control import gateway_state
from franklinwh_emulator.gateway import GatewayConfig


def test_defaults_by_region_keep_the_reference_numbers(clock):
    au = make_gateway(clock)                                       # AS 4777, 50 Hz
    us = make_gateway(clock, site={"ac_type": 1})                  # IEEE 1547, 60 Hz
    assert (au.site.model.agate.code, au.site.model.apower.code) == (102, 2)
    assert (us.site.model.agate.code, us.site.model.apower.code) == (103, 0)
    for gw in (au, us):                                            # aPower X: as before #180
        assert gw.site.capacity_wh == 13600 and gw.site.rate_w == 5000


def test_country_decides_before_frequency(clock):
    gw = make_gateway(clock, site={"ac_type": 1, "location": {"country": "CA"}})
    assert gw.site.model.region == models.NA
    with pytest.raises(ValueError, match="60 Hz"):                 # a US aGate on a 50 Hz grid
        make_gateway(clock, site={"model": {"agate": 103}})


def test_resolve_by_code_sku_or_name():
    m = models.resolve({"agate": "AGT-R1V1-AU", "apower": "aPower X-02-AU"}, hz=50)
    assert (m.agate.code, m.apower.code) == (102, 7)
    assert models.resolve({"apower": "APR-05K15V1-AU"}, hz=50).apower.code == 7   # second SKU
    with pytest.raises(ValueError, match="give the code"):          # 103 and 104 share an SKU
        models.resolve({"agate": "AGT-R1V3-US"}, hz=60)
    with pytest.raises(ValueError, match="unknown"):
        models.resolve({"apower": 99}, hz=50)
    with pytest.raises(ValueError, match="does not go with"):
        models.resolve({"agate": 102, "apower": 0}, hz=50)
    with pytest.raises(ValueError, match="does not support aPower S"):
        models.resolve({"agate": 100, "apower": 4}, hz=60)
    with pytest.raises(ValueError, match="unknown keys"):
        GatewayConfig.from_dict({"model": {"agate": 102, "colour": "red"}})


def test_apower_2_capacity_power_and_charge_limit(clock):
    gw = make_gateway(clock, site={"ac_type": 1, "apower_units": 2, "initial_soc": 50,
                                   "model": {"agate": 103, "apower": 3}})
    s = gw.site
    assert s.capacity_wh == 30000 and s.rate_w == 20000
    assert s._limits(0.0, 0.0) == pytest.approx((20000.0, 16000.0))   # charges at 8 kW per unit
    regs = gw.device.read(15020, 1)
    assert regs == [30000]                                             # capacity follows the model


def test_direct_connect_login_serves_the_codes(clock):
    gw = make_gateway(clock, site={"ac_type": 1, "apower_units": 2, "model": {"apower": 6}})
    r = gw.dc.payloads.login({})
    assert r["SyHdVersion"] == 103 and r["PE_HW_VER"] == [6, 6]
    old = make_gateway(clock, profile="agate-v10r01b04d00",
                       quirks={"dc_firmware": "V12R02B30D06_260304"})
    r = old.dc.payloads.login({})
    assert r["SyHdVersion"] == 102 and "PE_HW_VER" not in r        # older firmware: no HW_VER lists


def test_state_describes_the_model(clock):
    from franklinwh_emulator.control import gateway_state
    st = gateway_state(make_gateway(clock))
    assert st["model"]["agate"]["name"] == "aGate X-01-AU" and st["model"]["apower"]["units"] == 1
    assert st["apower_rating"] == {"wh": 13600, "w": 5000}


def test_spec_profile_has_no_franklinwh_model(clock):
    from franklinwh_emulator.control import gateway_state
    assert gateway_state(make_gateway(clock, profile="spec"))["model"] is None


def _offgrid_full(clock, **site):
    gw = make_gateway(clock, site={"initial_soc": 99.9, "cap_forced_solar": False, **site})
    s = gw.site
    s.solar_override_w, s.load_override_w = 6000, 2000
    s.set_grid(available=False)
    for _ in range(10):                      # charge to full off-grid
        clock.advance(60)
        snap = s.step()
    return gw, s, snap


def test_na_full_battery_cuts_pv_at_62_2_hz_until_97(clock):
    """PV Curtailment White Paper (NA): full battery → 62.2 Hz → PV fully curtailed; PV resumes
    once SoC < 97 % (#178)."""
    gw, s, snap = _offgrid_full(clock, ac_type=1)
    assert s.pv_cutoff and snap.solar_w == 0 and snap.hz == 62.2
    assert snap.battery_w == pytest.approx(2000, abs=1)              # the house runs on the battery
    gw.device.refresh(snap)
    assert gw.device.image.get(701, "Hz") == 62200                   # 701.Hz shows the formed frequency
    assert gw.dc.payloads.ibg_run_status({})["gridFreq"] == 62.2     # and Direct Connect 1707
    while s.soc >= 97.0:
        clock.advance(60)
        snap = s.step()
    clock.advance(60)
    snap = s.step()
    assert not s.pv_cutoff and snap.solar_w > 1900 and snap.hz == 60.0
    s.set_grid(available=True)                                       # grid back: no cut-off
    clock.advance(60)
    assert not s.pv_cutoff


def test_na_offgrid_surplus_droops_pv_by_frequency(clock):
    """PV Curtailment White Paper (NA): above 60 Hz + 0.036 Hz the inverters droop 33.33 % of
    rated PV per Hz (kOF 0.05); the aGate moves its frequency at 0.2 Hz/s (#178)."""
    gw = make_gateway(clock, site={"ac_type": 1, "initial_soc": 50, "solar_peak_w": 6000,
                                   "cap_forced_solar": False})
    s = gw.site
    s.solar_override_w, s.load_override_w = 6000, 500
    s.set_grid(available=False)
    clock.advance(1)
    snap = s.step()
    assert snap.solar_clipped_w > 100 and 60.036 + snap.solar_clipped_w / 6000 * 3.0 > 60.2
    assert snap.hz == pytest.approx(60.2)                            # 1 s at 0.2 Hz/s
    clock.advance(60)
    snap = s.step()
    assert snap.hz == pytest.approx(60.036 + snap.solar_clipped_w / 6000 * 3.0)
    gw.device.refresh(snap)
    assert gw.device.image.get(701, "Hz") == round(snap.hz * 1000)
    s.load_override_w = 6000                                          # the house takes it all
    clock.advance(60)
    assert s.step().hz == 60.0 and s.droop_hz is None


def test_au_full_battery_only_clips_pv_to_the_house(clock):
    gw, s, snap = _offgrid_full(clock)
    assert not s.pv_cutoff and snap.solar_w == pytest.approx(2000, abs=50) and snap.hz == 50.0


def test_x02_au_follows_the_au_datasheet(clock):
    """System Datasheet aGate X-01-AU & aPower X-02-AU V1.7: SKU APR-05K15V1-AU, 15 kWh, 5 kW."""
    m = models.resolve({"apower": "APR-05K15V1-AU"}, hz=50)
    assert (m.apower.code, m.apower.kwh, m.apower.kw, m.apower.peak_kw) == (7, 15.0, 5.0, 10.0)
    assert m.apower.skus[0] == "APR-05K15V1-AU"                        # the datasheet's SKU first
    gw = make_gateway(clock, site={"apower_units": 2, "model": {"apower": 7}})
    assert gw.site.capacity_wh == 30000 and gw.device.read(15020, 1) == [30000]


def _na_offgrid(clock, **site):
    gw = make_gateway(clock, site={"ac_type": 1, "initial_soc": 50, "cap_forced_solar": False,
                                   **site})
    gw.site.set_grid(available=False)
    return gw, gw.site


def test_na_offgrid_opens_the_relay_of_non_1547_inverters(clock):
    """PV Curtailment White Paper (NA): inverters that don't follow IEEE 1547 don't droop; the
    PV relay toggles instead (#178)."""
    gw, s = _na_offgrid(clock, solar_peak_w=6000, pv_non_1547=["remote1"],
                        pv_inputs={"proximal": 0.5, "remote1": 0.5})
    s.solar_override_w, s.load_override_w = 6000, 500
    clock.advance(1)
    s.step()                                                         # surplus: remote1 opens
    assert gateway_state(gw)["pv_relays_auto_open"] == {"remote1": "not IEEE 1547"}
    assert gw.dc.payloads.ibg_run_status({})["pvRelay2"] == 0
    clock.advance(60)
    snap = s.step()
    assert snap.solar_available_w == pytest.approx(3000) and snap.hz == 60.0   # proximal only
    clock.advance(300)
    s.step()                                                         # closes after 300 s …
    clock.advance(1)
    s.step()                                                         # … and opens again
    assert "remote1" in s.pv_auto_open and s.pv_relays["remote1"]


def test_na_offgrid_opens_pv_relays_beyond_what_droop_can_remove(clock):
    """Same paper: the relay opens if PV exceeds the aPower's charging capacity, whatever the
    frequency; here 15 kW of 20 kW would need 75 % curtailment, past 62.2 Hz's 72 % (#178)."""
    gw, s = _na_offgrid(clock, solar_peak_w=20000)
    s.solar_override_w, s.load_override_w = 20000, 0
    clock.advance(1)
    s.step()
    assert s.pv_auto_open["proximal"][0] == "above charge capacity"
    clock.advance(1)
    assert s.step().solar_w == 0
    s.set_grid(available=True)                                       # grid back: relays close
    for _ in range(100):
        clock.advance(10)
        if s.step().grid_connected:
            break
    clock.advance(1)
    assert s.step().grid_connected and s.pv_auto_open == {}


def test_au_offgrid_never_opens_pv_relays(clock):
    gw = make_gateway(clock, site={"initial_soc": 50, "solar_peak_w": 20000,
                                   "cap_forced_solar": False, "pv_non_1547": ["proximal"]})
    s = gw.site
    s.set_grid(available=False)
    s.solar_override_w, s.load_override_w = 20000, 0
    clock.advance(1)
    s.step()
    assert s.pv_auto_open == {}


def test_na_opens_pv_relay_at_the_export_limit(clock):
    """PCS Technical Brief (NA): over the export limit the aGate cuts off PV by opening the PV
    relay (#123, #180); it closes again after 300 s here (interval not documented)."""
    rules = {"utility": {"export": "limited", "export_limit_w": 2000}}
    gw = make_gateway(clock, site={"ac_type": 1, "initial_soc": 100, "cap_forced_solar": False,
                                   **rules})
    s = gw.site
    s.solar_override_w, s.load_override_w = 8000, 500
    for _ in range(3):
        clock.advance(60)
        s.step()
    assert gateway_state(gw)["pv_relays_auto_open"] == {"proximal": "export limit (PEL)"}
    clock.advance(60)
    snap = s.step()
    assert snap.solar_w == 0 and snap.battery_w + snap.grid_w == pytest.approx(500, abs=1)
    assert gw.dc.payloads.ibg_run_status({})["solarRelayStatus"] == 0
    clock.advance(300)
    s.step()
    clock.advance(1)
    assert s.step().solar_available_w > 0 or "proximal" in s.pv_auto_open   # closed, then may reopen
    au = make_gateway(clock, site={"initial_soc": 100, "cap_forced_solar": False, **rules})
    au.site.solar_override_w, au.site.load_override_w = 8000, 500
    for _ in range(3):
        clock.advance(60)
        au.site.step()
    assert au.site.pv_auto_open == {}                                 # AU: curtails smoothly
