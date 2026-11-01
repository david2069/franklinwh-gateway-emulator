"""Component age and warranty (#115): aPower SoH from age and throughput, PV panel ageing.

FranklinWH's warranty terms (owner's copies, not committed): *FranklinWH Products Australia
Limited End User Warranty* (effective June 2026) and *FranklinWH System Limited Warranty for End
Users* (US & CA, effective 2026-10-28):

- aPower X (13.6 kWh): 12 years or 43 MWh, whichever comes first, retaining at least 70 % of
  rated capacity; aPower X-02-AU (15 kWh): 12 years or 60 MWh; aPower 2 and aPower S: 15 years
  or 60 MWh. aGate: 12 years. The term starts at installation.

The SoH curve between is not documented. Here it falls linearly to the 70 % at the end of the
term, by age or by throughput, whichever is further along (plausible). It passes near the
reference aPower's measured SoH (94.8 % on 2026-10-07, about 2.2 years in; 1705 ``batSoh``).
PV inverters and panels have no FranklinWH term: 10 and 25 years, and panels lose 0.5 % of
output a year (plausible defaults, to be set per site).

    components: {apower: {installed: 2024-07-25}, pv_panels: {installed: 2019-03-01,
                 degradation_pct_per_year: 0.6}}
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, fields

from .models import APowerModel

RETAINED_PCT = 70.0
# SoH measured on the reference aPower with the 2026-10-07 calibration (docs/CALIBRATION.md):
# the loss model's efficiencies already include it, so capacity scales against this value.
CALIBRATION_SOH = 94.8
NAMES = ("agate", "apower", "pv_inverter", "pv_panels")


@dataclass
class Component:
    installed: str | None = None          # YYYY-MM-DD; None = the gateway's commissioned date
    warranty_years: float | None = None   # None = FranklinWH's term / the plausible default
    throughput_mwh: float | None = None   # aPower only: warranted energy per unit
    degradation_pct_per_year: float | None = None   # PV panels only


def warranty_terms(apower: APowerModel) -> tuple[float, float]:
    """(years, MWh per unit) of FranklinWH's aPower warranty for this model."""
    years = 15.0 if apower.family in ("aPower 2", "aPower S") else 12.0
    return years, (60.0 if apower.kwh >= 15.0 else 43.0)


class Components:
    def __init__(self, cfg: dict | None, apower: APowerModel):
        cfg = dict(cfg or {})
        bad = set(cfg) - set(NAMES)
        if bad:
            raise ValueError(f"components: unknown keys {sorted(bad)}; known {list(NAMES)}")
        known = {f.name for f in fields(Component)}
        self.parts: dict[str, Component] = {}
        for name in NAMES:
            d = dict(cfg.get(name) or {})
            extra = set(d) - known
            if extra:
                raise ValueError(f"components.{name}: unknown keys {sorted(extra)}; known {sorted(known)}")
            c = Component(**d)
            if c.installed is not None:
                try:
                    c.installed = dt.date.fromisoformat(str(c.installed)).isoformat()
                except ValueError:
                    raise ValueError(f"components.{name}.installed must be YYYY-MM-DD, "
                                     f"not {c.installed!r}") from None
            self.parts[name] = c
        years, mwh = warranty_terms(apower)
        defaults = {"agate": 12.0, "apower": years, "pv_inverter": 10.0, "pv_panels": 25.0}
        for name, c in self.parts.items():
            if c.warranty_years is None:
                c.warranty_years = defaults[name]
            if float(c.warranty_years) <= 0:
                raise ValueError(f"components.{name}.warranty_years must be > 0")
        a, p = self.parts["apower"], self.parts["pv_panels"]
        if a.throughput_mwh is None:
            a.throughput_mwh = mwh
        if p.degradation_pct_per_year is None:
            p.degradation_pct_per_year = 0.5
        self.default_installed: str | None = None   # the gateway's commissioned date

    def installed(self, name: str) -> str | None:
        return self.parts[name].installed or self.default_installed

    def age_years(self, name: str, today: str) -> float:
        start = self.installed(name)
        if start is None:
            return 0.0
        days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(start)).days
        return max(0.0, days / 365.25)

    def apower_soh(self, today: str, discharged_wh_per_unit: float) -> float:
        """SoH %: 100 new, 70 at the end of the warranty by age or throughput, whichever is
        further along; it keeps falling at that rate past the term."""
        a = self.parts["apower"]
        used = max(self.age_years("apower", today) / float(a.warranty_years),
                   discharged_wh_per_unit / (float(a.throughput_mwh) * 1e6))
        return max(0.0, 100.0 - (100.0 - RETAINED_PCT) * used)

    def pv_factor(self, today: str) -> float:
        p = self.parts["pv_panels"]
        return max(0.0, 1.0 - float(p.degradation_pct_per_year) / 100.0 * self.age_years("pv_panels", today))

    def warranty(self, name: str, today: str) -> dict:
        c, start = self.parts[name], self.installed(name)
        years = float(c.warranty_years)
        left = years - self.age_years(name, today)
        out = {"installed": start, "age_years": round(self.age_years(name, today), 2),
               "warranty_years": years, "years_left": round(max(0.0, left), 2),
               "expires": None if start is None else str(dt.date.fromisoformat(start).year + int(years)),
               "in_warranty": left > 0}
        return out
