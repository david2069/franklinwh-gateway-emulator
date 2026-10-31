"""Direct Connect frontend: the real franklinwh_local client against the emulator, and
cross-protocol consistency with the Modbus port (one Site behind both)."""

import socket
import struct
import time

import pytest

pytest.importorskip("franklinwh_local")
from franklinwh_local import protocol  # noqa: E402
from franklinwh_local.client import LocalClient  # noqa: E402

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
    c = LocalClient(*gw.dc_address, timeout=2, retries=0)
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
        c = LocalClient(*g.dc_address, timeout=2, retries=0)
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
    t = LocalClient(*gw.dc_address, timeout=0.5, retries=0)
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
        c = LocalClient(*g.dc_address, timeout=1, retries=0)
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
        c = LocalClient(*g.dc_address, timeout=2, retries=0)
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
