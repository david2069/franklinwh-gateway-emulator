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
        "defaults": {"tz_offset_hours": 0},
        "gateways": [
            {"id": "site", "seed": 7, "apower_units": 2, "ac_type": 1},
            {"id": "shed", "seed": 42, "span_unlock": True},
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
        b.write(15507, 1)
        assert b.read(15507, 1) == [1] and a.read(15507, 1) == [2]
        a.close(), b.close()


def test_fleet_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        Fleet.from_dict({"gateways": [{"id": "x"}, {"id": "x"}]})


def test_serial_matches_direct_connect_emulator():
    # Same seed → same serial as franklinwh_local.synthetic.SyntheticSite(7).serial
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


@pytest.mark.parametrize("profile,islanded_15016", [
    ("agate-v12r02b60d00-candidate", 1),   # observed twice, one site, 2026-10-21
    ("agate-v10r01b04d00", 2),             # no islanded observation: stays static
])
def test_episode_drop_then_return_islanded(clock, profile, islanded_15016):
    gw = make_gateway(clock, profile=profile, site={"initial_soc": 60})
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
