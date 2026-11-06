"""Timed and seeded-random weather events from the fleet file (#270).

``weather_events`` is a list; each entry darkens the sky for a while, on top of whatever the
weather model gives (open-meteo, synthetic or the bell), by that sky's haze and cloud field::

    weather_events:
      - {sky: storm, at: "13:00", for: 2h, every: 1d, times: 3}   # fixed: 3 days, 13:00-15:00
      - {sky: overcast, for: 30m..3h, chance: 0.3, per: day}      # random, from the seed

Fixed entries (``at``) start at that local time on the day the site starts, then every
``every`` (default 1d), ``times`` times (omitted: no end). Random entries (``chance``) roll
once per local day; a hit starts at a time drawn between 07:00 and 17:00 local (so it ends by
then where it can). ``for`` is a duration or a range ``low..high`` drawn per occurrence. Every
draw comes from the site's sky seed, so a run repeats exactly and grouped aGates agree.
Replayed (measured) solar is left as it is.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable

from .weather import SKIES, cloud_factor

DAY = 86400.0
KEYS = {"sky", "at", "for", "every", "times", "chance", "per"}
RANDOM_START = (7 * 3600.0, 17 * 3600.0)        # random events start in this local window


def _duration(v) -> float:
    from .scenario import parse_duration
    return parse_duration(v)


@dataclass
class Event:
    index: int
    sky: str
    dur: tuple[float, float]               # low, high seconds
    at: float | None                       # seconds after local midnight (fixed entries)
    every: float
    times: int | None
    chance: float | None

    @classmethod
    def parse(cls, i: int, d: dict) -> "Event":
        where = f"weather_events[{i}]"
        if not isinstance(d, dict):
            raise ValueError(f"{where}: an entry is a mapping, e.g. {{sky: storm, at: '13:00', for: 2h}}")
        bad = set(d) - KEYS
        if bad:
            raise ValueError(f"{where}: unknown keys {sorted(bad)}; known {sorted(KEYS)}")
        if d.get("sky") not in SKIES:
            raise ValueError(f"{where}: sky must be one of {sorted(SKIES)}")
        if "for" not in d:
            raise ValueError(f"{where}: needs `for` (a duration such as 2h, or a range 30m..3h)")
        lo, _, hi = str(d["for"]).partition("..")
        dur = (_duration(lo), _duration(hi or lo))
        if not 0 < dur[0] <= dur[1]:
            raise ValueError(f"{where}: `for` must be more than 0 and low..high in order")
        if ("at" in d) == ("chance" in d):
            raise ValueError(f"{where}: give either `at` (fixed) or `chance` (random), not both or neither")
        at = None
        if "at" in d:
            hh, _, mm = str(d["at"]).partition(":")
            try:
                at = int(hh) * 3600.0 + int(mm or 0) * 60.0
            except ValueError:
                raise ValueError(f"{where}: `at` is a local time HH:MM") from None
            if not 0 <= at < DAY:
                raise ValueError(f"{where}: `at` is a local time HH:MM")
        chance = None
        if "chance" in d:
            chance = float(d["chance"])
            if not 0 < chance <= 1:
                raise ValueError(f"{where}: chance is more than 0 and at most 1")
            if d.get("per", "day") != "day":
                raise ValueError(f"{where}: per: day is the only period")
        elif "per" in d:
            raise ValueError(f"{where}: `per` goes with `chance`")
        every = _duration(d.get("every", "1d"))
        if every <= 0:
            raise ValueError(f"{where}: every must be more than 0")
        if chance is not None and "every" in d:
            raise ValueError(f"{where}: a random entry rolls once per day; `every` is for fixed ones")
        times = d.get("times")
        if times is not None and (not isinstance(times, int) or times < 1):
            raise ValueError(f"{where}: times is a whole number, 1 or more")
        return cls(i, d["sky"], dur, at, every, times, chance)


class WeatherEvents:
    def __init__(self, specs: list | None, *, seed: int, start: float,
                 local_midnight: Callable[[float], float]):
        if specs is not None and not isinstance(specs, list):
            raise ValueError("weather_events is a list of entries")
        self.events = [Event.parse(i, d) for i, d in enumerate(specs or [])]
        self.seed = seed
        self.midnight = local_midnight
        self.day0 = local_midnight(start)
        self._rolls: dict[tuple[int, int], tuple[float, float] | None] = {}

    def _rng(self, *key) -> random.Random:
        return random.Random("|".join(map(str, (self.seed, "wx") + key)))

    def _dur(self, e: Event, k: int) -> float:
        lo, hi = e.dur
        return lo if lo == hi else self._rng(e.index, "for", k).uniform(lo, hi)

    def _roll(self, e: Event, day: int) -> tuple[float, float] | None:
        """A random entry on local day ``day`` (from the start day): (start, end) or None."""
        key = (e.index, day)
        if key not in self._rolls:
            r = self._rng(e.index, "day", day)
            hit = r.random() < e.chance
            span = None
            if hit:
                dur = self._dur(e, day)
                lo, hi = RANDOM_START
                start = r.uniform(lo, max(lo, hi - dur))
                span = (start, start + dur)
            self._rolls[key] = span
        return self._rolls[key]

    def _random_index(self, e: Event, day: int) -> int | None:
        """How many hits came before ``day`` (for ``times``), or None if this day isn't one."""
        if self._roll(e, day) is None:
            return None
        if e.times is None:
            return 0
        n = sum(1 for j in range(day) if self._roll(e, j) is not None)
        return n if n < e.times else None

    def active(self, ts: float) -> dict | None:
        """The entry darkening the sky now (the first listed wins): {sky, start, end, entry}."""
        for e in self.events:
            if e.at is not None:
                first = self.day0 + e.at
                k = math.floor((ts - first) / e.every)
                if k < 0 or (e.times is not None and k >= e.times):
                    continue
                start = first + k * e.every
                end = start + self._dur(e, k)
                if start <= ts < end:
                    return {"sky": e.sky, "start": start, "end": end, "entry": e.index}
            else:
                midnight = self.midnight(ts)
                day = round((midnight - self.day0) / DAY)
                for d, m in ((day, midnight), (day - 1, midnight - DAY)):   # one can run past midnight
                    if d < 0 or self._random_index(e, d) is None:
                        continue
                    s, t = self._roll(e, d)
                    if m + s <= ts < m + t:
                        return {"sky": e.sky, "start": m + s, "end": m + t, "entry": e.index}
        return None

    def factor(self, ts: float) -> float:
        """What the active event leaves of the sun (1.0 with none)."""
        a = self.active(ts)
        if a is None:
            return 1.0
        sky = SKIES[a["sky"]]
        return sky["haze"] * cloud_factor(self.seed + 1009 * (a["entry"] + 1), ts, sky["cover"], sky["opacity"])
