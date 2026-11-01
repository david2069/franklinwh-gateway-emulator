"""The site's connection rules (#123): the grid operator's limits and the main breaker.

The grid operator is called the **utility** (US/CA), the **DNSP** (AU) or the **lines company**
(NZ); the fleet file accepts any of ``utility:``, ``dnsp:``, ``lines_company:`` for the same block::

    utility: {operator: ausgrid, export: limited, export_limit_w: 10000, import_limit_w: null,
              battery_export: true, export_rule_source: dnsp}
    service: {main_breaker_a: 63}

``export``: ``allowed`` (no limit), ``limited`` (``export_limit_w``; ``export_per_phase_w`` on
multi-leg sites), ``prohibited`` (zero export: solar is curtailed, the battery never exports).
``grid_charge: false`` stops the battery charging from the grid (solar or generator only), and
``grid_charge_max_w`` caps how much of its charging may come from the grid: the app's *Grid charge*
and *Max charge power* (FranklinWH PCS Technical Brief, "Grid Import Limitations").
``battery_export: false`` lets solar export but caps battery discharge at what the home uses
(often a scheme rule). Unset, the model's factory setting applies: North American models ship in
ESS Import-Only Mode (no battery export; FranklinWH PCS Technical Brief), AU/NZ models export. These are the aGate's PCS settings, so they are enforced. Without a
``utility`` block the grid code's declared export limit is tracked as before.

``service: {busbar_a}`` (North American models) sets the PCS export limit by the 120 % rule
when ``utility`` sets none: ((busbar × 1.2) − main breaker) / 1.25 amps across the legs.

The main breaker trips on grid import above its rating, after an inverse-time delay (plausible,
not measured): ``120 / (r² − 1)`` seconds at ``r`` times the rating, at once from 5×. A tripped
breaker takes the site off the grid until it is reset (``service`` action, ``reset: true``).
"""

from __future__ import annotations

from dataclasses import dataclass, fields

EXPORT = ("allowed", "limited", "prohibited")
SOURCES = ("dnsp", "utility", "lines-company", "scheme")
ALIASES = ("utility", "dnsp", "lines_company")   # fleet keys for the same block

# Defaults by operator (documented connection rules). Ausgrid: NS194, 10 kW single phase,
# 5 kW per phase.
OPERATORS = {
    "ausgrid": {"export": "limited", "export_limit_w": 10000.0, "export_per_phase_w": 5000.0},
}


@dataclass
class Utility:
    operator: str | None = None
    export: str = "allowed"
    export_limit_w: float | None = None
    export_per_phase_w: float | None = None
    import_limit_w: float | None = None
    battery_export: bool | None = None      # None: the model's factory setting (Site.battery_export)
    grid_charge: bool = True                 # app "Grid charge": may the battery charge from the grid
    grid_charge_max_w: float | None = None   # app "Max charge power": grid-sourced charging cap
    export_rule_source: str | None = None

    @classmethod
    def from_dict(cls, d: dict | None) -> "Utility | None":
        if d is None:
            return None
        d = dict(d)
        known = {f.name for f in fields(cls)}
        bad = set(d) - known
        if bad:
            raise ValueError(f"utility: unknown keys {sorted(bad)}; known {sorted(known)}")
        base = dict(OPERATORS.get(str(d.get("operator", "")).lower(), {}))
        u = cls(**{**base, **d})
        if u.export not in EXPORT:
            raise ValueError(f"utility.export must be one of {list(EXPORT)}, not {u.export!r}")
        if u.export == "limited" and u.export_limit_w is None and u.export_per_phase_w is None:
            raise ValueError("utility.export limited needs export_limit_w (or export_per_phase_w)")
        if u.export_rule_source is not None and u.export_rule_source not in SOURCES:
            raise ValueError(f"utility.export_rule_source must be one of {list(SOURCES)}")
        for k in ("export_limit_w", "export_per_phase_w", "import_limit_w", "grid_charge_max_w"):
            v = getattr(u, k)
            if v is not None and float(v) < 0:
                raise ValueError(f"utility.{k} must be ≥ 0 (or null for none)")
        return u

    def export_limit(self, legs: int) -> float | None:
        """The export limit in force (W); 0 when prohibited, None when allowed."""
        if self.export == "prohibited":
            return 0.0
        if self.export == "allowed":
            return None
        if legs > 1 and self.export_per_phase_w is not None:
            return self.export_per_phase_w * legs
        return self.export_limit_w


def pick_block(d: dict) -> dict | None:
    """The ``utility`` / ``dnsp`` / ``lines_company`` block of a gateway entry (one only)."""
    given = [k for k in ALIASES if d.get(k) is not None]
    if len(given) > 1:
        raise ValueError(f"give one of {list(ALIASES)}, not {given} (they are the same block)")
    return d[given[0]] if given else None


@dataclass
class Service:
    main_breaker_a: float
    busbar_a: float | None = None     # the panel busbar's rating, for the 120 % rule

    @classmethod
    def from_dict(cls, d: dict | None) -> "Service | None":
        if d is None:
            return None
        d = dict(d)
        bad = set(d) - {"main_breaker_a", "busbar_a"}
        if bad:
            raise ValueError(f"service: unknown keys {sorted(bad)}; known ['busbar_a', 'main_breaker_a']")
        if "main_breaker_a" not in d or float(d["main_breaker_a"]) <= 0:
            raise ValueError("service.main_breaker_a: the main breaker's rating in amps (> 0)")
        busbar = d.get("busbar_a")
        if busbar is not None and float(busbar) <= 0:
            raise ValueError("service.busbar_a: the busbar's rating in amps (> 0)")
        return cls(float(d["main_breaker_a"]), None if busbar is None else float(busbar))

    def pel_a(self) -> float | None:
        """NEC 705.12 120 % rule (FranklinWH PCS Technical Brief): the export current the
        busbar allows, ((busbar × 1.2) − main breaker) / 1.25, at least 0; None without a
        busbar rating. 200 A busbar, 200 A main → 32 A."""
        if self.busbar_a is None:
            return None
        return max(0.0, (self.busbar_a * 1.2 - self.main_breaker_a) / 1.25)

    def rating_w(self, v_ln: float, legs: int) -> float:
        return self.main_breaker_a * v_ln * legs


def trip_seconds(ratio: float) -> float | None:
    """Seconds to trip at ``ratio`` times the rating (None = never)."""
    if ratio <= 1.05:
        return None
    if ratio >= 5.0:
        return 0.0
    return 120.0 / (ratio * ratio - 1.0)
