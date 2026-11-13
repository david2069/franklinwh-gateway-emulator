"""FranklinWH Cloud API, phase 2a (#303): mode, reserve SoC, off-grid and smart-circuit writes,
through franklinwh-cloud pointed at the emulator by url_base."""

import asyncio
import hashlib
import json
import time
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.cloud import CloudError
from franklinwh_emulator.dc_payloads import DcPayloads
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.site import MODE_BACKUP, MODE_SELF, MODE_TOU

pytest.importorskip("franklinwh_cloud")
from franklinwh_cloud.auth import TokenAuth  # noqa: E402
from franklinwh_cloud.client import Client  # noqa: E402
from franklinwh_cloud.models import GridStatus  # noqa: E402

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


def test_set_mode_and_reserve_change_the_site(fleet):
    site = fleet["home"].site
    assert _run(fleet, lambda c: c.set_mode("self_consumption", 35)) is True
    assert site.native_mode == MODE_SELF and site.self_reserve == 35
    assert DcPayloads(fleet["home"]).programme()[1] == "Self-Consumption"   # Direct Connect agrees
    assert _run(fleet, lambda c: c.set_mode("time_of_use", 40)) is True
    assert site.native_mode == MODE_TOU and site.tou_reserve == 40 and site.self_reserve == 35
    mode = _run(fleet, lambda c: c.get_mode())
    assert mode["workMode"] == 1 and int(mode["soc"]) == 40
    _run(fleet, lambda c: c.update_soc(requestedSOC=25, workMode=2))
    assert site.self_reserve == 25 and site.native_mode == MODE_TOU
    socs = {m["workMode"]: m for m in _run(fleet, lambda c: c.get_all_mode_soc())}
    assert socs[2]["soc"] == 25 and socs[1]["active"] is True


def test_emergency_backup_for_a_fixed_time_ends_in_the_next_mode(fleet):
    site, cloud = fleet["home"].site, fleet.cloud
    assert _run(fleet, lambda c: c.set_mode("emergency_backup", None, 2, 2, 30)) is True
    assert site.native_mode == MODE_BACKUP
    st = cloud.mode_state(fleet["home"])
    assert st["backupForeverFlag"] == 2 and st["timer"][1] - st["timer"][0] == 30 * 60
    st["timer"] = (time.time() - 1801, time.time() - 1)             # the 30 minutes are up
    assert _run(fleet, lambda c: c.get_mode())["workMode"] == 2      # the next cloud call
    assert site.native_mode == MODE_SELF


def test_out_of_range_reserve_and_bad_mode_are_refused(fleet):
    token, sn = _token(fleet), fleet["home"].serial
    for query in (f"workMode=2&soc=101&gatewayId={sn}", f"workMode=2&soc=2&gatewayId={sn}",
                  f"workMode=7&soc=30&gatewayId={sn}"):
        with pytest.raises(CloudError) as e:
            fleet.cloud.route("POST", "/hes-gateway/terminal/tou/updateSocV2?" + query,
                              {"logintoken": token}, b"")
        assert e.value.code == 400
    assert fleet["home"].site.self_reserve == 20


def test_off_grid_and_circuit_writes(fleet):
    gw = fleet["home"]
    _run(fleet, lambda c: c.set_grid_status(GridStatus.OFF, 15))
    assert gw.site.offgrid_requested and gw.site.config.offgrid_floor_soc == 15
    stats = _run(fleet, lambda c: c.get_stats())
    assert stats.current.grid_connection_state.name == "SIMULATED_OFF_GRID"
    _run(fleet, lambda c: c.set_grid_status(GridStatus.NORMAL))
    assert not gw.site.offgrid_requested
    assert gw.site.circuits[2].on
    _run(fleet, lambda c: c.set_smart_circuit_state(2, False))
    assert not gw.site.circuits[2].on
    assert _run(fleet, lambda c: c.get_stats()).current.switch_2_load == 0
