"""FranklinWH Cloud API, phase 2c (#307): storm, generator and grid-limit settings through
franklinwh-cloud."""

import asyncio
import hashlib
import json
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.cloud import CloudError
from franklinwh_emulator.dc_payloads import DcPayloads
from franklinwh_emulator.gateway import Fleet

pytest.importorskip("franklinwh_cloud")
from franklinwh_cloud.auth import TokenAuth  # noqa: E402
from franklinwh_cloud.client import Client  # noqa: E402

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}


@pytest.fixture
def fleet():
    f = Fleet.from_dict({"gateways": [
        {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "location": AU,
         "weather": {"model": "bell"}, "initial_soc": 40, "generator": {"installed": True}}]})
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


def test_storm_settings_round_trip(fleet):
    assert _run(fleet, lambda c: c.set_storm_settings(stormEn=1, setAdvanceBackupTime=90,
                                                      advanceTime=10, stormNoticeEn=1)) is True
    got = _run(fleet, lambda c: c.get_storm_settings())["result"]
    assert (got["enableStorm"], got["advanceBackupTime"], got["noticeEnable"], got["advanceTime"]) \
        == (1, 90, 1, 10)
    assert _run(fleet, lambda c: c.get_storm_list())["result"] == []
    assert _run(fleet, lambda c: c.set_storm_settings(setAdvanceBackupTime=5)) is False   # 30-300


def test_generator_schedule_reaches_the_model_and_direct_connect(fleet):
    gw = fleet["home"]
    res = _run(fleet, lambda c: c.set_generator_charge_schedule(
        [{"start": "11:00", "end": "13:30"}], confirm=True))
    assert res["verified"] is True
    block = gw.site.generator.block
    assert (block["charge1En"], block["charge1StartTime"], block["charge1EndTime"]) == (1, "11:00", "13:30")
    assert block["charge2En"] == 0
    assert DcPayloads(gw).generator({})["charge1StartTime"] == "11:00"     # Direct Connect agrees
    info = _run(fleet, lambda c: c.get_generator_info())
    assert info["genEn"] == 1 and info["genRatedPower"] == 7000 and "result" not in info


def test_grid_limits_change_the_site(fleet):
    site = fleet["home"].site
    _run(fleet, lambda c: c.set_power_control_settings(0, 0))
    assert site.utility_rules.grid_charge is False and site.battery_export() is False
    got = _run(fleet, lambda c: c.get_power_control_settings())["result"]
    assert (got["globalGridDischargeMax"], got["globalGridChargeMax"]) == (0, 0)
    _run(fleet, lambda c: c.set_power_control_settings(-1, 3.5))
    assert site.utility_rules.grid_charge and site.utility_rules.grid_charge_max_w == 3500
    got = _run(fleet, lambda c: c.get_power_control_settings())["result"]
    assert got["globalGridChargeMax"] == 3.5 and got["globalGridDischargeMax"] == -1
    with pytest.raises(CloudError):
        fleet.cloud.route("POST", "/hes-gateway/terminal/tou/setPowerControl?gatewayId="
                          + fleet["home"].serial, {"logintoken": _token(fleet)},
                          json.dumps({"globalGridChargeMax": -5}).encode())


def test_grid_charge_off_stops_grid_charging(fleet):
    """TOU GRID_CHARGE all day: the battery charges from the grid until the limit says 0."""
    gw = fleet["home"]
    from franklinwh_emulator.control import apply
    apply(gw, "tou", {"schedule": [{"name": "Charge", "startHourTime": "00:00", "endHourTime": "24:00",
                                    "waveType": 0, "dispatchId": 8}]})
    apply(gw, "mode", {"mode": 3})
    apply(gw, "pv", {"solar_w": 0})
    snap = gw.site.step()
    assert snap.battery_w < -1000 and snap.grid_w > 1000
    _run(fleet, lambda c: c.set_power_control_settings(-1, 0))
    snap = gw.site.step()
    assert snap.battery_w >= -1
