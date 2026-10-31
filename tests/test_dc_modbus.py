"""Direct Connect writes the control API sends to a gateway's own listener: Modbus TCP on/off
(#63) and reboot (#68)."""

import asyncio
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

pytest.importorskip("franklinwh_direct_connect_api")
from franklinwh_emulator.control import ApiError, ControlApi, apply  # noqa: E402
from franklinwh_emulator.gateway import Fleet  # noqa: E402

FLEET = {"gateways": [{"id": "fresh", "seed": 21, "setup": "factory", "dc_port": 0},
                      {"id": "nodc", "seed": 3, "profile": "spec"}]}


@pytest.fixture
def fleet(tmp_path):
    f = Fleet.from_dict(FLEET)
    f.start(control_port=0, ui=True, workspace=str(tmp_path))
    for gw in f.gateways.values():
        gw.modbus_apply_delay_s = 0.1
    yield f
    f.stop()


def post(f, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{f.control.port}{path}", method="POST",
                                 data=json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def state(f, gid):
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}/gateways/{gid}", timeout=5) as r:
        return json.load(r)


def wait_listening(f, gid, want, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = state(f, gid)
        if s["modbus_listening"] == want:
            return s
        time.sleep(0.05)
    raise AssertionError(f"{gid}: modbus_listening never became {want}")


def test_enable_then_disable_over_direct_connect(fleet):
    s = state(fleet, "fresh")
    assert (s["modbus_enabled"], s["modbus_listening"]) == (False, False)      # factory
    status, s = post(fleet, "/gateways/fresh/dc-modbus", {"on": True})
    assert status == 200 and s["modbus_enabled"] is True
    s = wait_listening(fleet, "fresh", True)
    socket.create_connection(tuple(s["address"]), timeout=2).close()          # :502 answers
    status, _ = post(fleet, "/gateways/fresh/dc-modbus", {"on": False})
    assert status == 200
    wait_listening(fleet, "fresh", False)
    log = [(e["action"], e["body"], e["ok"]) for e in fleet.control.log]
    assert log == [("dc-modbus", {"on": True}, True), ("dc-modbus", {"on": False}, True)]


def test_it_goes_over_the_wire(fleet):
    """With the gateway's Direct Connect listener stopped, the write can't be delivered."""
    gw = fleet["fresh"]
    fleet._loop.run(gw.dc.stop())
    status, err = post(fleet, "/gateways/fresh/dc-modbus", {"on": True})
    assert status == 400 and err["error"].startswith("dc-modbus: the Direct Connect write failed")
    assert gw.modbus_enabled is False
    assert fleet.control.log[-1]["ok"] is False


def test_refused_without_a_listener_or_a_value(fleet):
    status, err = post(fleet, "/gateways/nodc/dc-modbus", {"on": True})
    assert status == 400 and err["error"] == "dc-modbus: nodc has no Direct Connect listener"
    status, err = post(fleet, "/gateways/fresh/dc-modbus", {})
    assert status == 400 and "missing ['on']" in err["error"]


def test_restore_replays_it_over_the_wire(fleet):
    """Restore runs off the event loop, so a replayed dc-modbus doesn't deadlock."""
    post(fleet, "/gateways/fresh/dc-modbus", {"on": True})
    wait_listening(fleet, "fresh", True)
    status, saved = post(fleet, "/session/save", {"name": "modbus-on"})
    assert status == 200
    fleet["fresh"].set_modbus_enabled(False)
    wait_listening(fleet, "fresh", False)
    status, r = post(fleet, "/session/restore", {"name": "modbus-on"})
    assert status == 200 and r["failed"] == 0
    wait_listening(fleet, "fresh", True)
    assert [(e["action"], e["ok"]) for e in fleet.control.log if e["source"] == "restore:modbus-on"] == [
        ("dc-modbus", True), ("soc", True), ("soc", True)]


def test_not_in_process_and_not_on_the_loop(fleet):
    with pytest.raises(ApiError, match="sent over Direct Connect"):
        apply(fleet["fresh"], "dc-modbus", {"on": True})

    async def on_loop():
        ControlApi.dc_write(fleet["fresh"], "dc-modbus", {"on": True})
    with pytest.raises(RuntimeError, match="worker thread"):
        asyncio.run(on_loop())


def test_reboot_over_direct_connect(fleet, tmp_path):
    gw = fleet["fresh"]
    gw.reboot_s = 0.5
    post(fleet, "/gateways/fresh/dc-modbus", {"on": True})
    s = wait_listening(fleet, "fresh", True)
    modbus, dc = tuple(s["address"]), tuple(s["dc_address"])
    status, s = post(fleet, "/gateways/fresh/dc-reboot", {})
    assert status == 200 and s["rebooting"] is True
    time.sleep(0.2)
    for addr in (modbus, dc):                                   # both listeners are down
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(addr, timeout=1)
    end = time.monotonic() + 3
    while state(fleet, "fresh")["rebooting"] and time.monotonic() < end:
        time.sleep(0.05)
    s = state(fleet, "fresh")
    assert s["rebooting"] is False and s["modbus_listening"] is True
    for addr in (modbus, dc):                                   # back on the same ports
        socket.create_connection(addr, timeout=1).close()
    assert [e["action"] for e in fleet.control.log] == ["dc-modbus", "dc-reboot"]
    status, saved = post(fleet, "/session/save", {"name": "after-reboot"})
    text = (tmp_path / "sessions" / "after-reboot.yaml").read_text()
    assert "dc-modbus" in text and "dc-reboot" not in text      # a restore doesn't reboot


def test_reboot_goes_over_the_wire(fleet):
    gw = fleet["fresh"]
    fleet._loop.run(gw.dc.stop())
    status, err = post(fleet, "/gateways/fresh/dc-reboot", {})
    assert status == 400 and err["error"].startswith("dc-reboot: the Direct Connect write failed")
    assert gw.rebooting is False
    status, err = post(fleet, "/gateways/nodc/dc-reboot", {})
    assert status == 400 and err["error"] == "dc-reboot: nodc has no Direct Connect listener"
    with pytest.raises(ApiError, match="sent over Direct Connect"):
        apply(gw, "dc-reboot", {})
