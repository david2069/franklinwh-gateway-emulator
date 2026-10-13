"""Grid-connection codes: protection trip settings, enter-service window, and the
grid-support responses (frequency-watt, volt-watt, volt-var, export limit) a
grid-following DER applies.

Voltages are per unit of the code's nominal line-to-neutral voltage, so the same code
applies to single, split and three phase sites. Frequencies are absolute Hz.

* ``as4777-au-a`` — AS/NZS 4777.2:2020 region Australia A, the region for Ausgrid,
  Endeavour, Essential, Energex, Ergon, Evoenergy, Jemena, CitiPower, Powercor, United,
  AusNet and SA Power Networks. Values as published in Jemena's "Inverter Energy System
  Embedded Generators Protection Requirements and Settings Summary" (effective
  18 Dec 2021), which reproduces the standard's Australia A tables. Its enter-service
  window (205–253 V, 47.5–50.15 Hz, 60 s) is exactly what the reference aGate (Sydney)
  reports in model 703.
* ``ausgrid`` — Australia A plus Ausgrid export limits: 10 kW on a single-phase supply
  (NS194; confirmed by the project owner for Sydney), and on multi-phase supplies the
  basic micro-EG 5 kW per phase (unconfirmed for Ausgrid). Override from the actual
  connection offer.
* ``ieee1547-cat2`` / ``ieee1547-cat3`` — IEEE 1547-2018 default settings for
  abnormal-performance Category II / III (the US basis of UL 1741 SB; California
  Rule 21 and Hawaii build on Category III ride-through). From memory of the
  standard's default tables, not checked against its text.

Response *timing* (:class:`ResponseTiming`): each grid-support function responds after a
dead time and settles as a first-order lag. The AU codes use the AS/NZS 4777.2 envelope
(commence within 1 s; frequency/volt responses complete within 10 s; 0.1 Hz frequency
hysteresis) and AS/NZS 4777.1's 15 s for a soft export limit. The IEEE codes use 1547-2018's
default open-loop response times (frequency droop 5 s, volt-var 5 s, volt-watt 10 s).
Reconnection ramps linearly over ``es_ramp_s`` (703 ``ESRmpTms``; 360 s on the reference
aGate). The emulator takes the *slowest compliant* response, so tests exercise the worst
case. ``response=None`` restores instant responses.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TripStage:
    name: str          # e.g. "V<", "OV2"
    kind: str          # "v_lo" | "v_hi" | "hz_lo" | "hz_hi"
    threshold: float   # pu of nominal (voltage) or Hz (frequency)
    delay_s: float     # time the condition must persist before the relay opens


@dataclass(frozen=True)
class FreqWatt:
    """Frequency response breakpoints (AS/NZS 4777.2 naming), Hz.

    Over-frequency: generation falls from 100 % at ``f_ulco`` to 0 at ``f_pmin``; storage
    discharge falls to 0 at ``f_transition`` and then charging rises to rated at ``f_pmin``.
    Under-frequency: storage charging falls to 0 at ``f_stop_ch`` and discharge then rises
    to rated at ``f_pmax``.
    """
    f_llco: float
    f_stop_ch: float
    f_pmax: float
    f_ulco: float
    f_transition: float
    f_pmin: float


@dataclass(frozen=True)
class ResponseTiming:
    """How fast grid-support functions act. ``*_settle_s`` is the time to ``settle_frac`` of
    the step after the dead time ``start_s`` (first-order lag: tau = settle / -ln(1-frac))."""
    start_s: float = 1.0               # dead time before any response begins
    freq_settle_s: float = 10.0        # frequency-watt (storage and PV)
    volt_settle_s: float = 10.0        # volt-watt (generation and charging)
    var_settle_s: float = 10.0         # volt-var
    export_settle_s: float = 15.0      # soft export limit (grid code or CSIP opModExpLimW)
    settle_frac: float = 0.95          # AU "complete within"; IEEE OLRT is 0.90
    hysteresis_hz: float = 0.1         # frequency backlash before a response is released


AS4777_TIMING = ResponseTiming()
IEEE1547_TIMING = ResponseTiming(start_s=0.2, freq_settle_s=5.0, volt_settle_s=10.0,
                                 var_settle_s=5.0, export_settle_s=15.0, settle_frac=0.90,
                                 hysteresis_hz=0.0)
# What the reference aGate *declares* in its curve registers (scan 2026-10-07): 705 volt-var
# RspTms 0 s, 706 volt-watt and 711 freq-droop RspTms 0.1 s. A declared open-loop time,
# not a measured response; the AS4777 envelope stays the default.
AGATE_DECLARED_TIMING = ResponseTiming(start_s=0.0, freq_settle_s=0.1, volt_settle_s=0.1,
                                       var_settle_s=0.0, export_settle_s=15.0,
                                       settle_frac=0.90, hysteresis_hz=0.1)
RESPONSE_PRESETS = {"as4777-envelope": AS4777_TIMING, "ieee1547": IEEE1547_TIMING,
                    "agate-declared": AGATE_DECLARED_TIMING}


Curve = tuple[tuple[float, float], ...]   # ((v_pu, value), ...) ascending in v


@dataclass(frozen=True)
class GridCode:
    name: str
    description: str
    nominal_v_ln: float               # volts line-to-neutral
    nominal_hz: float
    typical_v_pu: float               # healthy-grid voltage the emulator starts at
    typical_hz: float
    trips: tuple[TripStage, ...]
    es_v_pu: tuple[float, float]      # enter-service window
    es_hz: tuple[float, float]
    es_delay_s: float
    es_ramp_s: float
    freq_watt: FreqWatt | None
    volt_watt: Curve | None = None          # max generation, fraction of rated
    volt_watt_charge: Curve | None = None   # max charging, fraction of rated
    volt_var: Curve | None = None           # reactive power, fraction of S rated; + = supplying
    sustained_ov: tuple[float, float] | None = None   # (threshold pu, window s)
    export_limit_per_phase_w: float | None = None
    export_limit_single_phase_w: float | None = None   # overrides per-phase on 1-leg sites
    response: ResponseTiming | None = AS4777_TIMING     # None = instant (no dynamics)

    def export_limit_w(self, legs: int) -> float | None:
        """Site export limit for a supply with ``legs`` phases (None = unlimited)."""
        if legs == 1 and self.export_limit_single_phase_w is not None:
            return self.export_limit_single_phase_w
        if self.export_limit_per_phase_w is None:
            return None
        return self.export_limit_per_phase_w * legs

    def es_volts(self) -> tuple[float, float]:
        return self.es_v_pu[0] * self.nominal_v_ln, self.es_v_pu[1] * self.nominal_v_ln


def interpolate(curve: Curve, x: float) -> float:
    """Piecewise-linear lookup, flat beyond the end points."""
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return curve[-1][1]


_AU_V = 230.0


def _au(v: float) -> float:
    return v / _AU_V


GRID_CODES: dict[str, GridCode] = {
    "as4777-au-a": GridCode(
        name="as4777-au-a",
        description="AS/NZS 4777.2:2020 Australia A",
        nominal_v_ln=_AU_V, nominal_hz=50.0,
        typical_v_pu=_au(241.8), typical_hz=49.99,       # the reference aGate's reading
        trips=(
            TripStage("V<<", "v_lo", _au(70), 1.0),
            TripStage("V<", "v_lo", _au(180), 10.0),
            TripStage("V>", "v_hi", _au(265), 1.0),
            TripStage("V>>", "v_hi", _au(275), 0.2),     # max disconnection time
            TripStage("F<", "hz_lo", 47.0, 1.0),
            TripStage("F>", "hz_hi", 52.0, 0.2),         # max disconnection time
        ),
        es_v_pu=(_au(205), _au(253)), es_hz=(47.5, 50.15),
        es_delay_s=60.0, es_ramp_s=360.0,
        freq_watt=FreqWatt(f_llco=49.75, f_stop_ch=49.0, f_pmax=48.0,
                           f_ulco=50.25, f_transition=50.75, f_pmin=52.0),
        volt_watt=((_au(253), 1.0), (_au(260), 0.2)),
        volt_watt_charge=((_au(207), 0.2), (_au(215), 1.0)),
        volt_var=((_au(207), 0.44), (_au(220), 0.0), (_au(240), 0.0), (_au(258), -0.60)),
        sustained_ov=(_au(258), 600.0),
    ),
    "ieee1547-cat2": GridCode(
        name="ieee1547-cat2",
        description="IEEE 1547-2018 Category II defaults (60 Hz)",
        nominal_v_ln=120.0, nominal_hz=60.0, typical_v_pu=1.0, typical_hz=60.0,
        trips=(
            TripStage("UV2", "v_lo", 0.45, 0.16),
            TripStage("UV1", "v_lo", 0.70, 10.0),
            TripStage("OV1", "v_hi", 1.10, 2.0),
            TripStage("OV2", "v_hi", 1.20, 0.16),
            TripStage("UF2", "hz_lo", 56.5, 0.16),
            TripStage("UF1", "hz_lo", 58.5, 300.0),
            TripStage("OF1", "hz_hi", 61.2, 300.0),
            TripStage("OF2", "hz_hi", 62.0, 0.16),
        ),
        es_v_pu=(0.917, 1.05), es_hz=(59.5, 60.1), es_delay_s=300.0, es_ramp_s=300.0,
        # 0.036 Hz deadband, 5 % droop: 1 pu of power per 3 Hz, through zero for storage.
        freq_watt=FreqWatt(f_llco=59.964, f_stop_ch=56.964, f_pmax=53.964,
                           f_ulco=60.036, f_transition=63.036, f_pmin=66.036),
        # 1547 requires volt-var/volt-watt capability but ships constant unity PF by default.
        response=IEEE1547_TIMING,
    ),
}
GRID_CODES["ausgrid"] = replace(
    GRID_CODES["as4777-au-a"],
    name="ausgrid",
    description="Ausgrid (NSW): AS/NZS 4777.2:2020 Australia A; export 10 kW single phase, "
                "5 kW/phase multi-phase",
    export_limit_per_phase_w=5000.0,
    export_limit_single_phase_w=10000.0,
)
GRID_CODES["ieee1547-cat3"] = replace(
    GRID_CODES["ieee1547-cat2"],
    name="ieee1547-cat3",
    description="IEEE 1547-2018 Category III defaults (60 Hz; basis of Rule 21 ride-through)",
    trips=(
        TripStage("UV2", "v_lo", 0.50, 2.0),
        TripStage("UV1", "v_lo", 0.88, 21.0),
        TripStage("OV1", "v_hi", 1.10, 13.0),
        TripStage("OV2", "v_hi", 1.20, 0.16),
        TripStage("UF2", "hz_lo", 56.5, 0.16),
        TripStage("UF1", "hz_lo", 58.5, 300.0),
        TripStage("OF1", "hz_hi", 61.2, 300.0),
        TripStage("OF2", "hz_hi", 62.0, 0.16),
    ),
)


def default_grid_code(ac_type: int) -> str:
    """Split phase is the US aGate; everything else defaults to Australia A."""
    return "ieee1547-cat2" if ac_type == 1 else "as4777-au-a"


def get_grid_code(name: str, **overrides) -> GridCode:
    try:
        code = GRID_CODES[name]
    except KeyError:
        raise ValueError(f"unknown grid code {name!r}; choose from {sorted(GRID_CODES)}") from None
    if overrides:
        if "trips" in overrides:
            overrides["trips"] = tuple(t if isinstance(t, TripStage) else TripStage(**t)
                                       for t in overrides["trips"])
        if isinstance(overrides.get("response"), str):
            try:
                overrides["response"] = RESPONSE_PRESETS[overrides["response"]]
            except KeyError:
                raise ValueError(f"response preset {overrides['response']!r}: choose from "
                                 f"{sorted(RESPONSE_PRESETS)}") from None
        if isinstance(overrides.get("response"), dict):
            overrides["response"] = replace(code.response or ResponseTiming(),
                                            **overrides["response"])
        if isinstance(overrides.get("freq_watt"), dict):
            overrides["freq_watt"] = FreqWatt(**overrides["freq_watt"])
        for key in ("es_v_pu", "es_hz", "sustained_ov"):
            if overrides.get(key) is not None:
                overrides[key] = tuple(overrides[key])
        for key in ("volt_watt", "volt_watt_charge", "volt_var"):
            if overrides.get(key) is not None:
                overrides[key] = tuple(tuple(p) for p in overrides[key])
        code = replace(code, **overrides)
    return code
