"""FranklinWH Cloud API, phase 1c (#301): energy history from the site's quarter-hour history."""

import asyncio
import calendar
import datetime as dt
import hashlib
import json
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.cloud_energy import split
from franklinwh_emulator.dc_payloads import DcPayloads
from franklinwh_emulator.gateway import Fleet

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}
POWER = "api-energy/power/getFhpPowerByDay"
ELECTRIC = "api-energy/electric/getFhpElectricData"


@pytest.fixture(scope="module")
def fleet():
    f = Fleet.from_dict({"gateways": [
        {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "location": AU,
         "weather": {"model": "bell"}, "initial_soc": 60, "commissioned": "2024-03-01"}]})
    f["home"].site.backfill(2)
    f.start(cloud_port=0)
    yield f
    f.stop()


def _get(f, path: str, **q):
    form = {"account": "owner@example.com", "password": hashlib.md5(b"emulator").hexdigest()}
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/terminal/initialize/"
                                 "appUserOrInstallerLogin",
                                 data=urllib.parse.urlencode(form).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        token = json.load(r)["result"]["token"]
    q = {"gatewayId": f["home"].serial, **q}
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/{path}?{urllib.parse.urlencode(q)}",
                                 headers={"loginToken": token})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def _yesterday(f) -> str:
    site = f["home"].site
    return (dt.date.fromisoformat(site.local_date(site.clock.now())) - dt.timedelta(1)).isoformat()


def test_split_conserves_every_source_and_the_home():
    """Solar 5 kW, home 2 kW, battery charging 2 kW, grid export 1 kW (powers × 900 s)."""
    f = split({"s": 900, "load": 2000 * 900, "uti": -1000 * 900, "fhp": -2000 * 900, "gen": 0})
    assert f["sun"] == pytest.approx(5000)
    assert (f["sun_home"], f["sun_bat"], f["sun_grid"]) == pytest.approx((2000, 2000, 1000))
    assert f["grid_home"] == f["grid_bat"] == f["bat_home"] == 0
    night = split({"s": 900, "load": 3000 * 900, "uti": 1000 * 900, "fhp": 2000 * 900, "gen": 0})
    assert (night["bat_home"], night["grid_home"], night["sun"]) == pytest.approx((2000, 1000, 0))
    assert split({"s": 0}) == {}


def test_power_by_day_is_the_day_in_quarter_hours(fleet):
    day = _yesterday(fleet)
    body = _get(fleet, POWER, dayTime=day)
    res = body["result"]
    assert body["code"] == 200 and len(res["deviceTimeArray"]) == 96
    assert res["deviceTimeArray"][0] == f"{day} 00:00:00" and res["deviceTimeArray"][-1] == f"{day} 23:45:00"
    cap = fleet["home"].site.capacity_wh / 1000
    for soc, kwh in zip(res["socArray"], res["kwhTotalArray"]):
        assert 0 <= soc <= 100 and kwh == pytest.approx(soc / 100 * cap, abs=0.02)   # soc is rounded to 0.1
    assert set(res["runStatusArray"]) <= {0, 1, 2} and 1 in res["runStatusArray"]
    assert max(res["powerSolarHomeArray"]) > 0 and res["powerSolarHomeArray"][0] == 0   # midnight
    assert len(_get(fleet, POWER, dayTime="2001-01-01")["result"]["socArray"]) == 0


def test_a_day_matches_direct_connect_1303_and_its_power_samples(fleet):
    day = _yesterday(fleet)
    e = _get(fleet, ELECTRIC, type=1, dayTime=day)["result"]
    assert e["deviceTimeArray"] == [day]
    dc = DcPayloads(fleet["home"]).energy_history({"date": day})
    for array, channel in (("kwhUtiInArray", "kwh_uti_in"), ("kwhUtiOutArray", "kwh_uti_out"),
                           ("kwhFhpChgArray", "kwh_fhp_chg"), ("kwhFhpDiArray", "kwh_fhp_di"),
                           ("kwhLoadArray", "kwh_load"), ("kwhSuArray", "kwh_sun")):
        assert e[array][0] == pytest.approx(dc[channel], abs=0.02), array
    p = _get(fleet, POWER, dayTime=day)["result"]
    home = sum(sum(p[a]) for a in ("powerSolarHomeArray", "powerGirdHomeArray",
                                   "powerFhpHomeArray", "powerGenHomeArray")) * 0.25
    assert home == pytest.approx(e["kwhLoadArray"][0], rel=0.01)
    assert e["kwhSolarLoadArray"][0] + e["kwhGridLoadArray"][0] + e["kwhFhpLoadArray"][0] \
        == pytest.approx(e["kwhLoadArray"][0], abs=0.05)
    assert e["mpptWhArray"] == [None]


def test_periods_follow_the_clouds_date_rules(fleet):
    day = dt.date.fromisoformat(_yesterday(fleet))
    monday = day - dt.timedelta(day.weekday())
    week = _get(fleet, ELECTRIC, type=2, startDate=monday.isoformat())["result"]
    assert week["deviceTimeArray"] == [(monday + dt.timedelta(n)).isoformat() for n in range(7)]
    assert sum(week["kwhLoadArray"]) > 0
    if day.weekday():
        assert _get(fleet, ELECTRIC, type=2, startDate=day.isoformat())["result"]["deviceTimeArray"] == []
    assert _get(fleet, ELECTRIC, type=2, dayTime=monday.isoformat())["result"]["kwhSuArray"] == []
    assert _get(fleet, ELECTRIC, type=1, startDate=day.isoformat())["result"]["deviceTimeArray"] == []
    month = _get(fleet, ELECTRIC, type=3, startDate=day.isoformat())["result"]["deviceTimeArray"]
    assert len(month) == calendar.monthrange(day.year, day.month)[1] and month[0].endswith("-01")
    year = _get(fleet, ELECTRIC, type=4, startDate=day.isoformat())["result"]
    assert year["deviceTimeArray"] == [f"{day.year}-{m:02d}" for m in range(1, 13)]
    assert year["kwhLoadArray"][day.month - 1] > 0
    total = _get(fleet, ELECTRIC, type=5)["result"]["deviceTimeArray"]
    site = fleet["home"].site
    assert total[0] == "2024" and total[-1] == site.local_date(site.clock.now())[:4]


def test_franklinwh_cloud_reads_the_energy_history(fleet):
    pytest.importorskip("franklinwh_cloud")
    from franklinwh_cloud.auth import TokenAuth
    from franklinwh_cloud.client import Client
    day = dt.date.fromisoformat(_yesterday(fleet))
    form = {"account": "owner@example.com", "password": hashlib.md5(b"emulator").hexdigest()}
    req = urllib.request.Request(f"http://127.0.0.1:{fleet.cloud.port}/hes-gateway/terminal/initialize/"
                                 "appUserOrInstallerLogin",
                                 data=urllib.parse.urlencode(form).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        token = json.load(r)["result"]["token"]

    async def go():
        c = Client(TokenAuth(token), fleet["home"].serial,
                   url_base=f"http://127.0.0.1:{fleet.cloud.port}/", heartbeat=False)
        by_day = await c.get_power_by_day(day.isoformat())
        week = await c.get_power_details(2, (day - dt.timedelta(day.weekday())).isoformat())
        return by_day, week
    by_day, week = asyncio.run(go())
    assert len(by_day["socArray"]) == 96
    assert len(week["deviceTimeArray"]) == 7
