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

    # -- session -------------------------------------------------------------
    max_connections: int | None = 1          # the aGate serves one client at a time
    busy_behaviour: str = "close"            # extra client: "close" | "hang"
    # Also answer the SunSpec chain at these bases (0 is always served).
    base_aliases: tuple[int, ...] = ()
    # Bases that accept SunSpec writes (None = every served base). Elsewhere: exception 2.
    write_bases: tuple[int, ...] | None = None

    # -- M704 sign conventions -------------------------------------------------
    wsetpct_positive_discharge: bool = True  # hardware: +WSetPct = discharge
    wset_positive_charge: bool = True        # hardware: +WSet = charge

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

    # -- Direct Connect ----------------------------------------------------------
    dc_pload_dup_bug: bool = True            # 1304 p_load repeats p_fhp (DEF-1303-PLOAD-DUP)


PROFILES: dict[str, Profile] = {
    "agate-v10r01b04d00": Profile(name="agate-v10r01b04d00"),
    # Candidate: one site, read-only, 2026-10-21. 15016 read 1 while islanded and 2 on-grid
    # in both off-grid episodes (Modbus bridge register watch). Not the default.
    "agate-v12r02b60d00-candidate": Profile(
        name="agate-v12r02b60d00-candidate",
        firmware="V12R02B60D00",
        islanded_15016=1,
        # The SunSpec map is mirrored at 40000 (read-only re-scan 2026-10-20, one site;
        # franklinwh-modbus docs/SUNSPEC_SNAPSHOT_2026-10-20_V12R02B60D00.md). Writes there
        # are unverified on hardware, so both bases accept them here.
        base_aliases=(40000,),
        # Read with the app in each mode, 2026-10-21, one site (franklinwh-modbus #63):
        # 15509 holds the TOU reserve (not a copy of 15508), and 16001 the active mode's
        # reserve (100 Emergency Backup, 8 Self-Consumption, 15 TOU).
        tou_reserve_mirrors_self=False,
        reserve_16001="active",
        # No WSetPct limit: V12's is not concluded (franklinwh-modbus test plan pending). Each
        # value measured once, 2026-10-21, discharge side: raw 100 latched, 110/200 didn't,
        # 500 refused with exception 3. Opt in with quirks={"wsetpct_max_abs_raw": 100}.
    ),
    "spec": Profile(
        name="spec",
        max_connections=None,
        wset_positive_charge=False,
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
    return replace(base, **overrides)
