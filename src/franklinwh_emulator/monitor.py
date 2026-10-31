"""Who is connected, and how the emulator process is coping (#141).

Each Modbus and Direct Connect listener keeps a :class:`ClientTable`: the clients connected
now (peer, since when, requests, last request) and the refusals of the last hour. The
control API's ``GET /monitor`` adds the process: CPU %, memory, threads, asyncio tasks,
event-loop lag and uptime. Standard library only; psutil is used for memory when installed.
"""

from __future__ import annotations

import asyncio
import os
import resource
import subprocess
import sys
import threading
import time
from collections import deque

REFUSED_WINDOW_S = 3600.0


class ClientTable:
    """Clients of one listener. Mutated only on the event-loop thread."""

    def __init__(self):
        self._open: dict[int, dict] = {}
        self._next = 0
        self.refused: deque[tuple[float, str]] = deque(maxlen=10_000)   # (time, peer)
        self.accepted = 0
        self.requests = 0                    # every request on this listener, all clients

    def open(self, peer) -> dict:
        self._next += 1
        self.accepted += 1
        entry = {"id": self._next, "peer": _peer(peer), "since": time.time(), "requests": 0, "last": None}
        self._open[self._next] = entry
        return entry

    def close(self, entry: dict) -> None:
        self._open.pop(entry["id"], None)

    def request(self, entry: dict | None) -> None:
        self.requests += 1
        if entry is not None:
            entry["requests"] += 1
            entry["last"] = time.time()

    def refuse(self, peer) -> None:
        self.refused.append((time.time(), _peer(peer)))

    def refused_recent(self, now: float | None = None) -> list[tuple[float, str]]:
        now = time.time() if now is None else now
        while self.refused and self.refused[0][0] < now - REFUSED_WINDOW_S:
            self.refused.popleft()
        return list(self.refused)

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        recent = self.refused_recent(now)
        return {
            "clients": [{"peer": e["peer"], "connected_s": round(now - e["since"], 1),
                         "requests": e["requests"],
                         "idle_s": None if e["last"] is None else round(now - e["last"], 1)}
                        for e in self._open.values()],
            "accepted": self.accepted,
            "requests": self.requests,
            "refused_1h": len(recent),
            "last_refused": None if not recent else {"peer": recent[-1][1], "ago_s": round(now - recent[-1][0], 1)},
        }


def _peer(peer) -> str:
    if isinstance(peer, (tuple, list)) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    return str(peer)


# -- the process ---------------------------------------------------------------------------------
def rss_mb() -> float | None:
    """Resident memory now, MB: psutil, else /proc (Linux), else ps (macOS); None if unknown."""
    try:
        import psutil                                    # optional
        return round(psutil.Process().memory_info().rss / 1e6, 1)
    except ImportError:
        pass
    try:
        with open("/proc/self/statm") as f:
            return round(int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6, 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True,
                             text=True, timeout=2).stdout.strip()
        return round(int(out) * 1024 / 1e6, 1) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / 1e6 if sys.platform == "darwin" else peak * 1024 / 1e6, 1)   # bytes vs KB


class ProcessMonitor:
    """CPU % between two reads, and the event loop's lag (sampled once a second)."""

    LAG_EVERY_S = 1.0

    def __init__(self):
        self.started = time.monotonic()
        self._cpu = (time.monotonic(), time.process_time())
        self.lag_ms = 0.0
        self.lag_max_ms = 0.0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._sample_lag())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _sample_lag(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            t0 = loop.time()
            await asyncio.sleep(self.LAG_EVERY_S)
            self.lag_ms = max(0.0, (loop.time() - t0 - self.LAG_EVERY_S) * 1000)
            self.lag_max_ms = max(self.lag_max_ms, self.lag_ms)

    def cpu_pct(self) -> float:
        """CPU used by this process since the last call, % of one core."""
        wall, cpu = time.monotonic(), time.process_time()
        w0, c0 = self._cpu
        self._cpu = (wall, cpu)
        return round(100.0 * (cpu - c0) / (wall - w0), 1) if wall - w0 > 0 else 0.0

    def snapshot(self) -> dict:
        try:
            tasks = len(asyncio.all_tasks())
        except RuntimeError:                              # not on the loop thread
            tasks = None
        return {"pid": os.getpid(), "uptime_s": round(time.monotonic() - self.started),
                "cpu_pct": self.cpu_pct(), "rss_mb": rss_mb(), "peak_rss_mb": peak_rss_mb(),
                "threads": threading.active_count(), "asyncio_tasks": tasks,
                "loop_lag_ms": round(self.lag_ms, 1), "loop_lag_max_ms": round(self.lag_max_ms, 1)}
