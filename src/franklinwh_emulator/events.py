"""What each gateway did, and when (#140).

The action log (``GET /log``) records what was asked; this records what the gateway did on
its own: mode, reserve and battery-status changes, SoC milestones, grid trips and returns,
alarms, derating, the main breaker, FORCE, export over the limit, listeners going down and up,
and clients connecting, leaving and being refused.

``events`` in the fleet file::

    events: normal                       # off | normal (default) | verbose
    events: {level: verbose, file: logs/site-events.jsonl}

``normal`` folds repeats of one kind from one host within a minute into the first entry
(``count``), so a client reconnecting every 10 s is one line; ``verbose`` keeps each one, with
its port. ``file`` appends every entry as a JSON line. Site changes are seen when the model
steps (each page poll, Modbus read or Direct Connect request), so the time is the step's.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from collections import deque
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .site import Site, Snapshot

LEVELS = ("off", "normal", "verbose")
MAX_EVENTS = 1000
FOLD_S = 60.0


class EventLog:
    def __init__(self, level: str = "normal", file: str | None = None,
                 site_time: Callable[[float], str] | None = None,
                 clock_now: Callable[[], float] | None = None):
        if level not in LEVELS:
            raise ValueError(f"events level must be one of {list(LEVELS)}, not {level!r}")
        self.level = level
        self.file = file
        self.site_time = site_time             # ts -> the site's local ISO time
        self.clock_now = clock_now             # the site clock, for events without a step time
        self.entries: deque[dict] = deque(maxlen=MAX_EVENTS)
        self.dropped = 0                       # entries pushed out of the ring
        self._seq = 0
        self._rev = 0                          # bumped on every add or fold: ?since= reads it
        self._lock = threading.Lock()
        self._prev: dict | None = None         # the site state at the last watch()

    @classmethod
    def from_config(cls, cfg, site_time=None, clock_now=None) -> "EventLog":
        if cfg is None:
            return cls(site_time=site_time, clock_now=clock_now)
        if isinstance(cfg, str):
            return cls(cfg, site_time=site_time, clock_now=clock_now)
        if isinstance(cfg, dict):
            bad = set(cfg) - {"level", "file"}
            if bad:
                raise ValueError(f"events: unknown keys {sorted(bad)}; known ['file', 'level']")
            return cls(cfg.get("level", "normal"), cfg.get("file"), site_time=site_time, clock_now=clock_now)
        raise ValueError("events: off | normal | verbose, or {level, file}")

    @property
    def on(self) -> bool:
        return self.level != "off"

    # -- adding --------------------------------------------------------------------------------
    def add(self, kind: str, text: str, *, ts: float | None = None, fold: str | None = None,
            **data) -> dict | None:
        """Record one event. ``fold``: a key; at ``normal``, a repeat with the same kind and key
        within a minute adds to the earlier entry's ``count`` instead."""
        if not self.on:
            return None
        now = dt.datetime.now(dt.timezone.utc)
        if ts is None and self.clock_now is not None:
            ts = self.clock_now()
        with self._lock:
            if fold is not None and self.level == "normal":
                for e in reversed(self.entries):
                    if (now - e["_t"]).total_seconds() > FOLD_S:
                        break
                    if e["kind"] == kind and e.get("_fold") == fold:
                        self._rev += 1
                        e["count"] += 1
                        e["rev"] = self._rev
                        e["last_at"] = now.isoformat(timespec="seconds")
                        e["last_site_time"] = self._site_time(ts)
                        return e
            self._seq += 1
            self._rev += 1
            entry = {"seq": self._seq, "rev": self._rev, "at": now.isoformat(timespec="seconds"),
                     "site_time": self._site_time(ts),
                     "kind": kind, "text": text, "count": 1, "last_at": None, "last_site_time": None, **data,
                     "_t": now, "_fold": fold}
            if len(self.entries) == self.entries.maxlen:
                self.dropped += 1
            self.entries.append(entry)
        if self.file:
            try:
                with open(self.file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(public(entry)) + "\n")
            except OSError:
                pass                             # a log file must never stop the gateway
        return entry

    def _site_time(self, ts: float | None) -> str | None:
        return None if ts is None or self.site_time is None else self.site_time(ts)

    def recent(self, limit: int = 200, since: int = 0) -> list[dict]:
        """Entries added or folded into after revision ``since`` (0 = all), oldest first."""
        with self._lock:
            out = [public(e) for e in self.entries if e["rev"] > since]
        return out[-limit:] if limit else out

    # -- site changes ----------------------------------------------------------------------------
    def watch(self, site: "Site", snap: "Snapshot") -> None:
        """Compare the site now with the last call; record what changed."""
        if not self.on:
            return
        from .site import ALARMS, MODE_NAMES, RUN_NAMES
        reserve = site.active_reserve()
        fd, fc = site.battery_temperature_factors()
        force = site.force_status()
        cur = {
            "mode": MODE_NAMES.get(snap.native_mode, str(snap.native_mode)),
            "reserve": reserve,
            "run": RUN_NAMES.get(snap.run_status, str(snap.run_status)),
            "grid": snap.grid_connected,
            "trip": snap.trip_reason,
            "alarms": {n for n, b in ALARMS.items() if snap.alarms & (1 << b)},
            "soc": snap.soc,
            "derate": (round(fd, 2), round(fc, 2)),
            "breaker": bool(getattr(site, "main_breaker_open", False)),
            "force": None if force is None else (force["direction"], force["via"], force["target_w"]),
            "over": snap.export_over_limit_w > 1.0,
        }
        prev, self._prev = self._prev, cur
        if prev is None:
            return
        ts = snap.ts
        if cur["mode"] != prev["mode"]:
            self.add("mode", f"Mode {prev['mode']} → {cur['mode']}", ts=ts)
        if cur["reserve"] != prev["reserve"]:
            self.add("reserve", f"Reserve {prev['reserve']:g} % → {cur['reserve']:g} %", ts=ts)
        if cur["run"] != prev["run"]:
            self.add("battery", f"Battery {prev['run']} → {cur['run']} "
                                f"({abs(snap.battery_w) / 1000:.2f} kW, SoC {snap.soc:.1f} %)", ts=ts)
        if cur["grid"] != prev["grid"]:
            self.add("grid", "Grid reconnected" if cur["grid"]
                     else "Grid lost" + (f" (trip: {cur['trip']})" if cur["trip"] else ""), ts=ts)
        for name in sorted(cur["alarms"] - prev["alarms"]):
            self.add("alarm", f"Alarm raised: {name}", ts=ts)
        for name in sorted(prev["alarms"] - cur["alarms"]):
            self.add("alarm", f"Alarm cleared: {name}", ts=ts)
        if prev["soc"] > reserve + 0.05 >= cur["soc"] and cur["grid"]:
            self.add("soc", f"SoC reached the reserve ({reserve:g} %)", ts=ts)
        if prev["soc"] < 99.95 <= cur["soc"]:
            self.add("soc", "Battery full (100 %)", ts=ts)
        if cur["derate"] != prev["derate"]:
            d, c = cur["derate"]
            if (d, c) == (1.0, 1.0):
                self.add("derate", "Battery no longer derated by temperature", ts=ts)
            elif prev["derate"] == (1.0, 1.0):
                self.add("derate", f"Battery derated by temperature: discharge {d:.0%}, charge {c:.0%} "
                                   f"(battery {site.battery_temp_c():.1f} °C)", ts=ts)
        if cur["breaker"] != prev["breaker"]:
            self.add("breaker", "Main breaker tripped" if cur["breaker"] else "Main breaker reset", ts=ts)
        if cur["force"] != prev["force"] and (cur["force"] is None or prev["force"] is None
                                              or cur["force"][:2] != prev["force"][:2]):
            if cur["force"] is None:
                self.add("force", "FORCE off", ts=ts)
            else:
                d, via, w = cur["force"]
                self.add("force", f"FORCE on: {d} {abs(w)} W via {via}", ts=ts)
        if cur["over"] != prev["over"]:
            self.add("export", (f"Export over the declared limit by {snap.export_over_limit_w:.0f} W"
                                if cur["over"] else "Export back within the declared limit"), ts=ts)

    # -- clients ---------------------------------------------------------------------------------
    def client(self, listener: str, what: str, peer) -> None:
        """``what``: connected | left | refused, on ``listener`` (Modbus TCP | Direct Connect)."""
        if not self.on:
            return
        host, port = (peer[0], peer[1]) if isinstance(peer, (tuple, list)) and len(peer) >= 2 else (str(peer), None)
        where = f"{host}:{port}" if self.level == "verbose" and port is not None else host
        self.add("client", f"{listener} client {where} {what}", fold=f"{listener}|{what}|{host}",
                 peer=None if port is None else f"{host}:{port}")


def public(e: dict) -> dict:
    return {k: v for k, v in e.items() if not k.startswith("_")}
