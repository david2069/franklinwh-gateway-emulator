"""Timed, repeatable scenarios with recording and assertions.

A scenario is a fleet, a start time, timed steps and checks::

    name: outage-during-vpp
    description: Grid fails mid-dispatch; battery must carry the house, then reconnect.
    start: "2026-01-15T17:30:00+10:00"     # ISO 8601; naive = the first gateway's local time
    duration: 2h                           # 90s, 10m, 2h, 1d or seconds
    record_every: 10s
    fleet:                                 # same schema as a fleet file
      gateways:
        - {id: home, seed: 7, grid_code: ausgrid, initial_soc: 80,
           location: {latitude: -33.87, longitude: 151.21, timezone: Australia/Sydney}}
    steps:
      - {at: 0,   do: load, with: {w: 1500}}
      - {at: 5m,  do: vpp,  with: {watts: -5000}}         # control-API vocabulary
      - {at: 15m, do: grid, with: {available: false}}
      - {at: 16m, expect: {grid_connected: false, grid_w: 0, battery_w: ">0"}}
      - {at: 45m, do: grid, with: {restore: true}}
      - {at: 47m, expect: {grid_connected: true}}
    checks: [energy_balance, soc_bounds, export_limit, reserve_floor, islanded_no_flow,
             reconnect_delay]

``do`` is any control-API action (see :mod:`.control`); ``gateway`` picks one gateway (default:
every gateway). ``expect`` maps state fields (dotted for nested, e.g. ``csip.source``) to a value
or a comparison string: ``">0"``, ``"<=10000"``, ``"==3"``, ``"!=0"``, ``"~5000"`` (±1 %, min 1).

**Offline** runs (default) step a manual clock with no sockets, so the same scenario produces
identical rows every time. **Live** runs start the servers on a scaled clock so an external
controller (a bridge) takes part; inputs are identical, timing is not.
"""

from __future__ import annotations

import csv
import json
import operator
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any

from .clock import ManualClock, ScaledClock
from .control import ApiError, apply, gateway_state
from .gateway import Fleet, Gateway

DEFAULT_START = "2026-01-15T12:00:00"
DEFAULT_CHECKS = ("energy_balance", "soc_bounds", "export_limit", "reserve_floor",
                  "islanded_no_flow", "reconnect_delay")

RECORD_FIELDS = (  # plus t, ts, local_time, utc_offset_hours and per-leg columns
    "solar_w", "load_w", "battery_w", "grid_w", "soc", "native_mode", "remote",
    "grid_connected", "trip_reason", "trip_stage", "v_ln", "hz", "var",
    "ambient_c", "solar_available_w", "solar_clipped_w", "load_shed_w", "inverter_state", "run_status",
    "alarms", "throttle_pct", "export_limit_w", "declared_export_limit_w",
    "export_over_limit_w", "der_connected", "csip_source", "generator_w", "gen_state",
)
RECORD_COUNTERS = ("grid_import_wh", "grid_export_wh", "pv_wh", "batt_charge_wh", "batt_discharge_wh")

_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?")
_UNITS = {None: 1.0, "": 1.0, "ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_duration(v: Any) -> float:
    """Seconds from 90, "90s", "10m", "2h", "1d" or compounds like "1h30m", "2m9s"."""
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v).replace(" ", "")
    parts = list(_PART.finditer(text))
    if not parts or "".join(p.group(0) for p in parts) != text:
        raise ValueError(f"bad duration {v!r} (use e.g. 90s, 10m, 2h, 1h30m)")
    return sum(float(p.group(1)) * _UNITS[p.group(2)] for p in parts)


# -- expectations -------------------------------------------------------------------
_OPS = (("<=", operator.le), (">=", operator.ge), ("==", operator.eq), ("!=", operator.ne),
        ("<", operator.lt), (">", operator.gt), ("~", None))


def _lookup(state: dict, path: str) -> Any:
    node: Any = state
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


def _coerce(text: str) -> Any:
    low = text.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null"):
        return None
    try:
        return float(text)
    except ValueError:
        return text.strip()


def check_expectation(actual: Any, expected: Any) -> bool:
    if isinstance(expected, str):
        for sym, op in _OPS:
            if expected.startswith(sym):
                target = _coerce(expected[len(sym):])
                if op is None:                                   # approximately
                    tol = max(1.0, abs(float(target)) * 0.01)
                    return actual is not None and abs(float(actual) - float(target)) <= tol
                try:
                    return bool(op(actual, target))
                except TypeError:
                    return False
        return actual == expected
    if isinstance(expected, bool) or expected is None:
        return actual == expected
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(actual - expected) <= 1e-6 * max(1.0, abs(expected)) + 1e-9
    return actual == expected


# -- model --------------------------------------------------------------------------
@dataclass
class Step:
    at: float
    gateway: str | None = None
    do: str | None = None
    args: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)
    label: str = ""


@dataclass
class Scenario:
    name: str
    fleet: dict
    steps: list[Step]
    duration_s: float
    start: str = DEFAULT_START
    record_every_s: float = 10.0
    checks: tuple[str, ...] = DEFAULT_CHECKS
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        from .gateway import fix_bool_keys
        d = fix_bool_keys(d)
        steps = []
        for i, raw in enumerate(d.get("steps", [])):
            raw = dict(raw)
            at = parse_duration(raw.pop("at", 0))
            step = Step(at=at, gateway=raw.pop("gateway", None), do=raw.pop("do", None),
                        args=dict(raw.pop("with", {}) or {}),
                        expect=dict(raw.pop("expect", {}) or {}),
                        label=raw.pop("label", f"step {i + 1}"))
            if raw:
                raise ValueError(f"{step.label}: unknown keys {sorted(raw)}")
            if not step.do and not step.expect:
                raise ValueError(f"{step.label}: needs `do` or `expect`")
            steps.append(step)
        steps.sort(key=lambda s: s.at)
        duration = parse_duration(d.get("duration", max([s.at for s in steps] + [0]) + 60))
        checks = d.get("checks", list(DEFAULT_CHECKS))
        bad = set(checks) - set(INVARIANTS)
        if bad:
            raise ValueError(f"unknown checks {sorted(bad)}; choose from {sorted(INVARIANTS)}")
        if "fleet" not in d:
            raise ValueError("scenario needs a `fleet:` (same schema as a fleet file)")
        return cls(name=d.get("name", "scenario"), description=d.get("description", ""),
                   fleet=d["fleet"], steps=steps, duration_s=duration,
                   start=str(d.get("start", DEFAULT_START)),
                   record_every_s=parse_duration(d.get("record_every", 10)),
                   checks=tuple(checks))

    @classmethod
    def load(cls, source: str | Path | dict) -> "Scenario":
        if isinstance(source, dict):
            return cls.from_dict(source)
        from .gateway import load_yaml
        path = Path(source)
        if not path.exists():
            path = builtin_path(str(source))
        return cls.from_dict(load_yaml(str(path)))

    def start_epoch(self) -> float:
        """``start`` as epoch seconds. A naive time is the first gateway's wall clock:
        its location timezone (DST-aware), else tz_offset_hours, else the host's zone."""
        dt = datetime.fromisoformat(self.start)
        if dt.tzinfo is None:
            gws = self.fleet.get("gateways") or [{}]
            g0 = {**self.fleet.get("defaults", {}), **gws[0]}
            zone = (g0.get("location") or {}).get("timezone")
            if zone:
                from zoneinfo import ZoneInfo
                dt = dt.replace(tzinfo=ZoneInfo(zone))
            elif g0.get("tz_offset_hours") is not None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=float(g0["tz_offset_hours"]))))
            else:
                return time.mktime(dt.timetuple())
        return dt.timestamp()


# -- invariants ---------------------------------------------------------------------
def _inv_energy_balance(gw, st, prev):
    err = st["load_w"] - (st["solar_w"] + st["grid_w"] + st["battery_w"] + st.get("generator_w", 0.0))
    return None if abs(err) < 1.0 else f"energy imbalance {err:+.1f} W"


def _inv_soc_bounds(gw, st, prev):
    return None if -1e-6 <= st["soc"] <= 100 + 1e-6 else f"SoC out of bounds {st['soc']:.2f}"


def _inv_export_limit(gw, st, prev):
    """An enforced (soft) limit must be met within the grid code's response envelope:
    a breach over 50 W lasting longer than two export settling times (plus the dead time) is a
    violation. With instant responses (``response: null``) any breach is."""
    lim = st["export_limit_w"]
    if lim is None or -st["grid_w"] <= lim + 1.0:
        return None
    timing = gw.site.grid.response
    if timing is not None:
        since = gw.site.export_breach_since
        grace = 2 * timing.export_settle_s + timing.start_s
        now = gw.site._t
        if since is None or now - since <= grace:
            return None
        return (f"export {-st['grid_w']:.0f} W above enforced limit {lim:.0f} W "
                f"for {now - since:.0f} s (> {grace:.0f} s response envelope)")
    return f"export {-st['grid_w']:.0f} W above enforced limit {lim:.0f} W"


def _inv_reserve_floor(gw, st, prev):
    site = gw.site
    if not st["grid_connected"] or st["battery_w"] <= 50:
        return None
    floor = site.active_reserve()
    if prev is not None and st["soc"] < floor - 0.5 and st["soc"] < prev["soc"] - 1e-6:
        return f"discharging {st['battery_w']:.0f} W below reserve ({st['soc']:.1f} < {floor:.0f} %)"
    return None


def _inv_islanded_no_flow(gw, st, prev):
    if st["grid_connected"] or abs(st["grid_w"]) < 1e-6:
        return None
    return f"islanded but grid {st['grid_w']:+.0f} W"


INVARIANTS = {
    "energy_balance": _inv_energy_balance,
    "soc_bounds": _inv_soc_bounds,
    "export_limit": _inv_export_limit,
    "reserve_floor": _inv_reserve_floor,
    "islanded_no_flow": _inv_islanded_no_flow,
    "reconnect_delay": None,          # checked from the relay event log at the end
}


# -- results ------------------------------------------------------------------------
@dataclass
class Result:
    scenario: str
    mode: str
    rows: list[dict] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    expectations: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.violations and not self.errors and all(e["ok"] for e in self.expectations)

    def write(self, out_dir: str | Path) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        if self.rows:
            fields = list(dict.fromkeys(k for r in self.rows for k in r))
            with open(out / "timeseries.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(self.rows)
        report = {"scenario": self.scenario, "mode": self.mode, "passed": self.passed,
                  "summary": self.summary, "expectations": self.expectations,
                  "violations": self.violations[:500], "errors": self.errors}
        (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        (out / "report.txt").write_text(self.text())
        return out

    def text(self) -> str:
        lines = [f"scenario {self.scenario} ({self.mode}): "
                 f"{'PASS' if self.passed else 'FAIL'}"]
        for gid, s in self.summary.items():
            k = s["kwh"]
            lines.append(f"  {gid}: " + "  ".join(f"{n} {v:.2f}" for n, v in k.items()) + " kWh")
            lines.append(f"    export breach {s['export_breach_s']:.0f} s / "
                         f"{s['export_breach_wh']:.0f} Wh · relay events "
                         f"{len(s['relay_events'])} · SoC {s['soc_start']:.1f}→{s['soc_end']:.1f} %"
                         f" · islanded {s['islanded_s']:.0f} s")
            rc = s.get("replay_compare")
            if rc:
                parts = [f"{k} MAE {v['mae']:.1f} bias {v['bias']:+.1f}"
                         for k, v in rc.items() if isinstance(v, dict) and "mae" in v]
                lines.append(f"    vs recorded ({rc['samples']} samples): " + " · ".join(parts))
                for part in ("native", "dispatched"):
                    sub = rc.get(part)
                    if sub and sub.get("samples"):
                        ps = [f"{k} MAE {v['mae']:.1f} bias {v['bias']:+.1f}"
                              for k, v in sub.items() if isinstance(v, dict)]
                        lines.append(f"      {part} ({sub['samples']}): " + " · ".join(ps))
        for e in self.expectations:
            mark = "ok  " if e["ok"] else "FAIL"
            lines.append(f"  {mark} t={e['t']:.0f}s {e['gateway']} {e['field']} "
                         f"expected {e['expected']!r} got {e['actual']!r}")
        for v in self.violations[:20]:
            lines.append(f"  VIOLATION t={v['t']:.0f}s {v['gateway']} {v['check']}: {v['detail']}")
        if len(self.violations) > 20:
            lines.append(f"  … {len(self.violations) - 20} more violations")
        for e in self.errors:
            lines.append(f"  ERROR t={e['t']:.0f}s {e['gateway']} {e['step']}: {e['error']}")
        return "\n".join(lines)


# -- runner -------------------------------------------------------------------------
class Runner:
    def __init__(self, scenario: Scenario):
        self.sc = scenario

    def _targets(self, fleet: Fleet, step: Step) -> list[Gateway]:
        if step.gateway is None:
            return list(fleet.gateways.values())
        if step.gateway not in fleet.gateways:
            raise ValueError(f"{step.label}: no gateway {step.gateway!r}")
        return [fleet.gateways[step.gateway]]

    def _do_step(self, fleet: Fleet, step: Step, t: float, res: Result) -> None:
        for gw in self._targets(fleet, step):
            if step.do:
                try:
                    apply(gw, step.do, dict(step.args))
                except (ApiError, ValueError, KeyError) as e:
                    res.errors.append({"t": t, "gateway": gw.config.id, "step": step.label,
                                       "error": str(e)})
            if step.expect:
                state = gateway_state(gw)
                for path, expected in step.expect.items():
                    try:
                        actual = _lookup(state, path)
                        ok = check_expectation(actual, expected)
                    except KeyError:
                        actual, ok = "<missing field>", False
                    res.expectations.append({"t": t, "gateway": gw.config.id,
                                             "field": path, "expected": expected,
                                             "actual": actual, "ok": ok, "step": step.label})

    def _sample(self, fleet: Fleet, t: float, res: Result, prev: dict) -> None:
        for gid, gw in fleet.gateways.items():
            snap = gw.site.step()
            st = {k: getattr(snap, k) for k in RECORD_FIELDS}
            row = {"t": round(t, 3), "ts": snap.ts,
                   "local_time": gw.site.local_datetime(snap.ts).isoformat(timespec="seconds"),
                   "utc_offset_hours": gw.site.utc_offset_hours(snap.ts),
                   "gateway": gid, **st}
            for ph, leg in snap.phases.items():
                row[f"{ph}_v"] = round(leg.v_ln, 1)
                row[f"{ph}_grid_w"] = round(leg.grid_w, 1)
            for name, w in snap.loads.items():
                row[f"appl_{name}"] = w
            for n, w in snap.circuit_w.items():
                row[f"circuit{n}_w"] = round(w, 1)
            if gw.site.replay is not None:
                rec = gw.site.replay.sample(snap.ts) or {}
                row["rec_battery_w"] = rec.get("battery_w")
                row["rec_grid_w"] = rec.get("grid_w")
                row["rec_soc"] = rec.get("soc")
                row["rec_dispatch"] = gw.site.replay.dispatch_w(snap.ts)
            # Appended after the original columns (#89): cabinet temperature, the sky (with a
            # weather model) and the running lifetime counters.
            row["cabinet_c"] = round(snap.cabinet_c, 2)
            if gw.site.weather is not None:
                row["sky"] = gw.site.weather.cfg.sky
            for k in RECORD_COUNTERS:
                row[k] = round(getattr(snap, k), 1)
            row["generator_wh"] = round(gw.site.gen_wh, 1)
            res.rows.append(row)
            for name in self.sc.checks:
                fn = INVARIANTS[name]
                if fn is None:
                    continue
                detail = fn(gw, st, prev.get(gid))
                if detail:
                    res.violations.append({"t": t, "gateway": gid, "check": name,
                                           "detail": detail})
            prev[gid] = st

    def _finish(self, fleet: Fleet, res: Result, t0: float, base: dict) -> None:
        for gid, gw in fleet.gateways.items():
            site = gw.site
            end = site._counters()
            rows = [r for r in res.rows if r["gateway"] == gid]
            dt = self.sc.record_every_s
            res.summary[gid] = {
                "kwh": {k: round((end[k] - base[gid]["counters"][k]) / 1000.0, 3) for k in end},
                "export_breach_s": site.export_breach_s - base[gid]["breach_s"],
                "export_breach_wh": site.export_breach_wh - base[gid]["breach_wh"],
                "relay_events": [{**e, "t": round(e["ts"] - t0, 1)}
                                 for e in site.relay_events[base[gid]["relay_n"]:]],
                "soc_start": rows[0]["soc"] if rows else site.soc,
                "soc_end": site.soc,
                "islanded_s": sum(dt for r in rows if not r["grid_connected"]),
                "seconds_by_mode": {
                    str(m): sum(dt for r in rows if r["native_mode"] == m)
                    for m in sorted({r["native_mode"] for r in rows})},
            }
            if site.replay is not None:
                from .replay import compare
                res.summary[gid]["replay_compare"] = compare(res.rows, gid)
                if site.replay.dispatches:
                    res.summary[gid]["replay_dispatches"] = [
                        {"start": site.local_datetime(d["start"]).isoformat(timespec="seconds"),
                         "minutes": round((d["end"] - d["start"]) / 60, 1),
                         "setpoint_w": d["setpoint_w"]} for d in site.replay.dispatches]
            if "reconnect_delay" in self.sc.checks:
                need = site.grid.es_delay_s
                for e in res.summary[gid]["relay_events"]:
                    nf = e.get("normal_for")
                    if e["event"] == "close" and nf is not None and nf < need - 1e-6:
                        res.violations.append({
                            "t": e["t"], "gateway": gid, "check": "reconnect_delay",
                            "detail": f"reclosed after {nf:.1f} s of healthy grid (< {need:.0f} s)"})

    @staticmethod
    def _baseline(fleet: Fleet) -> dict:
        return {gid: {"counters": gw.site._counters(), "breach_s": gw.site.export_breach_s,
                      "breach_wh": gw.site.export_breach_wh,
                      "relay_n": len(gw.site.relay_events)}
                for gid, gw in fleet.gateways.items()}

    def run(self) -> Result:
        """Offline: deterministic, no sockets, as fast as the CPU allows."""
        t0 = self.sc.start_epoch()
        clock = ManualClock(t0)
        fleet = Fleet.from_dict(self.sc.fleet, clock=clock)
        res = Result(self.sc.name, "offline")
        base = self._baseline(fleet)
        prev: dict = {}
        events = sorted({*(s.at for s in self.sc.steps),
                         *(i * self.sc.record_every_s
                           for i in range(int(self.sc.duration_s // self.sc.record_every_s) + 1))})
        pending = list(self.sc.steps)
        sample_every = self.sc.record_every_s
        for t in events:
            if t > self.sc.duration_s + 1e-9:
                break
            clock.set(t0 + t)
            while pending and pending[0].at <= t + 1e-9:
                self._do_step(fleet, pending.pop(0), t, res)
            if abs(t / sample_every - round(t / sample_every)) < 1e-9:
                self._sample(fleet, t, res, prev)
        self._finish(fleet, res, t0, base)
        return res

    def run_live(self, *, time_scale: float = 1.0, control_port: int | None = None,
                 on_start=None) -> Result:
        """Servers up on a scaled clock; external controllers can connect. ``on_start``
        is called with the started fleet (e.g. to print addresses)."""
        t0 = self.sc.start_epoch()
        clock = ScaledClock(time_scale, start=t0)
        fleet = Fleet.from_dict(self.sc.fleet, clock=clock)
        res = Result(self.sc.name, f"live x{time_scale:g}")
        fleet.start(control_port=control_port)
        try:
            if on_start:
                on_start(fleet)
            base = self._baseline(fleet)
            prev: dict = {}
            pending = list(self.sc.steps)
            next_sample = 0.0
            while True:
                t = clock.now() - t0
                while pending and pending[0].at <= t:
                    self._do_step(fleet, pending.pop(0), t, res)
                if t >= next_sample:
                    self._sample(fleet, t, res, prev)
                    next_sample += self.sc.record_every_s
                if t >= self.sc.duration_s:
                    break
                time.sleep(min(0.05, max(0.001, self.sc.record_every_s / time_scale / 4)))
            self._finish(fleet, res, t0, base)
        finally:
            fleet.stop()
        return res


def run_scenario(source: str | Path | dict, *, live: bool = False, **kw) -> Result:
    sc = Scenario.load(source)
    runner = Runner(sc)
    return runner.run_live(**kw) if live else runner.run()


# -- built-in scenarios ----------------------------------------------------------------
def builtin_names() -> list[str]:
    root = resources.files("franklinwh_emulator.scenarios")
    return sorted(p.name[:-5] for p in root.iterdir() if p.name.endswith(".yaml"))


def builtin_path(name: str) -> Path:
    root = resources.files("franklinwh_emulator.scenarios")
    path = root.joinpath(f"{name}.yaml")
    if not path.is_file():
        raise FileNotFoundError(f"no scenario file or built-in {name!r}; "
                                f"built-ins: {', '.join(builtin_names())}")
    return Path(str(path))
