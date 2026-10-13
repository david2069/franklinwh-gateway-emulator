"""CSIP-AUS client: registration, utility controls, default and comms-loss fallback."""

import pytest

from franklinwh_emulator.clock import ManualClock
from franklinwh_emulator.csip import lfdi_for, sfdi_for
from franklinwh_emulator.site import Site, SiteConfig

NOON = 1_767_225_600 + 12 * 3600


def site(**kw):
    clock = ManualClock(NOON)
    kw.setdefault("tz_offset_hours", 0)
    kw.setdefault("initial_soc", 60)
    s = Site(SiteConfig(seed=7, grid_code="ausgrid", **kw), clock)
    s.serial = "MOCK00073B1612DD272D"
    s.solar_override_w, s.load_override_w = 6000, 500
    return s, clock


def registered(**kw):
    s, clock = site(**kw)
    s.configure_csip(enabled=True, uri="https://csip.example/dcap")
    clock.advance(6)
    s.step()
    return s, clock


def test_sfdi_check_digit():
    # IEEE 2030.5 worked example: LFDI 3E4F45AB31EDFE5B67E343E5E4562E31984E23E5 → SFDI 167261211391
    assert sfdi_for("3E4F45AB31EDFE5B67E343E5E4562E31984E23E5") == 167261211391
    lfdi = lfdi_for("MOCK00073B1612DD272D")
    assert len(lfdi) == 40 and lfdi == lfdi_for("MOCK00073B1612DD272D")


def test_registration_needs_enable_and_server():
    s, clock = site()
    assert s.csip.status(clock.now()) == "disabled"
    s.set_csip_server(False)
    s.configure_csip(enabled=True)
    clock.advance(30)
    s.step()
    assert s.csip.status(clock.now()) == "registering"   # enable alone isn't registration
    s.set_csip_server(True)
    clock.advance(6)
    s.step()
    assert s.csip.status(clock.now()) == "registered" and s.csip.sfdi > 0


def test_export_event_is_enforced_then_expires():
    s, clock = registered(initial_soc=100)
    assert s.step().grid_w == -5500
    s.post_csip_control({"opModExpLimW": 1500}, duration_s=1800)
    clock.advance(120)                                # soft limit: met within 15 s, not instantly
    snap = s.step()
    assert snap.grid_w == pytest.approx(-1500, abs=1) and snap.export_limit_w == 1500
    assert snap.csip_source == "event" and snap.solar_w == pytest.approx(2000, abs=1)
    clock.advance(1801)
    assert s.step().grid_w == pytest.approx(-5500, abs=1)


def test_controls_ignored_until_registered():
    s, clock = site(initial_soc=100)
    s.post_csip_control({"opModExpLimW": 0})
    assert s.step().grid_w == -5500


def test_default_control_and_comms_loss():
    s, clock = registered(initial_soc=100)
    s.set_csip_default({"opModExpLimW": 5000})
    clock.advance(120)
    assert s.step().grid_w == pytest.approx(-5000, abs=1)   # no event: default applies
    s.post_csip_control({"opModExpLimW": 10000})
    clock.advance(120)
    assert s.step().grid_w == pytest.approx(-5500, abs=1)
    s.set_csip_server(False)
    clock.advance(299)
    assert s.step().grid_w == -5500                   # still within comms-loss timeout
    clock.advance(120)
    snap = s.step()
    assert snap.csip_source == "comms-loss-default" and snap.grid_w == pytest.approx(-5000, abs=1)


def test_zero_export_curtails_vpp_and_pv():
    s, clock = registered(initial_soc=80)
    s.update_remote(enabled=True, pct_hw=100.0)        # VPP wants to export 5 kW
    s.post_csip_control({"opModExpLimW": 0})
    clock.advance(120)
    snap = s.step()
    assert snap.grid_w == pytest.approx(0, abs=1) and snap.battery_w == pytest.approx(0, abs=1)
    assert snap.solar_w == pytest.approx(500, abs=1)


def test_import_limit_stops_grid_charging():
    s, clock = registered(initial_soc=50)
    s.solar_override_w, s.load_override_w = 0, 1000
    s.update_remote(enabled=True, pct_hw=-100.0)       # charge 5 kW from grid
    assert s.step().grid_w == 6000
    s.post_csip_control({"opModImpLimW": 2000})
    snap = s.step()
    assert snap.grid_w == 2000 and snap.battery_w == -1000


def test_generation_and_load_limits():
    s, clock = registered(initial_soc=80)
    s.update_remote(enabled=True, pct_hw=100.0)
    s.post_csip_control({"opModGenLimW": 7000})
    snap = s.step()
    assert snap.solar_w + snap.battery_w == 7000 and snap.battery_w == 1000
    s.clear_csip_events()
    s.update_remote(enabled=True, pct_hw=-100.0)
    s.post_csip_control({"opModLoadLimW": 1500})
    assert s.step().battery_w == -1500


def test_disconnect_stops_der_output():
    s, clock = registered()
    s.post_csip_control({"opModConnect": False})
    snap = s.step()
    assert not snap.der_connected and snap.solar_w == 0 and snap.battery_w == 0
    assert snap.grid_w == 500 and snap.grid_connected   # house stays on the grid


def test_ctl_csip_verbs():
    from franklinwh_emulator.cli import _ctl_request
    assert _ctl_request("csip", ["on", "https://x/dcap"]) == (
        "POST", "csip", {"enable": True, "uri": "https://x/dcap"})
    assert _ctl_request("csip", ["export", "1500", "1800"]) == (
        "POST", "csip", {"control": {"opModExpLimW": 1500}, "duration_s": 1800.0})
    assert _ctl_request("csip", ["connect", "off"]) == (
        "POST", "csip", {"control": {"opModConnect": False}})
    assert _ctl_request("csip", ["default", "export=5000", "import=0"]) == (
        "POST", "csip", {"default": {"opModExpLimW": 5000, "opModImpLimW": 0}})
    assert _ctl_request("csip", ["server", "down"]) == ("POST", "csip", {"server_up": False})
    assert _ctl_request("modbus", ["off"]) == ("POST", "der-comms", {"modbus": False})
    assert _ctl_request("export-limit", ["enforce"]) == ("POST", "export", {"enforced": True})


def test_sunsmden_toggles_modbus_listener():
    import socket
    import time as _time
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    gw = Gateway(GatewayConfig(seed=7))
    gw.modbus_apply_delay_s = 0.2
    host, port = gw.start()
    try:
        socket.create_connection((host, port), timeout=1).close()
        gw.set_modbus_enabled(False)
        socket.create_connection((host, port), timeout=1).close()   # delayed: still up
        _time.sleep(0.5)
        try:
            socket.create_connection((host, port), timeout=1).close()
            raise AssertionError(":502 still listening after sunsMdEn=0")
        except ConnectionRefusedError:
            pass
        gw.set_modbus_enabled(True)
        _time.sleep(0.5)
        socket.create_connection((host, port), timeout=1).close()    # same port again
    finally:
        gw.stop()
