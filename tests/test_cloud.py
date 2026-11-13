"""FranklinWH Cloud API, phase 1a (#297): login, gateway list, composite info."""

import asyncio
import hashlib
import json
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.control import apply
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.validate import validate_fleet

LOGIN = "hes-gateway/terminal/initialize/appUserOrInstallerLogin"
AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}


@pytest.fixture
def fleet():
    f = Fleet.from_dict({
        "cloud": {"account": "tester@example.com", "password": "pw"},
        "gateways": [
            {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "location": AU,
             "weather": {"model": "bell"}, "initial_soc": 70},
            {"id": "us", "port": 0, "dc_port": 0, "seed": 4, "ac_type": 1, "phases": ["L1", "L2"],
             "location": {"latitude": 33.45, "longitude": -112.07, "timezone": "America/Phoenix",
                          "country": "US"}},
        ]})
    f.start(cloud_port=0)
    yield f
    f.stop()


def _base(f) -> str:
    return f"http://127.0.0.1:{f.cloud.port}/"


def _call(f, method: str, path: str, *, form: dict | None = None, token: str | None = None):
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(_base(f) + path, data=data, method=method,
                                 headers={"loginToken": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def _login(f, account="tester@example.com", password="pw"):
    return _call(f, "POST", LOGIN, form={"account": account, "lang": "en_US", "type": 0,
                                         "password": hashlib.md5(password.encode()).hexdigest()})


def test_login_gives_a_token_and_a_wrong_password_401(fleet):
    status, body = _login(fleet)
    assert status == 200 and body["code"] == 200 and body["success"] is True
    assert len(body["result"]["token"]) == 32 and body["result"]["email"] == "tester@example.com"
    _, bad = _login(fleet, password="nope")
    assert bad["code"] == 401 and bad["result"] is None and bad["success"] is False
    _, other = _login(fleet, account="someone@example.com")
    assert other["code"] == 401


def test_calls_need_the_token(fleet):
    path = "hes-gateway/terminal/getHomeGatewayList"
    assert _call(fleet, "GET", path)[1]["code"] == 10009
    assert _call(fleet, "GET", path, token="0" * 32)[1]["code"] == 10009
    token = _login(fleet)[1]["result"]["token"]
    assert _call(fleet, "GET", path, token=token)[1]["code"] == 200


def test_gateway_list_names_every_gateway(fleet):
    token = _login(fleet)[1]["result"]["token"]
    rows = _call(fleet, "GET", "hes-gateway/terminal/getHomeGatewayList", token=token)[1]["result"]
    by_name = {r["name"]: r for r in rows}
    home, us = fleet["home"], fleet["us"]
    assert by_name["home"]["id"] == home.serial and by_name["us"]["id"] == us.serial
    assert by_name["home"]["countryId"] == 3 and by_name["us"]["countryId"] == 2
    assert by_name["home"]["zoneInfo"] == "Australia/Sydney" and by_name["home"]["status"] == 1
    home.faults.offline = True                                   # the gateway's offline fault
    rows = _call(fleet, "GET", "hes-gateway/terminal/getHomeGatewayList", token=token)[1]["result"]
    assert {r["name"]: r["status"] for r in rows} == {"home": 0, "us": 1}


def test_composite_is_the_live_site(fleet):
    token = _login(fleet)[1]["result"]["token"]
    gw = fleet["home"]
    path = f"hes-gateway/terminal/getDeviceCompositeInfo?gatewayId={gw.serial}&refreshFlag=1"
    res = _call(fleet, "GET", path, token=token)[1]["result"]
    rt = res["runtimeData"]
    snap = gw.site._last
    assert rt["soc"] == pytest.approx(snap.soc, abs=0.1)
    assert rt["p_fhp"] == pytest.approx(snap.battery_w, abs=0.1)
    assert rt["main_sw"][0] == 1 and res["solarHaveVo"]["offGridFlag"] == 0
    assert rt["pro_load"] == [1, 1, 0] and rt["v2lModeEnable"] == 0      # two circuits, both on
    assert res["currentWorkMode"] in (1, 2, 3)
    apply(gw, "circuit", {"n": 2, "on": False})
    apply(gw, "grid", {"available": False})                             # an outage, not requested
    res = _call(fleet, "GET", path, token=token)[1]["result"]
    assert res["runtimeData"]["pro_load"] == [1, 0, 0]
    assert res["solarHaveVo"]["offGridFlag"] == 1 and res["runtimeData"]["main_sw"][0] == 0
    assert _call(fleet, "GET", "hes-gateway/terminal/getDeviceCompositeInfo?gatewayId=nope",
                 token=token)[1]["code"] == 400


def test_send_mqtt_353_is_the_smart_circuit_meter(fleet):
    """#297: franklinwh-cloud's get_stats sends 353 when a circuit is on; the reply echoes the
    envelope with cmdType + 1 and the loads as a JSON string."""
    token = _login(fleet)[1]["result"]["token"]
    gw = fleet["home"]
    apply(gw, "circuit", {"n": 1, "on": True, "w": 1500})
    area = json.dumps({"opt": 0, "order": gw.serial}, separators=(",", ":"))
    envelope = json.dumps({"lang": "EN_US", "cmdType": 353, "equipNo": gw.serial, "type": 0,
                           "timeStamp": 0, "snno": 7, "len": len(area), "crc": "0", "dataArea": {}})
    req = urllib.request.Request(_base(fleet) + "hes-gateway/terminal/sendMqtt",
                                 data=envelope.encode(), method="POST",
                                 headers={"loginToken": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        res = json.load(r)["result"]
    assert res["cmdType"] == 354 and res["snno"] == 7 and res["equipNo"] == gw.serial
    loads = json.loads(res["dataArea"])
    assert loads["SW1ExpPower"] == pytest.approx(1500, abs=1) and "CarSWImpEnergy" in loads
    other = envelope.replace('"cmdType": 353', '"cmdType": 311')
    req = urllib.request.Request(req.full_url, data=other.encode(), method="POST",
                                 headers={"loginToken": token})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.load(r)["code"] == 400                       # not emulated yet (1b)


def test_unknown_path_is_404_with_the_envelope(fleet):
    token = _login(fleet)[1]["result"]["token"]
    status, body = _call(fleet, "GET", "hes-gateway/terminal/notAThing", token=token)
    assert status == 404 and body["code"] == 404 and body["success"] is False
    assert _call(fleet, "GET", "elsewhere")[0] == 404


def test_validate_checks_the_cloud_section():
    base = {"gateways": [{"id": "a"}]}
    assert validate_fleet({**base, "cloud": {"account": "a@example.com", "password": "x"}}) == []
    assert [p["path"] for p in validate_fleet({**base, "cloud": {"user": "x"}})] == ["cloud.user"]
    assert [p["path"] for p in validate_fleet({**base, "cloud": "x"})] == ["cloud"]


def test_franklinwh_cloud_client_reads_the_emulator(fleet):
    """Acceptance (#297): franklinwh-cloud, pointed here by url_base and nothing else."""
    pytest.importorskip("franklinwh_cloud")
    from franklinwh_cloud.auth import TokenAuth
    from franklinwh_cloud.client import Client
    gw = fleet["home"]
    token = _login(fleet)[1]["result"]["token"]     # 0.6.5's PasswordAuth logs in at the real cloud

    async def go():
        c = Client(TokenAuth(token), gw.serial, url_base=_base(fleet), heartbeat=False)
        listing = await c.get_home_gateway_list()
        stats = await c.get_stats()
        apply(gw, "circuit", {"n": 1, "on": True, "w": 1200})
        apply(gw, "mode", {"mode": 2})                  # Modbus 15507 numbering: self-consumption
        after = await c.get_stats()
        return listing, stats, after
    listing, stats, after = asyncio.run(go())
    assert {r["id"] for r in listing["result"]} == {gw.serial, fleet["us"].serial}
    assert not getattr(stats, "is_stale", False)                      # a reading, not a placeholder
    assert stats.current.battery_soc == pytest.approx(gw.site._last.soc, abs=0.2)
    assert stats.current.grid_connection_state.name == "CONNECTED"
    assert after.current.work_mode_desc == "Self-Consumption"
    assert after.current.switch_1_load == pytest.approx(1200, abs=1)       # via sendMqtt 353
