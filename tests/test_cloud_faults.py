"""FranklinWH Cloud API, phase 3 (#309): faults, as franklinwh-cloud sees them."""

import asyncio
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.cli import _ctl_request
from franklinwh_emulator.control import ApiError, apply
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.validate import validate_fleet

pytest.importorskip("franklinwh_cloud")
from franklinwh_cloud import exceptions as fx  # noqa: E402
from franklinwh_cloud.auth import TokenAuth  # noqa: E402
from franklinwh_cloud.client import Client  # noqa: E402

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}


@pytest.fixture
def fleet():
    f = Fleet.from_dict({"cloud": {"token_ttl_s": 1.0}, "gateways": [
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


def _run(f, fn, token=None):
    async def go():
        c = Client(TokenAuth(token or _token(f)), f["home"].serial,
                   url_base=f"http://127.0.0.1:{f.cloud.port}/", heartbeat=False)
        return await fn(c)
    return asyncio.run(go())


def test_offline_and_timeout_raise_the_clients_exceptions(fleet):
    gw = fleet["home"]
    apply(gw, "cloud-faults", {"timeout": True})
    with pytest.raises(fx.DeviceTimeoutException):
        _run(fleet, lambda c: c.get_power_info())
    apply(gw, "cloud-faults", {"timeout": False, "offline": True})
    with pytest.raises(fx.GatewayOfflineException):
        _run(fleet, lambda c: c.get_power_info())
    rows = _run(fleet, lambda c: c.get_home_gateway_list())["result"]
    assert rows[0]["status"] == 0
    apply(gw, "cloud-faults", {"offline": False})
    assert _run(fleet, lambda c: c.get_power_info())["gridFreq"] > 0
    with pytest.raises(ApiError):
        apply(gw, "cloud-faults", {"nope": True})


def test_an_empty_composite_gives_last_known_good_marked_stale(fleet):
    gw = fleet["home"]

    async def two(c):
        good = await c.get_stats()
        fresh = (getattr(good, "is_stale", False), good.current.battery_soc)
        apply(gw, "cloud-faults", {"empty_composite": True})
        return fresh, await c.get_stats()
    (was_stale, soc), stale = _run(fleet, two)
    assert was_stale is False and stale.is_stale is True        # the client's last-known-good
    assert stale.current.battery_soc == soc


def test_rate_limit_is_429_with_retry_after(fleet):
    gw = fleet["home"]
    apply(gw, "cloud-faults", {"rate_limit_per_min": 2})
    token = _token(fleet)
    url = (f"http://127.0.0.1:{fleet.cloud.port}/hes-gateway/terminal/getDeviceCompositeInfo"
           f"?gatewayId={gw.serial}")
    for _ in range(2):
        with urllib.request.urlopen(urllib.request.Request(url, headers={"loginToken": token})) as r:
            assert json.load(r)["code"] == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(urllib.request.Request(url, headers={"loginToken": token}))
    assert e.value.code == 429 and 1 <= int(e.value.headers["Retry-After"]) <= 61
    assert json.load(e.value)["code"] == 429


def test_latency_delays_the_reply(fleet):
    apply(fleet["home"], "cloud-faults", {"latency_s": 0.3})
    t0 = time.monotonic()
    _run(fleet, lambda c: c.get_device_composite_info())
    assert time.monotonic() - t0 >= 0.3


def test_tokens_expire_after_the_ttl(fleet):
    token = _token(fleet)
    assert _run(fleet, lambda c: c.get_device_composite_info(), token)["code"] == 200
    time.sleep(1.1)
    with pytest.raises(fx.TokenExpiredException):
        _run(fleet, lambda c: c.get_device_composite_info(), token)


def test_ctl_and_validate_know_the_cloud_faults():
    assert _ctl_request("cloud-fault", ["timeout=true"]) == ("POST", "cloud-faults", {"timeout": True})
    assert validate_fleet({"gateways": [{"id": "a"}], "cloud": {"token_ttl_s": 60}}) == []
