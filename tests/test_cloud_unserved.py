"""#316 step 2: the Cloud API calls the emulator doesn't answer yet show in the Monitor, the
events and `franklinwh-emulator monitor`."""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.cli import _monitor_text
from franklinwh_emulator.gateway import Fleet


@pytest.fixture
def fleet():
    f = Fleet.from_dict({"gateways": [{"id": "home", "port": 0, "dc_port": 0, "seed": 3}]})
    f.start(control_port=0, cloud_port=0)
    yield f
    f.stop()


def _token(f) -> str:
    form = {"account": "owner@example.com", "password": hashlib.md5(b"emulator").hexdigest()}
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/terminal/initialize/"
                                 "appUserOrInstallerLogin",
                                 data=urllib.parse.urlencode(form).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)["result"]["token"]


def _call(f, path, token, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}{path}", headers={"loginToken": token},
                                 data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def _monitor(f) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}/monitor", timeout=5) as r:
        return json.load(r)


def test_unserved_calls_are_counted_logged_and_listed(fleet):
    gw, token = fleet["home"], _token(fleet)
    sn = gw.serial
    for _ in range(2):
        assert _call(fleet, f"/hes-gateway/terminal/chargePowerDetails?gatewayId={sn}", token)[0] == 404
    status, body = _call(fleet, "/hes-gateway/terminal/sendMqtt", token,
                         {"cmdType": 317, "equipNo": sn, "dataArea": "{}"})
    assert status == 200 and body["code"] == 400
    assert _call(fleet, "/hes-gateway/common/getAppGlobalConfig", token)[0] == 404     # names no gateway
    assert _call(fleet, f"/hes-gateway/terminal/getDeviceCompositeInfo?gatewayId={sn}", token)[1]["code"] == 200

    m = _monitor(fleet)
    assert m["cloud"]["unserved"] == {"GET /hes-gateway/terminal/chargePowerDetails": 2,
                                      "sendMqtt cmdType 317": 1,
                                      "GET /hes-gateway/common/getAppGlobalConfig": 1}
    assert m["gateways"][0]["cloud"]["unserved"] == {"GET /hes-gateway/terminal/chargePowerDetails": 2,
                                                     "sendMqtt cmdType 317": 1}
    events = [e for e in gw.events.recent(200, 0) if e["kind"] == "cloud"]
    assert [(e["text"], e["count"]) for e in events] == [
        ("Cloud API call not emulated: GET /hes-gateway/terminal/chargePowerDetails", 2),
        ("Cloud API call not emulated: sendMqtt cmdType 317", 1)]
    text = _monitor_text(m)
    assert "Cloud API calls not emulated yet:" in text
    assert "      2  GET /hes-gateway/terminal/chargePowerDetails" in text


def test_nothing_listed_when_every_call_is_served(fleet):
    token = _token(fleet)
    _call(fleet, f"/hes-gateway/terminal/getDeviceCompositeInfo?gatewayId={fleet['home'].serial}", token)
    m = _monitor(fleet)
    assert m["cloud"]["unserved"] == {} and "not emulated" not in _monitor_text(m)
