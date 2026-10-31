"""Stateful, closed-loop model of one FranklinWH site (aGate + aPowers + PV + home + grid).

Unlike the time-of-day curves it descends from (franklinwh-direct-connect-api's
``SyntheticSite`` and the Modbus Bridge's ``synthetic_points``), the battery here is
*integrated*: commanded power moves SoC, SoC limits power, and lifetime energy
counters only ever grow. That is what makes Modbus control testable — a VPP charge
at 3 kW raises SoC and pulls from the grid until it hits 100 %.

The grid is modelled per phase. The utility can fail (black-out, or loss of one
phase), sag or swell (brown-out, over-voltage) or drift in frequency. The aGate's
protection follows the site's grid code (:mod:`.gridcodes`): it opens the grid relay
when any connected phase leaves a trip stage for that stage's delay, applies
frequency-watt and volt-watt responses while connected, and recloses once the grid
has been back inside the enter-service window for the reconnect delay. While
islanded the battery forms the grid: it carries the house down to the off-grid floor,
sheds load it cannot carry and curtails PV it cannot absorb. None of this has been
observed over Modbus on a real aGate during an outage, so the islanded register
picture follows SunSpec semantics (see README).

Phase layouts (``ac_type`` is SunSpec 701.ACType):
    0 single phase      L1 (or whichever single leg ``phases`` names)
    1 split phase       L1 + L2, 180° apart (US 120/240 V)
    2 three phase       L1 + L2 + L3, 120° apart; ``phases: [L1, L2]`` = two-phase supply

Sign conventions (internal, and matching the aGate's M714.DCW):
    battery_w  > 0  discharging (power out of the battery)
    battery_w  < 0  charging
    grid_w     > 0  importing
    solar_w, load_w >= 0     (solar_w after curtailment, load_w as served)
    load = solar + grid + battery   (exact balance, also per phase)

Solar and load curves are ported from ``SyntheticSite`` (half-sine solar bell 06–20 h,
breakfast/dinner load gaussians) and are deterministic for a seed and timestamp.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field, fields
from typing import Any
from datetime import datetime, timedelta, tzinfo
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .clock import Clock, SystemClock
from .csip import CsipClient
from .gridcodes import GridCode, default_grid_code, get_grid_code, interpolate
from .generator import Generator
from .replay import Replay, ReplayConfig
from .loads import DEFAULT_CIRCUITS, Circuit, Component, Context, LoadModel
from .weather import Weather, WeatherConfig
from . import tou

WH_PER_APOWER = 13_600          # 713.WHRtg on a single-aPower aGate
W_PER_APOWER = 5_000            # continuous charge/discharge per aPower
VA_PER_APOWER = 5_800           # 702.VAMaxRtg (S rated for volt-var)
# Battery losses against the SoC scale, fitted on 30 days of the reference aGate (one aPower,
# 10 s samples, Sep–Oct 2026; docs/CALIBRATION.md): discharging 12.45 kWh moves SoC by 100 %,
# charging 14.0 kWh does, and SoC falls a further ~0.53 %/h (~72 W, inverter/BMS overhead)
# while discharging. A full or idle battery holds. The per-band data suggest a 95–100 % taper
# (22.6 kWh/100 %), but modelling it made replays worse: it is the display holding 99 %.
# ``taper_above``/``taper_efficiency`` remain available for experiments. ``full_snap_soc``:
# all 30 arrivals at 100 % in the export jump the last 1–4 % within one 10 s sample while
# charging at 2–5 kW (median from 98 %): the BMS declaring full, not energy.
BATTERY_PRESETS = {
    "agate-measured": {"charge_efficiency": 0.971, "discharge_efficiency": 0.915,
                       "standby_w": 72.0, "taper_above": 100.0, "taper_efficiency": 1.0,
                       "full_snap_soc": 98.0},
    "ideal": {"charge_efficiency": 1.0, "discharge_efficiency": 1.0,
              "standby_w": 0.0, "taper_above": 100.0, "taper_efficiency": 1.0,
              "full_snap_soc": 100.0},
    "legacy": {"charge_efficiency": 0.95, "discharge_efficiency": 1.0,
               "standby_w": 0.0, "taper_above": 100.0, "taper_efficiency": 1.0,
               "full_snap_soc": 100.0},
}
MAX_STEP_S = 60.0               # integrate long gaps in sub-steps
FINE_STEP_S = 0.1               # ...but finely while a trip or reconnect timer runs
RESP_STEP_S = 0.5               # ...and in half seconds while a grid response is settling

# Native operating modes (extension register 15507). Each read on V12R02B60D00 with the app
# in that mode (2026-10-21, one site; franklinwh-modbus#75). No other value has been read.
MODE_BACKUP, MODE_SELF, MODE_TOU = 1, 2, 3
MODE_NAMES = {MODE_BACKUP: "Emergency Backup", MODE_SELF: "Self-Consumption", MODE_TOU: "TOU"}

PHASES = ("L1", "L2", "L3")
# Phase angles (degrees) and the legs each SunSpec ACType may use by default.
AC_LAYOUTS = {
    0: {"angles": {"L1": 0.0, "L2": -120.0, "L3": 120.0}, "default": ("L1",), "max": 1},
    1: {"angles": {"L1": 0.0, "L2": 180.0, "L3": 0.0}, "default": ("L1", "L2"), "max": 2},
    2: {"angles": {"L1": 0.0, "L2": -120.0, "L3": 120.0}, "default": PHASES, "max": 3},
}

# 701.Alrm bits (SunSpec DERMeasureAC).
ALARMS = {
    "GROUND_FAULT": 0, "DC_OVER_VOLT": 1, "AC_DISCONNECT": 2, "DC_DISCONNECT": 3,
    "GRID_DISCONNECT": 4, "CABINET_OPEN": 5, "MANUAL_SHUTDOWN": 6, "OVER_TEMP": 7,
    "OVER_FREQUENCY": 8, "UNDER_FREQUENCY": 9, "AC_OVER_VOLT": 10, "AC_UNDER_VOLT": 11,
    "BLOWN_STRING_FUSE": 12, "UNDER_TEMP": 13, "MEMORY_LOSS": 14, "HW_TEST_FAILURE": 15,
    "MANUFACTURER_ALRM": 16,
}
# Alarms that stop the inverter (battery power forced to 0, InvSt = FAULT).
TRIPPING_ALARMS = {"GROUND_FAULT", "DC_OVER_VOLT", "MANUAL_SHUTDOWN", "OVER_TEMP",
                   "HW_TEST_FAILURE"}

# 701.InvSt
INV_OFF, INV_RUNNING, INV_THROTTLED, INV_FAULT, INV_STANDBY = 0, 3, 4, 6, 7
# Cloud/Direct Connect run_status vocabulary (1301 power_flow).
RUN_STANDBY, RUN_CHARGING, RUN_DISCHARGING = 0, 1, 2
RUN_OFFGRID_STANDBY, RUN_OFFGRID_CHARGING, RUN_OFFGRID_DISCHARGING = 5, 6, 7
# 8 = Debug Mode and 9 = VPP mode (franklinwh-cloud RUN_STATUS). Neither is emulated yet.
RUN_DEBUG, RUN_VPP = 8, 9
RUN_NAMES = {RUN_STANDBY: "Standby", RUN_CHARGING: "Charging", RUN_DISCHARGING: "Discharging",
             RUN_OFFGRID_STANDBY: "Off-grid standby", RUN_OFFGRID_CHARGING: "Off-grid charging",
             RUN_OFFGRID_DISCHARGING: "Off-grid discharging"}
INV_NAMES = {INV_OFF: "Off", INV_RUNNING: "Running", INV_THROTTLED: "Throttled",
             INV_FAULT: "Fault", INV_STANDBY: "Standby"}   # SunSpec 701.InvSt names

PV_INPUTS = ("proximal", "remote1", "remote2")   # 15503, 15504, 15505
_TRIP_REASON = {"v_lo": "under-voltage", "v_hi": "over-voltage",
                "hz_lo": "under-frequency", "hz_hi": "over-frequency"}


@dataclass
class Location:
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    postcode: str = ""
    name: str = ""
    # For grouping in the UI only (#86); the model doesn't use them.
    country: str = ""                   # ISO 3166-1 alpha-2, e.g. AU, US
    state: str = ""                     # state or province, e.g. NSW
    city: str = ""                      # suburb or city
    site_id: str = ""

    @classmethod
    def from_dict(cls, d: dict | None) -> "Location":
        d = dict(d or {})
        unknown = sorted(set(d) - {f.name for f in fields(cls)})
        if unknown:
            raise ValueError(f"location: unknown keys {unknown}; known {[f.name for f in fields(cls)]}")
        loc = cls(**d)
        if loc.country and not (len(loc.country) == 2 and loc.country.isalpha() and loc.country.isupper()):
            raise ValueError(f"location.country {loc.country!r}: an ISO 3166-1 alpha-2 code, e.g. AU, US")
        if loc.timezone is not None:
            try:
                ZoneInfo(loc.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"unknown IANA timezone {loc.timezone!r} "
                                 "(e.g. Australia/Sydney, America/Los_Angeles)") from None
        if loc.latitude is not None and not -90 <= loc.latitude <= 90:
            raise ValueError(f"latitude {loc.latitude} out of range")
        if loc.longitude is not None and not -180 <= loc.longitude <= 180:
            raise ValueError(f"longitude {loc.longitude} out of range")
        if (loc.latitude is None) != (loc.longitude is None):
            raise ValueError("location needs both latitude and longitude, or neither")
        return loc


# Lifetime counters (Wh) read from the reference aGate's scan of 2026-10-07.
REFERENCE_COUNTERS = {"grid_import_wh": 1_463_431, "grid_export_wh": 4_614_656, "pv_wh": 12_959_408,
                      "batt_discharge_wh": 6_344_500, "batt_charge_wh": 6_478_740}


@dataclass
class SiteConfig:
    seed: int = 0
    apower_units: int = 1
    ac_type: int = 0
    # Legs wired to the aGate (and populated in 701's per-phase points). None → the
    # ac_type default. e.g. ac_type 2 with ["L1", "L2"] = two-phase supply.
    phases: list[str] | None = None
    # Share of home load / of aGate generation (PV + battery) on each leg. None → even.
    load_split: dict[str, float] | None = None
    gen_split: dict[str, float] | None = None
    # Grid code (gridcodes.GRID_CODES); None → from ac_type (split = IEEE, else AU).
    grid_code: str | None = None
    grid_overrides: dict = field(default_factory=dict)
    # Where the site is: {"latitude", "longitude", "timezone" (IANA, e.g. Australia/Sydney —
    # DST-aware), "postcode", "name"}. Weather inherits the coordinates; Direct Connect 1202
    # reports them. Timezone precedence: location.timezone > tz_offset_hours > host local.
    location: dict | None = None
    tz_offset_hours: float | None = None   # fixed UTC offset (no DST); None → host's local time
    native_mode: int = MODE_SELF
    self_reserve: int = 20
    tou_reserve: int = 20
    initial_soc: float | None = None       # None → derived from seed
    solar_peak_w: float | None = None      # None → derived from seed
    home_base_w: float | None = None       # None → derived from seed
    # Solar resource: {"model": "bell"|"synthetic"|"open-meteo", "latitude", "longitude",
    # "kwp", "tilt", "azimuth", "sky", ...} — see weather.py. None = the original bell.
    weather: dict | None = None
    # House load: a preset name ("sydney-family"), a list of components, or
    # {"preset", "add", "remove", "set"} — see loads.py. None = the original curve.
    loads: Any = None
    # Replay recorded solar/load (bridge metrics CSV export or SQLite): a path, or
    # {"source", "use": [solar, load, ambient], "align": day|absolute, "loop", ...} — replay.py.
    replay: Any = None
    # Smart circuits: {1: {"name": "...", "on": true, "soc_low_cutoff": 0}, ...}; all on by default.
    circuits: dict | None = None
    # Share of the solar curve on each PV input (aGate built-in vs aPbox remote).
    pv_inputs: dict[str, float] = field(default_factory=lambda: {"proximal": 1.0})
    offgrid_floor_soc: float = 5.0         # islanded discharge floor (1723 offgridSoc)
    # TOU mode: the schedule (franklinwh-cloud block format, tou.py) and prices per tariff
    # period ({waveType: {buy, sell}}, display only). None → tou.DEFAULT_SCHEDULE.
    tou_schedule: list | None = None
    tou_rates: dict | None = None
    # Forced solar (pv solar_w) can't exceed what the solar model gives now (0 at night, #56).
    # False: the forced value is used as given; for lab tests where PV is an input.
    cap_forced_solar: bool = True
    # The grid code's export limit is the connection-agreement limit. Whether the aGate
    # itself enforces it is a separate question: on the reference Ausgrid site it does
    # not (local 1903 grid_feed_max = -1, cloud notControlExportSolar = true), so the
    # default only *tracks* breaches. CSIP-AUS opModExpLimW is always enforced.
    export_limit_enforced: bool = False
    # Battery losses: a preset name (agate-measured, ideal, legacy) or {"preset",
    # "charge_efficiency", "discharge_efficiency", "standby_w" (per aPower), "taper_above",
    # "taper_efficiency", "full_snap_soc"}.
    battery: Any = "agate-measured"
    # Generator on the aGate's generator input: {"installed": true, "rated_w": 7000, ...} plus
    # any Direct Connect 1901 field (genStartElec, genCloseElec, startDelTime, charge1..3,
    # oilmano*, manuSw) and hardware facts (crank_s, warmup_s, cooldown_s, min_run_s,
    # fuel_l). None = the reference site: feature off, nothing fitted. See generator.py.
    generator: dict | None = None
    # Starting values for the lifetime counters (Wh). None → the reference scan's
    # (REFERENCE_COUNTERS); a factory gateway sets them to 0 (gateway.py).
    grid_import_wh: float | None = None
    grid_export_wh: float | None = None
    pv_wh: float | None = None
    batt_discharge_wh: float | None = None
    batt_charge_wh: float | None = None


def _battery_model(spec: Any) -> dict:
    if spec is None:
        spec = "agate-measured"
    if isinstance(spec, str):
        spec = {"preset": spec}
    spec = dict(spec)
    name = spec.pop("preset", "agate-measured")
    if name not in BATTERY_PRESETS:
        raise ValueError(f"battery preset {name!r}: choose from {sorted(BATTERY_PRESETS)}")
    model = {**BATTERY_PRESETS[name], **spec}
    bad = set(model) - set(BATTERY_PRESETS["ideal"])
    if bad:
        raise ValueError(f"battery: unknown {sorted(bad)}")
    for k in ("charge_efficiency", "discharge_efficiency", "taper_efficiency"):
        if not 0 < model[k] <= 1:
            raise ValueError(f"battery.{k} must be in (0, 1]")
    return model


@dataclass
class RemoteControl:
    """M704 remote control: a FORCE while ``enabled`` (a WSet/WSetPct setpoint from a Modbus
    client). ``pct_hw`` uses the hardware sign (positive = discharge)."""
    enabled: bool = False
    mode: int = 0                 # WSetMod: 0 = percent (WSetPct), 1 = watts (WSet)
    watts: int = 0                # WSet, positive = charge
    pct_hw: float = 0.0           # WSetPct in percent, positive = discharge
    revert_enabled: bool = True   # WSetEnaRvrt
    revert_s: int = 60            # WSetRvrtTms
    revert_deadline: float | None = None
    revert_effective: bool = False  # hardware reversion actually releases control

    def remaining(self, now: float) -> int:
        if self.revert_deadline is None:
            return 0
        return max(0, int(math.ceil(self.revert_deadline - now)))


@dataclass
class Utility:
    """What the utility supplies at the meter, per leg — the knobs for grid scenarios."""
    available: dict[str, bool]    # False on every leg = black-out; on one = phase loss
    v_pu: dict[str, float]        # per unit of the grid code's nominal L-N voltage
    hz: float


@dataclass
class PhaseState:
    v_ln: float = 0.0
    grid_w: float = 0.0
    load_w: float = 0.0
    gen_w: float = 0.0             # aGate output on this leg (PV + battery)
    var: float = 0.0               # reactive power on this leg, + = supplying
    import_wh: float = 0.0
    export_wh: float = 0.0


@dataclass
class Snapshot:
    ts: float
    solar_w: float
    load_w: float
    battery_w: float
    grid_w: float
    soc: float
    native_mode: int
    remote: bool
    ambient_c: float
    cabinet_c: float
    grid_import_wh: float
    grid_export_wh: float
    pv_wh: float
    batt_discharge_wh: float
    batt_charge_wh: float
    # grid & inverter state
    grid_connected: bool = True        # grid relay closed
    offgrid_requested: bool = False
    trip_reason: str | None = None
    trip_stage: str | None = None
    v_ln: float = 0.0                  # mean of connected legs, measured at the aGate
    hz: float = 0.0
    phases: dict = field(default_factory=dict)   # leg -> PhaseState (connected legs only)
    solar_available_w: float = 0.0
    solar_clipped_w: float = 0.0
    load_shed_w: float = 0.0
    pv_by_input: dict = field(default_factory=dict)
    inverter_state: int = INV_RUNNING
    run_status: int = RUN_STANDBY
    alarms: int = 0                    # 701.Alrm bitfield
    dc_alarms: int = 0                 # 714.PrtAlrms bitfield
    throttle_pct: int = 0
    var: float = 0.0                   # volt-var reactive power, + = supplying
    export_limit_w: float | None = None          # limit being enforced right now
    declared_export_limit_w: float | None = None  # connection-agreement limit
    export_over_limit_w: float = 0.0              # export above the declared limit
    der_connected: bool = True                    # CSIP opModConnect/opModEnergize
    csip_source: str = "none"                     # none | event | default | comms-loss-default
    loads: dict = field(default_factory=dict)     # served W per load component
    circuit_w: dict = field(default_factory=dict) # served W per smart circuit
    circuits_shed: bool = False                   # islanded: smart circuits dropped
    generator_w: float = 0.0                      # generator output into the house/battery
    gen_state: int = 0                            # genStat: 0 off, 1 running, 2 cooldown, 3 fault
    gen_relay_closed: bool = False


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _even_split(phases: tuple[str, ...], shares: dict[str, float] | None) -> dict[str, float]:
    if not shares:
        return {ph: 1.0 / len(phases) for ph in phases}
    bad = set(shares) - set(phases)
    if bad:
        raise ValueError(f"split names legs {sorted(bad)} that are not connected {phases}")
    total = sum(shares.values()) or 1.0
    return {ph: shares.get(ph, 0.0) / total for ph in phases}


class Site:
    """One site. Thread-safe: protocol frontends call in from their own threads."""

    def __init__(self, config: SiteConfig | None = None, clock: Clock | None = None):
        self.config = cfg = config or SiteConfig()
        self.clock = clock or SystemClock()
        self.lock = threading.RLock()
        rng = random.Random(cfg.seed)
        self.solar_peak_w = cfg.solar_peak_w or (3500 + 2500 * rng.random())
        self.home_base_w = cfg.home_base_w or (400 + 400 * rng.random())
        default_soc = 25 + 20 * rng.random()
        self.capacity_wh = WH_PER_APOWER * max(1, cfg.apower_units)
        self.battery_model = _battery_model(cfg.battery)
        self.generator = Generator(cfg.generator)
        self.gen_wh = 0.0
        self._islanded_since: float | None = None
        self.rate_w = W_PER_APOWER * max(1, cfg.apower_units)
        # aPowers switched off at their physical switch (set_apower). They add no power; the
        # one combined SoC and capacity stay as they were (per-aPower SoC isn't modelled).
        self.apower_off: set[int] = set()
        # Debug Mode (DC 1821 enable): the battery stops while grid-connected (candidate, #101).
        self.battery_held = False

        if cfg.ac_type not in AC_LAYOUTS:
            raise ValueError(f"ac_type must be 0, 1 or 2, not {cfg.ac_type!r}")
        layout = AC_LAYOUTS[cfg.ac_type]
        self.phase_angles = layout["angles"]
        self.phases: tuple[str, ...] = tuple(cfg.phases or layout["default"])
        if not self.phases or set(self.phases) - set(PHASES) or len(set(self.phases)) != len(self.phases):
            raise ValueError(f"phases must be distinct legs from {PHASES}, not {cfg.phases!r}")
        if len(self.phases) > layout["max"]:
            raise ValueError(f"ac_type {cfg.ac_type} allows at most {layout['max']} legs")
        if cfg.ac_type == 1 and set(self.phases) - {"L1", "L2"}:
            raise ValueError("split phase uses L1 and L2")
        self.load_split = _even_split(self.phases, cfg.load_split)
        self.gen_split = _even_split(self.phases, cfg.gen_split)

        self.grid: GridCode = get_grid_code(cfg.grid_code or default_grid_code(cfg.ac_type),
                                            **dict(cfg.grid_overrides))
        bad = set(cfg.pv_inputs) - set(PV_INPUTS)
        if bad:
            raise ValueError(f"unknown PV inputs {sorted(bad)}; choose from {PV_INPUTS}")

        self.soc = cfg.initial_soc if cfg.initial_soc is not None else default_soc
        if cfg.native_mode not in MODE_NAMES:
            raise ValueError(f"native_mode must be one of {sorted(MODE_NAMES)}")
        self.native_mode = cfg.native_mode
        self.self_reserve = cfg.self_reserve
        self.tou_reserve = cfg.tou_reserve
        self.remote = RemoteControl()
        # Grid-response dynamics (gridcodes.ResponseTiming): lagged values of each function,
        # the dead-time history, the frequency backlash, the enter-service ramp and the soft
        # export-limit curtailment.
        self._resp: dict[str, float] | None = None
        self._resp_hist: deque = deque()
        self._f_over: float | None = None
        self._f_under: float | None = None
        self._es_ramp_from: float | None = None
        self._exp_cut = 0.0
        self._exp_need = 0.0
        self.export_breach_since: float | None = None   # enforced limit exceeded (> 50 W) since
        self._out_batt = 0.0                     # last actual battery W and PV fraction, so a
        self._out_gen = 1.0                      # tightening response lags from the output
        self.solar_override_w: float | None = None
        self.load_override_w: float | None = None
        self.ambient_override_c: float | None = None
        self.location = Location.from_dict(cfg.location)
        if self.location.timezone:
            self.tz: tzinfo | None = ZoneInfo(self.location.timezone)
        elif cfg.tz_offset_hours is not None:
            self.tz = dt_timezone(timedelta(hours=float(cfg.tz_offset_hours)))
        else:
            self.tz = None                                    # the host's local zone
        self.loads = LoadModel(cfg.loads, seed=cfg.seed, legs=self.phases) if cfg.loads else None
        self.circuits: dict[int, Circuit] = {n: Circuit(name) for n, name in DEFAULT_CIRCUITS.items()}
        for n, c in (cfg.circuits or {}).items():
            n = int(n)
            if n not in self.circuits:
                raise ValueError(f"circuit {n}: the aGate has smart circuits 1–3")
            for k, v in c.items():
                if not hasattr(self.circuits[n], k):
                    raise ValueError(f"circuit {n}: unknown field {k!r}")
                setattr(self.circuits[n], k, v)
        self.replay = None
        if cfg.replay:
            rcfg = ReplayConfig.from_dict(cfg.replay)
            rcfg.apower_units = max(1, cfg.apower_units)
            self.replay = Replay(rcfg, local_midnight=self.local_midnight)
        wdict = dict(cfg.weather or {})
        if self.location.latitude is not None:              # weather inherits the location
            wdict.setdefault("latitude", self.location.latitude)
            wdict.setdefault("longitude", self.location.longitude)
        wcfg = WeatherConfig.from_dict(wdict)
        self.weather = None if wcfg.model == "bell" else Weather(
            wcfg, seed=cfg.seed, kwp=self.solar_peak_w / 1000.0 / wcfg.efficiency)

        # Grid, relays, alarms.
        self.utility = self._healthy_utility()
        self.grid_relay_closed = True
        self.offgrid_requested = False
        self.trip_reason: str | None = None
        self.trip_stage: str | None = None
        self.relay_events: list[dict] = []
        self._stage_since: dict[str, float] = {}
        self._normal_since: float | None = None
        window = self.grid.sustained_ov[1] if self.grid.sustained_ov else 0.0
        self._v_hist: deque[tuple[float, float]] = deque([(window, self.grid.typical_v_pu)])
        self.pv_relays = {name: True for name in PV_INPUTS}
        self.forced_alarms: dict[str, bool] = {}     # name -> latched (cleared by reset)
        self.dc_alarms = 0

        # Export compliance and the CSIP-AUS client.
        self.serial = ""                   # set by the owning Gateway (for the LFDI)
        self.export_limit_enforced = cfg.export_limit_enforced
        self.tou_schedule = tou.parse_schedule(cfg.tou_schedule or tou.DEFAULT_SCHEDULE)
        self.tou_rates = tou.parse_rates(cfg.tou_rates)
        self.export_breach_s = 0.0
        self.export_breach_wh = 0.0
        self.csip = CsipClient()

        self.load_wh = 0.0
        # Daily totals (Direct Connect 1301 kwh_*) and quarter-hour history (1303),
        # both on the site's local calendar. Today's totals start when the site does.
        self.history: dict[str, list[dict[str, float]]] = {}
        self._day_key: str | None = None
        self._day_base: dict[str, float] = {}

        start = {k: REFERENCE_COUNTERS[k] if getattr(cfg, k) is None else float(getattr(cfg, k))
                 for k in REFERENCE_COUNTERS}
        self.grid_import_wh = start["grid_import_wh"]
        self.grid_export_wh = start["grid_export_wh"]
        self.pv_wh = start["pv_wh"]
        self.batt_discharge_wh = start["batt_discharge_wh"]
        self.batt_charge_wh = start["batt_charge_wh"]
        n = len(self.phases)
        self.phase_import_wh = {ph: self.grid_import_wh / n for ph in self.phases}
        self.phase_export_wh = {ph: self.grid_export_wh / n for ph in self.phases}

        self._t = self.clock.now()
        if self.replay is not None:
            self.replay.start(self._t)
        self._roll_day(self._t)
        self._last = self._instant(self._t)

    def backfill(self, days: float, step_s: float = 60.0) -> None:
        """Run the model over the ``days`` before now, as if the site had been running: the
        quarter-hour history, today's totals and the lifetime counters advance together. The
        configured state (SoC, counters) is the state at the start of that period."""
        from .clock import ManualClock
        real = self.clock
        end = real.now()
        sim = ManualClock(end - days * 86400.0)
        with self.lock:
            self.clock = sim
            self._t = sim.now()
            self._day_key = None
            self.history.clear()
            if self.replay is not None:
                self.replay.start(self._t)
            self._roll_day(self._t)
            self._last = self._instant(self._t)
        while sim.now() < end:
            sim.advance(min(step_s, end - sim.now()))
            self.step()
        with self.lock:
            self.clock = real

    def _healthy_utility(self) -> Utility:
        g = self.grid
        return Utility(available={ph: True for ph in self.phases},
                       v_pu={ph: g.typical_v_pu for ph in self.phases}, hz=g.typical_hz)

    # -- time ------------------------------------------------------------------
    def local_datetime(self, ts: float) -> datetime:
        """Wall-clock time at the site (DST-aware with an IANA timezone)."""
        if self.tz is None:
            return datetime.fromtimestamp(ts).astimezone()
        return datetime.fromtimestamp(ts, self.tz)

    def local_midnight(self, ts: float) -> float:
        d = self.local_datetime(ts)
        return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def local_time(self, ts: float) -> time.struct_time:
        return self.local_datetime(ts).timetuple()

    def local_date(self, ts: float) -> str:
        return self.local_datetime(ts).strftime("%Y-%m-%d")

    def utc_offset_hours(self, ts: float) -> float:
        return self.local_datetime(ts).utcoffset().total_seconds() / 3600

    def is_dst(self, ts: float) -> bool:
        return bool(self.local_datetime(ts).dst())

    @property
    def timezone_name(self) -> str:
        if self.location.timezone:
            return self.location.timezone
        if self.config.tz_offset_hours is not None:
            return f"UTC{float(self.config.tz_offset_hours):+g}"
        return ""

    def _counters(self) -> dict[str, float]:
        return {"kwh_uti_in": self.grid_import_wh, "kwh_uti_out": self.grid_export_wh,
                "kwh_sun": self.pv_wh, "kwh_gen": self.gen_wh, "kwh_fhp_di": self.batt_discharge_wh,
                "kwh_fhp_chg": self.batt_charge_wh, "kwh_load": self.load_wh}

    def today_kwh(self) -> dict[str, float]:
        """Energy since local midnight (or since the site started, on its first day)."""
        with self.lock:
            now = self._counters()
            base = self._day_base or now
            return {k: (now[k] - base[k]) / 1000.0 for k in now}

    def _roll_day(self, ts: float) -> str:
        key = self.local_date(ts)
        if key != self._day_key:
            self._day_key = key
            self._day_base = self._counters()
            self.history.setdefault(key, [])
            for old in sorted(self.history)[:-106]:      # ~105-day window, like the aGate
                del self.history[old]
        return key

    def _record_history(self, ts: float, dt: float, snap: "Snapshot") -> None:
        key = self._roll_day(ts)
        lt = self.local_time(ts)
        slot = min(95, (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec) // 900)
        slots = self.history[key]
        while len(slots) <= slot:
            slots.append({"s": 0.0, "uti": 0.0, "fhp": 0.0, "load": 0.0, "gen": 0.0})
        h = slots[slot]
        h["s"] += dt
        h["uti"] += snap.grid_w * dt
        h["fhp"] += snap.battery_w * dt
        h["load"] += snap.load_w * dt
        h["gen"] = h.get("gen", 0.0) + snap.generator_w * dt

    def local_hour(self, ts: float) -> float:
        d = self.local_datetime(ts)
        return d.hour + d.minute / 60 + (d.second + d.microsecond / 1e6) / 3600

    # -- environment curves (ported from SyntheticSite) -------------------------
    def solar_w(self, ts: float) -> float:
        """PV output: the solar model's, or a forced value capped at it, since forced solar
        cannot exceed what the sun gives at this place and time (0 at night; #56)."""
        model = self.solar_model_w(ts)
        if self.solar_override_w is not None:
            forced = max(0.0, self.solar_override_w)
            return min(forced, model) if self.config.cap_forced_solar else forced
        return model

    def solar_model_w(self, ts: float) -> float:
        """What the solar model gives now: replayed data, the weather model, or the bell curve."""
        if self.replay is not None:
            v = self.replay.solar_w(ts)
            if v is not None:
                return v
        if self.weather is not None:
            return self.weather.solar_w(ts)
        hour = self.local_hour(ts)
        factor = max(0.0, math.sin((hour - 6.0) * math.pi / 14.0)) if 6.0 <= hour <= 20.0 else 0.0
        cloud = 0.88 + 0.12 * math.sin(ts / 15.0 + self.config.seed * 0.7)
        return max(0.0, self.solar_peak_w * factor ** 1.3 * cloud)

    def load_w(self, ts: float) -> float:
        if self.load_override_w is not None:
            return max(0.0, self.load_override_w)
        hour = self.local_hour(ts)
        morning = 900 * math.exp(-((hour - 8.0) ** 2) / 1.5)
        evening = 1400 * math.exp(-((hour - 19.0) ** 2) / 3.5)
        jitter = 1.0 + 0.04 * math.sin(ts / 4.0 + self.config.seed * 1.3)
        return max(0.0, (self.home_base_w + morning + evening) * jitter)

    def _pv_inputs(self, ts: float) -> dict[str, float]:
        """Solar available on each PV input, after its relay."""
        total = self.solar_w(ts)
        shares = self.config.pv_inputs
        norm = sum(shares.values()) or 1.0
        return {name: (total * shares.get(name, 0.0) / norm if self.pv_relays[name] else 0.0)
                for name in PV_INPUTS}

    # -- grid ----------------------------------------------------------------------
    def nominal_v_ln(self) -> float:
        return self.grid.nominal_v_ln

    def v_ll(self, a: str, b: str, v: dict[str, float]) -> float:
        """Line-to-line magnitude from two L-N phasors."""
        va, vb = v.get(a, 0.0), v.get(b, 0.0)
        theta = math.radians(self.phase_angles[a] - self.phase_angles[b])
        return math.sqrt(max(0.0, va * va + vb * vb - 2 * va * vb * math.cos(theta)))

    def _all_up(self) -> bool:
        return all(self.utility.available.values())

    def _v_extremes(self) -> tuple[float, float]:
        vs = [self.utility.v_pu[ph] for ph in self.phases if self.utility.available[ph]]
        return (min(vs), max(vs)) if vs else (0.0, 0.0)

    def _stage_active(self, kind: str, threshold: float) -> bool:
        lo, hi = self._v_extremes()
        hz = self.utility.hz
        return {"v_lo": lo < threshold, "v_hi": hi > threshold,
                "hz_lo": hz < threshold, "hz_hi": hz > threshold}[kind]

    def _in_es_window(self) -> bool:
        if not self._all_up():
            return False
        g = self.grid
        lo, hi = self._v_extremes()
        return (g.es_v_pu[0] <= lo and hi <= g.es_v_pu[1]
                and g.es_hz[0] <= self.utility.hz <= g.es_hz[1])

    def _v_average(self) -> float:
        total = sum(dt for dt, _ in self._v_hist) or 1.0
        return sum(dt * v for dt, v in self._v_hist) / total

    def _record_voltage(self, dt: float) -> None:
        if not self.grid.sustained_ov or dt <= 0:
            return
        window = self.grid.sustained_ov[1]
        self._v_hist.append((dt, self._v_extremes()[1] if self._all_up() else 0.0))
        excess = sum(d for d, _ in self._v_hist) - window
        while excess > 1e-9:
            d, v = self._v_hist[0]
            if d <= excess:
                self._v_hist.popleft()
                excess -= d
            else:
                self._v_hist[0] = (d - excess, v)
                break

    def _timers_running(self) -> bool:
        return bool(self._stage_since) or self._normal_since is not None

    def _open_relay(self, reason: str, stage: str | None, ts: float) -> None:
        if self.grid_relay_closed:
            self._islanded_since = ts
            self.relay_events.append({"ts": ts, "event": "open", "reason": reason,
                                      "stage": stage})
        self.grid_relay_closed = False
        self.trip_reason, self.trip_stage = reason, stage
        self._stage_since.clear()
        self._normal_since = None

    def _protect(self, ts: float) -> None:
        """Open/close the grid relay per the grid code."""
        g = self.grid
        if self.offgrid_requested:
            if self.grid_relay_closed or self.trip_reason != "requested":
                self._open_relay("requested", None, ts)
            return
        if self.grid_relay_closed:
            up = self.utility.available
            if not any(up.values()):
                self._open_relay("outage", None, ts)
                return
            if not all(up.values()):
                lost = ",".join(ph for ph in self.phases if not up[ph])
                self._open_relay("phase-loss", lost, ts)
                return
            for st in g.trips:
                if self._stage_active(st.kind, st.threshold):
                    since = self._stage_since.setdefault(st.name, ts)
                    if ts - since >= st.delay_s - 1e-9:
                        self._open_relay(_TRIP_REASON[st.kind], st.name, ts)
                        return
                else:
                    self._stage_since.pop(st.name, None)
            if g.sustained_ov and self._v_average() > g.sustained_ov[0]:
                self._open_relay("over-voltage", "sustained", ts)
        else:
            if not self._in_es_window():
                self._normal_since = None
                return
            if self._normal_since is None:
                self._normal_since = ts
            if ts - self._normal_since >= g.es_delay_s - 1e-9:
                self.relay_events.append({"ts": ts, "event": "close", "reason": None,
                                          "stage": None, "normal_for": ts - self._normal_since})
                self.grid_relay_closed = True
                self._islanded_since = None
                self.trip_reason = self.trip_stage = None
                self._normal_since = None
                if g.response is not None and g.es_ramp_s > 0:
                    self._es_ramp_from = ts               # 703 ESRmpTms ramp from zero

    # -- grid-support responses ----------------------------------------------------------
    def _response_targets(self) -> dict[str, float]:
        """Instantaneous grid-support targets, from the curves. Over- and under-frequency
        responses see the frequency through a backlash of ``hysteresis_hz`` (a response is
        released only once the frequency has come back by that much)."""
        g, rated = self.grid, float(self.rate_w)
        f = self.utility.hz
        hyst = g.response.hysteresis_hz if g.response else 0.0
        if self._f_over is None or f >= self._f_over or f < self._f_over - hyst:
            self._f_over = f if (self._f_over is None or f >= self._f_over) else f + hyst
        if self._f_under is None or f <= self._f_under or f > self._f_under + hyst:
            self._f_under = f if (self._f_under is None or f <= self._f_under) else f - hyst
        fo, fu = self._f_over, self._f_under
        t = {"fw_hi": rated, "fw_lo": -rated, "vw_hi": rated, "vw_lo": -rated,
             "gen_fw": 1.0, "gen_vw": 1.0, "var": 0.0}
        fw = g.freq_watt
        if fw:
            if fo > fw.f_ulco:
                if fo < fw.f_transition:
                    t["fw_hi"] = rated * (1.0 - (fo - fw.f_ulco) / (fw.f_transition - fw.f_ulco))
                else:
                    t["fw_hi"] = -rated * min(1.0, (fo - fw.f_transition)
                                              / (fw.f_pmin - fw.f_transition))
                t["gen_fw"] = _clamp01(1.0 - (fo - fw.f_ulco) / (fw.f_pmin - fw.f_ulco))
            if fu < fw.f_llco:
                if fu > fw.f_stop_ch:
                    t["fw_lo"] = -rated * (1.0 - (fw.f_llco - fu) / (fw.f_llco - fw.f_stop_ch))
                else:
                    t["fw_lo"] = rated * min(1.0, (fw.f_stop_ch - fu) / (fw.f_stop_ch - fw.f_pmax))
        v_lo, v_hi = self._v_extremes()
        if g.volt_watt:
            t["vw_hi"] = rated * interpolate(g.volt_watt, v_hi)
            t["gen_vw"] = interpolate(g.volt_watt, v_hi)
        if g.volt_watt_charge:
            t["vw_lo"] = -rated * interpolate(g.volt_watt_charge, v_lo)
        if g.volt_var:
            vs = [self.utility.v_pu[ph] for ph in self.phases]
            t["var"] = interpolate(g.volt_var, sum(vs) / len(vs)) * self.s_rated_va()
        return t

    _RESP_GROUP = {"fw_hi": "freq", "fw_lo": "freq", "gen_fw": "freq",
                   "vw_hi": "volt", "vw_lo": "volt", "gen_vw": "volt", "var": "var"}

    def _update_responses(self, dt: float, end: float) -> None:
        """Advance the response dynamics over ``[self._t, end]`` with the grid as it is now."""
        timing = self.grid.response
        targets = self._response_targets()
        if timing is None or self._resp is None:
            self._resp = dict(targets)
            self._resp_hist.clear()
            self._resp_hist.append((self._t, targets))
            return
        self._resp_hist.append((self._t, targets))
        cutoff = end - timing.start_s
        while len(self._resp_hist) > 1 and self._resp_hist[1][0] <= cutoff + 1e-9:
            self._resp_hist.popleft()
        delayed = self._resp_hist[0][1] if self._resp_hist[0][0] <= cutoff + 1e-9 else None
        if delayed is not None and dt > 0:
            # Responses are timed on the *output* (AS/NZS 4777.2): a tightening cap starts
            # from what is actually being delivered, not from the rating.
            r = self._resp
            if delayed["fw_hi"] < r["fw_hi"]:
                r["fw_hi"] = min(r["fw_hi"], max(self._out_batt, delayed["fw_hi"]))
            if delayed["vw_hi"] < r["vw_hi"]:
                r["vw_hi"] = min(r["vw_hi"], max(self._out_batt, delayed["vw_hi"]))
            if delayed["fw_lo"] > r["fw_lo"]:
                r["fw_lo"] = max(r["fw_lo"], min(self._out_batt, delayed["fw_lo"]))
            if delayed["vw_lo"] > r["vw_lo"]:
                r["vw_lo"] = max(r["vw_lo"], min(self._out_batt, delayed["vw_lo"]))
            for g in ("gen_fw", "gen_vw"):
                if delayed[g] < r[g]:
                    r[g] = min(r[g], max(self._out_gen, delayed[g]))
            k = -math.log(1.0 - timing.settle_frac)
            taus = {"freq": timing.freq_settle_s / k, "volt": timing.volt_settle_s / k,
                    "var": timing.var_settle_s / k}
            for key, tgt in delayed.items():
                tau = taus[self._RESP_GROUP[key]]
                a = math.exp(-dt / tau) if tau > 0 else 0.0
                self._resp[key] = tgt + (self._resp[key] - tgt) * a
        if dt > 0:
            tau = timing.export_settle_s / -math.log(1.0 - timing.settle_frac)
            a = math.exp(-dt / tau) if tau > 0 else 0.0
            self._exp_cut = self._exp_need + (self._exp_cut - self._exp_need) * a
        if self._es_ramp_from is not None and end - self._es_ramp_from >= self.grid.es_ramp_s:
            self._es_ramp_from = None

    def _responses_settling(self) -> bool:
        if self.grid.response is None or self._resp is None:
            return False
        if self._es_ramp_from is not None or abs(self._exp_cut - self._exp_need) > 1.0:
            return True
        targets = self._response_targets()
        if any(h[1] != targets for h in self._resp_hist):
            return True                               # a change still inside the dead time
        return any(abs(self._resp[k] - v) > (0.002 if k.startswith("gen") else 1.0)
                   for k, v in targets.items())

    def _es_ramp(self, ts: float) -> float:
        """Enter-service ramp factor after a reconnect (1.0 when not ramping)."""
        if self._es_ramp_from is None:
            return 1.0
        return _clamp01((ts - self._es_ramp_from) / self.grid.es_ramp_s)

    def _applied(self) -> dict[str, float]:
        return self._resp if self._resp is not None else self._response_targets()

    def _storage_window(self, ts: float | None = None) -> tuple[float, float]:
        """(highest, lowest) battery power the grid code allows right now.

        Frequency-watt can cap discharge (and beyond f_transition force charging) or cap
        charging (and beyond f_stop_ch force discharging); volt-watt caps discharge at
        high voltage and charging at low voltage. Lagged per the grid code's response
        timing; discharge also follows the enter-service ramp after a reconnect.
        """
        r = self._applied()
        hi = min(r["fw_hi"], r["vw_hi"]) if r["fw_hi"] > 0 else r["fw_hi"]
        lo = max(r["fw_lo"], r["vw_lo"]) if r["fw_lo"] < 0 else r["fw_lo"]
        if hi > 0:
            hi = min(hi, self.rate_w * self._es_ramp(self._t if ts is None else ts))
        return hi, lo

    def _generation_factor(self, ts: float | None = None) -> float:
        """PV output cap from over-frequency, volt-watt and the enter-service ramp."""
        r = self._applied()
        return min(r["gen_fw"], r["gen_vw"], self._es_ramp(self._t if ts is None else ts))

    def s_rated_va(self) -> float:
        return VA_PER_APOWER * self.apower_units_on()

    def _reactive_var(self) -> float:
        """Volt-var: reactive power (+ supplying) from the mean connected-leg voltage, lagged."""
        if not self.grid.volt_var:
            return 0.0
        return self._applied()["var"]

    # -- control ------------------------------------------------------------------
    def set_tou_schedule(self, blocks: list | None = None, rates: dict | None = None) -> None:
        """Replace the TOU schedule (None = the default) and/or the rates. Doesn't change the mode."""
        schedule = tou.parse_schedule(blocks or tou.DEFAULT_SCHEDULE) if blocks is not False else None
        parsed_rates = tou.parse_rates(rates) if rates is not None else None
        with self.lock:
            self.step()
            if schedule is not None:
                self.tou_schedule = schedule
            if parsed_rates is not None:
                self.tou_rates = parsed_rates

    def tou_now(self, ts: float) -> dict:
        """The schedule, the rates and the active block with its time left."""
        lt = self.local_datetime(ts)
        minute = lt.hour * 60 + lt.minute + lt.second / 60.0
        b = tou.block_at(self.tou_schedule, minute)
        active = {**tou.public(b), "remaining_min": round(b["_end"] - minute, 1),
                  "rates": self.tou_rates.get(b["waveType"])}
        return {"schedule": [tou.public(x) for x in self.tou_schedule],
                "rates": {str(k): v for k, v in self.tou_rates.items()}, "active": active}

    def active_reserve(self) -> float:
        if self.native_mode == MODE_TOU:
            return float(self.tou_reserve)
        if self.native_mode == MODE_BACKUP:
            return 100.0
        return float(self.self_reserve)

    def force_status(self) -> dict | None:
        """The M704 force, if one is on: direction from the requested battery power."""
        r = self.remote
        if not r.enabled:
            return None
        t = self._remote_target()
        direction = "discharge" if t > 0 else "charge" if t < 0 else "standby"
        return {"direction": direction, "via": "WSet" if r.mode == 1 else "WSetPct", "target_w": round(t)}

    def _remote_target(self) -> float:
        """Requested battery power (internal sign) while M704 control is enabled."""
        r = self.remote
        if r.mode == 1:
            return -float(r.watts)                     # WSet: positive = charge
        # WSetPct is a percentage of the 5 kW *rating* (M702 does not scale with
        # aPower count); values beyond ±100 % saturate at the rating.
        pct = max(-100.0, min(100.0, r.pct_hw))
        return pct / 100.0 * W_PER_APOWER

    def _native_target(self, solar: float, load: float, hour: float) -> float:
        net = load - solar                             # >0 deficit, <0 surplus
        mode = self.native_mode
        if mode == MODE_BACKUP:
            return min(0.0, net)                       # soak surplus, never discharge
        if mode == MODE_TOU:                           # the schedule's block decides (tou.py)
            block = tou.block_at(self.tou_schedule, hour * 60.0)
            return tou.target_w(block["dispatchId"], net, self.rate_w)
        return net                                     # self-consumption

    def _limits(self, floor: float, dt: float) -> tuple[float, float]:
        """(max discharge W, max charge W) given rate, SoC, floor and step length."""
        max_dis = max_chg = float(self.rate_w)
        if dt > 0:
            hours = dt / 3600
            bm = self.battery_model
            max_dis = min(max_dis, max(0.0, (self.soc - floor) / 100 * self.capacity_wh)
                          * bm["discharge_efficiency"] / hours)
            max_chg = min(max_chg, max(0.0, (100.0 - self.soc) / 100 * self.capacity_wh)
                          / hours / min(bm["charge_efficiency"], bm["taper_efficiency"]))
        else:
            if self.soc <= floor:
                max_dis = 0.0
            if self.soc >= 100.0:
                max_chg = 0.0
        return max_dis, max_chg

    # -- alarms ------------------------------------------------------------------
    def _alarm_bits(self, islanded: bool) -> int:
        bits = 0
        for name in self.forced_alarms:
            bits |= 1 << ALARMS[name]
        if islanded and self.trip_reason != "requested":
            bits |= 1 << ALARMS["GRID_DISCONNECT"]
        if any(self.utility.available.values()):
            for st in self.grid.trips:
                if self._stage_active(st.kind, st.threshold):
                    bits |= 1 << ALARMS[{"v_lo": "AC_UNDER_VOLT", "v_hi": "AC_OVER_VOLT",
                                         "hz_lo": "UNDER_FREQUENCY",
                                         "hz_hi": "OVER_FREQUENCY"}[st.kind]]
        return bits

    def inverter_faulted(self) -> bool:
        return any(name in TRIPPING_ALARMS for name in self.forced_alarms)

    def ambient_c(self, ts: float) -> float:
        if self.ambient_override_c is not None:
            return self.ambient_override_c
        if self.replay is not None:
            v = self.replay.ambient_c(ts)
            if v is not None:
                return v
        measured = self.weather.ambient_c(ts) if self.weather is not None else None
        if measured is not None:
            return measured
        hour = self.local_hour(ts)
        sun = max(0.0, math.sin((hour - 6.0) * math.pi / 14.0)) if 6.0 <= hour <= 20.0 else 0.0
        return 18.0 + (self.config.seed % 8) + 4.0 * sun

    def _load_breakdown(self, ts: float, solar_avail: float, islanded: bool,
                        ambient: float) -> tuple[dict[str, float], dict[str, Component | None]]:
        """Demand per component (W) and the component objects (None for the legacy curve).
        Switched-off smart circuits, and circuits below their islanded SoC cutoff, demand 0."""
        if self.load_override_w is not None:
            return {"override": max(0.0, self.load_override_w)}, {"override": None}
        if self.replay is not None:
            v = self.replay.load_w(ts)
            if v is not None:
                return {"recorded": v}, {"recorded": None}
        if self.loads is None:
            return {"house": self.load_w(ts)}, {"house": None}
        ctx = Context(ts, self.local_datetime(ts), ambient, 0.0)
        out: dict[str, float] = {}
        comps: dict[str, Component | None] = {}

        def allowed(c: Component) -> bool:
            if c.circuit is None:
                return True
            circ = self.circuits[c.circuit]
            if not circ.on:
                return False
            return not (islanded and circ.soc_low_cutoff and self.soc < circ.soc_low_cutoff)

        for c in self.loads.components:
            if c.type != "ev":
                out[c.name] = c.demand(ctx, self.loads.seed) if allowed(c) else 0.0
                comps[c.name] = c
        ctx.surplus_w = solar_avail - sum(out.values())
        for c in self.loads.components:
            if c.type == "ev":
                out[c.name] = c.demand(ctx, self.loads.seed) if allowed(c) else 0.0
                comps[c.name] = c
        return out, comps

    # -- integration ---------------------------------------------------------------
    def _instant(self, ts: float, dt: float = 0.0) -> Snapshot:
        hour = self.local_hour(ts)
        pv = self._pv_inputs(ts)
        solar_avail = sum(pv.values())
        islanded = not self.grid_relay_closed
        ambient = self.ambient_c(ts)
        demand, comps = self._load_breakdown(ts, solar_avail, islanded, ambient)
        load = sum(demand.values())
        circuits_shed = False
        faulted = self.inverter_faulted()
        g = self.grid
        clipped = shed = var = 0.0
        throttle = 0
        ctl: dict = {}
        source = "none"
        enforced_limit = None
        der_on = True

        if islanded:
            # The battery forms the grid: it must balance the house on its own.
            max_dis, max_chg = self._limits(self.config.offgrid_floor_soc, dt)
            if faulted:
                max_dis = max_chg = 0.0
            gen_w = self.generator.output_w(load - solar_avail, max_chg)
            net = load - solar_avail - gen_w
            on_circuits = [n for n, c in comps.items() if c is not None and c.circuit and demand[n] > 0]
            if net > max_dis and on_circuits:
                # Short of power off-grid: the aGate sheds smart circuits first.
                for n in on_circuits:
                    demand[n] = 0.0
                circuits_shed = True
                load = sum(demand.values())
                gen_w = self.generator.output_w(load - solar_avail, max_chg)
                net = load - solar_avail - gen_w
            if net >= 0:
                batt = min(net, max_dis)
                shed = net - batt
            else:
                batt = -min(-net, max_chg)
                clipped = -net + batt                    # surplus the battery can't take
            solar = solar_avail - clipped
            served = load - shed
            grid = 0.0
            dark = served <= 0 and load > 0 and gen_w <= 0
            if faulted and gen_w <= 0 or dark:
                # Nothing forms the grid: the house is dark and PV inverters drop out.
                v_legs = {ph: 0.0 for ph in self.phases}
                hz = 0.0
                inv = INV_FAULT if faulted else INV_STANDBY
                solar, clipped, shed, served, batt = 0.0, solar_avail, load, 0.0, 0.0
                gen_w = 0.0
            else:
                v_legs = {ph: g.nominal_v_ln for ph in self.phases}
                hz = g.nominal_hz
                inv = INV_RUNNING
            if batt < -50:
                run = RUN_OFFGRID_CHARGING
            elif batt > 50:
                run = RUN_OFFGRID_DISCHARGING
            else:
                run = RUN_OFFGRID_STANDBY
            load = served
        else:
            gen_w = 0.0                             # never paralleled with the grid
            floor = self.active_reserve()           # under VPP control too
            max_dis, max_chg = self._limits(floor, dt)
            if faulted or self.battery_held:
                max_dis = max_chg = 0.0
            replayed = dispatch = None
            if self.replay is not None:
                replayed = self.replay.battery_w(ts)
                dispatch = self.replay.dispatch_w(ts)
            if self.remote.enabled:
                target = self._remote_target()
            elif replayed is not None:
                target = replayed                     # recorded battery power (use: battery)
            elif dispatch is not None:
                target = dispatch                     # inferred dispatch command (use: dispatch)
            else:
                target = self._native_target(solar_avail, load, hour)
            # Grid support per the grid code: frequency-watt and volt-watt bound the
            # battery, PV follows its own over-frequency/volt-watt curtailment, then the
            # export limit trims battery discharge first and PV second.
            hi, lo = self._storage_window(ts)
            batt = max(lo, min(hi, target))
            batt = max(-max_chg, min(max_dis, batt))      # SoC limits win
            solar = solar_avail * self._generation_factor(ts)
            # CSIP-AUS controls from the utility server (or its default).
            ctl, source = self.csip.effective(ts)
            if ctl.get("opModConnect") is False or ctl.get("opModEnergize") is False:
                der_on = False
                batt, solar = 0.0, 0.0
            else:
                lim = ctl.get("opModGenLimW")
                if lim is not None and solar + max(0.0, batt) > lim:
                    excess = solar + max(0.0, batt) - lim
                    cut = min(excess, max(0.0, batt))
                    batt -= cut
                    solar -= min(excess - cut, solar)
                lim = ctl.get("opModLoadLimW")
                if lim is not None and -batt > lim:
                    batt = -lim
            grid = load - solar - batt
            lim = ctl.get("opModImpLimW")
            if lim is not None and grid > lim and batt < 0:
                batt = min(0.0, batt + (grid - lim))      # stop charging from the grid
                grid = load - solar - batt
            enforced_limit = self._enforced_export_limit(ctl)
            need = max(0.0, -grid - enforced_limit) if enforced_limit is not None else 0.0
            if dt > 0:
                self._exp_need = need                 # the lagged curtailment chases this
            if need > 0:
                # A soft limit is met within export_settle_s, not instantly.
                excess = need if self.grid.response is None else min(need, self._exp_cut)
                cut = min(excess, max(0.0, batt))
                batt -= cut
                solar -= min(excess - cut, solar)
                grid = load - solar - batt
            clipped = solar_avail - solar
            if solar_avail > 0 and clipped > 1e-6:
                throttle = int(round(clipped / solar_avail * 100))
            responding = bool(throttle) or hi < self.rate_w or lo > -self.rate_w
            var = 0.0 if faulted else self._reactive_var()
            v_legs = {ph: self.utility.v_pu[ph] * g.nominal_v_ln for ph in self.phases}
            hz = self.utility.hz
            inv = INV_FAULT if faulted else (INV_THROTTLED if responding else INV_RUNNING)
            if batt < -50:
                run = RUN_CHARGING
            elif batt > 50:
                run = RUN_DISCHARGING
            else:
                run = RUN_STANDBY

        # Per-phase picture: house load and aGate output split across the legs. Islanded,
        # nothing flows to the grid on any leg, so each leg's output follows its load.
        gen = solar + batt
        total_demand = sum(demand.values())
        frac = (load / total_demand) if total_demand > 0 else 0.0     # served share
        served = {n: w * frac for n, w in demand.items()}
        leg_load = {ph: 0.0 for ph in self.phases}
        for n, w in served.items():
            c = comps.get(n)
            if c is not None and c.leg:
                leg_load[c.leg] += w
            else:
                for ph in self.phases:
                    leg_load[ph] += w * self.load_split[ph]
        phases = {}
        for ph in self.phases:
            pl = leg_load[ph]
            pg = pl if islanded else gen * self.gen_split[ph]
            phases[ph] = PhaseState(v_ln=v_legs[ph], grid_w=pl - pg, load_w=pl, gen_w=pg,
                                    var=var * self.gen_split[ph],
                                    import_wh=self.phase_import_wh[ph],
                                    export_wh=self.phase_export_wh[ph])

        declared = g.export_limit_w(len(self.phases))
        over = max(0.0, -grid - declared) if declared is not None else 0.0
        scale = (solar / solar_avail) if solar_avail > 0 else 0.0
        circuit_w = {n: 0.0 for n in self.circuits}
        for name, w in served.items():
            c = comps.get(name)
            if c is not None and c.circuit:
                circuit_w[c.circuit] += w
        return Snapshot(
            ts=ts, solar_w=solar, load_w=load, battery_w=batt, grid_w=grid,
            soc=self.soc, native_mode=self.native_mode, remote=self.remote.enabled,
            ambient_c=ambient, cabinet_c=ambient + 8.0 + abs(batt) / 1000.0,
            grid_import_wh=self.grid_import_wh, grid_export_wh=self.grid_export_wh,
            pv_wh=self.pv_wh, batt_discharge_wh=self.batt_discharge_wh,
            batt_charge_wh=self.batt_charge_wh,
            grid_connected=not islanded, offgrid_requested=self.offgrid_requested,
            trip_reason=self.trip_reason, trip_stage=self.trip_stage,
            v_ln=sum(v_legs.values()) / len(v_legs), hz=hz, phases=phases,
            solar_available_w=solar_avail, solar_clipped_w=clipped, load_shed_w=shed,
            pv_by_input={k: v * scale for k, v in pv.items()},
            inverter_state=inv, run_status=run,
            alarms=self._alarm_bits(islanded), dc_alarms=self.dc_alarms,
            throttle_pct=throttle, var=var,
            export_limit_w=enforced_limit, declared_export_limit_w=declared,
            export_over_limit_w=over, der_connected=der_on, csip_source=source,
            loads={n: round(w, 1) for n, w in served.items()}, circuit_w=circuit_w,
            circuits_shed=circuits_shed, generator_w=gen_w,
            gen_state=self.generator.state, gen_relay_closed=self.generator.relay_closed,
        )

    def _enforced_export_limit(self, ctl: dict) -> float | None:
        limits = []
        declared = self.grid.export_limit_w(len(self.phases))
        if self.export_limit_enforced and declared is not None:
            limits.append(declared)
        if ctl.get("opModExpLimW") is not None:
            limits.append(ctl["opModExpLimW"])
        return min(limits) if limits else None

    def _advance(self, dt: float, end: float | None = None) -> None:
        self._protect(self._t)
        ts = self._t + dt if end is None else end
        self._update_responses(dt, ts)
        islanded = not self.grid_relay_closed
        self.generator.update(ts, dt, self.local_datetime(ts), islanded,
                              (ts - self._islanded_since) if islanded and self._islanded_since
                              is not None else 0.0, self.soc)
        if self.replay is not None:
            state = self.replay.state(self._t)        # recorded mode/reserves (use: mode)
            if state:
                self.native_mode = state["native_mode"]
                self.self_reserve = state.get("self_reserve", self.self_reserve)
                self.tou_reserve = state.get("tou_reserve", self.tou_reserve)
        snap = self._instant(ts, dt)
        hours = dt / 3600
        bm = self.battery_model
        if snap.battery_w > 0:
            self.batt_discharge_wh += snap.battery_w * hours
            self.soc -= (snap.battery_w / bm["discharge_efficiency"] * hours
                         / self.capacity_wh * 100)
        elif snap.battery_w < 0:
            self.batt_charge_wh += -snap.battery_w * hours
            eff = bm["charge_efficiency"] if self.soc < bm["taper_above"] else bm["taper_efficiency"]
            self.soc += -snap.battery_w * eff * hours / self.capacity_wh * 100
            if self.soc >= bm["full_snap_soc"]:
                self.soc = 100.0                       # BMS declares full
        # Overhead (inverter, BMS) drawn from the cells while discharging. Charging or idle,
        # PV or grid covers it: the reference aGate holds 100 % for hours with DCW 0. It
        # never takes SoC below the active floor.
        drain = bm["standby_w"] * self.apower_units_on() * hours / self.capacity_wh * 100
        if drain and snap.battery_w > 0:
            floor = self.config.offgrid_floor_soc if not snap.grid_connected else self.active_reserve()
            self.soc = max(min(self.soc, floor), self.soc - drain)
        self._out_batt = snap.battery_w
        self._out_gen = (snap.solar_w / snap.solar_available_w
                         if snap.solar_available_w > 1.0 else 1.0)
        lim = snap.export_limit_w
        if lim is not None and -snap.grid_w > lim + 50.0:
            if self.export_breach_since is None:
                self.export_breach_since = ts
        else:
            self.export_breach_since = None
        # Following a recorded dispatch: the real BMS recalibrates to 100 % at the top of
        # charge (seen as a jump from ~96 %), so re-anchor when the recording reports full.
        if self.replay is not None and "battery" in self.replay.cfg.use and self.soc >= 90.0:
            rec = self.replay.sample(ts)
            if rec and rec.get("soc") is not None and rec["soc"] >= 99.5:
                self.soc = 100.0
        self.soc = max(0.0, min(100.0, self.soc))
        # Per-leg metering: totals are the sums of each leg's import and export.
        for ph, st in snap.phases.items():
            if st.grid_w > 0:
                self.phase_import_wh[ph] += st.grid_w * hours
                self.grid_import_wh += st.grid_w * hours
            else:
                self.phase_export_wh[ph] += -st.grid_w * hours
                self.grid_export_wh += -st.grid_w * hours
        self.pv_wh += snap.solar_w * hours
        self.gen_wh += snap.generator_w * hours
        self.generator.burn(snap.generator_w, dt)
        self.load_wh += snap.load_w * hours
        for n, w in snap.circuit_w.items():
            self.circuits[n].energy_wh += w * hours
        if self.loads is not None:
            ctx = Context(ts, self.local_datetime(ts), snap.ambient_c, 0.0)
            for c in self.loads.components:
                c.commit(ctx, snap.loads.get(c.name, 0.0), dt)
        self._record_history(ts - dt / 2, dt, snap)
        if snap.export_over_limit_w > 0.5:
            self.export_breach_s += dt
            self.export_breach_wh += snap.export_over_limit_w * hours
        self._record_voltage(dt)
        self._t = ts
        self._expire_reversion(ts)

    def _expire_reversion(self, ts: float) -> None:
        r = self.remote
        if r.revert_deadline is not None and ts >= r.revert_deadline:
            r.revert_deadline = None
            if r.revert_effective:
                r.enabled = False

    def step(self) -> Snapshot:
        """Advance to the clock's now and return the current state."""
        with self.lock:
            now = self.clock.now()
            while now - self._t > 1e-9:
                self.csip.tick(self._t)
                self._protect(self._t)
                limit = (FINE_STEP_S if self._timers_running()
                         else RESP_STEP_S if self._responses_settling() else MAX_STEP_S)
                remaining = now - self._t
                if remaining <= limit:
                    self._advance(remaining, end=now)     # land exactly on `now`
                else:
                    self._advance(limit)
            self.csip.tick(self._t)
            self._protect(self._t)
            self._last = self._instant(self._t)
            return self._last

    # -- control entry points (protocol frontends and the control API) ------------
    def set_native_mode(self, mode: int) -> None:
        if int(mode) not in MODE_NAMES:
            raise ValueError(f"mode must be one of {sorted(MODE_NAMES)}")
        with self.lock:
            self.step()
            self.native_mode = int(mode)

    def set_reserves(self, self_reserve: int | None = None, tou_reserve: int | None = None) -> None:
        with self.lock:
            self.step()
            if self_reserve is not None:
                self.self_reserve = int(self_reserve)
            if tou_reserve is not None:
                self.tou_reserve = int(tou_reserve)

    def update_remote(self, **changes) -> None:
        """Apply M704 changes; (re)arms the reversion countdown on enable."""
        with self.lock:
            self.step()
            r = self.remote
            was_enabled = r.enabled
            for k, v in changes.items():
                setattr(r, k, v)
            if r.enabled and (not was_enabled or "revert_s" in changes):
                r.revert_deadline = (self._t + r.revert_s) if (r.revert_enabled and r.revert_s > 0) else None
            if not r.enabled:
                r.revert_deadline = None

    def set_grid(self, *, available: bool | None = None, v_pu: float | None = None,
                 voltage: float | None = None, hz: float | None = None,
                 hz_offset: float | None = None, phase: str | None = None) -> None:
        """Change what the utility supplies, on one leg (``phase``) or all of them.

        ``voltage`` is volts line-to-neutral, ``v_pu`` per unit of the grid code's
        nominal; ``hz`` is absolute, ``hz_offset`` relative to nominal. Frequency is
        common to all legs.
        """
        with self.lock:
            self.step()
            u = self.utility
            if phase is not None and phase not in self.phases:
                raise ValueError(f"phase {phase!r} is not connected; legs are {self.phases}")
            legs = [phase] if phase else list(self.phases)
            for ph in legs:
                if available is not None:
                    u.available[ph] = bool(available)
                if v_pu is not None:
                    u.v_pu[ph] = float(v_pu)
                if voltage is not None:
                    u.v_pu[ph] = float(voltage) / self.grid.nominal_v_ln
            if hz is not None:
                u.hz = float(hz)
            if hz_offset is not None:
                u.hz = self.grid.nominal_hz + float(hz_offset)
            self.step()

    def restore_grid(self) -> None:
        """Healthy utility again; the relay recloses after the enter-service delay."""
        with self.lock:
            self.step()
            self.utility = self._healthy_utility()
            self.step()

    def request_offgrid(self, on: bool) -> None:
        """User-initiated islanding (Direct Connect 1723 offgridSet)."""
        with self.lock:
            self.step()
            self.offgrid_requested = bool(on)
            if not on and self.trip_reason == "requested":
                self.trip_reason = None
                self.grid_relay_closed = self._in_es_window()
                if self.grid_relay_closed:
                    self.relay_events.append({"ts": self._t, "event": "close",
                                              "reason": "requested", "stage": None,
                                              "normal_for": None})
            self.step()

    def set_pv_relay(self, name: str, closed: bool) -> None:
        if name not in PV_INPUTS:
            raise ValueError(f"unknown PV input {name!r}; choose from {PV_INPUTS}")
        with self.lock:
            self.step()
            self.pv_relays[name] = bool(closed)

    def force_alarm(self, name: str, *, latched: bool = True) -> None:
        """Raise a 701.Alrm bit. Latched alarms clear on AlarmReset (715); others
        persist until :meth:`clear_alarm`. Tripping alarms stop the inverter."""
        if name not in ALARMS:
            raise ValueError(f"unknown alarm {name!r}; choose from {sorted(ALARMS)}")
        with self.lock:
            self.step()
            self.forced_alarms[name] = latched

    def clear_alarm(self, name: str | None = None) -> None:
        with self.lock:
            self.step()
            if name is None:
                self.forced_alarms.clear()
                self.dc_alarms = 0
            else:
                self.forced_alarms.pop(name, None)

    def reset_alarms(self) -> None:
        """What a 715.AlarmReset write does: clear latched alarms."""
        with self.lock:
            self.step()
            self.forced_alarms = {n: l for n, l in self.forced_alarms.items() if not l}
            self.dc_alarms = 0

    # -- export limit & CSIP-AUS (the control API plays the utility server) -----------
    def set_export_limit_enforced(self, on: bool) -> None:
        with self.lock:
            self.step()
            self.export_limit_enforced = bool(on)

    def configure_csip(self, *, enabled: bool | None = None, uri: str | None = None,
                       pin: str | None = None) -> None:
        """What a Direct Connect 1205 der_comms write does to the 2030.5 client."""
        with self.lock:
            self.step()
            self.csip.configure(self._t, enabled=enabled, uri=uri, pin=pin, serial=self.serial)

    def set_csip_server(self, up: bool) -> None:
        with self.lock:
            self.step()
            self.csip.set_server(self._t, up)

    def post_csip_control(self, controls: dict, duration_s: float | None = None) -> str:
        """The utility posts a DERControl event; returns its mRID."""
        with self.lock:
            self.step()
            return self.csip.post(self._t, controls, duration_s).mrid

    def set_csip_default(self, controls: dict) -> None:
        from .csip import validate
        with self.lock:
            self.step()
            self.csip.default = validate(controls)

    def clear_csip_events(self) -> None:
        with self.lock:
            self.step()
            self.csip.events.clear()

    # -- loads & smart circuits --------------------------------------------------------
    def apower_units_on(self) -> int:
        return max(1, self.config.apower_units) - len(self.apower_off)

    def set_apower(self, n: int, on: bool) -> None:
        """Switch aPower ``n`` on or off as its physical switch does (System User Manual: one per
        unit). Off: it adds no charge or discharge power. What Direct Connect or Modbus report
        for an off unit isn't measured, so their device lists don't change."""
        units = max(1, self.config.apower_units)
        if not 1 <= n <= units:
            raise ValueError(f"aPower {n}: this site has aPower 1–{units}")
        with self.lock:
            self.step()
            (self.apower_off.discard if on else self.apower_off.add)(n)
            self.rate_w = W_PER_APOWER * self.apower_units_on()

    def set_circuit(self, n: int, on: bool) -> None:
        with self.lock:
            self.step()
            if n not in self.circuits:
                raise ValueError("the aGate has smart circuits 1–3")
            self.circuits[n].on = bool(on)

    def force_appliance(self, name: str, state: bool | None) -> None:
        """True = run now, False = off, None = back to its own schedule."""
        if self.loads is None:
            raise ValueError("this gateway has no appliances: add `loads:` to its fleet entry "
                             "(e.g. `loads: sydney-family`) and restart")
        with self.lock:
            self.step()
            try:
                self.loads.by_name(name).forced = state
            except KeyError:
                raise ValueError(f"no appliance {name!r}; this gateway has "
                                 f"{[c.name for c in self.loads.components]}") from None
