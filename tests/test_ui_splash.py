"""#329: the start-up screen's data, GET /gateways/<id>/days, and the page's parts (design board F)."""

import json
import re
import urllib.error
import urllib.request

import pytest

from franklinwh_emulator.cloud_energy import electric_data
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.ui.server import STATIC

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}


@pytest.fixture(scope="module")
def ui():
    fleet = Fleet.from_dict({"gateways": [
        {"id": "site", "seed": 3, "dc_port": 0, "location": AU, "weather": {"model": "bell"}},
        {"id": "fresh", "seed": 4, "dc_port": 0}]})
    fleet["site"].site.backfill(8)              # eight past days of history, as test_cloud_energy
    fleet.start(control_port=0, ui=True)
    yield fleet, f"http://127.0.0.1:{fleet.control.port}"
    fleet.stop()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def test_last_seven_days_match_the_cloud_api_daily_totals(ui):
    fleet, base = ui
    days = _get(base + "/gateways/site/days")["days"]
    assert len(days) == 7 and [d["date"] for d in days] == sorted(d["date"] for d in days)
    last = sorted(fleet["site"].site.history)[-7:]
    assert [d["date"] for d in days] == last
    assert all(d["solar_kwh"] > 1 and d["home_kwh"] > 1 for d in days[:-1])
    gw = fleet["site"]
    for d in days[:-1]:
        cloud = electric_data(gw, {"dayTime": d["date"], "type": 1})
        assert d["solar_kwh"] == pytest.approx(sum(cloud["kwhSuArray"]), abs=0.05)
        assert d["home_kwh"] == pytest.approx(sum(cloud["kwhLoadArray"]), abs=0.05)
    assert len(_get(base + "/gateways/site/days?n=3")["days"]) == 3


def test_a_new_run_has_only_today(ui):
    _, base = ui
    days = _get(base + "/gateways/fresh/days")["days"]
    assert len(days) <= 1
    for d in days:
        assert set(d) == {"date", "solar_kwh", "home_kwh"}


@pytest.mark.parametrize("q", ["n=0", "n=32", "n=x"])
def test_bad_n_is_refused(ui, q):
    _, base = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base + "/gateways/site/days?" + q)
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base + "/gateways/nope/days")
    assert e.value.code == 404


def test_page_has_the_boards_parts():
    page = (STATIC / "index.html").read_text()
    js = (STATIC / "splash.js").read_text()
    for text in ("Welcome", "to FranklinWH Gateway Emulator", "AGATE EMULATOR · NO HARDWARE NEEDED",
                 "Unofficial · not affiliated with FranklinWH", "Solar, kWh a day", "Home use, kWh a day"):
        assert text in page, text
    assert re.search(r'class="splash"[^>]*x-show="splash"', page)
    assert "@keydown.escape.window=\"closeSplash()\"" in page and "@click=\"closeSplash()\"" in page
    assert "sessionStorage" in js and "SPLASH_MIN_MS" in js        # once a session; on screen long enough
    assert "FranklinWH logo" not in page and "franklinwh.com" not in page
