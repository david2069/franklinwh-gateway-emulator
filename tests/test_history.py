"""Factory vs in-service energy (#53): zero totals, commissioning date, pre-filled history."""

import datetime as dt

import pytest

pytest.importorskip("franklinwh_local")
from franklinwh_emulator.clock import ManualClock  # noqa: E402
from franklinwh_emulator.control import gateway_state  # noqa: E402
from franklinwh_emulator.dc_payloads import DcPayloads  # noqa: E402
from franklinwh_emulator.gateway import Gateway, GatewayConfig  # noqa: E402
from franklinwh_emulator.site import REFERENCE_COUNTERS  # noqa: E402
from franklinwh_emulator.validate import validate_fleet  # noqa: E402

SYD = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}
OCT7_NOON = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=11))).timestamp()


def gw(clock=None, **kw):
    d = {"id": "g", "seed": 7, "location": SYD, "weather": {"model": "synthetic", "kwp": 6.6},
         **kw}
    return Gateway(GatewayConfig.from_dict(d), clock=clock or ManualClock(OCT7_NOON))


def sno(g, date):
    g.site.clock.advance(1800)                  # record half an hour of history first
    g.site.step()
    return DcPayloads(g).energy_history({"date": date})["sno"]


def test_factory_starts_at_zero_and_commissioned_today():
    g = gw(setup="factory")
    s = g.site
    assert (s.grid_import_wh, s.grid_export_wh, s.pv_wh, s.batt_charge_wh, s.batt_discharge_wh) == (0,) * 5
    assert g.commissioned == "2026-10-07"
    assert sno(g, "2026-10-07") == 0


def test_factory_keeps_totals_the_fleet_file_gives():
    g = gw(setup="factory", grid_import_wh=1234)
    assert g.site.grid_import_wh == 1234 and g.site.pv_wh == 0


def test_in_service_defaults_to_the_reference_site():
    g = gw(setup="in-service")
    assert g.site.grid_import_wh == REFERENCE_COUNTERS["grid_import_wh"]
    assert sno(g, "2026-10-07") == 804                    # what the reference site read that day


def test_commissioned_date_sets_sno():
    g = gw(setup="in-service", commissioned="2026-01-01")
    assert sno(g, "2026-10-07") == (dt.date(2026, 10, 7) - dt.date(2026, 1, 1)).days


def test_history_days_fills_history_totals_and_counters():
    g = gw(setup="in-service", history_days=2, initial_soc=50)
    s = g.site
    days = sorted(s.history)
    assert days == ["2026-10-05", "2026-10-06", "2026-10-07"]
    assert len(s.history["2026-10-06"]) == 96                      # a full past day
    assert s._t == pytest.approx(OCT7_NOON)                        # back on the live clock
    # The counters moved by exactly what the history recorded (two and a half days).
    imported = sum(DcPayloads(g).energy_history({"date": d})["kwh_uti_in"] for d in days)
    pv = sum(DcPayloads(g).energy_history({"date": d})["kwh_sun"] for d in days)
    assert (s.grid_import_wh - REFERENCE_COUNTERS["grid_import_wh"]) / 1000 == pytest.approx(imported, rel=0.02, abs=0.05)
    assert (s.pv_wh - REFERENCE_COUNTERS["pv_wh"]) / 1000 == pytest.approx(pv, rel=0.05, abs=0.1)
    assert pv > 5                                                    # two sunny-ish days
    today = gateway_state(g)["today_kwh"]
    assert today["kwh_sun"] > 0                                      # since local midnight
    y = DcPayloads(g).energy_history({"date": "2026-10-06"})
    assert y["pointId"] == 96 and y["sno"] == 803


@pytest.mark.parametrize("bad,msg", [
    ({"setup": "factory", "history_days": 3}, "history_days needs setup: in-service"),
    ({"setup": "in-service", "history_days": 200}, "history_days must be 0-105"),
    ({"setup": "in-service", "commissioned": "07/10/2026"}, "commissioned must be YYYY-MM-DD"),
])
def test_refused(bad, msg):
    with pytest.raises(ValueError, match=msg):
        GatewayConfig.from_dict({"id": "g", **bad})
    assert msg in validate_fleet({"gateways": [{"id": "g", **bad}]})[0]["error"]
