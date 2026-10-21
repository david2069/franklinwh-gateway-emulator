"""Write semantics: what the faithful aGate profile applies, discards, or rejects."""

import struct

from conftest import RawClient, make_gateway

WSETENA, WSETMOD, WSET, WSETPCT = 318, 319, 320, 324
WSETENARVRT, WSETRVRTTMS, WSETRVRTREM = 326, 327, 329
CONTROLLER_HB, LOCREMCTL = 1092, 1089
WCHARTEMAX = 259


def s16(v):
    return struct.unpack(">h", struct.pack(">H", v))[0]


def test_vpp_charge_via_wsetpct(client, gateway, clock):
    # Hardware sign: negative WSetPct = charge. -60.0 % of 5 kW = 3 kW charge.
    client.write(WSETMOD, 0)
    client.write(WSETPCT, (-600) & 0xFFFF)
    client.write(WSETENA, 1)
    gateway.site.solar_override_w = 0
    gateway.site.load_override_w = 500
    clock.advance(1)
    batt = s16(client.read(1042 + 6, 1)[0])            # 714.DCW
    assert batt == -3000
    grid = s16(client.read(80, 1)[0])                  # 701.W: import positive
    assert grid == 3500
    soc0 = client.read(1037, 1)[0]
    clock.advance(3600)
    assert client.read(1037, 1)[0] > soc0              # SoC rose


def test_vpp_discharge_stops_at_reserve(gateway, clock):
    site = gateway.site
    site.solar_override_w, site.load_override_w = 0, 0
    c = RawClient(*gateway.address)
    c.write(WSETPCT, 1000)                             # +100 % = 5 kW discharge
    c.write(WSETENA, 1)
    clock.advance(1)
    assert s16(c.read(1048, 1)[0]) == 5000
    for _ in range(12):
        clock.advance(900)
        site.step()
    assert abs(site.soc - site.self_reserve) < 0.01
    assert s16(c.read(1048, 1)[0]) == 0
    c.close()


def test_wsetpct_over_100_saturates(client, gateway, clock):
    gateway.site.solar_override_w = gateway.site.load_override_w = 0
    client.write(WSETPCT, (-1500) & 0xFFFF)            # -150 %
    client.write(WSETENA, 1)
    clock.advance(1)
    assert s16(client.read(1048, 1)[0]) == -5000


def test_wset_watts_mode(client, gateway, clock):
    gateway.site.solar_override_w = gateway.site.load_override_w = 0
    client.write(WSETMOD, 1)
    client.write(WSET, [0, 2000])                      # int32 +2000 = charge
    client.write(WSETENA, 1)
    clock.advance(1)
    assert s16(client.read(1048, 1)[0]) == -2000


def test_reversion_countdown_is_cosmetic(client, gateway, clock):
    client.write(WSETRVRTTMS, [0, 30])
    client.write(WSETPCT, 500)
    client.write(WSETENA, 1)
    clock.advance(10)
    assert client.read(WSETRVRTREM, 2) == [0, 20]
    clock.advance(60)
    assert client.read(WSETRVRTREM, 2) == [0, 0]
    assert client.read(WSETENA, 1) == [1]              # still enabled (PICS Issue 4)


def test_reversion_effective_in_spec_profile(clock):
    gw = make_gateway(clock, profile="spec")
    with gw:
        c = RawClient(*gw.address)
        c.write(WSETRVRTTMS, [0, 30])
        c.write(WSETENA, 1)
        clock.advance(31)
        assert c.read(WSETENA, 1) == [0]
        c.close()


def test_discarded_writes_are_acknowledged(client):
    assert client.write(CONTROLLER_HB, [0, 42]) == "ok"
    assert client.read(CONTROLLER_HB, 2) == [0, 0]
    assert client.write(LOCREMCTL, 0) == "ok"
    assert client.read(LOCREMCTL, 1) == [1]
    assert client.write(WCHARTEMAX, 3000) == "ok"
    assert client.read(WCHARTEMAX, 1) == [0xFFFF]
    assert client.write(310, 1) == "ok"                # WMaxLimPctEna
    assert client.read(310, 1) == [0]


def test_locremctl_stays_local_during_vpp(client):
    client.write(WSETENA, 1)
    assert client.read(LOCREMCTL, 1) == [1]


def test_extension_writes_discarded_without_span_unlock(client):
    assert client.write(15507, 3) == "ok"
    assert client.read(15507, 1) == [2]
    assert client.write(15508, 40) == "ok"
    assert client.read(15508, 1) == [20]


def test_span_unlock_applies_mode_and_reserves(clock):
    gw = make_gateway(clock, span_unlock=True, quirks={"tou_reserve_mirrors_self": False})
    with gw:
        c = RawClient(*gw.address)
        c.write(15507, 3)
        c.write(15509, 40)
        assert c.read(15507, 3) == [3, 20, 40]
        assert gw.site.native_mode == 3 and gw.site.tou_reserve == 40
        c.close()


def test_span_unlock_tou_reserve_mirror_quirk(clock):
    gw = make_gateway(clock, span_unlock=True)
    with gw:
        c = RawClient(*gw.address)
        c.write(15509, 40)
        assert gw.site.tou_reserve == 40
        assert c.read(15509, 1) == [20]                # still reads 15508
        c.close()


def test_sticky_setting_write(client):
    # 705.Ena is RW and not special-cased: stored as written.
    assert client.write(365, 0) == "ok"
    assert client.read(365, 1) == [0]


def test_readonly_write_spec_profile_raises(clock):
    gw = make_gateway(clock, profile="spec")
    with gw:
        c = RawClient(*gw.address)
        assert c.write(80, 5) == ("exc", 2)            # 701.W is read-only
        c.close()


def test_15507_only_modes_read_on_hardware(clock):
    """15507 has only ever read 1, 2 and 3 (franklinwh-modbus#75); 0 and 4 are not modes."""
    import pytest
    from franklinwh_emulator.site import MODE_NAMES
    assert sorted(MODE_NAMES) == [1, 2, 3]
    gw = make_gateway(clock, span_unlock=True)
    with gw:
        c = RawClient(*gw.address)
        for bad in (0, 4):
            c.write(15507, bad)                       # acknowledged, not applied
            assert c.read(15507, 1) == [2]
        c.close()
        with pytest.raises(ValueError):
            gw.site.set_native_mode(4)
    with pytest.raises(ValueError):
        make_gateway(clock, site={"native_mode": 4})
