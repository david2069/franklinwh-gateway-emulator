"""#322: richo's franklinwh library (2026.3, the homeassistant-franklinwh integration's client)
against the Cloud API: every call it makes, the five endpoints it needed, and sendMqtt 311 as the
app's captures show it."""

import asyncio
import hashlib
import json
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.gateway import Fleet

pytest.importorskip("franklinwh")
from franklinwh import client as richo  # noqa: E402

FLEET = {"gateways": [
    {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "commissioned": "2025-03-01",
     "accessories": {"smart_circuits": True, "generator_module": True}},
    {"id": "bare", "port": 0, "dc_port": 0, "seed": 4,
     "accessories": {"smart_circuits": False, "generator_module": False}}]}


@pytest.fixture
def fleet(monkeypatch):
    f = Fleet.from_dict(FLEET)
    f.start(control_port=0, cloud_port=0)
    # login, sendMqtt, getHomeGatewayList and updateTouMode ignore url_base (franklinwh 2026.3)
    monkeypatch.setattr(richo, "DEFAULT_URL_BASE", f"http://127.0.0.1:{f.cloud.port}/")
    yield f
    f.stop()


def _run(f, fn, gw="home"):
    async def go():
        c = richo.UnknownMethodsClient(richo.TokenFetcher("owner@example.com", "emulator"), f[gw].serial,
                                       url_base=f"http://127.0.0.1:{f.cloud.port}/")
        await c.get_home_gateway_list()          # logs in: the raw-session calls don't on their own
        return await fn(c)
    return asyncio.run(go())


def _monitor(f) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}/monitor", timeout=5) as r:
        return json.load(r)


def test_every_call_the_integration_and_library_make_is_served(fleet):
    async def all_calls(c):
        await c.get_stats()                      # the integration: sensors
        await c.get_smart_switch_state()         # the integration: switches
        await c.set_smart_switch_state(richo.SwitchState((False, None, None)))
        await c.get_mode()
        await c.get_export_settings()
        await c.get_accessories()
        await c.get_accessory_list()
        await c.get_equipment_list()
        await c.get_composite_info()
        await c.set_mode(richo.Mode.self_consumption(30))
        await c.set_export_settings(richo.ExportMode.SOLAR_ONLY, 5.0)
        await c.set_generator(False)
        await c.set_grid_status(richo.GridStatus.NORMAL)
    _run(fleet, all_calls)
    assert _monitor(fleet)["cloud"]["unserved"] == {}


def test_switches_and_mode_through_sendmqtt_311(fleet):
    site = fleet["home"].site
    assert _run(fleet, lambda c: c.get_smart_switch_state())[0] is site.circuits[1].on
    _run(fleet, lambda c: c.set_smart_switch_state(richo.SwitchState((False, True, None))))
    assert (site.circuits[1].on, site.circuits[2].on) == (False, True)
    assert tuple(_run(fleet, lambda c: c.get_smart_switch_state()))[:2] == (False, True)
    _run(fleet, lambda c: c.set_mode(richo.Mode.time_of_use(35)))
    assert site.tou_reserve == 35
    assert _run(fleet, lambda c: c.get_mode()) == ("time_of_use", 0)   # touMinSoc, as captured
    _run(fleet, lambda c: c.set_mode(richo.Mode.self_consumption(25)))
    assert site.self_reserve == 25 and _run(fleet, lambda c: c.get_mode())[0] == "self_consumption"


def test_accessories_list_what_is_fitted(fleet):
    home = _run(fleet, lambda c: c.get_accessories())          # no gatewayId: the whole account
    assert sorted(a["accessoryType"] for a in home) == [3, 4]
    a = home[0]
    assert set(a) == {"id", "sn", "accessoryType", "createTime", "loadOption", "gerOption"}
    assert a["createTime"] == "2025-03-01 00:00:00" and isinstance(a["id"], str)
    assert _run(fleet, lambda c: c.get_accessory_list())["result"] == home
    assert _run(fleet, lambda c: c.get_equipment_list(), "bare")["result"] == []
    url = (f"http://127.0.0.1:{fleet.cloud.port}/hes-gateway/common/getAccessoryList"
           f"?gatewayId={fleet['bare'].serial}&lang=en_US")
    form = {"account": "owner@example.com", "password": hashlib.md5(b"emulator").hexdigest()}
    login = urllib.request.Request(f"http://127.0.0.1:{fleet.cloud.port}/hes-gateway/terminal/initialize/"
                                   "appUserOrInstallerLogin", data=urllib.parse.urlencode(form).encode())
    with urllib.request.urlopen(login, timeout=5) as r:
        token = json.load(r)["result"]["token"]
    with urllib.request.urlopen(urllib.request.Request(url, headers={"loginToken": token}), timeout=5) as r:
        assert json.load(r) == {"code": 200, "message": "Query success!", "result": [], "success": True,
                                "total": 0}


def test_export_settings_round_trip_into_the_site(fleet):
    site = fleet["home"].site
    for mode, kw, back in ((richo.ExportMode.SOLAR_ONLY, 5.0, 5.0),
                           (richo.ExportMode.SOLAR_AND_APOWER, None, None),
                           (richo.ExportMode.NO_EXPORT, None, 0.0)):    # gridFeedMax 0: no export
        _run(fleet, lambda c: c.set_export_settings(mode, kw))
        got = _run(fleet, lambda c: c.get_export_settings())
        assert (got.mode, got.limit_kw) == (mode, back)
    assert site.utility_rules.export == "prohibited" and not site.battery_export()
    _run(fleet, lambda c: c.set_export_settings(richo.ExportMode.SOLAR_AND_APOWER, 3.5))
    u = site.utility_rules
    assert (u.export, u.export_limit_w, site.battery_export()) == ("limited", 3500.0, True)


def test_the_app_sends_update_tou_mode_in_the_query_and_set_power_control_v2_alone(fleet):
    gw = fleet["home"]
    form = {"account": "owner@example.com", "password": hashlib.md5(b"emulator").hexdigest()}
    base = f"http://127.0.0.1:{fleet.cloud.port}/hes-gateway/terminal/"
    with urllib.request.urlopen(urllib.request.Request(
            base + "initialize/appUserOrInstallerLogin", data=urllib.parse.urlencode(form).encode())) as r:
        token = json.load(r)["result"]["token"]
    q = urllib.parse.urlencode({"backupForeverFlag": 1, "currendId": 9323, "electricityType": 1,
                                "gatewayId": gw.serial, "lang": "EN_US", "oldIndex": 2, "soc": 40,
                                "stromEn": 0, "workMode": 2})
    with urllib.request.urlopen(urllib.request.Request(base + "tou/updateTouMode?" + q, data=b"",
                                                       headers={"loginToken": token})) as r:
        assert json.load(r) == {"code": 200, "message": "Operation success!", "result": None, "success": True}
    assert gw.site.self_reserve == 40
    body = {"gatewayId": gw.serial, "gridFeedMax": 2.5, "gridFeedMaxFlag": 2, "gridMax": -1.0, "gridMaxFlag": 1}
    with urllib.request.urlopen(urllib.request.Request(
            base + f"tou/setPowerControlV2?gatewayId={gw.serial}", data=json.dumps(body).encode(),
            headers={"loginToken": token, "Content-Type": "application/json"})) as r:
        assert json.load(r)["result"] == {"powerControlTipMsg": None}
    u = gw.site.utility_rules
    assert (u.export, u.export_limit_w, u.import_limit_w, gw.site.battery_export()) == (
        "limited", 2500.0, None, True)
    with urllib.request.urlopen(urllib.request.Request(
            base + "tou/setPowerControlV2", data=json.dumps({**body, "gridFeedMaxFlag": 7}).encode(),
            headers={"loginToken": token})) as r:
        assert json.load(r)["code"] == 400
