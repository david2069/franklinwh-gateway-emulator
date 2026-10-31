"""Hardware models and their capabilities (#180)."""

import pytest

from conftest import make_gateway
from franklinwh_emulator import models
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
