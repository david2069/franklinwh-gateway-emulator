"""House load as composable, seeded appliance components, plus the aGate's smart circuits.

``SiteConfig.loads`` is a preset name (``"sydney-family"``), a list of components, or
``{"preset": ..., "add": [...], "remove": [names], "set": {name: {field: value}}}``. With no
``loads`` the original breakfast/dinner curve is used, so old configs behave as before.

Component types (every component has ``name``, optional ``circuit`` 1–3 and ``leg``):

``base``      ``w``, ``drift`` (± fraction): always on, wandering slowly.
``daily``     the original curve: ``w`` base, ``morning_w`` @ 08:00, ``evening_w`` @ 19:00.
``schedule``  ``w``, ``on: ["10:00-14:00", ...]``, optional ``days`` (mon..sun).
``cycle``     ``w``, ``period`` (s/"30m"), ``duty`` 0–1, optional ``on`` windows: a compressor.
``events``    ``w``, ``duration``, ``per_day``, ``windows``: seeded random appliance use (kettle,
              oven, dryer); the times are fixed per (site seed, name, local date).
``hvac``      ``w``, ``cool_above`` °C, ``heat_below`` °C, ``span`` °C, optional ``on``: duty grows with
              the distance of the site's ambient temperature beyond the set points; cycles.
``ev``        ``w`` (max), ``min_w`` (6 A), ``arrive`` / ``depart`` (local HH:MM), ``kwh`` per session,
              ``mode``: immediate | offpeak (from ``start``) | solar (surplus only, between min_w
              and w). Stateful: stops when the session's kWh is delivered.

Randomness is a pure function of (site seed, component name, local date/time), so offline
and live runs see the same appliance events.
"""

from __future__ import annotations

import copy
import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

PRESETS: dict[str, list[dict]] = {
    # A Sydney family home with a 6.6 kWp aGate site: hot water on circuit 1 soaking midday
    # solar, the EV charger on circuit 2, the AC on circuit 3.
    "sydney-family": [
        {"name": "base", "type": "base", "w": 280, "drift": 0.15},
        {"name": "fridge", "type": "cycle", "w": 140, "period": "40m", "duty": 0.35},
        {"name": "cooking", "type": "schedule", "w": 1800, "on": ["17:45-18:30"]},
        {"name": "kettle", "type": "events", "w": 2200, "duration": "3m", "per_day": 5,
         "windows": ["06:30-08:30", "15:00-21:30"]},
        {"name": "dishwasher", "type": "events", "w": 1900, "duration": "50m", "per_day": 1,
         "windows": ["19:30-21:30"]},
        {"name": "evening", "type": "daily", "w": 0, "morning_w": 350, "evening_w": 650},
        {"name": "pool_pump", "type": "schedule", "w": 1100, "on": ["10:00-14:00"]},
        {"name": "hot_water", "type": "schedule", "w": 3600, "on": ["11:00-13:30"], "circuit": 1},
        {"name": "ev", "type": "ev", "w": 7000, "min_w": 1400, "arrive": "18:00",
         "depart": "07:30", "kwh": 18, "mode": "offpeak", "start": "22:00", "circuit": 2},
        {"name": "aircon", "type": "hvac", "w": 2400, "cool_above": 26, "heat_below": 15,
         "span": 8, "on": ["07:00-23:00"], "circuit": 3},
    ],
    "small-flat": [
        {"name": "base", "type": "base", "w": 180, "drift": 0.1},
        {"name": "fridge", "type": "cycle", "w": 110, "period": "45m", "duty": 0.3},
        {"name": "kettle", "type": "events", "w": 2000, "duration": "3m", "per_day": 3,
         "windows": ["06:30-08:30", "17:00-22:00"]},
        {"name": "cooking", "type": "events", "w": 1500, "duration": "25m", "per_day": 1,
         "windows": ["18:00-19:30"]},
    ],
}

# Smart circuits as the reference aGate reports them (Direct Connect 1410 Sw{n}Name).
DEFAULT_CIRCUITS = {1: "Water Heater", 2: "EV Charger", 3: "Air Conditioner"}


def _seconds(v: Any) -> float:
    from .scenario import parse_duration
    return parse_duration(v)


def _hm(text: str) -> float:
    h, m = text.strip().split(":")
    return int(h) + int(m) / 60.0


def _windows(spec: list[str] | None) -> list[tuple[float, float]]:
    out = []
    for w in spec or []:
        a, b = w.split("-")
        out.append((_hm(a), _hm(b)))
    return out


def _in_windows(hour: float, wins: list[tuple[float, float]]) -> bool:
    if not wins:
        return True
    for a, b in wins:
        if (a <= hour < b) if a <= b else (hour >= a or hour < b):   # wraps midnight
            return True
    return False


def _rng(*parts: Any) -> random.Random:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _noise(seed: int, name: str, t: float, period: float) -> float:
    """Smooth deterministic noise in [-1, 1]."""
    k, f = divmod(t / period, 1.0)
    a = _rng(seed, name, int(k)).uniform(-1, 1)
    b = _rng(seed, name, int(k) + 1).uniform(-1, 1)
    f = f * f * (3 - 2 * f)
    return a * (1 - f) + b * f


@dataclass
class Context:
    ts: float
    local: datetime
    ambient_c: float
    surplus_w: float          # solar minus the non-EV load, for solar-divert EV charging


@dataclass
class Component:
    name: str
    type: str
    spec: dict
    circuit: int | None = None
    leg: str | None = None
    forced: bool | None = None    # control API: True on / False off / None auto
    state: dict = field(default_factory=dict)

    # -- demand -----------------------------------------------------------------------
    def demand(self, ctx: Context, seed: int) -> float:
        if self.forced is False:
            return 0.0
        w = float(self.spec.get("w", 0.0))
        if self.forced is True and self.type != "ev":
            return w
        hour = ctx.local.hour + ctx.local.minute / 60 + ctx.local.second / 3600
        fn = getattr(self, f"_{self.type}")
        return max(0.0, fn(ctx, seed, w, hour))

    def _base(self, ctx, seed, w, hour):
        return w * (1 + float(self.spec.get("drift", 0.1)) * _noise(seed, self.name, ctx.ts, 1800))

    def _daily(self, ctx, seed, w, hour):
        m = float(self.spec.get("morning_w", 900)) * math.exp(-((hour - 8.0) ** 2) / 1.5)
        e = float(self.spec.get("evening_w", 1400)) * math.exp(-((hour - 19.0) ** 2) / 3.5)
        jitter = 1.0 + 0.04 * math.sin(ctx.ts / 4.0 + seed * 1.3)
        return (w + m + e) * jitter

    def _schedule(self, ctx, seed, w, hour):
        days = self.spec.get("days")
        if days and DAYS[ctx.local.weekday()] not in [d.lower()[:3] for d in days]:
            return 0.0
        return w if _in_windows(hour, _windows(self.spec.get("on"))) else 0.0

    def _cycle(self, ctx, seed, w, hour):
        if not _in_windows(hour, _windows(self.spec.get("on"))):
            return 0.0
        period = _seconds(self.spec.get("period", "30m"))
        duty = float(self.spec.get("duty", 0.5))
        phase = _rng(seed, self.name, "phase").random()
        return w if ((ctx.ts / period + phase) % 1.0) < duty else 0.0

    def _events(self, ctx, seed, w, hour):
        day = ctx.local.strftime("%Y-%m-%d")
        starts = self.state.get(day)
        if starts is None:
            rng = _rng(seed, self.name, day)
            wins = _windows(self.spec.get("windows")) or [(0.0, 24.0)]
            spans = [((b - a) if b > a else b + 24 - a) for a, b in wins]
            n = int(self.spec.get("per_day", 1))
            starts = []
            for _ in range(n):
                pick = rng.random() * sum(spans)
                for (a, _b), span in zip(wins, spans):
                    if pick < span:
                        starts.append((a + pick) % 24)
                        break
                    pick -= span
            self.state = {day: starts}                        # keep only today
        dur_h = _seconds(self.spec.get("duration", "5m")) / 3600
        return w if any(s <= hour < s + dur_h for s in starts) else 0.0

    def _hvac(self, ctx, seed, w, hour):
        if not _in_windows(hour, _windows(self.spec.get("on"))):
            return 0.0
        span = float(self.spec.get("span", 8))
        cool, heat = self.spec.get("cool_above"), self.spec.get("heat_below")
        t = ctx.ambient_c
        need = 0.0
        if cool is not None and t > cool:
            need = (t - cool) / span
        elif heat is not None and t < heat:
            need = (heat - t) / span
        duty = min(1.0, need)
        if duty <= 0:
            return 0.0
        if duty >= 0.95:
            return w
        period = 1200.0
        phase = _rng(seed, self.name, "phase").random()
        return w if ((ctx.ts / period + phase) % 1.0) < duty else 0.0

    def _ev(self, ctx, seed, w, hour):
        arrive, depart = _hm(self.spec.get("arrive", "18:00")), _hm(self.spec.get("depart", "07:30"))
        if not _in_windows(hour, [(arrive, depart)]):
            self.state.pop("session", None)
            return 0.0
        session = self._session_key(ctx.local, arrive)
        delivered = self.state.get("delivered", 0.0) if self.state.get("session") == session else 0.0
        if delivered >= float(self.spec.get("kwh", 15)) * 1000.0:
            return 0.0
        if self.forced is True:
            return w
        mode = self.spec.get("mode", "immediate")
        min_w = float(self.spec.get("min_w", 1400))
        if mode == "offpeak" and not _in_windows(hour, [(_hm(self.spec.get("start", "22:00")), depart)]):
            return 0.0
        if mode == "solar":
            return min(w, ctx.surplus_w) if ctx.surplus_w >= min_w else 0.0
        return w

    @staticmethod
    def _session_key(local: datetime, arrive: float) -> str:
        """Sessions are named by the date of the evening they started."""
        hour = local.hour + local.minute / 60
        if hour < arrive:
            from datetime import timedelta
            return (local - timedelta(days=1)).strftime("%Y-%m-%d")
        return local.strftime("%Y-%m-%d")

    # -- state --------------------------------------------------------------------------
    def commit(self, ctx: Context, served_w: float, dt: float) -> None:
        if self.type != "ev" or served_w <= 0 or dt <= 0:
            return
        arrive = _hm(self.spec.get("arrive", "18:00"))
        session = self._session_key(ctx.local, arrive)
        if self.state.get("session") != session:
            self.state = {"session": session, "delivered": 0.0}
        self.state["delivered"] += served_w * dt / 3600.0


def resolve(spec: Any) -> list[dict]:
    """Expand a preset name / list / {preset, add, remove, set} into component dicts."""
    if spec is None:
        return []
    if isinstance(spec, str):
        spec = {"preset": spec}
    if isinstance(spec, list):
        return copy.deepcopy(spec)
    if not isinstance(spec, dict):
        raise ValueError("loads must be a preset name, a list of components or a dict")
    name = spec.get("preset")
    if name is not None and name not in PRESETS:
        raise ValueError(f"unknown load preset {name!r}; choose from {sorted(PRESETS)}")
    comps = copy.deepcopy(PRESETS.get(name, []))
    remove = set(spec.get("remove", []))
    comps = [c for c in comps if c["name"] not in remove]
    for cname, changes in (spec.get("set") or {}).items():
        match = [c for c in comps if c["name"] == cname]
        if not match:
            raise ValueError(f"loads.set: no component {cname!r}")
        match[0].update(changes)
    comps += copy.deepcopy(spec.get("add", []))
    return comps


class LoadModel:
    TYPES = ("base", "daily", "schedule", "cycle", "events", "hvac", "ev")

    def __init__(self, spec: Any, *, seed: int, legs: tuple[str, ...]):
        self.seed = seed
        self.components: list[Component] = []
        names = set()
        for raw in resolve(spec):
            raw = dict(raw)
            name, typ = raw.get("name"), raw.get("type")
            if not name or typ not in self.TYPES:
                raise ValueError(f"load component needs a name and a type from {self.TYPES}: {raw}")
            if name in names:
                raise ValueError(f"duplicate load component {name!r}")
            names.add(name)
            circuit = raw.get("circuit")
            if circuit is not None and circuit not in (1, 2, 3):
                raise ValueError(f"{name}: circuit must be 1, 2 or 3")
            leg = raw.get("leg")
            if leg is not None and leg not in legs:
                raise ValueError(f"{name}: leg {leg!r} is not connected ({legs})")
            self.components.append(Component(name, typ, raw, circuit, leg))

    def by_name(self, name: str) -> Component:
        for c in self.components:
            if c.name == name:
                return c
        raise KeyError(name)


@dataclass
class Circuit:
    name: str
    on: bool = True
    soc_low_cutoff: int = 0           # islanded: shed when SoC falls below (1410 SwXSocLowSet)
    energy_wh: float = 0.0
    power_w: float = 0.0
