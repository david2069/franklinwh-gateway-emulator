"""Component age and warranty (#115): SoH from age and throughput, PV ageing, warranty status."""

import pytest

from conftest import make_gateway
from franklinwh_emulator import models
from franklinwh_emulator.components import Components, warranty_terms
from franklinwh_emulator.control import gateway_state


def test_franklinwh_warranty_terms_by_model():
    """AU & NZ and US & CA warranties: aPower X 12 y / 43 MWh, X-02-AU 12 y / 60 MWh,
    aPower 2 and S 15 y / 60 MWh."""
    a = models.APOWERS
    assert warranty_terms(a[2]) == (12.0, 43.0) and warranty_terms(a[7]) == (12.0, 60.0)
    assert warranty_terms(a[3]) == (15.0, 60.0) and warranty_terms(a[4]) == (15.0, 60.0)


def test_soh_falls_to_70_at_the_end_of_the_term_by_age_or_throughput():
    c = Components({"apower": {"installed": "2020-01-01"}}, models.APOWERS[2])
    assert c.apower_soh("2020-01-01", 0) == 100
    assert c.apower_soh("2026-01-01", 0) == pytest.approx(85, abs=0.1)        # half the term
    assert c.apower_soh("2032-01-01", 0) == pytest.approx(70, abs=0.1)
    assert c.apower_soh("2021-01-01", 43e6) == pytest.approx(70)              # 43 MWh first
    # The reference aPower, about 2.2 years in on 2026-10-07: measured 94.8 % (1705 batSoh).
    ref = Components({"apower": {"installed": "2024-07-25"}}, models.APOWERS[2])
    assert ref.apower_soh("2026-10-07", 6_344_500) == pytest.approx(94.8, abs=1.0)
    with pytest.raises(ValueError, match="unknown keys"):
        Components({"battery": {}}, models.APOWERS[2])
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        Components({"apower": {"installed": "last year"}}, models.APOWERS[2])


def test_age_is_served_and_scales_capacity_and_pv(clock):
    old = make_gateway(clock, commissioned="2016-01-01", site={"cap_forced_solar": False})
    new = make_gateway(clock, commissioned="2026-01-01", site={"cap_forced_solar": False})
    for gw in (old, new):
        gw.device.refresh(gw.site.step())
    assert old.site.soh < 80 < new.site.soh
    assert old.device.image.get(713, "SoH") == round(old.site.soh * 10)
    assert old.dc.payloads.battery_cells({"id": 1})["batSoh"] == round(old.site.soh, 1)
    assert old.site.capacity_wh < new.site.capacity_wh and old.site.rated_capacity_wh == 13600
    assert old.site._pv_age == pytest.approx(1 - 0.005 * 10, abs=0.002)
    w = gateway_state(old)["components"]
    assert w["apower"]["expires"] == "2028" and w["apower"]["in_warranty"]
    assert w["pv_inverter"]["in_warranty"] is False                          # 10 years: ended
