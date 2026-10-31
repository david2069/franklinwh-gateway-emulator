"""Write semantics: what the faithful aGate profile applies, discards, or rejects."""

import struct

from conftest import RawClient, make_gateway

WSETENA, WSETMOD, WSET, WSETPCT = 318, 319, 320, 324
WSETENARVRT, WSETRVRTTMS, WSETRVRTREM = 326, 327, 329
WSETRVRT, WSETPCTRVRT = 322, 325
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
    client.write(WSET, [0xFFFF, 0x10000 - 2000])       # int32 -2000 = charge (m704_wset_sign)
    client.write(WSETENA, 1)
    clock.advance(1)
    assert s16(client.read(1048, 1)[0]) == -2000
    client.write(WSET, [0, 1500])                      # +1500 = discharge
    clock.advance(1)
    assert s16(client.read(1048, 1)[0]) == 1500


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


def test_v12_reversion_is_inverted(clock):
    """V12R02B60D00 (firmware_quirks m704_wsetrvrttms_reversion, 3 of 3 runs; #159): the
    reversion value applies while WSetRvrtTms counts down, the setpoint after; WSetEna stays 1.
    The issue's repro: WSetPct +100 (10 %), WSetPctRvrt 0, WSetRvrtTms 12 s."""
    gw = make_gateway(clock, profile="agate-v12r02b60d00-candidate")
    with gw:
        gw.site.solar_override_w, gw.site.load_override_w = 0, 1000
        c = RawClient(*gw.address)
        c.write(WSETMOD, 0)
        c.write(WSETPCT, 100)
        c.write(WSETPCTRVRT, 0)
        c.write(WSETENARVRT, 1)
        c.write(WSETRVRTTMS, [0, 12])
        c.write(WSETENA, 1)
        seen = []
        for _ in range(8):                             # poll every 3 s, as the repro did
            clock.advance(3)
            seen.append((s16(c.read(1048, 1)[0]), c.read(WSETENA, 1)[0], c.read(WSETRVRTREM, 2)[1]))
        assert seen[:3] == [(0, 1, 9), (0, 1, 6), (0, 1, 3)]          # reversion value (0)
        assert seen[4:] == [(500, 1, 0)] * 4                          # then the setpoint
        assert c.read(WSETPCTRVRT, 1) == [0] and c.read(WSETPCT, 1) == [100]
        # The countdown restarts on the next WSetRvrtTms write, with a non-zero reversion value.
        c.write(WSETPCTRVRT, 50)
        c.write(WSETRVRTTMS, [0, 6])
        clock.advance(1)
        assert s16(c.read(1048, 1)[0]) == 200          # 5 % of 5 kW, rounded to 100 W
        clock.advance(6)
        assert s16(c.read(1048, 1)[0]) == 500
        c.close()


def test_v10_reversion_still_cosmetic_with_a_reversion_value(client, gateway, clock):
    """V10 (measured 2026-03-13): the countdown runs and nothing else changes (#159)."""
    gateway.site.solar_override_w, gateway.site.load_override_w = 0, 1000
    client.write(WSETPCT, 100)
    client.write(WSETPCTRVRT, 0)
    client.write(WSETRVRTTMS, [0, 12])
    client.write(WSETENA, 1)
    clock.advance(3)
    assert s16(client.read(1048, 1)[0]) == 500
    clock.advance(12)
    assert s16(client.read(1048, 1)[0]) == 500 and client.read(WSETENA, 1) == [1]


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


def test_extension_writes_never_applied(clock):
    """15507–15509 writes are acknowledged and not applied (franklinwh-modbus firmware_quirks
    ext_15507_15509_writes); there is no option that makes them take effect (#10)."""
    gw = make_gateway(clock, quirks={"tou_reserve_mirrors_self": False})
    with gw:
        c = RawClient(*gw.address)
        assert c.write(15507, 3) == "ok"
        assert c.write(15508, 40) == "ok"
        assert c.write(15509, 50) == "ok"
        assert c.read(15507, 3) == [2, 20, 20]
        assert (gw.site.native_mode, gw.site.self_reserve, gw.site.tou_reserve) == (2, 20, 20)
        assert [o for a, _, o in gw.device.write_log if a in (15507, 15508, 15509)] == ["discarded"] * 3
        c.close()


def test_span_unlock_option_is_gone(clock):
    import pytest
    from franklinwh_emulator.gateway import GatewayConfig
    with pytest.raises(TypeError):
        GatewayConfig.from_dict({"id": "x", "span_unlock": True})
    with pytest.raises(TypeError):
        make_gateway(clock, span_unlock=True)


def test_tou_reserve_mirror_quirk_on_reads(clock):
    gw = make_gateway(clock, profile="agate-v10r01b04d00", site={"self_reserve": 20, "tou_reserve": 40})
    with gw:
        c = RawClient(*gw.address)
        assert c.read(15509, 1) == [20]                # mirrors 15508 (V10)
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
    gw = make_gateway(clock)
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


def test_v12_candidate_reserves_follow_mode(clock):
    """V12R02B60D00, one site (franklinwh-modbus #63): 15509 = TOU reserve; 16001 = the
    active mode's reserve (EB 100, SC self reserve, TOU TOU reserve)."""
    gw = make_gateway(clock, profile="agate-v12r02b60d00-candidate",
                      site={"self_reserve": 8, "tou_reserve": 15})
    with gw:
        c = RawClient(*gw.address)
        for mode, want in ((2, 8), (3, 15), (1, 100)):
            gw.site.set_native_mode(mode)
            assert c.read(15507, 3) == [mode, 8, 15]
            assert c.read(16001, 1) == [want], mode
        c.close()
    gw = make_gateway(clock, profile="agate-v10r01b04d00", site={"self_reserve": 8, "tou_reserve": 15})
    with gw:
        c = RawClient(*gw.address)
        gw.site.set_native_mode(3)
        assert c.read(15508, 2) == [8, 8] and c.read(16001, 1) == [8]
        c.close()


def test_v12_wsetpct_range_and_charge_refusal(clock):
    """V12R02B60D00 (firmware_quirks m704_wsetpct_accepted_range, m704_wset_negative_refused;
    measured, one site): WSetPct raw 0..+100 accepted, above +100 and every negative refused
    with exception 3; negative WSet refused with exception 3."""
    gw = make_gateway(clock, profile="agate-v12r02b60d00-candidate", site={"initial_soc": 60})
    with gw:
        c = RawClient(*gw.address)
        for raw in (0, 10, 50, 100):
            assert c.write(WSETPCT, raw) == "ok", raw
        for raw in (101, 500, 1000, -10, -100, -500):
            assert c.write(WSETPCT, raw & 0xFFFF) == ("exc", 3), raw
        assert c.read(WSETPCT, 1) == [100]                        # refused writes not stored
        assert c.write(WSET, [0xFFFF, 0x10000 - 500]) == ("exc", 3)   # WSet -500 W (charge)
        assert c.write(WSET, [0, 5000]) == "ok"                       # +5000 W discharge
        c.close()
    gw = make_gateway(clock, profile="agate-v10r01b04d00")         # V10: no limit, charge allowed
    with gw:
        c = RawClient(*gw.address)
        assert c.write(WSETPCT, 1000) == "ok"
        assert c.write(WSETPCT, (-100) & 0xFFFF) == "ok"
        assert c.write(WSET, [0xFFFF, 0x10000 - 500]) == "ok"
        c.close()


def test_write_reject_rules(gateway, client):
    import pytest
    gateway.device.reject_writes(704, "WSetMod", code=3)          # every write to WSetMod
    gateway.device.reject_writes(704, "WSetPct", gt=500)
    assert client.write(WSETMOD, 1) == ("exc", 3)
    assert client.write(WSETPCT, 500) == "ok"
    assert client.write(WSETPCT, 501) == ("exc", 3)
    assert client.write(WSETPCT, (-900) & 0xFFFF) == "ok"
    assert client.write(WSETENA, 1) == "ok"
    gateway.device.write_rejects.clear()
    assert client.write(WSETMOD, 1) == "ok"
    with pytest.raises(ValueError):
        gateway.device.reject_writes(704, "NoSuchPoint")
