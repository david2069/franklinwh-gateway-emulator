"""The register layout must match the real aGate scan (AGATE_FULL_SCAN_2026-05-14)."""

from franklinwh_emulator.device import ILLEGAL_ADDRESS

# (model id, address of its ID register, L) from the scan, minus the 40000 display offset.
SCANNED_CHAIN = [
    (1, 2, 66), (701, 70, 153), (702, 225, 50), (703, 277, 17), (704, 296, 65),
    (705, 363, 67), (706, 432, 31), (707, 465, 105), (708, 572, 105), (709, 679, 135),
    (710, 816, 135), (711, 953, 32), (712, 987, 44), (713, 1033, 7), (714, 1042, 43),
    (715, 1087, 7), (502, 1096, 28),
]


def test_chain_matches_scan(client):
    assert client.read(0, 2) == [0x5375, 0x6E53]
    for mid, addr, length in SCANNED_CHAIN:
        assert client.read(addr, 2) == [mid, length], mid
    assert client.read(1126, 2) == [0xFFFF, 0]


def test_documented_point_addresses(gateway):
    img = gateway.device.image
    assert img.ref(704, "WSetEna").address == 318        # quirks doc: M704.WSetEna (318)
    assert img.ref(715, "ControllerHb").address == 1092  # quirks doc: read addr 1092
    assert img.ref(715, "LocRemCtl").address == 1089


def test_base_40000_is_illegal_by_default(client):
    assert client.read(40000, 2) == ("exc", ILLEGAL_ADDRESS)
    assert client.read(2000, 1) == ("exc", ILLEGAL_ADDRESS)


def test_base_alias(clock):
    from conftest import RawClient, make_gateway
    gw = make_gateway(clock, seed=1, quirks={"base_aliases": [40000]})
    with gw:
        c = RawClient(*gw.address)
        assert c.read(40000, 4) == c.read(0, 4)
        c.close()


def test_uid_agnostic(gateway):
    from conftest import RawClient
    for unit in (1, 2, 126, 247):
        c = RawClient(*gateway.address, unit=unit)
        assert c.read(0, 4) == [0x5375, 0x6E53, 1, 66]
        c.close()


def test_extension_and_hires_ranges(client, gateway):
    ext = client.read(15500, 14)
    assert ext[7] == 2                                  # Self-Consumption
    assert ext[8] == ext[9] == 20                       # 15509 mirrors 15508
    assert ext[6] % 100 == 0                            # 15506 coarse load
    hires = client.read(16000, 3)
    assert abs(hires[0] - ext[6]) <= 50
    assert client.read(15020, 1) == [13600]             # WHRtg mirror
    assert client.read(15514, 1) == ("exc", ILLEGAL_ADDRESS)


def test_nameplate(client, gateway):
    regs = client.read(4, 16)
    mn = b"".join(r.to_bytes(2, "big") for r in regs).rstrip(b"\0").decode()
    assert mn == "FranklinWH Technologies Co., Ltd"
    sn_regs = client.read(52, 16)
    sn = b"".join(r.to_bytes(2, "big") for r in sn_regs).rstrip(b"\0").decode()
    assert sn == gateway.serial and len(sn) == 20


def test_faithful_profile_quantises_soc_and_battery_power():
    """30 days of reference exports: 713.SoC on whole percent, 714.DCW on 100 W steps."""
    from franklinwh_emulator.clock import ManualClock
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    from franklinwh_emulator.site import SiteConfig
    for profile, exact in (("agate-v10r01b04d00", False), ("spec", True)):
        gw = Gateway(GatewayConfig(seed=7, profile=profile,
                                   site=SiteConfig(seed=7, initial_soc=55.37)),
                     clock=ManualClock(1_767_225_600 + 2 * 3600))
        soc = gw.device.read(1037, 1)[0]
        dcw = gw.device.read(1048, 1)[0]
        dcw = dcw - 0x10000 if dcw & 0x8000 else dcw
        if exact:
            assert soc == round(gw.site.soc * 10)
        else:
            assert soc % 10 == 0 and dcw % 100 == 0


def _decode_image(gw):
    """Every point of the emulator's SunSpec chain, as {model id: {path: value}}."""
    from franklinwh_emulator.sunspec import _walk
    gw.device.read(0, 2)                                   # refresh from the site
    return {str(mid): {p: pt.value for p, pt in _walk(model)}
            for mid, model in gw.device.image.models.items()}


def test_faithful_profile_matches_reference_scan_static_points():
    """589 static points (live values and the serial removed) from a read-only scan of the
    reference aGate on 2026-10-07, including the 705–712 curve groups the May scan missed."""
    import json
    import pathlib
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    ref = json.loads((pathlib.Path(__file__).parent / "data"
                      / "reference_scan_static_2026-10-07.json").read_text())
    gw = Gateway(GatewayConfig(seed=7))
    assert [[m, gw.device.image.model_addr[m], gw.device.image.models[m].model_len]
            for m in gw.device.image.models] == ref["layout"]
    emu = _decode_image(gw)
    diffs = [(mid, p, v, emu[mid].get(p)) for mid, pts in ref["points"].items()
             for p, v in pts.items() if emu[mid].get(p) != v]
    assert diffs == []


def test_curves_unimplemented_for_non_au_grid_codes():
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    from franklinwh_emulator.site import SiteConfig
    au = _decode_image(Gateway(GatewayConfig(seed=7)))
    assert au["705"]["Crv[0].Pt[0].V"] == 20700 and au["706"]["Crv[0].RspTms"] == 1
    us = _decode_image(Gateway(GatewayConfig(seed=7, site=SiteConfig(seed=7, ac_type=1))))
    assert us["705"]["Crv[0].Pt[0].V"] is None and us["711"]["Ctl[0].DbOf"] is None


def test_714_port_block_and_throtpct_per_profile():
    from franklinwh_emulator.gateway import Gateway, GatewayConfig
    real = _decode_image(Gateway(GatewayConfig(seed=7)))
    assert real["714"]["Prt[0].DCW"] == 0 and real["714"]["Prt[0].IDStr"] == "ID_String"
    assert real["701"]["ThrotPct"] is None
    spec = _decode_image(Gateway(GatewayConfig(seed=7, profile="spec")))
    assert spec["714"]["Prt[0].DCW"] == spec["714"]["DCW"] and spec["701"]["ThrotPct"] == 0
