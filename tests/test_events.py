"""Event log per gateway (#140): what the gateway did, and when."""

import json
import time
import urllib.request

import pytest

from conftest import RawClient, make_gateway
from franklinwh_emulator.control import apply
from franklinwh_emulator.events import EventLog
from franklinwh_emulator.gateway import Fleet, GatewayConfig
from franklinwh_emulator.site import MODE_BACKUP


def texts(gw):
    return [e["text"] for e in gw.events.recent()]


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_mode_reserve_battery_and_full(clock):
    gw = make_gateway(clock, site={"initial_soc": 95.0})
    site = gw.site
    site.solar_override_w, site.load_override_w = 0, 1000
    site.step()                                     # first look: nothing to compare yet
    assert texts(gw) == []
    site.set_native_mode(MODE_BACKUP)               # charges from the grid (#138)
    clock.advance(1)
    site.step()
    clock.advance(1800)
    site.step()
    t = texts(gw)
    assert "Mode Self-Consumption → Emergency Backup" in t
    assert "Reserve 20 % → 100 %" in t
    assert "Battery Discharging → Charging (5.00 kW, SoC 95.0 %)" in t
    assert "Battery full (100 %)" in t
    e = gw.events.recent()[0]
    assert e["site_time"] and e["at"] and e["seq"] == 1 and e["count"] == 1


def test_grid_lost_and_back_alarms(clock):
    gw = make_gateway(clock)
    gw.site.step()
    apply(gw, "grid", {"available": False})
    clock.advance(1)
    gw.site.step()
    apply(gw, "alarms", {"set": ["OVER_TEMP"]})
    clock.advance(1)
    gw.site.step()
    apply(gw, "alarms", {"clear": "all"})
    apply(gw, "grid", {"restore": True})
    clock.advance(600)
    gw.site.step()
    t = texts(gw)
    assert any(x.startswith("Grid lost") for x in t) and "Grid reconnected" in t
    assert "Alarm raised: OVER_TEMP" in t and "Alarm cleared: OVER_TEMP" in t


def test_off_records_nothing_and_bad_config(clock):
    gw = make_gateway(clock, events="off")
    gw.site.set_native_mode(MODE_BACKUP)
    gw.site.step()
    assert gw.events.recent() == []
    with pytest.raises(ValueError, match="events level"):
        GatewayConfig.from_dict({"events": "loud"})
    with pytest.raises(ValueError, match="unknown keys"):
        GatewayConfig.from_dict({"events": {"level": "normal", "colour": "red"}})


def test_file_gets_json_lines(clock, tmp_path):
    f = tmp_path / "ev.jsonl"
    gw = make_gateway(clock, events={"level": "normal", "file": str(f)})
    gw.site.step()
    gw.site.set_native_mode(MODE_BACKUP)
    gw.site.step()
    lines = [json.loads(x) for x in f.read_text().splitlines()]
    assert lines[0]["kind"] == "mode" and "_t" not in lines[0]


def test_normal_folds_repeats_verbose_keeps_ports():
    ev = EventLog("normal")
    for port in (50001, 50002, 50003):
        ev.client("Modbus TCP", "refused", ("127.0.0.1", port))
    (e,) = ev.recent()
    assert e["text"] == "Modbus TCP client 127.0.0.1 refused" and e["count"] == 3 and e["last_at"]
    assert e["seq"] == 1 and e["rev"] == 3                 # folds bump the revision ...
    assert ev.recent(since=2) == [e]                       # ... so ?since= returns the updated entry
    assert ev.recent(since=3) == []
    ev = EventLog("verbose")
    for port in (50001, 50002):
        ev.client("Modbus TCP", "refused", ("127.0.0.1", port))
    assert [x["text"] for x in ev.recent()] == ["Modbus TCP client 127.0.0.1:50001 refused",
                                               "Modbus TCP client 127.0.0.1:50002 refused"]


def test_clients_and_api():
    fleet = Fleet.from_dict({"gateways": [{"id": "a", "setup": "in-service", "events": "verbose"}]})
    fleet.start(control_port=0)
    try:
        gw = fleet["a"]
        c = RawClient(*gw.address)
        c.read(0, 2)
        c2 = RawClient(*gw.address)                      # one session: refused
        assert _wait(lambda: any("refused" in x for x in texts(gw)))
        c.close()
        c2.sock.close()
        assert _wait(lambda: any(x.endswith(" left") for x in texts(gw)))
        url = f"http://127.0.0.1:{fleet.control.port}/gateways/a/events"
        with urllib.request.urlopen(url, timeout=2) as r:
            body = json.load(r)
        kinds = [e["text"].rsplit(" ", 1)[-1] for e in body["events"] if e["kind"] == "client"]
        assert body["level"] == "verbose" and kinds[:2] == ["connected", "refused"] and "left" in kinds
        last = max(e["rev"] for e in body["events"])
        with urllib.request.urlopen(url + f"?since={last}", timeout=2) as r:
            assert json.load(r)["events"] == []
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(url + "?since=x", timeout=2)
    finally:
        fleet.stop()


def test_modbus_close_and_open_are_events():
    fleet = Fleet.from_dict({"gateways": [{"id": "a", "setup": "in-service"}]})
    fleet.start()
    try:
        gw = fleet["a"]
        gw.modbus_apply_delay_s = 0
        gw.set_modbus_enabled(False)
        assert _wait(lambda: "Modbus TCP Server closed" in texts(gw))
        gw.set_modbus_enabled(True)
        assert _wait(lambda: any(x.startswith("Modbus TCP Server opened") for x in texts(gw)))
    finally:
        fleet.stop()
