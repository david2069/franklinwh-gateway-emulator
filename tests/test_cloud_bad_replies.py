"""FranklinWH Cloud API, #311: broken replies below the envelope and the account-wide rate limit,
as franklinwh-cloud sees them."""

import asyncio
import hashlib
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.control import ApiError, apply
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.validate import validate_fleet

pytest.importorskip("franklinwh_cloud")
import httpx  # noqa: E402
from franklinwh_cloud import exceptions as fx  # noqa: E402
from franklinwh_cloud.auth import TokenAuth  # noqa: E402
from franklinwh_cloud.client import Client  # noqa: E402

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}


@pytest.fixture
def fleet():
    f = Fleet.from_dict({"gateways": [
        {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "location": AU,
         "weather": {"model": "bell"}, "initial_soc": 60}]})
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


def _composite(f, token, timeout=5):
    url = (f"http://127.0.0.1:{f.cloud.port}/hes-gateway/terminal/getDeviceCompositeInfo"
           f"?gatewayId={f['home'].serial}")
    return urllib.request.urlopen(urllib.request.Request(url, headers={"loginToken": token}),
                                  timeout=timeout)


@pytest.mark.parametrize("kind, raises", [
    ("reset", httpx.TransportError), ("truncate", httpx.TransportError),
    ("not_json", fx.InvalidResponseError), ("html_503", fx.InvalidResponseError),
    ("bad_data_area", ValueError)])
def test_franklinwh_cloud_raises_on_each_broken_reply(fleet, kind, raises):
    apply(fleet["home"], "cloud-faults", {"bad_reply": kind})
    with pytest.raises(raises) as e:
        _run(fleet, lambda c: c.get_power_info())
    if kind == "html_503":
        assert e.value.status_code == 503 and "<html>" in e.value.body


def test_replies_without_the_envelope_or_success_are_taken_at_their_code(fleet):
    gw = fleet["home"]
    apply(gw, "cloud-faults", {"bad_reply": "not_envelope"})
    with pytest.raises(fx.FranklinWHError) as e:                # no code: not 200
        _run(fleet, lambda c: c.get_power_info())
    assert e.value.code is None
    for kind in ("success_false", "wrong_cmd_type", "bad_crc"):  # the client doesn't check these
        apply(gw, "cloud-faults", {"bad_reply": kind})
        assert _run(fleet, lambda c: c.get_power_info())["gridFreq"] > 0, kind


def test_sendmqtt_faults_leave_other_requests_alone(fleet):
    apply(fleet["home"], "cloud-faults", {"bad_reply": "bad_crc", "bad_reply_count": 1})
    with _composite(fleet, _token(fleet)) as r:
        assert json.load(r)["code"] == 200
    assert fleet["home"].cloud_faults.bad_reply == "bad_crc"   # not used up


def test_count_and_chance(fleet):
    gw, token = fleet["home"], _token(fleet)
    apply(gw, "cloud-faults", {"bad_reply": "html_502", "bad_reply_count": 2})
    for _ in range(2):
        with pytest.raises(urllib.error.HTTPError) as e:
            _composite(fleet, token)
        assert e.value.code == 502 and e.value.headers["Content-Type"] == "text/html"
    with _composite(fleet, token) as r:
        assert json.load(r)["code"] == 200
    assert gw.cloud_faults.bad_reply is None
    apply(gw, "cloud-faults", {"bad_reply": "html_500", "bad_reply_p": 0.5})
    bad = 0
    for _ in range(40):
        try:
            _composite(fleet, token).close()
        except urllib.error.HTTPError:
            bad += 1
    assert 8 <= bad <= 32
    with pytest.raises(ApiError):
        apply(gw, "cloud-faults", {"bad_reply": "nope"})


def test_a_hang_times_out_the_client_and_doesnt_hold_stop(fleet):
    apply(fleet["home"], "cloud-faults", {"bad_reply": "hang"})
    t0 = time.monotonic()
    with pytest.raises((socket.timeout, urllib.error.URLError, TimeoutError)):
        _composite(fleet, _token(fleet), timeout=0.5)
    assert time.monotonic() - t0 < 3
    s = socket.create_connection(("127.0.0.1", fleet.cloud.port))   # still held when stop() runs
    s.sendall(b"GET /hes-gateway/terminal/getDeviceCompositeInfo?gatewayId="
              + fleet["home"].serial.encode() + b" HTTP/1.1\r\nloginToken: "
              + _token(fleet).encode() + b"\r\n\r\n")
    time.sleep(0.2)
    t0 = time.monotonic()
    fleet.stop()
    assert time.monotonic() - t0 < 3
    s.close()


def test_the_account_rate_limit_counts_every_gateway():
    f = Fleet.from_dict({"cloud": {"rate_limit_per_min": 3}, "gateways": [
        {"id": a, "port": 0, "dc_port": 0, "seed": 3, "location": AU} for a in ("a", "b")]})
    f.start(cloud_port=0)
    try:
        token = _token(f)
        base = f"http://127.0.0.1:{f.cloud.port}/hes-gateway/terminal/"
        urls = [base + "getHomeGatewayList"] + [base + f"getDeviceCompositeInfo?gatewayId={f[a].serial}"
                                                for a in ("a", "b")]
        for url in urls:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"loginToken": token})) as r:
                assert json.load(r)["code"] == 200
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(urls[1], headers={"loginToken": token}))
        assert e.value.code == 429 and int(e.value.headers["Retry-After"]) >= 1
    finally:
        f.stop()
    assert validate_fleet({"gateways": [{"id": "a"}], "cloud": {"rate_limit_per_min": 60}}) == []
