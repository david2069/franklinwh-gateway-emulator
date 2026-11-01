"""FranklinWH hardware models and what each can do (#180).

A gateway is one aGate model and one aPower model (all its aPowers alike). Each is keyed by the
numeric code FranklinWH's APIs report: the aGate's cloud ``sysHdVersion`` = Direct Connect 1102
``SyHdVersion`` (100-104), and the aPower's cloud ``peHwVersion`` (0-7). They are two separate key
spaces. SKUs are labels only: FranklinWH revises them, so one model can carry more than one
(franklinwh-cloud DEF-CATALOG-SKU-NOT-A-STABLE-KEY).

``model:`` in the fleet file::

    model: {agate: 102, apower: 2}          # codes
    model: {agate: AGT-R1V1-AU}             # an SKU or name resolves to its code

Without it, the region decides: AU/NZ → the reference site's 102 + 2; North America (US, CA) →
103 + 0. The region comes from ``location.country``, else from the grid code's frequency.

Sources (docs/sources-and-regions.md): codes, SKUs and names from franklinwh-cloud
``const/device_catalog.json`` 1.1.0 via fwhai's device catalogue seed, all **inferred** except
aGate 102 (measured on the reference aGate; aPower code 2 matches its Direct Connect
``PE_HW_VER`` [2], and that this field indexes the table is itself inferred). Capacity, power,
MPPT and AC solar limits are FranklinWH datasheet values (aPower comparison table and technical
specifications, 2026-05-02, via fwhai). Charge capacity and peak: the PV Curtailment White
Paper (North America, 2025-05-14), aPower X and aPower 2 only.
"""

from __future__ import annotations

from dataclasses import dataclass

NA, AUNZ = "NA", "AU/NZ"
REGIONS = (NA, AUNZ)
REGION_OF_COUNTRY = {"US": NA, "CA": NA, "AU": AUNZ, "NZ": AUNZ}


@dataclass(frozen=True)
class AGateModel:
    code: int                     # sysHdVersion / SyHdVersion
    skus: tuple[str, ...]
    name: str
    family: str
    region: str
    hz: int
    max_service_a: int
    generation: int | None
    status: str                   # "measured" | "inferred"
    note: str = ""
    # Smart circuits with a relay and a meter (#189). AU: 2, per the AU system datasheet and the
    # reference aGate's 1412 meter block (SW1, SW2 only; fwh-local capture, V12R02B30D06). NA: 3,
    # unchanged (no evidence either way).
    smart_circuits: int = 3
    # The Generator Module's maximum continuous current, where documented (#189). AU/NZ: 80 A,
    # FranklinWH Generator Module Datasheet (AU & NZ), 2026-06-24. None = no documented limit.
    gen_module_max_a: int | None = None


@dataclass(frozen=True)
class APowerModel:
    code: int                     # peHwVersion
    skus: tuple[str, ...]
    name: str
    family: str                   # aPower X | aPower 2 | aPower S
    region: str
    hz: int
    kwh: float                    # rated capacity
    kw: float                     # continuous (nominal) output
    peak_kw: float                # peak output (datasheet)
    charge_kw: float | None       # charging capacity; None = not documented (use ``kw``)
    peak_charge: tuple[float, float] | None   # (kW, seconds) above charge_kw; None = not documented
    mppt_count: int               # DC PV inputs (aPower S)
    mppt_max_kw: float | None
    ac_solar_max_kw: float | None
    status: str                   # "inferred": the code; specs are datasheet values
    note: str = ""

    @property
    def charge_w(self) -> float:
        return (self.charge_kw if self.charge_kw is not None else self.kw) * 1000


AGATES: dict[int, AGateModel] = {m.code: m for m in (
    AGateModel(100, ("AGT-R1V1-US",), "aGate X-10", "aGate X 1.1", NA, 60, 200, 1, "inferred",
               "discontinued; no aPower S"),
    AGateModel(101, ("AGT-R1V2-US",), "aGate X-20", "aGate X 1.3", NA, 60, 200, 2, "inferred",
               "discontinued; supports aPower S"),
    AGateModel(102, ("AGT-R1V1-AU",), "aGate X-01-AU", "aGate X", AUNZ, 50, 100, 1, "measured",
               "the reference aGate (realSysHdVersion FHP1.2)", smart_circuits=2,
               gen_module_max_a=80),
    AGateModel(103, ("AGT-R1V3-US",), "aGate X 20 (US)", "aGate X 1.3.1", NA, 60, 200, 2, "inferred"),
    AGateModel(104, ("AGT-R1V3-US",), "aGate X 20 (US)", "aGate X 1.3.1", NA, 60, 200, 2, "inferred",
               "a hardware revision of 103"),
)}

# aPower X and aPower 2 charge figures: PV Curtailment White Paper, Table 1 (North America).
_X_CHARGE, _X_PEAK = 5.0, (7.6, 1800.0)
_TWO_CHARGE, _TWO_PEAK = 8.0, (13.0, 30.0)

APOWERS: dict[int, APowerModel] = {m.code: m for m in (
    APowerModel(0, ("APR-05K1V1-US",), "aPower X-10", "aPower X", NA, 60, 13.6, 5.0, 10.0,
                _X_CHARGE, _X_PEAK, 0, None, 5.0, "inferred"),
    APowerModel(1, ("APR-05K11V1-US",), "aPower X-10", "aPower X", NA, 60, 13.6, 5.0, 10.0,
                _X_CHARGE, _X_PEAK, 0, None, 5.0, "inferred", "hardware revision V11"),
    APowerModel(2, ("APR-05K13V1-AU",), "aPower X-01-AU", "aPower X", AUNZ, 50, 13.6, 5.0, 10.0,
                None, None, 0, None, 5.0, "inferred", "matches the reference site's PE_HW_VER [2]"),
    APowerModel(3, ("APR-10K15V2-US",), "aPower X-20", "aPower 2", NA, 60, 15.0, 10.0, 15.0,
                _TWO_CHARGE, _TWO_PEAK, 0, None, 10.0, "inferred"),
    APowerModel(4, ("APRS-10K15V1-US",), "aPower S-10", "aPower S", NA, 60, 15.0, 11.5, 15.0,
                None, None, 4, 15.0, 5.0, "inferred", "DC hybrid: 4 MPPT inputs"),
    APowerModel(5, ("APRS-11K15V2-US",), "aPower S-10", "aPower S", NA, 60, 15.0, 11.5, 15.0,
                None, None, 4, 15.0, 5.0, "inferred", "aPower S V2"),
    APowerModel(6, ("APR-05K15V1-US",), "aPower X-10", "aPower X", NA, 60, 13.6, 5.0, 10.0,
                _X_CHARGE, _X_PEAK, 0, None, 5.0, "inferred", "hardware revision V15"),
    # FranklinWH System Datasheet aGate X-01-AU & aPower X-02-AU (V1.7, 2026-09-10): SKU
    # APR-05K15V1-AU, 15 kWh usable (16 kWh rated, 93.75 % DoD), 5 kW / 5 kVA, peak 10 kW for
    # 10 s, 89 % round trip. The catalogue's SKU for code 7 is APR-05K13V2-AU (SKUs drift); the
    # code itself is still inferred.
    APowerModel(7, ("APR-05K15V1-AU", "APR-05K13V2-AU"), "aPower X-02-AU", "aPower X", AUNZ, 50,
                15.0, 5.0, 10.0, None, None, 0, None, 5.0, "inferred",
                "specs documented (AU datasheet V1.7); peak 10 kW for 10 s"),
)}

DEFAULTS = {AUNZ: (102, 2), NA: (103, 0)}


def _resolve(table: dict, kind: str, value) -> int:
    if isinstance(value, bool):
        raise ValueError(f"model.{kind}: a code, SKU or name, not {value!r}")
    if isinstance(value, int):
        if value in table:
            return value
    elif isinstance(value, str):
        v = value.strip()
        if v.isdigit() and int(v) in table:
            return int(v)
        hits = sorted({m.code for m in table.values() if v in m.skus or v == m.name})
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError(f"model.{kind} {value!r} names codes {hits}; give the code")
    known = ", ".join(f"{m.code} {m.name}" for m in table.values())
    raise ValueError(f"model.{kind}: unknown {value!r}; known: {known}")


@dataclass(frozen=True)
class Model:
    agate: AGateModel
    apower: APowerModel

    @property
    def region(self) -> str:
        return self.agate.region


# Accessory modules a gateway has fitted (#189). Direct Connect does not report them: a missing
# module's keys are still sent, zeroed (fwh-local capture, V12R02B30D06), so they are set here.
# Default: all fitted, as before #189.
ACCESSORIES = ("smart_circuits", "generator_module", "apbox")


def accessories(cfg: dict | None) -> dict[str, bool]:
    """``accessories:`` → {name: fitted}; a name left out is fitted."""
    cfg = dict(cfg or {})
    bad = set(cfg) - set(ACCESSORIES)
    if bad:
        raise ValueError(f"accessories: unknown keys {sorted(bad)}; known {list(ACCESSORIES)}")
    for k, v in cfg.items():
        if not isinstance(v, bool):
            raise ValueError(f"accessories.{k}: true or false, not {v!r}")
    return {k: cfg.get(k, True) for k in ACCESSORIES}


def resolve(cfg: dict | None, *, country: str = "", hz: float = 50.0) -> Model:
    """The gateway's models from ``model:`` (codes, SKUs or names), else its region's defaults."""
    cfg = dict(cfg or {})
    bad = set(cfg) - {"agate", "apower"}
    if bad:
        raise ValueError(f"model: unknown keys {sorted(bad)}; known ['agate', 'apower']")
    region = REGION_OF_COUNTRY.get(country.upper()) or (NA if round(hz) == 60 else AUNZ)
    agate = AGATES[_resolve(AGATES, "agate", cfg["agate"])] if "agate" in cfg else AGATES[DEFAULTS[region][0]]
    if "apower" in cfg:
        apower = APOWERS[_resolve(APOWERS, "apower", cfg["apower"])]
    else:                                  # the aGate's region decides when only it is given
        apower = APOWERS[DEFAULTS[agate.region][1]]
    if apower.region != agate.region:
        raise ValueError(f"model: {apower.name} ({apower.region}) does not go with "
                         f"{agate.name} ({agate.region})")
    if apower.family == "aPower S" and agate.code in (100,):
        raise ValueError(f"model: {agate.name} does not support aPower S "
                         "(FranklinWH Backwards Compatibility Statement)")
    if round(hz) != agate.hz:
        raise ValueError(f"model: {agate.name} is a {agate.hz} Hz model; this site's grid code is "
                         f"{hz:g} Hz")
    return Model(agate, apower)


def describe(m: Model, units: int) -> dict:
    """The models as the control API and page show them."""
    a, p = m.agate, m.apower
    return {"region": m.region,
            "agate": {"code": a.code, "name": a.name, "family": a.family, "skus": list(a.skus),
                      "status": a.status, "smart_circuits": a.smart_circuits},
            "apower": {"code": p.code, "name": p.name, "family": p.family, "skus": list(p.skus),
                       "status": p.status, "units": units, "kwh": p.kwh, "kw": p.kw,
                       "peak_kw": p.peak_kw, "charge_kw": p.charge_kw, "mppt_count": p.mppt_count}}
