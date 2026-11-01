"""Direct Connect frontend: the real franklinwh_direct_connect_api client against the emulator, and
cross-protocol consistency with the Modbus port (one Site behind both)."""

import socket
from pathlib import Path
import struct
import time

import pytest

pytest.importorskip("franklinwh_direct_connect_api")
from franklinwh_direct_connect_api import protocol  # noqa: E402
from franklinwh_direct_connect_api.client import DirectConnectClient  # noqa: E402

from conftest import RawClient, make_gateway  # noqa: E402

TODAY = "2026-01-01"


@pytest.fixture
def gw(clock):
    g = make_gateway(clock, seed=7, dc_port=0, site={"initial_soc": 60, "apower_units": 2})
    g.start()
    yield g
    g.stop()


@pytest.fixture
def dc(gw):
    c = DirectConnectClient(*gw.dc_address, timeout=2, retries=0)
    c.transport.connect()
    c.login()
    yield c
    c.transport.close()


def s16(v):
    return struct.unpack(">h", struct.pack(">H", v))[0]


def test_login_manifest(dc, gw):
    fw = dc.firmware()
    assert dc.equip_no == gw.serial
    assert fw.get("IBG_VER") == "V12R02B60D00_260924" and fw.get("protocolVer") == "V1.12.02"   # V12 default
    assert "SUNSPEC_FW" not in dc.login()       # #74: no source for it; the raw manifest, unfiltered


def test_power_flow_matches_modbus(dc, gw, clock):
    gw.site.solar_override_w, gw.site.load_override_w = 0, 1500
    pf = dc.power_flow()
    m = RawClient(*gw.address)
    assert s16(m.read(80, 1)[0]) == round(pf["p_uti"])          # 701.W
    assert s16(m.read(1048, 1)[0]) == round(pf["p_fhp"])        # 714.DCW, same sign
    assert m.read(1037, 1)[0] == round(pf["soc"] * 10)          # 713.SoC
    m.close()
    assert pf["p_load"] == 1500 and abs(pf["p_load"] - (pf["p_sun"] + pf["p_uti"] + pf["p_fhp"])) < 0.2
    assert len(pf["fhpSn"]) == 2 and pf["infi_status"] == [0, 0] and pf["main_sw"][0] == 1
    assert "devNum" not in pf and pf["reason"] == 0          # as on the reference aGate


def test_daily_totals_and_energy_history(dc, gw, clock):
    gw.site.solar_override_w, gw.site.load_override_w = 0, 2000
    gw.site.soc = 20                                  # at reserve: grid carries the load
    clock.advance(3600)
    pf = dc.power_flow()
    assert abs(pf["kwh_uti_in"] - 2.0) < 0.01 and abs(pf["kwh_load"] - 2.0) < 0.01
    hist = dc.energy_history(TODAY)
    assert hist["pointMax"] == 96 and hist["pointId"] == len(hist["p_uti"]) >= 52
    assert hist["p_uti"][-1] == 2000
    assert hist["p_load"] == hist["p_fhp"]            # faithful firmware bug
    assert abs(hist["kwh_uti_in"] - 2.0) < 0.05


def test_energy_history_needs_date(dc):
    assert dc.call(1303, {"opt": 0})["result"] == 1


def test_energy_history_key_set_is_the_same_on_every_date(dc, clock):
    """#134: a date with no data had no sectionEngy, so the key set varied by date."""
    clock.advance(3600)
    today = dc.energy_history(TODAY)
    for date in ("2025-12-31", "2020-01-01"):              # before the site started: no data
        other = dc.energy_history(date)
        assert set(other) == set(today) and other["sectionEngy"] == [0] * 7, date


def test_mode_set_reaches_modbus(dc, gw):
    modes = dc.mode_list()
    assert {m["scheduling_type"] for m in modes["list"]} == {1, 2, 3}
    dc.set_mode("tou")
    assert gw.site.native_mode == 3
    m = RawClient(*gw.address)
    assert m.read(15507, 1) == [3]                    # same site behind :502
    m.close()


def test_offgrid_write_islands_site(dc, gw):
    assert dc.offgrid()["offgridState"] == 0
    dc.set_offgrid(True, soc=10)
    og = dc.offgrid()
    assert og["offgridSet"] == 1 and og["offgridState"] == 1 and og["offgridSoc"] == 10
    assert dc.relay_status()["gridRelayOpen"] == 1
    m = RawClient(*gw.address)
    assert m.read(75, 1) == [0]                        # 701.ConnSt
    m.close()
    dc.set_offgrid(False)
    assert dc.offgrid()["offgridState"] == 0


def test_outage_visible_on_dc(dc, gw):
    gw.site.set_grid(available=False)
    assert dc.relay_status()["gridRelayOpen"] == 1
    og = dc.offgrid()
    assert og["offgridSet"] == 0 and og["offgridState"] == 1   # real outage, not requested
    assert dc.power_flow()["run_status"] in (5, 6, 7)


def test_der_comms_csip_registration(dc, gw, clock):
    d = dc.der_comms()
    assert d["sunsMdEn"] == 1 and d["enable"] == 0 and d["status2030_5"] == 0
    dc.set_der_comms(sep2=True)
    assert dc.der_comms()["status2030_5"] == 1          # registering
    clock.advance(6)
    d = dc.der_comms()
    assert d["status2030_5"] == 2 and len(d["lfdi"]) == 40 and d["sfdi"]


def test_der_comms_partial_write_refused(dc):
    assert dc.call(1205, {"opt": 1, "sunsMdEn": 0})["reason"] == -2


def test_der_comms_sunsmden_closes_modbus(dc, gw):
    gw.modbus_apply_delay_s = 0.2
    host, port = gw.address
    dc.set_der_comms(sunspec_modbus=False)
    time.sleep(0.5)
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection((host, port), timeout=1)
    dc.set_der_comms(sunspec_modbus=True)
    time.sleep(0.5)
    socket.create_connection((host, port), timeout=1).close()


def test_solar_pv_and_export_limit(clock):
    g = make_gateway(clock, seed=7, dc_port=0,
                     site={"grid_code": "ausgrid", "pv_inputs": {"proximal": 1.0},
                           "cap_forced_solar": False})
    with g:
        c = DirectConnectClient(*g.dc_address, timeout=2, retries=0)
        c.transport.connect()
        c.login()
        g.site.solar_override_w = 4200
        pv = c.solar_pv()
        assert pv["solarPower"] == 42000 and pv["grid_feed_max"] == -1   # declared, not applied
        g.site.set_export_limit_enforced(True)
        assert c.solar_pv()["grid_feed_max"] == 10.0
        c.transport.close()


def test_per_unit_reads(dc, gw):
    assert dc.device_ids() == [1, 2]
    cells = dc.battery_cells(2)
    assert cells["id"] == 2 and len(cells["batVolt"]) == 16
    assert dc.device_states(1)["bmsState"] in (5, 6, 7)
    assert dc.device_firmware(2)["fhp_sn"] != dc.device_firmware(1)["fhp_sn"]
    assert dc.call(1705, {"opt": 0})["result"] == 1                    # missing id


def test_ibg_run_status_relays(dc, gw):
    gw.site.set_pv_relay("proximal", False)
    rs = dc.ibg_run_status()
    assert rs["solarRelayStatus"] == 0 and rs["mainRelay1Status"] == 1
    assert rs["dspSettingFreq"] == 50 and rs["gridLineVol"] == 2418


def test_wrong_opt_gets_no_reply_and_unknown_is_refused(gw):
    t = DirectConnectClient(*gw.dc_address, timeout=0.5, retries=0)
    t.transport.connect()
    t.login()
    with pytest.raises(TimeoutError):
        t.call(1725, {"opt": 0})
    assert t.call(1501)["reason"] == 4                 # in-band, unimplemented
    t.transport.close()


def test_out_of_band_code_drops_connection(gw):
    s = socket.create_connection(gw.dc_address, timeout=1)
    s.sendall(protocol.encode_frame(211, gw.serial, {"opt": 0}))
    assert s.recv(64) == b""
    s.close()


def test_reboot_drops_both_ports_then_returns(clock):
    g = make_gateway(clock, seed=7, dc_port=0)
    g.reboot_s = 0.3
    with g:
        c = DirectConnectClient(*g.dc_address, timeout=1, retries=0)
        c.transport.connect()
        c.login()
        assert c.reboot().get("dropped")              # 1721: the aGate drops the socket
        time.sleep(0.15)
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(g.address, timeout=1)
        time.sleep(0.4)
        socket.create_connection(g.address, timeout=1).close()
        socket.create_connection(g.dc_address, timeout=1).close()
        c.transport.close()


def test_fleet_yaml_directconnect_shorthand():
    from franklinwh_emulator.gateway import GatewayConfig
    cfg = GatewayConfig.from_dict({"id": "x", "modbus": {"port": 5099},
                                   "directconnect": {"port": 9099}})
    assert cfg.port == 5099 and cfg.dc_port == 9099


def test_ctl_dc_fault_verb():
    from franklinwh_emulator.cli import _ctl_request
    assert _ctl_request("dc-fault", ["latency_s=0.5"]) == ("POST", "dc-faults", {"latency_s": 0.5})


def test_payload_shapes_match_reference_capture(gw):
    """Key sets captured from the reference aGate (V12R02B30D06, 2026-10-07) via the Local
    Bridge. Values are site-specific; the keys and container types are what clients parse."""
    from franklinwh_emulator.dc_payloads import DcPayloads
    p = DcPayloads(gw)
    real = {
        1301: {"opt", "result", "reason", "mode", "name", "electricity_type", "run_status",
               "slaver_stat", "elecnet_state", "infi_status", "p_uti", "p_sun", "p_gen", "p_fhp",
               "p_load", "kwh_uti_in", "kwh_uti_out", "kwh_sun", "kwh_gen", "kwh_fhp_di",
               "kwh_fhp_chg", "kwh_load", "soc", "t_amb", "main_sw", "pro_load", "doStatus",
               "diStatus", "cd_alm", "ent", "sharp", "peak", "flat", "valley", "fhpSn", "fhpSoc",
               "fhpPower", "genStat", "offgridreason"},
        1707: {"opt", "result", "reason", "ibgRunStatus", "name", "electricity_type",
               "dspRunstatus", "gridVol1", "gridVol2", "gridCurr1", "gridCurr2", "loadCurr1",
               "loadCurr2", "gridFreq", "dspSettingFreq", "mainRelay1Status", "mainRelay2Status",
               "solarRelayStatus", "smartRelay1Status", "smartRelay2Status", "smartRelay3Status",
               "loadRelay1Stat", "loadRelay2Stat", "gridLineVol", "gridRelay2", "pvRelay2",
               "blackStartRelay", "genVoltage", "isTripPhsPvEn", "isTripPhsGridEn"},
        1201: {"opt", "result", "reason", "time", "timezone", "timezoneStr", "DST", "laitude",
               "longitude", "postcode"},
        1831: {"opt", "result", "reason", "devNum", "devMap"},
    }
    for cmd, keys in real.items():
        assert set(p.handlers[cmd]({"opt": 0})) == keys, cmd
    ml = p.handlers[1725]({"opt": 1})
    assert set(ml) == {"current_id", "result", "opt", "list"}          # no "reason"
    assert {"opt", "result", "reason"} <= set(p.handlers[1903]({"opt": 0}))
    # Single-phase aGate X: both internal legs read half the line voltage.
    run = p.handlers[1707]({"opt": 0})
    assert run["gridVol1"] == run["gridVol2"] and abs(run["gridLineVol"] / 10 - 2 * run["gridVol1"]) < 0.2
    cells = p.handlers[1705]({"opt": 0, "id": 1})
    assert all(isinstance(v, int) and 3000 <= v <= 3400 for v in cells["batVolt"])   # LFP, mV


def test_unconfirmed_cmdtypes_read_as_captured_and_refuse_writes(gw):
    """1207/1209/1821/1823/1825 as read on the reference aGate (franklinwh-direct-connect-api#2).
    1821–1825 look like aPower power control: writes must never appear to work."""
    from franklinwh_emulator.dc_payloads import DcPayloads
    p = DcPayloads(gw)
    assert p.handlers[1207]({"opt": 0}) == {"opt": 0, "result": 0, "reason": 0, "enable": 111, "mode": 222}
    assert p.handlers[1823]({"opt": 0, "id": 2})["powerOff"] == 0          # id ignored on read
    for cmd in (1209, 1823, 1825):                                        # 1821 is Debug Mode (#101)
        assert p.handlers[cmd]({"opt": 1, "powerOff": 1})["reason"] == 4


def test_1207_write_is_stored_without_unlocking_writes(gw):
    """Reference aGate 2026-10-08: 1207 opt:1 echoes and persists; 15508 writes stay discarded."""
    from franklinwh_emulator.dc_payloads import DcPayloads
    p = DcPayloads(gw)
    assert p.handlers[1207]({"opt": 1, "enable": 1, "mode": 2}) == \
        {"opt": 1, "result": 0, "reason": 0, "enable": 1, "mode": 2}
    assert p.handlers[1207]({"opt": 0}) == {"opt": 0, "result": 0, "reason": 0, "enable": 1, "mode": 2}
    p.handlers[1207]({"opt": 1, "enable": 111, "mode": 222})
    assert p.handlers[1207]({"opt": 0})["mode"] == 222
    assert DcPayloads(gw).handlers[1207]({"opt": 0})["enable"] == 111      # per instance


def test_1829_device_alarms_counts_active_alarms(gw):
    from franklinwh_emulator.dc_payloads import DcPayloads
    p = DcPayloads(gw)
    assert p.handlers[1829]({"opt": 0}) == {"opt": 0, "result": 0, "reason": 0, "num": 0}
    gw.site.force_alarm("OVER_TEMP")
    assert p.handlers[1829]({"opt": 0})["num"] >= 1


# -- factory default (#40): Direct Connect always on, Modbus TCP off until 1205 enables it ------
def test_factory_agate_has_dc_and_no_modbus_until_1205(clock):
    g = make_gateway(clock, seed=7, setup="factory", port=0)       # dc_port not given
    with g:
        assert g.dc_address is not None and g.dc_address[1] > 0    # listener by default
        assert g.modbus_enabled is False
        assert g.server._server is None                             # :502 not listening
        g.modbus_apply_delay_s = 0.2
        c = DirectConnectClient(*g.dc_address, timeout=2, retries=0)
        c.transport.connect()
        c.login()
        c.set_der_comms(sunspec_modbus=True)                        # the real 1205 write
        time.sleep(0.6)
        host, port = g.address
        assert port > 0
        socket.create_connection((host, port), timeout=1).close()   # now serving
        c.transport.close()


def test_default_setup_is_factory():
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    g = Gateway(GatewayConfig(seed=7))
    assert g.config.setup == "factory" and g.modbus_enabled is False and g.dc is not None


@pytest.mark.parametrize("cfg,modbus,dc", [
    ({"setup": "in-service"}, True, True),
    ({"setup": "factory", "modbus_enabled": True}, True, True),
    ({"setup": "in-service", "modbus_enabled": False}, False, True),
    ({"profile": "spec"}, True, False),                    # not an aGate: as before
    ({"profile": "spec", "dc_port": 0}, True, True),
])
def test_setup_and_overrides(cfg, modbus, dc):
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    g = Gateway(GatewayConfig.from_dict({"id": "x", "seed": 7, **cfg}))
    assert g.modbus_enabled is modbus and (g.dc is not None) is dc


def test_unknown_setup_is_refused():
    from franklinwh_emulator.gateway import GatewayConfig
    with pytest.raises(ValueError, match="setup must be one of"):
        GatewayConfig.from_dict({"id": "x", "setup": "commissioned"})


def test_debug_mode_is_1821_and_stops_the_battery(gw, dc):
    """#101: Debug Mode is DC 1821 enable. On the reference aGate (2026-10-23) the battery
    stopped, the house went to the grid and 1301 run_status went 2 -> 0; off resumed it."""
    from franklinwh_emulator.control import apply, gateway_state
    gw.site.solar_override_w, gw.site.load_override_w = 0, 2000
    assert dc.call(1821)["enable"] == 0 and abs(dc.power_flow()["p_fhp"]) > 100
    apply(gw, "debug-mode", {"on": True})
    pf = dc.power_flow()
    assert dc.call(1821)["enable"] == 1 and gateway_state(gw)["debug_mode"] is True
    assert pf["p_fhp"] == 0 and pf["run_status"] == 0 and pf["p_uti"] > 1500     # house on the grid
    r = dc.call(1821, {"opt": 1, "enable": 0})                                    # a 1821 write turns it off
    assert (r["result"], r["enable"]) == (0, 0) and gateway_state(gw)["debug_mode"] is False
    assert abs(dc.power_flow()["p_fhp"]) > 100
    assert dc.call(1821, {"opt": 1, "enable": 1})["enable"] == 1 and dc.power_flow()["p_fhp"] == 0
    assert dc.call(1821, {"opt": 1})["reason"] == -2                              # no enable: refused



# The vpp action writes WSetPct (watts converted on the 5 kW rating), so every case is WSetPct.
@pytest.mark.parametrize("body,direction,via", [({"watts": 3000}, "charge", "WSetPct"),
                                                ({"watts": -2000}, "discharge", "WSetPct"),
                                                ({"pct_hw": 40}, "discharge", "WSetPct"),
                                                ({"watts": 0}, "standby", "WSetPct")])
def test_force_reports_programme_9_and_empty_name(gw, dc, body, direction, via):
    """#91: during an M704 force, 1707 ibgRunStatus reads 9 and 1301 name is empty (measured
    by the DC bridge on the reference aGate, 2026-10-12); 1301 run_status follows the battery.
    State calls it FORCE with its direction, not VPP."""
    from franklinwh_emulator.control import apply, gateway_state
    before_rs, before_pf = dc.ibg_run_status(), dc.power_flow()
    assert before_rs["ibgRunStatus"] != 9 and before_pf["name"] and gateway_state(gw)["force"] is None
    apply(gw, "vpp", body)
    rs, pf = dc.ibg_run_status(), dc.power_flow()
    assert rs["ibgRunStatus"] == 9 and pf["name"] == "" and pf["run_status"] != 8
    assert pf["mode"] == before_pf["mode"]                          # 1301 mode: no evidence, unchanged
    f = gateway_state(gw)["force"]
    assert (f["direction"], f["via"]) == (direction, via)
    apply(gw, "release", {})
    assert dc.ibg_run_status()["ibgRunStatus"] == before_rs["ibgRunStatus"]
    assert dc.power_flow()["name"] == before_pf["name"] and gateway_state(gw)["force"] is None


def test_no_deprecated_library_name():
    """#133: the client library removes the `franklinwh_local` alias in 0.6.0."""
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src"
    old = re.compile(r"^\s*(from|import)\s+franklinwh_local\b", re.M)
    hits = [str(p) for p in src.rglob("*.py") if old.search(p.read_text())]
    assert hits == []


def test_tou_schedule_1407(dc, gw):
    """#132: 1407 serves the site's schedule, decodable by the client library."""
    from franklinwh_direct_connect_api import energy
    r = dc.tou_schedule()
    assert r["result"] == 0 and r["workdayLv"] == 3 and r["weekendFlag"] == r["workdayFlag"]
    blocks = energy.decode_tou(r, "workday")
    assert [(b.start, b.end, b.wave_name) for b in blocks] == [
        ("00:00", "17:00", "Off-Peak"), ("17:00", "21:00", "On-Peak"), ("21:00", "24:00", "Off-Peak")]
    gw.site.set_tou_schedule([{"name": "Peak", "startHourTime": "16:00", "endHourTime": "20:00",
                      "waveType": 2, "dispatchId": 6}])   # gaps either side read as off-peak
    r = dc.tou_schedule()
    assert (r["workdayTime"], r["workdayFlag"]) == (["00:00", "16:00", "20:00"], [0, 2, 0])
    assert dc.call(1407, {"opt": 1})["reason"] == 4     # writes are not emulated


def test_replies_echo_the_request_opt(dc, gw):
    """#135: an app capture of the reference aGate: every reply echoes its request's opt."""
    on = {f"Sw{n}Mode": 1 for n in (1, 2, 3)}
    assert dc.call(1409, {"opt": 1, **on})["opt"] == 1
    assert dc.call(1723, {"opt": 1, "offgridSet": 0, "offgridSoc": 5})["opt"] == 1
    assert dc.call(1821, {"opt": 1, "enable": 0})["opt"] == 1
    assert dc.call(1409, {"opt": 0})["opt"] == 0 and dc.call(1301, {"opt": 0})["opt"] == 0
    assert dc.call(1407, {"opt": 1})["opt"] == 1            # refusals too (reason 4)


# -- dc-faults act on Direct Connect, or are refused (#170) ---------------------------------------
def _dc_login(gw, timeout=1.0):
    c = DirectConnectClient(*gw.dc_address, timeout=timeout, retries=0)
    c.transport.connect()
    try:
        c.login()
        return c, True
    except Exception:
        return c, False


def test_dc_session_held_refuses_the_client(gw):
    gw.dc_faults.session_held = True
    c, ok = _dc_login(gw)
    c.transport.close()
    assert not ok and gw.dc.clients.snapshot()["refused_1h"] == 1
    gw.dc_faults.session_held = False
    c, ok = _dc_login(gw)
    c.transport.close()
    assert ok


def test_dc_reset_after_closes_the_connection(gw):
    c, ok = _dc_login(gw)
    assert ok
    gw.dc_faults.reset_after = 1
    assert c.call(1301, {"opt": 0})["result"] == 0     # one more request is answered
    with pytest.raises(Exception):
        c.call(1301, {"opt": 0})                        # then the connection is closed
    c.transport.close()
    assert gw.dc_faults.reset_after is None


def test_dc_slow_replies(gw):
    import time as _t
    c, ok = _dc_login(gw, timeout=2)
    assert ok
    gw.dc_faults.slow_p, gw.dc_faults.slow_ms = 1.0, 400
    t0 = _t.monotonic()
    c.call(1301, {"opt": 0})
    assert _t.monotonic() - t0 >= 0.35
    gw.dc_faults.slow_p = 0.0
    c.transport.close()


def test_dc_range_fault_is_refused(gw):
    from franklinwh_emulator.control import ApiError, apply
    with pytest.raises(ApiError, match=r"unknown keys \['range_fault'\]"):
        apply(gw, "dc-faults", {"range_fault": {"start": 0, "end": 10, "mode": "silent"}})
    assert gw.dc_faults.range_fault is None
    from franklinwh_emulator.schema import ACTIONS
    assert "range_fault" not in ACTIONS["dc-faults"]["fields"] and "range_fault" in ACTIONS["faults"]["fields"]


@pytest.mark.parametrize("grid_code, ac_type, phases, line_v", [
    ("as4777-au-a", 0, None, 241.8), ("as4777-au-a", 1, None, 483.6),
    ("as4777-au-a", 2, None, 418.8), ("as4777-au-a", 2, ["L1", "L2"], 418.8),
    ("ieee1547-cat2", 1, None, 240.0), ("ieee1547-cat2", 2, None, 207.8),
])
def test_grid_line_vol_is_line_to_line(clock, grid_code, ac_type, phases, line_v):
    """#151: three phase gives √3 × line-to-neutral, as the key's "line V" says and as split
    phase already did; single and split phase unchanged. Currents are unchanged."""
    site = {"ac_type": ac_type, "grid_code": grid_code}
    if phases:
        site["phases"] = phases
    g = make_gateway(clock, site=site)
    g.site.solar_override_w, g.site.load_override_w = 0, 3000
    s = g.site.step()
    rs = g.dc.payloads.ibg_run_status({})
    assert abs(rs["gridLineVol"] / 10 - line_v) <= 0.15, rs["gridLineVol"]
    legs_v = [p.v_ln for p in s.phases.values()]
    if ac_type == 2:
        assert rs["gridVol1"] == round(legs_v[0], 1)                      # legs stay L-N
        assert rs["gridCurr1"] == round(abs(s.grid_w) / s.v_ln, 1)      # as before #151


def test_1405_full_block_write_applies_bbbackupsoc(gw):
    """#171: the DC library's catalogue (proven 2026-09-14): a full-block write applies
    BBBackupSoc; the eight min/max fields are inert and stay 0; partial writes are refused."""
    from franklinwh_emulator.dc_payloads import DcPayloads
    p = DcPayloads(gw)
    block = {k: v for k, v in p.handlers[1405]({"opt": 0}).items() if k not in ("opt", "result", "reason")}
    assert block["BBBackupSoc"] == 20
    w = p.handlers[1405]({"opt": 1, **{k: 30 for k in block}, "BBBackupSoc": 55})
    assert (w["result"], w["reason"]) == (0, 0)
    after = p.handlers[1405]({"opt": 0})
    assert after["BBBackupSoc"] == 55
    assert all(after[k] == 0 for k in block if k != "BBBackupSoc")
    assert p.handlers[1405]({"opt": 1, "selfMinSoc": 30})["reason"] == -2
    assert p.handlers[1405]({"opt": 0})["BBBackupSoc"] == 55


def test_error_rules_as_documented(gw):
    """#152: odd unimplemented requests get reason 4; even (reply) codes get no reply; odd codes
    outside 1101-1997 close; a read missing id/date keeps its keys, empty."""
    from franklinwh_emulator.dc_payloads import DcPayloads

    def send(code):
        s = socket.create_connection(gw.dc_address, timeout=0.5)
        s.sendall(protocol.encode_frame(code, gw.serial, {"opt": 0}))
        try:
            data = s.recv(4096)
            return "closed" if data == b"" else "reply"
        except TimeoutError:
            return "no reply"
        finally:
            s.close()
    assert {c: send(c) for c in (1099, 1100, 1993, 1996, 1997, 1998, 1999, 2000)} == {
        1099: "closed", 1100: "no reply", 1993: "reply", 1996: "no reply", 1997: "reply",
        1998: "no reply", 1999: "closed", 2000: "no reply"}
    p = DcPayloads(gw)
    for cmd, full in ((1303, {"opt": 0, "date": "2026-10-01"}), (1833, {"opt": 0, "id": 1})):
        bare = p.handlers[cmd]({"opt": 0})
        assert (bare["result"], bare["reason"]) == (1, -1)
        assert set(p.handlers[cmd](full)) - set(bare) <= {"date", "dayType", "id"}, cmd   # keys kept, empty
    text = (Path(__file__).resolve().parent.parent / "docs" / "DIRECT_CONNECT.md").read_text()
    assert "odd codes, 1101–1997" in text and "Even codes are reply codes" in text
    assert "the empty key set" not in text and "only on split/three phase" not in text


def test_smart_circuits_per_model(clock):
    """#189: AU aGate X-01-AU has smart circuits 1-2 (datasheet; its 1412 meters SW1/SW2 only),
    yet 1410 sends a full inert Sw3 slot (Mode 0, SocLowSet 20), as the reference capture did.
    North American models keep 1-3, with circuit 3 metered as the car switch."""
    from franklinwh_emulator.dc_payloads import DcPayloads
    au = make_gateway(clock, seed=7)
    us = make_gateway(clock, seed=8, site={"ac_type": 1})
    assert sorted(au.site.circuits) == [1, 2] and sorted(us.site.circuits) == [1, 2, 3]
    with pytest.raises(ValueError, match="smart circuits 1–2"):
        au.site.set_circuit(3, True)
    with pytest.raises(ValueError, match="aircon: circuit 3: aGate X-01-AU has smart circuits 1–2"):
        make_gateway(clock, seed=9, site={"loads": [{"name": "aircon", "type": "base", "w": 1, "circuit": 3}]})
    p = DcPayloads(au)
    cfg = p.handlers[1409]({"opt": 0})
    assert {k for k in cfg if k.startswith("Sw3")} == {f"Sw3{k}" for k in (
        "Name", "Mode", "AtuoEn", "Freq", "MsgType", "ProLoad", "SocLowSet", "Time", "TimeEn", "TimeSet")}
    assert (cfg["Sw3Mode"], cfg["Sw3SocLowSet"], cfg["Sw3ProLoad"]) == (0, 20, 0)
    assert not any(k.startswith(("SW3", "Sw3")) for k in p.handlers[1411]({"opt": 0}))
    au.site.circuits[2].on = True
    with au.site.lock:
        au.site.loads = None
    m = p.handlers[1411]({"opt": 0})
    assert m["Sw2Volt"] > 0 and m["CarSWPower"] == 0          # SW2 is metered; no car switch here


def test_1701_install_profile_from_the_site(clock):
    """1701 → 1702 (#123): the catalogue's key set, the site's values, writes stored only with
    Debug Mode on, never enforced."""
    gw = make_gateway(clock, site={"service": {"main_breaker_a": 63}})
    p = gw.dc.payloads
    r = p.handlers[1701]({"opt": 0})
    assert (r["electricSupply"], r["airSwitchCur"], r["gridExportEnable"], r["isPcsDischgEn"]) == (63, 100, 1, 1)
    assert r["fhpRatePower"] == r["genRatePower"] == r["solarInstallState"] == 0
    limits = {"kwRatePower": 2000, "gridSoftLimit": 2000, "gridHardLimit": -1, "gridImportLimit": -1}
    assert p.handlers[1701]({"opt": 1, "kwRatePower": 2000})["reason"] == -2      # full block only
    assert p.handlers[1701]({"opt": 1, **limits})["result"] == 0
    assert p.handlers[1701]({"opt": 0})["kwRatePower"] == -1                       # Debug Mode off
    gw.set_debug_mode(True)
    p.handlers[1701]({"opt": 1, **limits})
    r = p.handlers[1701]({"opt": 0})
    assert (r["kwRatePower"], r["gridSoftLimit"]) == (2000, 2000)
    us = make_gateway(clock, site={"ac_type": 1, "utility": {"export": "prohibited"}})
    r = us.dc.payloads.handlers[1701]({"opt": 0})
    assert (r["gridExportEnable"], r["isPcsDischgEn"], r["airSwitchCur"], r["electricSupply"]) == (0, 0, 200, 63)


def test_1702_key_set_follows_the_firmware(clock):
    """#228: V12R02B30D06 sends no gridImportLimit (fwh-local capture, 4 replies); V12R02B60D00
    adds it; ratedGridHz reads 1."""
    measured = {"airSwitchCur", "electricSupply", "electricSys", "fhpRatePower", "genRatePower",
                "gridExportEnable", "gridHardLimit", "gridPhaseConSet", "gridPhaseSeqSet",
                "gridSoftLimit", "isPcsDischgEn", "isThreePhaseInstall", "kwRatePower",
                "ratedGridHz", "ratedGridVolt", "solarInstallState", "solarInvManu",
                "solarInvRatePower", "opt", "result", "reason"}
    old = make_gateway(clock, profile="agate-v10r01b04d00").dc.payloads
    r = old.handlers[1701]({"opt": 0})
    assert set(r) == measured and r["ratedGridHz"] == 1
    assert old.handlers[1701]({"opt": 1, "kwRatePower": 1, "gridSoftLimit": 1,
                               "gridHardLimit": -1})["result"] == 0     # full block on this firmware
    new = make_gateway(clock, profile="agate-v12r02b60d00-candidate").dc.payloads
    assert set(new.handlers[1701]({"opt": 0})) == measured | {"gridImportLimit"}
