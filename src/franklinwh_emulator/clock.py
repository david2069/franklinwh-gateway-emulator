"""Clocks the site model integrates against: real, scaled (fast-forward) or manual."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class ScaledClock:
    """Wall clock running ``factor`` times faster from ``start`` (default: now)."""

    def __init__(self, factor: float, start: float | None = None):
        self.factor = float(factor)
        self._real0 = time.time()
        self._sim0 = self._real0 if start is None else float(start)

    def now(self) -> float:
        return self._sim0 + (time.time() - self._real0) * self.factor


class ManualClock:
    """Only moves when told to — for deterministic tests."""

    def __init__(self, start: float = 1_767_225_600.0):  # 2026-01-01T00:00:00Z
        self.t = float(start)

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def set(self, t: float) -> None:
        self.t = float(t)
