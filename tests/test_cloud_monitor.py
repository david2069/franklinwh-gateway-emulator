"""#314: Cloud API clients in the Monitor, the gateway state and the events, like Modbus TCP and
Direct Connect."""

import asyncio
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.cli import _monitor_text
from franklinwh_emulator.control import apply, gateway_state
from franklinwh_emulator.gateway import Fleet

pytest.importorskip("franklinwh_cloud")
from franklinwh_cloud.auth import TokenAuth  # noqa: E402
from franklinwh_cloud.client import Client  # noqa: E402

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}


@pytest.fixture
def fleet():
    f = Fleet.from_dict({"gateways": [
        {"id": a, "port": 0, "dc_port": 0, "seed": s, "location": AU} for a, s in (("home", 3), ("away", 4))]})
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


def _monitor(f) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}/monitor", timeout=5) as r:
        return json.load(r)


def _stats(f, gw="home"):
    async def go():
        c = Client(TokenAuth(_token(f)), f[gw].serial,
                   url_base=f"http://127.0.0.1:{f.cloud.port}/", heartbeat=False)
        return await c.get_stats()
    return asyncio.run(go())


def test_a_session_shows_in_the_monitor_with_its_app_version(fleet):
    m = _monitor(fleet)
    assert m["cloud"]["address"] == ["127.0.0.1", fleet.cloud.port]
    home = next(r for r in m["gateways"] if r["id"] == "home")
    assert home["cloud"]["listening"] and home["cloud"]["accepted"] == 0
    _stats(fleet)
    m = _monitor(fleet)
    home, away = (next(r for r in m["gateways"] if r["id"] == g) for g in ("home", "away"))
    [c] = home["cloud"]["clients"]
    assert c["peer"] == "127.0.0.1" and c["app"] == "APP2.4.1" and c["requests"] >= 2
    assert away["cloud"]["clients"] == []                       # only the gateway it asked about
    assert m["cloud"]["logins"] == 1 and m["totals"]["with_clients"] == 1
    _stats(fleet)                                               # a second login: a second client
    assert _monitor(fleet)["gateways"][0]["cloud"]["accepted"] == 2
    assert "Cloud API" in _monitor_text(_monitor(fleet))


def test_connected_left_and_refused_are_events(fleet):
    gw = fleet["home"]
    _stats(fleet)
    texts = [e["text"] for e in gw.events.recent(200, 0)]
    assert "Cloud API client 127.0.0.1 connected" in texts
    gw.cloud_clients.sweep(time.time() + 301)                  # five minutes without a request
    assert gw.cloud_clients.snapshot(time.time() + 302)["last_client"]["requests"] >= 2
    assert "Cloud API client 127.0.0.1 left" in [e["text"] for e in gw.events.recent(200, 0)]
    apply(gw, "cloud-faults", {"rate_limit_per_min": 0})
    url = (f"http://127.0.0.1:{fleet.cloud.port}/hes-gateway/terminal/getDeviceCompositeInfo"
           f"?gatewayId={gw.serial}")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(urllib.request.Request(url, headers={"loginToken": _token(fleet)}))
    assert gw.cloud_clients.snapshot()["refused_1h"] == 1
    assert "Cloud API client 127.0.0.1 refused" in [e["text"] for e in gw.events.recent(200, 0)]


def test_gateway_state_carries_the_cloud_address_and_faults(fleet):
    gw = fleet["home"]
    assert gateway_state(gw)["cloud_address"] == ["127.0.0.1", fleet.cloud.port]
    apply(gw, "cloud-faults", {"bad_reply": "html_503"})
    assert gateway_state(gw)["cloud_faults"]["bad_reply"] == "html_503"
    fleet.stop()
    assert gateway_state(gw)["cloud_address"] is None


def test_without_cloud_port_the_column_says_not_listening():
    f = Fleet.from_dict({"gateways": [{"id": "a", "port": 0, "dc_port": 0, "seed": 3}]})
    f.start(control_port=0)
    try:
        m = _monitor(f)
        assert m["cloud"] is None and m["gateways"][0]["cloud"]["listening"] is False
        assert gateway_state(f["a"])["cloud_address"] is None
    finally:
        f.stop()


def test_a_cloud_api_started_as_the_cli_does_shows_in_the_ui():
    """#318: cli._run builds CloudApi itself, not through Fleet.start."""
    from franklinwh_emulator.cloud import CloudApi
    f = Fleet.from_dict({"gateways": [{"id": "a", "port": 0, "dc_port": 0, "seed": 3}]})
    gw = f["a"]

    async def go():
        api = CloudApi({"a": gw}, port=0)
        await api.start()
        on = gateway_state(gw)["cloud_address"]
        await api.stop()
        return api.port, on, gateway_state(gw)["cloud_address"]
    port, on, off = asyncio.run(go())
    assert on == ["127.0.0.1", port] and off is None
