"""FranklinWH Cloud API, phase 2b (#305): TOU schedules through franklinwh-cloud."""

import asyncio
import datetime as dt
import hashlib
import json
import logging
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.cloud import CloudError
from franklinwh_emulator.control import apply
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.site import MODE_SELF

pytest.importorskip("franklinwh_cloud")
from franklinwh_cloud.auth import TokenAuth  # noqa: E402
from franklinwh_cloud.client import Client  # noqa: E402

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}


@pytest.fixture
def fleet():
    f = Fleet.from_dict({"gateways": [
        {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "location": AU,
         "weather": {"model": "bell"}, "initial_soc": 60, "native_mode": 2}]})
    f.start(cloud_port=0)
    yield f
    f.stop()


def _token(f) -> str:
    form = {"account": "owner@example.com", "password": hashlib.md5(b"emulator").hexdigest()}
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/terminal/initialize/"
                                 "appUserOrInstallerLogin",
                                 data=urllib.parse.urlencode(form).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)["result"]["token"]


def _run(f, fn):
    async def go():
        c = Client(TokenAuth(_token(f)), f["home"].serial,
                   url_base=f"http://127.0.0.1:{f.cloud.port}/", heartbeat=False)
        return await fn(c)
    return asyncio.run(go())


def _today(f) -> dt.date:
    site = f["home"].site
    return dt.date.fromisoformat(site.local_date(site.clock.now()))


def _hours(site) -> list:
    return [(b["startHourTime"], b["endHourTime"], b["dispatchId"], b["waveType"])
            for b in site.tou_schedule]


def test_the_site_schedule_reads_back_as_one_everyday_season(fleet):
    detail = _run(fleet, lambda c: c.get_tou_dispatch_detail())["result"]
    season = detail["strategyList"][0]
    assert season["month"] == "1,2,3,4,5,6,7,8,9,10,11,12"
    day = season["dayTypeVoList"][0]
    assert day["dayType"] == 3
    assert [(b["startHourTime"], b["endHourTime"], b["dispatchId"], b["waveType"])
            for b in day["detailVoList"]] == _hours(fleet["home"].site)
    assert {d["dispatchCode"] for d in detail["detailDefaultVo"]["touDispatchList"]} >= {"SELF", "GRID_CHARGE"}
    apply(fleet["home"], "tou", {"schedule": [{"name": "Peak", "startHourTime": "00:00",
                                               "endHourTime": "24:00", "waveType": 2,
                                               "dispatchId": 2}]})
    again = _run(fleet, lambda c: c.get_tou_dispatch_detail())["result"]   # set elsewhere
    assert again["strategyList"][0]["dayTypeVoList"][0]["detailVoList"][0]["dispatchId"] == 2


def test_set_tou_schedule_changes_the_site_and_not_the_mode(fleet, caplog):
    site = fleet["home"].site
    blocks = [{"name": "Grid charge", "startHourTime": "01:00", "endHourTime": "05:00",
               "waveType": 0, "dispatchId": 8}]
    _run(fleet, lambda c: c.set_tou_schedule("CUSTOM", blocks, rates={"off_peak": 0.12, "peak": 0.45}))
    assert ("01:00", "05:00", 8, 0) in _hours(site)
    assert _hours(site)[0][:3] == ("00:00", "01:00", 6)                      # gap: SELF
    assert site.tou_rates[0]["buy"] == pytest.approx(0.12)
    assert site.native_mode == MODE_SELF                                     # save doesn't switch
    with caplog.at_level(logging.WARNING, logger="franklinwh_cloud"):
        mode = _run(fleet, lambda c: c.get_mode(1))
    assert "get_tou_info failed" not in caplog.text and mode["workMode"] == 1


def test_seasons_follow_the_date(fleet):
    today = _today(fleet)
    other = [m for m in range(1, 13) if m != today.month]
    day = lambda dispatch: [{"startHourTime": "00:00", "endHourTime": "24:00", "waveType": 0,
                             "name": "All day", "dispatchId": dispatch}]
    strategy = [
        {"seasonName": "Now", "month": str(today.month),
         "dayTypeVoList": [{"dayName": "weekday", "dayType": 1, "detailVoList": day(2)},
                           {"dayName": "weekend", "dayType": 2, "detailVoList": day(3)}]},
        {"seasonName": "Rest", "month": ",".join(map(str, other)),
         "dayTypeVoList": [{"dayName": "everyday", "dayType": 3, "detailVoList": day(7)}]},
    ]
    _run(fleet, lambda c: c.set_tou_schedule_multi(strategy))
    site = fleet["home"].site
    assert _hours(site) == [("00:00", "24:00", 3 if today.weekday() >= 5 else 2, 0)]
    store = fleet.cloud.tou(fleet["home"])
    store._today = lambda: today.replace(day=1) + dt.timedelta(days=40)      # next month
    store.sync()
    assert _hours(site) == [("00:00", "24:00", 7, 0)]
    nxt = _run(fleet, lambda c: c.get_tou_info(1))
    assert nxt                                                               # client finds a block


def test_bad_schedules_are_refused(fleet):
    store_before = list(fleet["home"].site.tou_schedule)
    token = _token(fleet)
    path = "/hes-gateway/terminal/tou/saveTouDispatch?gatewayId=" + fleet["home"].serial
    good = {"dayType": 3, "detailVoList": [{"name": "x", "startHourTime": "00:00",
                                            "endHourTime": "24:00", "waveType": 0, "dispatchId": 6}]}
    for strategy in ([],
                     [{"month": "13", "dayTypeVoList": [good]}],
                     [{"month": "1", "dayTypeVoList": [{**good, "dayType": 1}]}],
                     [{"month": "1", "dayTypeVoList": [good]}, {"month": "1,2", "dayTypeVoList": [good]}],
                     [{"month": "1", "dayTypeVoList": [{**good, "detailVoList": [
                         {"name": "x", "startHourTime": "00:00", "endHourTime": "24:00",
                          "waveType": 0, "dispatchId": 99}]}]}]):
        with pytest.raises(CloudError) as e:
            fleet.cloud.route("POST", path, {"logintoken": token},
                              json.dumps({"template": {}, "strategyList": strategy}).encode())
        assert e.value.code == 400
    assert fleet["home"].site.tou_schedule == store_before
