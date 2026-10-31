"""Session behaviour, fault injection, and multi-gateway fleets."""

import socket
import time

import pytest

from conftest import RawClient, make_gateway
from franklinwh_emulator.gateway import Fleet


def _closed_without_reply(addr) -> bool:
    s = socket.create_connection(addr, timeout=1)
    try:
        s.sendall(bytes.fromhex("000100000006010300000002"))
        return s.recv(16) == b""
    except (ConnectionResetError, socket.timeout):
        return True
    finally:
        s.close()


def test_single_session_by_default(gateway, client):
    assert client.read(0, 2) == [0x5375, 0x6E53]
    assert _closed_without_reply(gateway.address)
    client.close()
    time.sleep(0.1)
    c2 = RawClient(*gateway.address)
    assert c2.read(0, 2) == [0x5375, 0x6E53]
    c2.close()


def test_multi_client_opt_out(clock):
    gw = make_gateway(clock, quirks={"max_connections": None})
    with gw:
        a, b = RawClient(*gw.address), RawClient(*gw.address)
        assert a.read(0, 1) == b.read(0, 1) == [0x5375]
        a.close(), b.close()


def test_session_held_hang(clock):
    gw = make_gateway(clock, quirks={"busy_behaviour": "hang"})
    with gw:
        gw.faults.session_held = True
        s = socket.create_connection(gw.address, timeout=0.5)
        s.sendall(bytes.fromhex("000100000006010300000002"))
        with pytest.raises(socket.timeout):
            s.recv(16)                                 # port open, never answers
        s.close()


def test_exception_and_latency_faults(gateway, client):
    gateway.faults.exception = 6                       # SERVER DEVICE BUSY
    assert client.read(0, 1) == ("exc", 6)
    gateway.faults.exception = None
    gateway.faults.latency_s = 0.2
    t0 = time.monotonic()
    client.read(0, 1)
    assert time.monotonic() - t0 >= 0.2


def test_offline(gateway):
    gateway.faults.offline = True
    assert _closed_without_reply(gateway.address)


def test_bad_function_code(client):
    r = client._call(bytes([0x2B, 0x0E, 0x01, 0x00]))  # read device id: unsupported
    assert r == bytes([0xAB, 1])


def test_fleet_isolated_and_distinct():
    fleet = Fleet.from_dict({
        "defaults": {"tz_offset_hours": 0, "setup": "in-service"},
        "gateways": [
            {"id": "site", "seed": 7, "apower_units": 2, "ac_type": 1},
            {"id": "shed", "seed": 42},
        ],
    })
    with fleet:
        home, shed = fleet["site"], fleet["shed"]
        assert home.serial != shed.serial
        assert home.address[1] != shed.address[1]
        a, b = RawClient(*home.address), RawClient(*shed.address)
        assert a.read(72, 1) == [1]                    # 701.ACType split phase
        assert b.read(72, 1) == [0]
        assert a.read(1035, 1) == [27200]              # 713.WHRtg for two aPowers
        shed.site.set_native_mode(1)
        assert b.read(15507, 1) == [1] and a.read(15507, 1) == [2]
        a.close(), b.close()


def test_fleet_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        Fleet.from_dict({"gateways": [{"id": "x"}, {"id": "x"}]})


def test_serial_matches_direct_connect_emulator():
    # Same seed → same serial as franklinwh_direct_connect_api.synthetic.SyntheticSite(7).serial
    from franklinwh_emulator.gateway import mock_serial
    assert mock_serial(7) == "MOCK00073B1612DD272D"


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _refused(addr) -> bool:
    try:
        socket.create_connection(addr, timeout=0.5).close()
        return False
    except ConnectionRefusedError:
        return True


@pytest.mark.parametrize("profile,quirks,islanded_15016", [
    # 15016 = 1 during the 2026-10-21 episodes is "reported" (ext_15016_status): opt-in only.
    ("agate-v12r02b60d00-candidate", {"islanded_15016": 1}, 1),
    ("agate-v12r02b60d00-candidate", {}, 2),
    ("agate-v10r01b04d00", {}, 2),
])
def test_episode_drop_then_return_islanded(clock, profile, quirks, islanded_15016):
    gw = make_gateway(clock, profile=profile, quirks=quirks, site={"initial_soc": 60})
    with gw:
        addr = gw.address
        c = RawClient(*addr)
        connst = gw.device.image.ref(701, "ConnSt").address
        alrm = gw.device.image.ref(701, "Alrm").address
        assert c.read(connst, 1) == [1] and c.read(15016, 1) == [2]
        gw.episode(down_s=0.5, offgrid_s=0.5)
        assert _wait(lambda: gw.episode_phase == "down")
        with pytest.raises(ConnectionResetError):       # RST: recv raises; a FIN would read b""
            c.sock.recv(1)
        c.sock.close()
        assert _refused(addr)                            # listener gone: connect refused
        assert _wait(lambda: gw.episode_phase == "offgrid")
        c = RawClient(*addr)                             # same port, back islanded
        assert c.read(connst, 1) == [0]
        a = c.read(alrm, 2)
        assert (a[0] << 16 | a[1]) & (1 << 4)            # GRID_DISCONNECT
        assert c.read(15016, 1) == [islanded_15016]
        assert _wait(lambda: gw.episode_phase is None)
        clock.advance(61)                                # enter-service delay (AS4777: 60 s)
        assert c.read(connst, 1) == [1] and c.read(15016, 1) == [2]
        c.close()


def test_range_fault_hits_only_that_range(gateway, client):
    gateway.faults.fail_range(15500, 15509, "exception", code=2)
    assert client.read(15500, 10) == ("exc", 2)
    assert client.read(15505, 1) == ("exc", 2)
    assert client.read(15495, 6) == ("exc", 2)                 # overlaps by one register
    assert client.read(0, 2) == [0x5375, 0x6E53]                # SunSpec unaffected
    assert isinstance(client.read(15510, 4), list)              # just past the range
    gateway.faults.range_fault = None
    assert isinstance(client.read(15500, 10), list)


def test_range_fault_silent_short_and_expiry(gateway, client):
    import struct as _s
    gateway.faults.fail_range(16001, 16001, "silent")
    client.sock.settimeout(0.3)
    with pytest.raises(socket.timeout):
        client.read(16001, 1)
    gateway.faults.range_fault = None
    client.sock.close()
    c = RawClient(*gateway.address)
    gateway.faults.fail_range(15500, 15509, "short")
    c.sock.sendall(_s.pack(">HHHBBHH", 9, 0, 6, 1, 3, 15500, 10))
    head = c._recv(7)
    body = c._recv(_s.unpack(">HHHB", head)[2] - 1)
    assert body[0] == 3 and body[1] == 10                      # 5 registers, not 10
    gateway.faults.fail_range(15500, 15509, "exception", for_s=0.2)
    assert c.read(15500, 1) == ("exc", 4)
    time.sleep(0.25)
    assert isinstance(c.read(15500, 1), list)
    assert gateway.faults.range_fault is None
    c.close()


def test_range_fault_over_control_api():
    import json as _j
    import urllib.request as _u
    fleet = Fleet.from_dict({"gateways": [{"id": "a", "setup": "in-service"}]})
    fleet.start(control_port=0)
    try:
        def post(body):
            req = _u.Request(f"http://127.0.0.1:{fleet.control.port}/gateways/a/range-fault",
                             data=_j.dumps(body).encode(), method="POST")
            return _u.urlopen(req, timeout=2)
        post({"start": 15500, "end": 15509, "mode": "silent", "for_s": 5})
        rf = fleet["a"].faults.range_fault
        assert (rf["start"], rf["end"], rf["mode"]) == (15500, 15509, "silent")
        post({"clear": True})
        assert fleet["a"].faults.range_fault is None
        with pytest.raises(_u.HTTPError):
            post({"start": 15500, "end": 15509, "mode": "bogus"})
    finally:
        fleet.stop()


def test_slow_reply_lands_on_the_next_read(gateway):
    """A reply delayed past the client's timeout arrives on the same socket later, where a
    client that doesn't check the transaction id takes it as the answer to its next request."""
    import struct as _s
    gateway.faults.slow_p, gateway.faults.slow_ms = 1.0, 300
    s = socket.create_connection(gateway.address, timeout=0.1)
    s.sendall(_s.pack(">HHHBBHH", 1, 0, 6, 1, 3, 0, 1))
    with pytest.raises(socket.timeout):
        s.recv(64)                                   # client gives up on tid 1
    gateway.faults.slow_p = 0.0
    s.sendall(_s.pack(">HHHBBHH", 2, 0, 6, 1, 3, 2, 1))
    s.settimeout(2)
    buf = b""
    while len(buf) < 11:
        buf += s.recv(64)
    assert _s.unpack(">H", buf[:2])[0] == 1          # the late tid-1 reply comes first
    s.close()


def test_slow_p_delays_only_a_fraction(gateway, client):
    gateway.faults.slow_p, gateway.faults.slow_ms = 0.3, 50
    slow = 0
    for _ in range(40):
        t0 = time.monotonic()
        client.read(0, 1)
        slow += time.monotonic() - t0 >= 0.045
    assert 4 <= slow <= 20                           # seeded: repeatable, neither none nor all


def test_write_reject_over_control_api():
    import json as _j
    import urllib.request as _u
    fleet = Fleet.from_dict({"gateways": [{"id": "a", "setup": "in-service"}]})
    fleet.start(control_port=0)
    try:
        def post(body):
            req = _u.Request(f"http://127.0.0.1:{fleet.control.port}/gateways/a/write-reject",
                             data=_j.dumps(body).encode(), method="POST")
            return _j.load(_u.urlopen(req, timeout=2))
        st = post({"model": 704, "point": "WSetPct", "abs_gt": 100, "code": 3})
        assert st["write_rejects"][0]["abs_gt"] == 100
        assert post({"clear": True})["write_rejects"] == []
        with pytest.raises(_u.HTTPError):
            post({"model": 704, "point": "WSetPct", "bogus": 1})
    finally:
        fleet.stop()
