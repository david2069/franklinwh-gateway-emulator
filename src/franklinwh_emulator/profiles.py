"""Quirk profiles: how faithfully to copy the real aGate's Modbus behaviour.

``agate-v10r01b04d00`` reproduces what franklinwh-modbus documents for firmware
V10R01B04D00 (FRANKLINWH_SUNSPEC_QUIRKS.md, modbus_capability.json, the PICS
cross-reference). ``spec`` is a well-behaved SunSpec DER for comparison testing.
Every field can also be overridden per gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Profile:
    name: str
    baseline: str = "agate_v10r01b04d00"
    firmware: str = "V10R01B04D00"
    model_name: str = "aGate X"
    # The aGate (IBG) firmware Direct Connect reports: picks the 1102/1834 versions (DC_FIRMWARE).
    dc_firmware: str = "V12R02B30D06_260304"

    # -- session -------------------------------------------------------------
    max_connections: int | None = 1          # the aGate serves one client at a time
    busy_behaviour: str = "close"            # extra client: "close" | "hang"
    # Also answer the SunSpec chain at these bases (0 is always served).
    base_aliases: tuple[int, ...] = ()
    # Bases that accept SunSpec writes (None = every served base). Elsewhere: exception 2.
    write_bases: tuple[int, ...] | None = None
    # A write to an alias base is answered with the base-0 address (FC06/FC16 echo).
    write_echo_base0: bool = False

    # -- M704 sign conventions -------------------------------------------------
    wsetpct_positive_discharge: bool = True  # hardware: +WSetPct = discharge
    # +WSet = discharge: measured on V12R02B60D00 (franklinwh-modbus firmware_quirks
    # m704_wset_sign) and SunSpec's sign. Not measured on V10R01B04D00; the same is assumed.
    wset_positive_charge: bool = False

    # -- writes accepted at the protocol level but silently discarded ---------
    discard_controller_hb: bool = True       # 715.ControllerHb reads back 0
    discard_m702_settings: bool = True       # WChaRteMax etc. stay 0xFFFF
    discard_wmaxlimpct_ena: bool = True
    discard_varset_ena: bool = True
    locremctl_fixed_local: bool = True       # 715.LocRemCtl always 1, read-only
    readonly_write: str = "discard"          # write to an R point: "discard" | "exception"

    # -- reversion ---------------------------------------------------------------
    reversion_effective: bool = False        # WSetRvrtTms countdown is cosmetic (PICS Issue 4)

    # -- read-side quirks --------------------------------------------------------
    tou_reserve_mirrors_self: bool = True    # 15509 always reads 15508
    m713_sta_zero: bool = True               # 713.Sta always 0
    m714_dca_zero: bool = True               # 714.DCA always 0
    m714_port_zero: bool = True              # 714.Prt[0] all 0 ("ID_String"), not mirrored
    throtpct_unimplemented: bool = True      # 701.ThrotPct reads unimplemented
    m702_wmaxrtg: int | None = 0             # observed 0 (sometimes 1000)
    quantize_w: int = 100                    # 502.OutPw / 15506 / 714.DCW coarse steps
    soc_whole_percent: bool = True           # 713.SoC moves in 1 % steps (raw ×10, so 550, 560…)
    unused_phase_registers: str = "zero"     # 701 points of unwired legs: "zero" | "unimplemented"
    islanded_15016: int | None = None        # 15016 while islanded; None = stays static
    reserve_16001: str = "self"              # "self" reserve | the "active" mode's reserve
    wsetpct_max_abs_raw: int | None = None   # 704.WSetPct writes beyond ±this raw: exception 3
    refuse_charge: bool = False              # negative 704.WSet / WSetPct writes: exception 3

    # -- Direct Connect ----------------------------------------------------------
    dc_pload_dup_bug: bool = True            # 1304 p_load repeats p_fhp (DEF-1303-PLOAD-DUP)


PROFILES: dict[str, Profile] = {
    "agate-v10r01b04d00": Profile(name="agate-v10r01b04d00"),
    # Candidate: one site. Every firmware-specific setting below is measured for V12R02B60D00
    # in franklinwh-modbus firmware_quirks.json (see QUIRK_BASIS). Not the default.
    # 15016 = 1 during the 2026-10-21 episodes is only "reported" (ext_15016_status), so it
    # is opt-in: quirks={"islanded_15016": 1}.
    "agate-v12r02b60d00-candidate": Profile(
        name="agate-v12r02b60d00-candidate",
        firmware="V12R02B60D00",
        dc_firmware="V12R02B60D00_260924",
        base_aliases=(40000,),           # sunspec_map_at_pdu_40000; both bases accept writes
        write_echo_base0=True,           # pdu_40000_write_response_address: 40319 -> 319
        tou_reserve_mirrors_self=False,  # ext_15509_tou_reserve: 15509 holds the TOU reserve
        # ext_16001_reserve: the active mode's reserve, 100 in Emergency Backup
        # (franklinwh-modbus#63; EB read in franklinwh-modbus-async#16 G2).
        reserve_16001="active",
        wsetpct_max_abs_raw=100,         # m704_wsetpct_accepted_range: raw 0..+100 accepted
        refuse_charge=True,              # m704_wset_negative_refused, m704_wsetpct_accepted_range
    ),
    "spec": Profile(
        name="spec",
        max_connections=None,
        discard_controller_hb=False,
        discard_m702_settings=False,
        discard_wmaxlimpct_ena=False,
        discard_varset_ena=False,
        locremctl_fixed_local=False,
        readonly_write="exception",
        reversion_effective=True,
        tou_reserve_mirrors_self=False,
        m713_sta_zero=False,
        m714_dca_zero=False,
        m714_port_zero=False,
        throtpct_unimplemented=False,
        m702_wmaxrtg=5000,
        quantize_w=1,
        soc_whole_percent=False,
        unused_phase_registers="unimplemented",
        dc_pload_dup_bug=False,
    ),
}
DEFAULT_PROFILE = "agate-v10r01b04d00"

# Direct Connect 1102 login fields per aGate (IBG) firmware, as measured on the reference
# aGate (one site, one aPower). ``per_apower`` keys are lists with one entry per aPower:
# the list shape is measured, one entry per unit is inferred (no multi-aPower reply seen).
# ``extra`` holds keys recorded only on that firmware. 1834 uses ibg_iot = AWS_VER and
# ibg_local = APP_VER, as in the 2026-10-07 capture.
DC_FIRMWARE: dict[str, dict] = {
    # Reference capture 2026-10-07 (docs/CALIBRATION.md firmware table): values only; the
    # list shape is assumed from V12R02B60D00.
    "V12R02B30D06_260304": {
        "protocolVer": "V1.11.03", "AWS_VER": "V12R00B06D04_260304", "APP_VER": "V12R00B07D04_260205",
        "SL_VER": "V12R02B07D81", "METER_VER": "V12R00B11D00", "SyHdVersion": 102,
        "per_apower": {"FPGA_VER": "V12R01B00D00", "DCDC_VER": "V12R02B07D01", "INV_VER": "V12R02B09D03",
                       "BMS_VER": "V11R50B03D00", "BL_VER": "V11R06B00D00", "TH_VER": "V11R02B01D00"},
        "extra": {},
    },
    # The full 1102 payload in fwh-direct-connect-bridge's firmware sweep (read
    # ~2026-10-20T20:1xZ, after the update), relayed 2026-10-31 (#78).
    "V12R02B60D00_260924": {
        "protocolVer": "V1.12.02", "AWS_VER": "V12R00B15D00_260918", "APP_VER": "V12R00B16D00_260825",
        "SL_VER": "V12R02B12D00", "METER_VER": "V12R00B11D00", "SyHdVersion": 102,
        "per_apower": {"FPGA_VER": "V12R01B00D00", "DCDC_VER": "V12R02B10D00", "INV_VER": "V12R02B15D00",
                       "BMS_VER": "V11R50B07D00", "BL_VER": "V11R06B00D00", "TH_VER": "V11R02B01D00"},
        "extra": {"deviceType": 0, "threePhasePvPower": 0, "PE_HW_VER": [2], "BMS_MAIN_HW_VER": [0],
                  "BMS_BL_HW_VER": [0], "BMS_TH_HW_VER": [0]},
    },
}

# Profile fields that assert firmware behaviour: the value at which a field asserts nothing
# (ALWAYS = every value asserts something) and the franklinwh-modbus firmware_quirks.json
# behaviours that must back it. tests/test_quirks_drift.py checks every agate-* profile.
ALWAYS = object()
QUIRK_BASIS: dict[str, tuple[object, tuple[str, ...]]] = {
    "base_aliases": ((), ("sunspec_map_at_pdu_40000",)),
    "write_echo_base0": (False, ("pdu_40000_write_response_address",)),
    "wsetpct_positive_discharge": (ALWAYS, ("m704_wsetpct_sign",)),
    "wset_positive_charge": (ALWAYS, ("m704_wset_sign",)),
    "tou_reserve_mirrors_self": (ALWAYS, ("ext_15509_tou_reserve",)),
    "reserve_16001": (ALWAYS, ("ext_16001_reserve",)),
    "islanded_15016": (None, ("ext_15016_status",)),
    "wsetpct_max_abs_raw": (None, ("m704_wsetpct_accepted_range",)),
    "refuse_charge": (False, ("m704_wset_negative_refused", "m704_wsetpct_accepted_range")),
}

# Settings a profile asserts without a measurement in the file, and why.
QUIRK_ASSUMED: dict[tuple[str, str], str] = {
    ("agate-v10r01b04d00", "wset_positive_charge"):
        "no V10 measurement; SunSpec's sign, as measured on V12",
    ("agate-v10r01b04d00", "wsetpct_positive_discharge"):
        "franklinwh-modbus FRANKLINWH_SUNSPEC_QUIRKS.md (V10 era); not in firmware_quirks.json",
}


def get_profile(name: str = DEFAULT_PROFILE, **overrides) -> Profile:
    try:
        base = PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown profile {name!r}; choose from {sorted(PROFILES)}") from None
    known = {f.name for f in fields(Profile)}
    bad = set(overrides) - known
    if bad:
        raise ValueError(f"unknown profile overrides: {sorted(bad)}")
    if "base_aliases" in overrides:
        overrides["base_aliases"] = tuple(overrides["base_aliases"])
    if overrides.get("write_bases") is not None:
        overrides["write_bases"] = tuple(overrides["write_bases"])
    profile = replace(base, **overrides)
    if profile.dc_firmware not in DC_FIRMWARE:
        raise ValueError(f"dc_firmware {profile.dc_firmware!r}: no Direct Connect versions for it; "
                         f"known {sorted(DC_FIRMWARE)}")
    return profile
