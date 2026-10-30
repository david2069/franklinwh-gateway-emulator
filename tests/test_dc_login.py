"""Direct Connect 1102 login: the full reply per aGate firmware, as measured (#78)."""

import pytest

pytest.importorskip("franklinwh_local")
from franklinwh_local import LocalClient  # noqa: E402

from conftest import make_gateway  # noqa: E402
from franklinwh_emulator.profiles import get_profile  # noqa: E402

# Key order of the real reply (fwh-direct-connect-bridge firmware sweep, IBG V12R02B60D00_260924).
V12_B60_KEYS = ["opt", "result", "reason", "IBG_SN", "deviceType", "protocolVer", "IBG_VER", "AWS_VER",
                "APP_VER", "SL_VER", "FHP_NUM", "FHP_SN", "PE_SN", "FPGA_VER", "DCDC_VER", "INV_VER",
                "BMS_SN", "BMS_VER", "BL_VER", "TH_VER", "SyHdVersion", "METER_VER", "threePhasePvPower",
                "PE_HW_VER", "BMS_MAIN_HW_VER", "BMS_BL_HW_VER", "BMS_TH_HW_VER"]
LISTS = ["FHP_SN", "PE_SN", "FPGA_VER", "DCDC_VER", "INV_VER", "BMS_SN", "BMS_VER", "BL_VER", "TH_VER"]


def login(profile, units=1, dev=1):
    g = make_gateway(None, seed=7, dc_port=0, profile=profile, site={"apower_units": units})
    with g:
        c = LocalClient(*g.dc_address, timeout=2, retries=0)
        c.transport.connect()
        m = c.login()
        d = c.device_firmware(dev)
        c.transport.close()
    return g, m, d


def test_v12_b60_reply_matches_the_recorded_one():
    g, m, d = login("agate-v12r02b60d00-candidate")
    assert list(m) == V12_B60_KEYS
    assert (m["result"], m["reason"]) == (1, -2)          # as recorded, with the full manifest (#80)
    assert (m["IBG_VER"], m["protocolVer"], m["AWS_VER"], m["APP_VER"], m["SL_VER"], m["METER_VER"]) == (
        "V12R02B60D00_260924", "V1.12.02", "V12R00B15D00_260918", "V12R00B16D00_260825", "V12R02B12D00",
        "V12R00B11D00")
    assert (m["FPGA_VER"], m["DCDC_VER"], m["INV_VER"], m["BMS_VER"], m["BL_VER"], m["TH_VER"]) == (
        ["V12R01B00D00"], ["V12R02B10D00"], ["V12R02B15D00"], ["V11R50B07D00"], ["V11R06B00D00"],
        ["V11R02B01D00"])
    assert (m["PE_HW_VER"], m["BMS_MAIN_HW_VER"], m["deviceType"], m["threePhasePvPower"]) == ([2], [0], 0, 0)
    assert m["FHP_NUM"] == 1 and m["SyHdVersion"] == 102 and m["IBG_SN"] == g.serial
    assert len(m["FHP_SN"][0]) == 20 and len(m["PE_SN"][0]) == 22 and len(m["BMS_SN"][0]) == 22
    assert "SUNSPEC_FW" not in m
    assert (d["ibg_ver"], d["ibg_iot"], d["ibg_local"], d["bms_ver"]) == (
        "V12R02B60D00_260924", "V12R00B15D00_260918", "V12R00B16D00_260825", "V11R50B07D00")
    assert (d["pe_sn"], d["bms_sn"]) == (m["PE_SN"][0], m["BMS_SN"][0])


def test_default_profile_is_the_2026_10_07_capture():
    _, m, d = login("agate-v10r01b04d00")
    assert (m["IBG_VER"], m["protocolVer"], m["APP_VER"], m["AWS_VER"], m["SL_VER"], m["METER_VER"]) == (
        "V12R02B30D06_260304", "V1.11.03", "V12R00B07D04_260205", "V12R00B06D04_260304", "V12R02B07D81",
        "V12R00B11D00")
    assert (m["FPGA_VER"], m["DCDC_VER"], m["INV_VER"], m["BMS_VER"], m["BL_VER"], m["TH_VER"]) == (
        ["V12R01B00D00"], ["V12R02B07D01"], ["V12R02B09D03"], ["V11R50B03D00"], ["V11R06B00D00"],
        ["V11R02B01D00"])
    assert (m["result"], m["reason"]) == (0, 0)            # no record for this firmware
    for k in ("deviceType", "threePhasePvPower", "PE_HW_VER"):     # first recorded on V12R02B60D00
        assert k not in m
    assert (d["ibg_ver"], d["ibg_iot"], d["ibg_local"]) == (
        "V12R02B30D06_260304", "V12R00B06D04_260304", "V12R00B07D04_260205")


def test_one_entry_per_apower():
    """Inferred: no multi-aPower reply has been seen."""
    _, m, d = login("agate-v12r02b60d00-candidate", units=3, dev=3)
    assert m["FHP_NUM"] == 3
    for k in LISTS + ["PE_HW_VER", "BMS_MAIN_HW_VER", "BMS_BL_HW_VER", "BMS_TH_HW_VER"]:
        assert len(m[k]) == 3, k
    assert len(set(m["FHP_SN"])) == 3 and d["fhp_sn"] == m["FHP_SN"][2]


def test_unknown_dc_firmware_refused():
    with pytest.raises(ValueError, match="dc_firmware 'V9': no Direct Connect versions"):
        get_profile("agate-v10r01b04d00", dc_firmware="V9")
