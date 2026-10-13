"""Acceptance: the real franklinwh-modbus controller against the emulator."""

import pytest

from conftest import make_gateway

fm = pytest.importorskip("franklinwh_modbus")
from franklinwh_modbus import FranklinWHController  # noqa: E402
from franklinwh_modbus.types import BatteryCommand  # noqa: E402


@pytest.fixture
def controller(gateway):
    host, port = gateway.address
    c = FranklinWHController(host, port=port, unit_id=2, timeout=3)
    assert c.connect()
    yield c
    c.disconnect()


def test_scan_finds_all_models(controller):
    found = sorted(k for k in controller.models if isinstance(k, int))
    assert found == [1, 502, 701, 702, 703, 704, 705, 706, 707, 708, 709, 710,
                     711, 712, 713, 714, 715]


def test_reads(controller, gateway):
    assert controller.read_nameplate()["serial"] == gateway.serial
    assert controller.read_battery_status()["soc"] == 60.0
    assert controller.read_grid_status()["connection_state"] == "Connected"
    native = controller.read_native_mode()
    assert native["mode_name"] == "Self-Consumption"
    assert controller.RATED_MAX_CHARGE_W == 5000


def test_connect_does_not_write(gateway):
    # franklinwh-modbus >= develop@8af8301: connect() never writes; the
    # extension writability test is opt-in (test_extension_writability()).
    writes = []
    real = gateway.device.write
    gateway.device.write = lambda address, values: writes.append(address) or real(address, values)
    host, port = gateway.address
    c = FranklinWHController(host, port=port, unit_id=2, timeout=3)
    try:
        assert c.connect()
        assert writes == []
        assert not c.get_extension_write_status()["tested"]
    finally:
        c.disconnect()
        gateway.device.write = real


def test_extension_probe_sees_read_only(controller):
    controller.test_extension_writability()
    status = controller.get_extension_write_status()
    assert status["tested"] and not status["ongrid_mode"]["writable"]


def test_send_command_and_reset(controller, gateway, clock):
    gateway.site.solar_override_w = gateway.site.load_override_w = 0
    ok, _ = controller.send_command(BatteryCommand(power_watts=3000))
    assert ok
    clock.advance(1)
    assert controller.read_battery_status()["battery_power_w"] == -3000
    status = controller.read_control_status()
    assert status["wset_enabled"] == 1 and status["loc_rem_ctl_name"] == "Local"
    assert controller.reset_control_state(handshake_wait_s=0)
    clock.advance(1)
    assert controller.read_control_status()["wset_enabled"] == 0
    assert gateway.site.step().battery_w == 0


def test_native_mode_with_span_unlock(clock):
    gw = make_gateway(clock, span_unlock=True, quirks={"tou_reserve_mirrors_self": False})
    with gw:
        c = FranklinWHController(*gw.address, unit_id=2, timeout=3)
        assert c.connect()
        c.test_extension_writability()
        assert c.get_extension_write_status()["ongrid_mode"]["writable"]
        ok, _ = c.set_native_mode(3)
        assert ok and gw.site.native_mode == 3
        ok, _ = c.set_tou_reserve(40)
        assert ok and gw.site.tou_reserve == 40
        c.disconnect()
