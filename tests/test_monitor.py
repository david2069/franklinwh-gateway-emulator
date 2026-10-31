"""Monitor (#141): clients per gateway and overall, refusals, and the process."""

import json
import time
import urllib.request

import pytest

from conftest import RawClient
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.monitor import ClientTable, rss_mb


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def fleet():
    f = Fleet.from_dict({"gateways": [{"id": "a", "setup": "in-service"},
                                      {"id": "b", "setup": "in-service"}]})
    f.start(control_port=0)
    yield f
    f.stop()


def monitor(fleet):
    with urllib.request.urlopen(f"http://127.0.0.1:{fleet.control.port}/monitor", timeout=2) as r:
        return json.load(r)


def test_client_table_counts_and_window():
    t = ClientTable()
    e = t.open(("127.0.0.1", 50001))
    t.request(e)
    t.request(e)
    t.refuse(("127.0.0.1", 50002))
    snap = t.snapshot()
    assert snap["clients"][0]["peer"] == "127.0.0.1:50001" and snap["clients"][0]["requests"] == 2
    assert snap["refused_1h"] == 1 and snap["last_refused"]["peer"] == "127.0.0.1:50002"
    assert t.snapshot(now=time.time() + 3601)["refused_1h"] == 0      # older than an hour: dropped
    t.close(e)
    assert t.snapshot()["clients"] == [] and t.accepted == 1 and t.requests == 2


def test_monitor_shows_the_client_and_the_refused_second(fleet):
    c = RawClient(*fleet["a"].address)
    c.read(0, 2)
    c2 = RawClient(*fleet["a"].address)                 # one session: this one is refused
    assert _wait(lambda: fleet["a"].server.clients.snapshot()["refused_1h"] == 1)
    m = monitor(fleet)
    a = next(g for g in m["gateways"] if g["id"] == "a")
    b = next(g for g in m["gateways"] if g["id"] == "b")
    assert len(a["modbus"]["clients"]) == 1 and a["modbus"]["clients"][0]["requests"] == 1
    assert a["modbus"]["listening"] and a["modbus"]["port"] == fleet["a"].address[1]
    assert a["modbus"]["refused_1h"] == 1 and b["modbus"]["clients"] == []
    assert m["totals"] == {**m["totals"], "gateways": 2, "with_clients": 1, "connections": 1, "refused_1h": 1}
    c.close()
    c2.sock.close()
    assert _wait(lambda: fleet["a"].server.clients.snapshot()["clients"] == [])


def test_monitor_remembers_a_client_that_left(fleet):
    """#154: a short visit must still show once the client has gone."""
    c = RawClient(*fleet["a"].address)
    c.read(0, 2)
    c.close()
    assert _wait(lambda: fleet["a"].server.clients.snapshot()["clients"] == [])
    m = monitor(fleet)
    a = next(g for g in m["gateways"] if g["id"] == "a")["modbus"]
    b = next(g for g in m["gateways"] if g["id"] == "b")["modbus"]
    assert a["clients"] == [] and a["accepted"] == 1
    assert a["last_client"]["peer"].startswith("127.0.0.1:") and a["last_client"]["requests"] == 1
    assert a["last_client"]["ago_s"] >= 0 and a["last_client"]["stayed_s"] >= 0
    assert b["last_client"] is None and b["accepted"] == 0
    assert m["totals"]["accepted"] == 1 and m["totals"]["connections"] == 0


def test_monitor_counts_direct_connect_clients(fleet):
    pytest.importorskip("franklinwh_direct_connect_api")
    from franklinwh_direct_connect_api.client import DirectConnectClient
    c = DirectConnectClient(*fleet["b"].dc_address, timeout=2, retries=0)
    c.transport.connect()
    c.login()
    try:
        dc = next(g for g in monitor(fleet)["gateways"] if g["id"] == "b")["dc"]
        assert len(dc["clients"]) == 1 and dc["clients"][0]["requests"] >= 1
    finally:
        c.transport.close()
    assert _wait(lambda: fleet["b"].dc.clients.snapshot()["clients"] == [])


def test_monitor_process_and_rate(fleet):
    monitor(fleet)                                       # first read sets the rate baseline
    c = RawClient(*fleet["a"].address)
    for _ in range(5):
        c.read(0, 2)
    m = monitor(fleet)
    c.close()
    p = m["process"]
    assert p["threads"] >= 2 and p["asyncio_tasks"] >= 1 and p["uptime_s"] >= 0
    assert p["cpu_pct"] >= 0 and p["peak_rss_mb"] > 0 and p["loop_lag_ms"] >= 0
    assert m["totals"]["requests_per_s"] > 0


def test_rss_is_known_here():
    assert rss_mb() is None or rss_mb() > 0
