"""Commissioning lifecycle (#185): factory → commissioning → in-service, the app's System
Parameters, the PTO date (FranklinWH Commissioning Guide, MAC 1 & aPower 2 & aPower S)."""

import pytest

from conftest import make_gateway
from franklinwh_emulator.control import ApiError, apply, gateway_state
from franklinwh_emulator.gateway import GatewayConfig


def test_installer_commissions_a_factory_gateway(clock):
    gw = make_gateway(clock, setup="factory", site={"ac_type": 1})
    assert gateway_state(gw)["lifecycle"]["state"] == "factory"
    with pytest.raises(ApiError, match="start commissioning first"):
        apply(gw, "commission", {"pcs_derate_w": 3000})
    apply(gw, "commission", {"start": True})
    apply(gw, "commission", {"grid_import": "not-allowed", "energy_export": "solar",
                             "grid_export_limit_w": 4000, "pcs_derate_w": 3000,
                             "pto_date": "2026-12-15"})
    u, s = gw.site.utility_rules, gw.site
    assert (u.grid_charge, u.export, u.export_limit_w, u.battery_export) == (False, "limited", 4000, False)
    assert s.rate_w == 3000 and s.pcs_export_limit() == 4000
    apply(gw, "commission", {"energy_export": "none"})
    assert s.utility_rules.export == "prohibited"
    clock.advance(86400 * 3)
    apply(gw, "commission", {"complete": True})
    life = gateway_state(gw)["lifecycle"]
    assert life == {"state": "in-service", "commissioned": s.local_date(clock.now()),
                    "pto_date": "2026-12-15"}
    with pytest.raises(ApiError, match="not commissioning"):
        apply(gw, "commission", {"complete": True})
    apply(gw, "commission", {"grid_import_limit_w": 8000})        # changeable in service
    assert s.utility_rules.import_limit_w == 8000


def test_fleet_file_starts_at_any_state(clock):
    gw = make_gateway(clock, setup="commissioning", pto_date="2027-01-31")
    assert gateway_state(gw)["lifecycle"]["state"] == "commissioning"
    assert gw.site.grid_import_wh == 0 and not gw.modbus_enabled      # as factory
    with pytest.raises(ValueError, match="pto_date must be YYYY-MM-DD"):
        GatewayConfig.from_dict({"pto_date": "soon"})
    with pytest.raises(ApiError, match="energy_export"):
        apply(gw, "commission", {"energy_export": "battery"})
