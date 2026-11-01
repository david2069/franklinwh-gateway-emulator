"""Grid events, grid codes, islanding, PV relays and alarms — in the site model and on the wire."""

import json
import struct
import urllib.request

import pytest

from conftest import RawClient, make_gateway
from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.site import (ALARMS, INV_FAULT, INV_STANDBY, RUN_OFFGRID_DISCHARGING,
                                      Site, SiteConfig)

NOON = 1_767_225_600 + 12 * 3600
CONNST, INVST, ST, ALRM, DERMODE = 75, 74, 73, 76, 78
ALARM_RESET = 1094


def bit(name):
    return 1 << ALARMS[name]


def site(**kw):
    clock = ManualClock(NOON)
    kw.setdefault("tz_offset_hours", 0)
    kw.setdefault("initial_soc", 60)
    kw.setdefault("cap_forced_solar", False)     # PV is a test input here (#56)
    s = Site(SiteConfig(seed=7, **kw), clock)
    s.solar_override_w, s.load_override_w = 0, 2000
    return s, clock


def balanced(s):
    return abs(s.load_w - (s.solar_w + s.grid_w + s.battery_w)) < 1e-6


def after(s, clock, seconds):
    clock.advance(seconds)
    return s.step()


# -- black-out ----------------------------------------------------------------------
def test_outage_islands_immediately_and_battery_carries_house():
    s, _ = site()
    s.set_grid(available=False)
    snap = s.step()
    assert not snap.grid_connected and snap.trip_reason == "outage"
    assert snap.grid_w == 0 and snap.battery_w == 2000 and balanced(snap)
    assert snap.run_status == RUN_OFFGRID_DISCHARGING
    assert snap.alarms & bit("GRID_DISCONNECT")


def test_outage_ignores_vpp_and_reserve_until_offgrid_floor():
    s, clock = site(self_reserve=50, initial_soc=10)
    s.update_remote(enabled=True, pct_hw=-100.0)       # VPP charge request
    s.set_grid(available=False)
    assert after(s, clock, 60).battery_w == 2000      # forms the grid instead
    for _ in range(20):
        snap = after(s, clock, 900)
    assert abs(snap.soc - 5.0) < 1e-6                 # off-grid floor, not the 50 % reserve
    assert snap.inverter_state == INV_STANDBY and snap.load_w == 0
    assert snap.load_shed_w == 2000 and snap.v_ln == 0


def test_islanded_surplus_curtails_pv():
    s, clock = site(initial_soc=99.99)
    s.solar_override_w, s.load_override_w = 6000, 1000
    s.set_grid(available=False)
    snap = after(s, clock, 10)
    assert snap.grid_w == 0 and balanced(snap)
    assert snap.solar_clipped_w > 4000 and snap.solar_w < 2000


def test_restore_recloses_after_enter_service_delay():
    s, clock = site()
    s.set_grid(available=False)
    clock.advance(30)
    s.restore_grid()
    assert not after(s, clock, 59).grid_connected
    snap = after(s, clock, 2)
    assert snap.grid_connected and snap.trip_reason is None
    assert not snap.alarms & bit("GRID_DISCONNECT")


# -- AS/NZS 4777.2 Australia A ------------------------------------------------------------
def test_au_is_default_and_starts_at_scanned_voltage():
    s, _ = site()
    snap = s.step()
    assert s.grid.name == "as4777-au-a"
    assert abs(snap.v_ln - 241.8) < 1e-6 and snap.hz == 49.99


def test_au_mild_brownout_stays_connected():
    s, clock = site()
    s.set_grid(voltage=207)
    snap = after(s, clock, 60)
    assert snap.grid_connected and abs(snap.v_ln - 207) < 1e-6 and not snap.alarms


def test_au_undervoltage_stages():
    s, clock = site()
    s.set_grid(voltage=175)                           # V< 180 V for 10 s
    assert s.step().alarms & bit("AC_UNDER_VOLT")
    assert after(s, clock, 9.5).grid_connected
    snap = after(s, clock, 0.6)
    assert not snap.grid_connected and snap.trip_reason == "under-voltage"
    assert snap.trip_stage == "V<"
    s, clock = site()
    s.set_grid(voltage=60)                            # V<< 70 V for 1 s
    snap = after(s, clock, 1.05)
    assert not snap.grid_connected and snap.trip_stage == "V<<"


def test_au_short_sag_rides_through():
    s, clock = site()
    s.set_grid(voltage=175)
    clock.advance(5)
    s.restore_grid()
    assert after(s, clock, 30).grid_connected


def test_au_overvoltage_stages():
    s, clock = site()
    s.set_grid(voltage=268)                           # V> 265 V for 1 s
    assert after(s, clock, 0.9).grid_connected
    assert after(s, clock, 0.2).trip_stage == "V>"
    s, clock = site()
    s.set_grid(voltage=280)                           # V>> 275 V, 0.2 s
    assert after(s, clock, 0.25).trip_stage == "V>>"


def test_au_sustained_overvoltage_ten_minute_average():
    s, clock = site()
    s.set_grid(voltage=260)                           # under V> but above 258 V average
    assert after(s, clock, 500).grid_connected
    snap = after(s, clock, 60)
    assert not snap.grid_connected and snap.trip_stage == "sustained"


def test_au_frequency_trips():
    s, clock = site()
    s.set_grid(hz=52.5)
    snap = after(s, clock, 0.25)
    assert snap.trip_reason == "over-frequency" and snap.alarms & bit("OVER_FREQUENCY")
    s, clock = site()
    s.set_grid(hz=46.5)
    assert after(s, clock, 0.9).grid_connected
    assert after(s, clock, 0.2).trip_reason == "under-frequency"


def test_au_no_reconnect_until_frequency_in_es_window():
    s, clock = site()
    s.set_grid(hz=52.5)
    clock.advance(1)
    s.set_grid(hz=50.3)                               # inside trip window, outside ES (50.15)
    assert not after(s, clock, 120).grid_connected
    s.set_grid(hz=50.0)
    assert after(s, clock, 61).grid_connected


def test_au_over_frequency_curtails_pv_and_storage():
    s, clock = site(initial_soc=100)
    s.solar_override_w, s.load_override_w = 5000, 1000
    assert after(s, clock, 1).grid_w == -4000
    s.set_grid(hz=51.125)                             # PV halfway 50.25 → 52
    snap = after(s, clock, 60)                        # settled (AS4777: within 10 s)
    assert snap.grid_connected and snap.solar_w == pytest.approx(2500, abs=1)
    assert snap.grid_w == pytest.approx(-1500, abs=1)
    assert snap.throttle_pct == 50 and snap.inverter_state == 4


def test_au_over_frequency_storage_transition():
    s, clock = site(initial_soc=50)
    s.solar_override_w, s.load_override_w = 0, 4000
    s.set_grid(hz=50.5)                               # discharge cap halfway 50.25 → 50.75
    assert s.step().battery_w == 2500
    s.set_grid(hz=50.75)                              # f_transition: no discharge
    assert s.step().battery_w == 0
    s.set_grid(hz=51.375)                             # halfway 50.75 → 52: charge ≥ 50 %
    assert s.step().battery_w == -2500


def test_au_under_frequency_storage():
    s, clock = site(initial_soc=60)
    s.solar_override_w, s.load_override_w = 0, 1000
    s.update_remote(enabled=True, pct_hw=-100.0)      # VPP: charge at 5 kW
    s.set_grid(hz=49.375)                             # charge cap halfway 49.75 → 49
    assert s.step().battery_w == -2500
    s.set_grid(hz=49.0)                               # f_stop_ch: no charging
    assert s.step().battery_w == 0
    s.set_grid(hz=48.0)                               # f_Pmax: discharge at rated
    snap = s.step()
    assert snap.battery_w == 5000 and snap.grid_w == -4000


def test_au_volt_watt_generation_and_charging():
    s, clock = site(initial_soc=60)
    s.solar_override_w, s.load_override_w = 4000, 0
    s.set_grid(voltage=256.5)                         # halfway 253 → 260 V: 60 %
    assert abs(s.step().solar_w - 2400) < 1e-6
    s2, _ = site(initial_soc=60)
    s2.solar_override_w, s2.load_override_w = 0, 0
    s2.update_remote(enabled=True, pct_hw=-100.0)
    s2.set_grid(voltage=211)                          # halfway 207 → 215 V: charge 60 %
    assert abs(s2.step().battery_w + 3000) < 1e-6


def test_au_volt_var():
    s, _ = site()
    snap = s.step()                                   # 241.8 V: absorbing 6 % of 5800 VA
    assert abs(snap.var - (-0.06 * 5800)) < 1e-6
    s.set_grid(voltage=207)
    assert abs(s.step().var - 0.44 * 5800) < 1e-6     # supplying
    s.set_grid(voltage=230)
    assert s.step().var == 0


def test_ausgrid_export_limit_declared_not_enforced_by_default():
    # The reference site: 1903 grid_feed_max = -1, solar export uncontrolled.
    s, clock = site(grid_code="ausgrid", initial_soc=100)
    s.solar_override_w, s.load_override_w = 14000, 1000
    snap = s.step()
    assert snap.grid_w == -13000 and snap.export_limit_w is None
    assert snap.declared_export_limit_w == 10000 and snap.export_over_limit_w == 3000
    after(s, clock, 60)
    assert abs(s.export_breach_s - 60) < 1e-6 and abs(s.export_breach_wh - 50) < 1e-6
    s3, _ = site(grid_code="ausgrid", ac_type=2)
    assert s3.step().declared_export_limit_w == 15000           # 5 kW per phase
    assert site()[0].step().declared_export_limit_w is None     # generic Australia A


def test_latent_breach_midday_vpp_discharge():
    # BACKLOG: 5.7 kW solar + 5 kW battery = 10.7 kW > 10 kW, once they coincide.
    s, _ = site(grid_code="ausgrid", initial_soc=80)
    s.solar_override_w, s.load_override_w = 5700, 0
    s.update_remote(enabled=True, pct_hw=100.0)
    snap = s.step()
    assert snap.grid_w == -10700 and abs(snap.export_over_limit_w - 700) < 1e-6


def test_ausgrid_export_limit_enforced_option():
    s, clock = site(grid_code="ausgrid", initial_soc=100, export_limit_enforced=True)
    s.solar_override_w, s.load_override_w = 14000, 1000
    snap = after(s, clock, 120)                       # a soft limit settles within 15 s
    assert snap.grid_w == pytest.approx(-10000, abs=1) and snap.export_limit_w == 10000
    assert snap.solar_w == pytest.approx(11000, abs=1)
    assert snap.export_over_limit_w == pytest.approx(0, abs=1)
    s.update_remote(enabled=True, pct_hw=100.0)       # VPP discharge can't push past it
    assert after(s, clock, 120).grid_w == pytest.approx(-10000, abs=1)


# -- IEEE 1547-2018 (US) --------------------------------------------------------------------
def test_split_phase_defaults_to_ieee_cat2():
    s, _ = site(ac_type=1)
    snap = s.step()
    assert s.grid.name == "ieee1547-cat2" and s.phases == ("L1", "L2")
    assert snap.hz == 60.0 and abs(snap.v_ln - 120) < 1e-6
    assert abs(s.v_ll("L1", "L2", {"L1": 120, "L2": 120}) - 240) < 1e-6


def test_ieee_cat2_trips():
    s, clock = site(ac_type=1)
    s.set_grid(v_pu=0.65)                             # UV1 0.70 pu, 10 s
    assert after(s, clock, 9.9).grid_connected
    assert after(s, clock, 0.2).trip_stage == "UV1"
    s, clock = site(ac_type=1)
    s.set_grid(hz=61.5)                               # OF1 61.2 Hz, 300 s ride-through
    assert after(s, clock, 299).grid_connected
    assert after(s, clock, 2).trip_stage == "OF1"
    s, clock = site(ac_type=1)
    s.set_grid(hz=62.5)                               # OF2 62 Hz, 0.16 s
    assert after(s, clock, 0.2).trip_stage == "OF2"


def test_ieee_enter_service_delay_is_300s():
    s, clock = site(ac_type=1)
    s.set_grid(available=False)
    s.restore_grid()
    assert not after(s, clock, 290).grid_connected
    assert after(s, clock, 11).grid_connected


def test_ieee_cat3_longer_ride_through():
    s, clock = site(ac_type=1, grid_code="ieee1547-cat3")
    s.set_grid(v_pu=0.85)                             # UV1 0.88 pu, 21 s
    assert after(s, clock, 20).grid_connected
    assert after(s, clock, 1.1).trip_stage == "UV1"


def test_grid_overrides():
    s, clock = site(grid_overrides={"es_delay_s": 5, "es_ramp_s": 0, "freq_watt": None})
    s.set_grid(available=False)
    s.restore_grid()
    assert after(s, clock, 6).grid_connected
    s.solar_override_w = 3000
    s.set_grid(hz=51.0)
    assert s.step().throttle_pct == 0


# -- phases -----------------------------------------------------------------------------------
def test_three_phase_line_voltages_and_phase_loss():
    s, clock = site(ac_type=2)
    snap = s.step()
    assert set(snap.phases) == {"L1", "L2", "L3"}
    v = {ph: st.v_ln for ph, st in snap.phases.items()}
    assert abs(s.v_ll("L1", "L2", v) - 241.8 * 3 ** 0.5) < 1e-6
    s.set_grid(available=False, phase="L2")
    snap = s.step()
    assert not snap.grid_connected and snap.trip_reason == "phase-loss" and snap.trip_stage == "L2"


def test_one_leg_sag_trips():
    s, clock = site(ac_type=2)
    s.set_grid(voltage=170, phase="L3")
    snap = after(s, clock, 10.1)
    assert snap.trip_stage == "V<"


def test_unbalanced_load_per_phase():
    s, _ = site(ac_type=2, load_split={"L1": 3, "L2": 1, "L3": 0}, initial_soc=20)
    s.load_override_w = 4000
    snap = s.step()                                   # at reserve: grid carries it all
    w = {ph: st.grid_w for ph, st in snap.phases.items()}
    assert w == {"L1": 3000, "L2": 1000, "L3": 0}
    assert abs(sum(w.values()) - snap.grid_w) < 1e-6


def test_invalid_phase_layouts():
    with pytest.raises(ValueError):
        site(ac_type=0, phases=["L1", "L2"])
    with pytest.raises(ValueError):
        site(ac_type=1, phases=["L1", "L3"])
    with pytest.raises(ValueError):
        site(ac_type=2, load_split={"L4": 1})


# -- requested off-grid / PV relays / alarms -----------------------------------------------------
def test_requested_offgrid_has_no_disconnect_alarm():
    s, _ = site()
    s.request_offgrid(True)
    snap = s.step()
    assert not snap.grid_connected and snap.trip_reason == "requested"
    assert not snap.alarms & bit("GRID_DISCONNECT")
    s.request_offgrid(False)
    assert s.step().grid_connected


def test_pv_relay_drops_its_input():
    s, _ = site(pv_inputs={"proximal": 0.6, "remote1": 0.4})
    s.solar_override_w = 5000
    snap = s.step()
    assert snap.pv_by_input["proximal"] == 3000 and snap.pv_by_input["remote1"] == 2000
    s.set_pv_relay("remote1", False)
    snap = s.step()
    assert snap.pv_by_input["remote1"] == 0 and snap.solar_w == 3000


def test_tripping_alarm_stops_inverter_until_reset():
    s, _ = site()
    s.force_alarm("OVER_TEMP")
    snap = s.step()
    assert snap.inverter_state == INV_FAULT and snap.battery_w == 0 and snap.grid_w == 2000
    s.reset_alarms()
    assert s.step().battery_w == 2000


def test_persistent_alarm_survives_reset():
    s, _ = site()
    s.force_alarm("CABINET_OPEN", latched=False)
    s.reset_alarms()
    assert s.step().alarms & bit("CABINET_OPEN")
    s.clear_alarm("CABINET_OPEN")
    assert not s.step().alarms


def test_fault_during_outage_is_a_blackout():
    s, _ = site()
    s.set_grid(available=False)
    s.force_alarm("GROUND_FAULT")
    snap = s.step()
    assert snap.load_w == 0 and snap.load_shed_w == 2000 and snap.inverter_state == INV_FAULT


# -- on the wire --------------------------------------------------------------------------------
def s16(v):
    return struct.unpack(">h", struct.pack(">H", v))[0]


def addr(gw, point):
    return gw.device.image.ref(701, point).address


def test_registers_reflect_outage_and_alarm_reset(clock):
    gw = make_gateway(clock, site={"initial_soc": 60})
    with gw:
        c = RawClient(*gw.address)
        gw.site.solar_override_w, gw.site.load_override_w = 0, 1500
        assert c.read(CONNST, 1) == [1] and c.read(DERMODE, 2) == [0, 1]
        gw.site.set_grid(available=False)
        assert c.read(CONNST, 1) == [0]
        assert c.read(DERMODE, 2) == [0, 2]            # grid forming
        alrm = c.read(ALRM, 2)
        assert (alrm[0] << 16 | alrm[1]) & bit("GRID_DISCONNECT")
        assert s16(c.read(80, 1)[0]) == 0
        gw.site.force_alarm("OVER_TEMP")
        assert c.read(INVST, 1) == [INV_FAULT] and c.read(ST, 1) == [0]
        c.write(ALARM_RESET, 1)                       # what controller.clear_alarms() does
        c.write(ALARM_RESET, 0)
        assert c.read(INVST, 1) == [3]
        gw.site.restore_grid()
        clock.advance(61)
        assert c.read(CONNST, 1) == [1]
        c.close()


def test_single_phase_registers_match_scan_shape(clock):
    gw = make_gateway(clock)
    with gw:
        c = RawClient(*gw.address)
        assert c.read(addr(gw, "LLV"), 2) == [2418, 2418]       # LLV == LNV
        assert c.read(addr(gw, "VL1L2"), 1) == [2418]
        assert c.read(addr(gw, "VL2"), 1) == [0] and c.read(addr(gw, "WL3"), 1) == [0]
        assert s16(c.read(addr(gw, "Var"), 1)[0]) == -348      # volt-var absorbing at 241.8 V
        gw.site.set_grid(hz=50.8, voltage=230)
        assert c.read(addr(gw, "Hz"), 2) == [0, 50800]
        assert c.read(addr(gw, "LNV"), 1) == [2300]
        c.close()


def test_703_reflects_grid_code(clock):
    for code, expected in (("as4777-au-a", [2530, 2050, 0, 5015, 0, 4750, 0, 60]),
                           ("ieee1547-cat2", [1260, 1100, 0, 6010, 0, 5950, 0, 300])):
        gw = make_gateway(clock, site={"grid_code": code, "ac_type": 1 if code.startswith("ieee") else 0})
        with gw:
            c = RawClient(*gw.address)
            assert c.read(280, 8) == expected, code    # ESVHi..ESDlyTms
            c.close()


def test_two_phase_registers(clock):
    gw = make_gateway(clock, profile="spec", quirks={"unused_phase_registers": "zero"},   # AU aGates: one phase (#242)
                      site={"ac_type": 2, "phases": ["L1", "L2"]})
    with gw:
        c = RawClient(*gw.address)
        assert c.read(addr(gw, "ACType"), 1) == [2]
        assert c.read(addr(gw, "VL1L2"), 1) == [round(2418 * 3 ** 0.5)]
        assert c.read(addr(gw, "VL2L3"), 1) == [0] and c.read(addr(gw, "VL3"), 1) == [0]
        assert c.read(addr(gw, "VL2"), 1) == [2418]
        c.close()


def test_single_leg_on_l2_and_unimplemented_unused(clock):
    gw = make_gateway(clock, site={"phases": ["L2"]},
                      quirks={"unused_phase_registers": "unimplemented"})
    with gw:
        c = RawClient(*gw.address)
        gw.site.solar_override_w, gw.site.load_override_w = 0, 1000
        gw.site.soc = 20                               # at reserve: grid carries the load
        assert s16(c.read(addr(gw, "WL2"), 1)[0]) == 1000
        assert c.read(addr(gw, "WL1"), 1) == [0x8000]  # int16 unimplemented
        assert c.read(addr(gw, "VL1"), 1) == [0xFFFF]  # uint16 unimplemented
        c.close()


def test_per_phase_registers_unbalanced(clock):
    gw = make_gateway(clock, profile="spec", site={"ac_type": 2, "load_split": {"L1": 2, "L2": 1, "L3": 1},
                                   "initial_soc": 20})
    with gw:
        c = RawClient(*gw.address)
        gw.site.solar_override_w, gw.site.load_override_w = 0, 4000
        w = [s16(c.read(addr(gw, f"W{ph}"), 1)[0]) for ph in ("L1", "L2", "L3")]
        assert w == [2000, 1000, 1000] and s16(c.read(addr(gw, "W"), 1)[0]) == 4000
        gw.site.set_grid(voltage=220, phase="L3")
        assert c.read(addr(gw, "VL3"), 1) == [2200]
        c.close()


def test_pv_inputs_on_extension_registers(clock):
    gw = make_gateway(clock, site={"pv_inputs": {"proximal": 0.5, "remote1": 0.5}})
    with gw:
        c = RawClient(*gw.address)
        gw.site.solar_override_w = 4000
        assert c.read(15502, 4) == [4000, 2000, 2000, 0]
        gw.site.set_pv_relay("proximal", False)
        assert c.read(15502, 4) == [2000, 0, 2000, 0]
        c.close()


# -- control API --------------------------------------------------------------------------------
def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


def test_control_api_round_trip():
    fleet = Fleet.from_dict({"gateways": [
        {"id": "site", "seed": 7, "setup": "in-service", "tz_offset_hours": 0, "ac_type": 2, "profile": "spec"}]})
    fleet.start(control_port=0)
    try:
        base = f"http://127.0.0.1:{fleet.control.port}"
        with urllib.request.urlopen(base + "/gateways", timeout=3) as r:
            assert json.load(r)[0]["id"] == "site"
        state = _post(base, "/gateways/site/grid", {"available": False, "phase": "L3"})
        assert state["grid_connected"] is False and state["trip_reason"] == "phase-loss"
        assert state["legs"] == ["L1", "L2", "L3"] and state["grid_code"] == "as4777-au-a"
        state = _post(base, "/gateways/site/alarms", {"set": ["OVER_TEMP"]})
        assert "OVER_TEMP" in state["alarm_names"]
        state = _post(base, "/gateways/site/pv", {"input": "proximal", "relay": False})
        assert state["pv_relays"]["proximal"] is False
        state = _post(base, "/gateways/site/faults", {"latency_s": 0.01})
        assert state["faults"]["latency_s"] == 0.01
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base, "/gateways/site/alarms", {"set": ["NOPE"]})
        assert e.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base, "/gateways/site/grid", {"voltage": 200, "phase": "L9"})
        assert e.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base, "/gateways/nope/grid", {"available": False})
        assert e.value.code == 404
    finally:
        fleet.stop()


def test_ctl_verbs_map_to_requests():
    from franklinwh_emulator.cli import _ctl_request
    assert _ctl_request("outage", []) == ("POST", "grid", {"available": False})
    assert _ctl_request("outage", ["L2"]) == ("POST", "grid", {"available": False, "phase": "L2"})
    assert _ctl_request("voltage", ["0.8pu"]) == ("POST", "grid", {"v_pu": 0.8})
    assert _ctl_request("voltage", ["195", "L3"]) == ("POST", "grid", {"voltage": 195.0, "phase": "L3"})
    assert _ctl_request("pv", ["remote1", "off"]) == ("POST", "pv", {"input": "remote1", "relay": False})
    assert _ctl_request("solar", ["auto"]) == ("POST", "pv", {"solar_w": None})
    assert _ctl_request("alarm", ["over_temp", "--persistent"]) == (
        "POST", "alarms", {"set": ["OVER_TEMP"], "latched": False})
    assert _ctl_request("fault", ["session_held=true", "latency_s=0.5"]) == (
        "POST", "faults", {"session_held": True, "latency_s": 0.5})


def test_islanded_legs_carry_their_own_load():
    s, _ = site(ac_type=2, load_split={"L1": 3, "L2": 1, "L3": 1})
    s.set_grid(available=False)
    snap = s.step()
    assert all(st.grid_w == 0 for st in snap.phases.values())
    assert snap.phases["L1"].gen_w == 1200 and snap.phases["L2"].gen_w == 400


# -- response timing (AS/NZS 4777.2 envelope; IEEE 1547 open-loop response times) -------------
def _pv_over_freq_site(**kw):
    s, clock = site(initial_soc=100, **kw)
    s.solar_override_w, s.load_override_w = 4000, 0
    after(s, clock, 5)
    return s, clock


def test_frequency_response_has_dead_time_then_settles_within_10s():
    s, clock = _pv_over_freq_site()
    s.set_grid(hz=51.125)                              # target: PV halfway, 2000 W
    assert after(s, clock, 0.5).solar_w == pytest.approx(4000, abs=1)   # within the 1 s dead time
    mid = after(s, clock, 3.0).solar_w                 # 3.5 s in: moving
    assert 2000 < mid < 3900
    snap = after(s, clock, 7.5)                        # 11 s in: dead time + 10 s
    assert abs(snap.solar_w - 2000) <= 0.05 * 2000 + 1  # within 5 % of the step


def test_frequency_release_hysteresis():
    s, clock = _pv_over_freq_site()
    s.set_grid(hz=51.125)
    held = after(s, clock, 60).solar_w                 # settled at 2000 W
    s.set_grid(hz=51.075)                              # back 0.05 Hz: inside the 0.1 Hz backlash
    assert after(s, clock, 60).solar_w == pytest.approx(held, abs=1)
    s.set_grid(hz=50.9)                                # back 0.225 Hz: released to 51.0 Hz
    expected = 4000 * (1 - (51.0 - 50.25) / (52.0 - 50.25))
    assert after(s, clock, 60).solar_w == pytest.approx(expected, abs=2)


def test_enter_service_ramp_after_reconnect():
    s, clock = site(initial_soc=100, grid_overrides={"es_delay_s": 5})
    s.solar_override_w, s.load_override_w = 4000, 0
    s.set_grid(available=False)
    after(s, clock, 1)
    s.restore_grid()
    assert after(s, clock, 6).grid_connected
    early = after(s, clock, 90).solar_w                # ~90 s of a 360 s ramp
    assert 800 < early < 1300
    assert after(s, clock, 300).solar_w == pytest.approx(4000, abs=1)


def test_soft_export_limit_settles_within_15s():
    s, clock = site(grid_code="ausgrid", initial_soc=100, export_limit_enforced=True)
    s.solar_override_w, s.load_override_w = 5000, 0
    after(s, clock, 30)
    s.solar_override_w = 14000                         # cloud edge: 14 kW available
    first = after(s, clock, 0.5)
    assert -first.grid_w > 10000 + 2000                # breaches at first
    assert -after(s, clock, 15).grid_w <= 10000 + 0.06 * 4000   # 95 % of the 4 kW step by 15 s
    assert -after(s, clock, 15).grid_w <= 10000 + 50             # inside the invariant's band


def test_instant_responses_when_timing_disabled():
    s, clock = _pv_over_freq_site(grid_overrides={"response": None})
    s.set_grid(hz=51.125)
    assert after(s, clock, 0.1).solar_w == pytest.approx(2000, abs=1)


def test_ieee_timing_is_faster_for_frequency():
    s, clock = site(grid_code="ieee1547-cat2", ac_type=1, initial_soc=100)
    s.solar_override_w, s.load_override_w = 4000, 0
    after(s, clock, 5)
    s.set_grid(hz=61.536)                               # PV droop: 4 kW → 3 kW
    snap = after(s, clock, 5.2)                         # 0.2 s dead time + 5 s OLRT
    target = 4000 * (1 - (61.536 - 60.036) / (66.036 - 60.036))
    assert abs(snap.solar_w - target) <= 0.10 * (4000 - target) + 1   # 90 % (OLRT)


def test_volt_var_lags():
    s, clock = site()
    before = after(s, clock, 5).var                     # 241.8 V: already slightly absorbing
    s.set_grid(voltage=258.0)                                 # AU volt-var: -60 % of S at 258 V
    assert after(s, clock, 0.5).var == pytest.approx(before, abs=1)   # dead time
    settled = after(s, clock, 30).var
    assert settled == pytest.approx(-0.60 * 5800, rel=0.01)


def test_agate_declared_response_preset():
    s, clock = _pv_over_freq_site(grid_overrides={"response": "agate-declared"})
    s.set_grid(hz=51.125)
    assert after(s, clock, 0.5).solar_w == pytest.approx(2000, abs=5)   # 0.1 s, no dead time
    with pytest.raises(ValueError):
        site(grid_overrides={"response": "nope"})
