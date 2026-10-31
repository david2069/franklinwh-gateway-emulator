"""Where the aGate and aPower are installed, and the cabinet temperature that follows (#110).

``install`` in the fleet file::

    install: {indoor: heated | unheated | uninsulated}
    install: {outdoor: {sun: morning | afternoon | all-day | shade, cover: none | roof | enclosure}}

The surroundings' temperature comes from the placement: a heated room stays near 20 °C, an
unheated one (a garage) lags the outside air by hours and runs a few degrees warmer, an
uninsulated shed follows it within the hour; outdoors it is the outside air plus the sun on the
cabinet, which depends on the sun's height, the cloud, which side it faces (the sun's azimuth:
east = morning, west = afternoon) and the cover. The cabinet then follows its surroundings with a
lag, plus heat from charging and discharging. Every number is plausible, not measured; each can
be set per gateway. Rain isn't modelled (no rain in the weather models).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

INDOOR = ("heated", "unheated", "uninsulated")
SUN = ("morning", "afternoon", "all-day", "shade")
COVER = ("none", "roof", "enclosure")

# Sun on the cabinet at 1000 W/m², in °C above the air, by cover; enclosure also shelters it
# from wind (+°C).
SUN_GAIN_C = {"none": 15.0, "roof": 3.0, "enclosure": 6.0}
SHELTER_C = {"none": 0.0, "roof": 0.0, "enclosure": 2.0}
OFF_SIDE = 0.15          # diffuse light on the side away from the sun


@dataclass
class Install:
    indoor: str | None = None          # heated | unheated | uninsulated
    sun: str = "shade"                 # outdoor: which sun reaches the cabinet
    cover: str = "none"                # outdoor
    cabinet_lag_h: float = 1.5         # cabinet time constant towards its surroundings
    self_heat_c_per_kw: float = 3.0    # cabinet above surroundings per kW charged or discharged
    room_c: float = 20.0               # heated room set point

    @classmethod
    def from_dict(cls, d: dict | None) -> "Install | None":
        if d is None:
            return None
        d = dict(d)
        out = cls()
        if "indoor" in d and "outdoor" in d:
            raise ValueError("install: indoor or outdoor, not both")
        if "indoor" in d:
            out.indoor = d.pop("indoor")
            if out.indoor not in INDOOR:
                raise ValueError(f"install.indoor must be one of {list(INDOOR)}, not {out.indoor!r}")
        elif "outdoor" in d:
            o = dict(d.pop("outdoor") or {})
            out.sun, out.cover = o.pop("sun", "shade"), o.pop("cover", "none")
            if o:
                raise ValueError(f"install.outdoor: unknown keys {sorted(o)}; known ['cover', 'sun']")
            if out.sun not in SUN:
                raise ValueError(f"install.outdoor.sun must be one of {list(SUN)}, not {out.sun!r}")
            if out.cover not in COVER:
                raise ValueError(f"install.outdoor.cover must be one of {list(COVER)}, not {out.cover!r}")
        else:
            raise ValueError("install needs indoor: … or outdoor: {sun, cover}")
        tunables = {f.name for f in fields(cls)} - {"indoor", "sun", "cover"}
        bad = set(d) - tunables
        if bad:
            raise ValueError(f"install: unknown keys {sorted(bad)}; known indoor, outdoor, {sorted(tunables)}")
        for k, v in d.items():
            setattr(out, k, float(v))
        return out

    @property
    def label(self) -> str:
        if self.indoor:
            return f"indoor, {self.indoor}"
        sun = "shade" if self.sun == "shade" else f"{self.sun} sun"
        return f"outdoor, {sun}, {'no cover' if self.cover == 'none' else self.cover}"


def sun_on_cabinet(inst: Install, elevation_deg: float | None, azimuth_deg: float | None,
                   irradiance_w_m2: float | None) -> float:
    """0..1: how much sun reaches the cabinet now. ``azimuth_deg`` in Open-Meteo's convention
    (0 = south, +90 = west), so negative = east = morning. ``irradiance_w_m2`` (from the weather
    model) carries the cloud; without it the sun's height alone is used."""
    if inst.indoor or inst.sun == "shade" or elevation_deg is None or elevation_deg <= 0:
        return 0.0
    level = (min(1.0, max(0.0, irradiance_w_m2) / 1000.0) if irradiance_w_m2 is not None
             else math.sin(math.radians(elevation_deg)))
    if inst.sun == "all-day" or azimuth_deg is None:
        side = 1.0
    else:
        east = azimuth_deg < 0
        side = 1.0 if (inst.sun == "morning") == east else OFF_SIDE
    return level * side


def surroundings_c(inst: Install, ambient_c: float, ambient_lagged_c: dict[str, float],
                   sun: float) -> float:
    """The temperature around the cabinet. ``ambient_lagged_c``: the outside air smoothed with a
    1 h and an 8 h time constant (for sheds and garages)."""
    if inst.indoor == "heated":
        return inst.room_c + 0.1 * (ambient_c - inst.room_c)
    if inst.indoor == "unheated":
        return ambient_lagged_c["8h"] + 4.0
    if inst.indoor == "uninsulated":
        return ambient_lagged_c["1h"] + 1.0
    return ambient_c + SUN_GAIN_C[inst.cover] * sun + SHELTER_C[inst.cover]


def lag(value: float, target: float, dt_s: float, tau_h: float) -> float:
    """First-order step of ``value`` towards ``target``."""
    if tau_h <= 0:
        return target
    return target + (value - target) * math.exp(-dt_s / (tau_h * 3600.0))
