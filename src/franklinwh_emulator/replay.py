"""Replay recorded site data as the emulator's solar and load inputs.

Sources:
* the Modbus bridge's metrics **CSV export** (``GET /api/metrics/export?range=30d&format=csv``):
  ``timestamp`` (ISO-8601 UTC or epoch), ``battery_w``, ``grid_w``, ``solar_w``, ``home_w``,
  ``soc``, ``ambient_temp_c``, …
* the Modbus bridge's **SQLite store** directly (read-only): ``metrics`` plus the downsampled
  ``metrics_archive``, optionally filtered by ``gateway_id``;
* any CSV with a ``columns`` mapping onto those names.

Only the *inputs* are replayed: solar, house load and (optionally) ambient temperature. The
battery, grid and SoC stay simulated, so the controls under test still act. ``battery`` in
``use`` additionally drives the battery at the recorded power (instead of the native mode),
so dispatches the recording contains (VPP, schedules, force charge) are reproduced and SoC
tracking tests only the energy model. M704 remote control, when enabled, still wins.

``dispatch`` in ``use`` is the alternative for scoring the *native* model: dispatches are
inferred from where the recorded battery departs from what the native mode would do (see
:func:`infer_dispatches`) and replayed as battery setpoints, like the bridge's commands. The
emulator's own limits still apply (reserve, full, rate), and between dispatches the native
mode decides. ``mode`` replays the recorded mode and reserves (``mode_name``,
``self_reserve_pct``, ``tou_reserve_pct``) as step changes. The recorded
``battery_w``/``grid_w``/``soc`` are kept for comparison: recordings carry them as
``rec_battery_w``/``rec_grid_w``/``rec_soc``, and :func:`compare` scores how closely the
emulated site tracks reality.

Time mapping (``align``):
``day``       (default) the first whole local day in the recording plays on the site's first
              day; later days follow; ``loop`` repeats the recorded span in whole days.
``absolute``  recorded timestamps are used as they are; the site clock must sit inside the
              recording.

Sign conventions are the bridge's and the emulator's: ``battery_w`` + = discharge, ``grid_w``
+ = import.
"""

from __future__ import annotations

import csv
import math
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANONICAL = ("solar_w", "home_w", "battery_w", "grid_w", "soc", "ambient_temp_c")
STATE = ("mode_name", "self_reserve_pct", "tou_reserve_pct")      # stepped, not interpolated
MODE_BY_NAME = {"Emergency Backup": 1, "Self-Consumption": 2, "TOU": 3, "Time-of-Use": 3}
RATE_W = 5000.0                                                    # per aPower


@dataclass
class ReplayConfig:
    source: str
    use: tuple[str, ...] = ("solar", "load")      # solar, load, ambient, battery, dispatch, mode
    align: str = "day"                            # day | absolute
    loop: bool = True
    max_gap: float = 600.0                        # seconds; longer gaps fall back to the model
    gateway_id: str | None = None                 # SQLite: filter metrics by gateway
    columns: dict | None = None                   # CSV: {canonical: "their column"}
    scale_solar: float = 1.0
    scale_load: float = 1.0
    dispatch_threshold_w: float = 500.0           # departure from native that counts as dispatch
    dispatch_min_s: float = 120.0                 # shorter departures are noise
    apower_units: int = 1                         # for the native rate when inferring

    @classmethod
    def from_dict(cls, d: dict | str) -> "ReplayConfig":
        if isinstance(d, str):
            d = {"source": d}
        cfg = cls(**d)
        cfg.use = tuple(cfg.use)
        known = {"solar", "load", "ambient", "battery", "dispatch", "mode"}
        bad = set(cfg.use) - known
        if bad:
            raise ValueError(f"replay.use: unknown {sorted(bad)} ({', '.join(sorted(known))})")
        if {"battery", "dispatch"} <= set(cfg.use):
            raise ValueError("replay.use: battery (follow) and dispatch (commands) are exclusive")
        if cfg.align not in ("day", "absolute"):
            raise ValueError("replay.align must be day or absolute")
        return cfg


def _parse_ts(v: str) -> float:
    v = v.strip()
    try:
        return float(v)
    except ValueError:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def load_rows(cfg: ReplayConfig) -> list[dict]:
    """Rows as {ts, solar_w, home_w, battery_w, grid_w, soc, ambient_temp_c}, sorted by ts."""
    path = Path(cfg.source).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"replay source not found: {path}")
    rows: list[dict] = []
    if path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for table in ("metrics_archive", "metrics"):
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                if not cols:
                    continue
                want = ["ts"] + [c for c in CANONICAL + STATE if c in cols]
                sql = f"SELECT {', '.join(want)} FROM {table}"
                args: tuple = ()
                if cfg.gateway_id and "gateway_id" in cols:
                    sql += " WHERE gateway_id = ?"
                    args = (cfg.gateway_id,)
                for rec in con.execute(sql, args):
                    rows.append({k: (float(v) if k == "ts" else v if k in STATE else _num(v))
                                 for k, v in zip(want, rec)})
        finally:
            con.close()
    else:
        mapping = {"ts": "timestamp", **{c: c for c in CANONICAL}, **(cfg.columns or {})}
        with open(path, newline="", encoding="utf-8") as f:
            for rec in csv.DictReader(f):
                ts_col = mapping["ts"] if mapping["ts"] in rec else ("ts" if "ts" in rec else None)
                if ts_col is None:
                    raise ValueError(f"{path}: no timestamp column ({mapping['ts']!r} or 'ts')")
                row = {"ts": _parse_ts(rec[ts_col])}
                for c in CANONICAL:
                    row[c] = _num(rec.get(mapping.get(c, c)))
                for c in STATE:
                    v = rec.get(mapping.get(c, c))
                    row[c] = v if v not in (None, "") else None
                rows.append(row)
    rows.sort(key=lambda r: r["ts"])
    dedup: list[dict] = []
    for r in rows:                                   # archive and raw can overlap
        if dedup and abs(dedup[-1]["ts"] - r["ts"]) < 1e-6:
            dedup[-1] = r
        else:
            dedup.append(r)
    if len(dedup) < 2:
        raise ValueError(f"replay source {path} has fewer than two rows")
    return dedup


class Replay:
    def __init__(self, cfg: ReplayConfig, *, local_midnight):
        """``local_midnight(ts)`` returns the epoch of the site's local midnight at or before
        ``ts`` (DST-aware); used to align recorded days to simulated days."""
        self.cfg = cfg
        self.rows = load_rows(cfg)
        self.ts = [r["ts"] for r in self.rows]
        self._midnight = local_midnight
        self._sim_anchor: float | None = None
        first = self._midnight(self.ts[0])
        # The first whole local day: today's midnight if the recording starts on it,
        # otherwise the next one.
        self.rec_anchor = first if self.ts[0] - first < 1.0 else self._midnight(first + 86400 + 7200)
        whole_days = int((self.ts[-1] - self.rec_anchor) // 86400)
        self.span = max(1, whole_days) * 86400.0

        self.dispatches = (infer_dispatches(self.rows, cfg)
                           if "dispatch" in cfg.use else [])
        self._d_starts = [d["start"] for d in self.dispatches]
        self.state_ts = [r["ts"] for r in self.rows if r.get("mode_name")]
        self.state_rows = [r for r in self.rows if r.get("mode_name")]

    def start(self, sim_ts: float) -> None:
        if self._sim_anchor is None:
            self._sim_anchor = self._midnight(sim_ts)

    def recorded_ts(self, sim_ts: float) -> float:
        if self.cfg.align == "absolute":
            return sim_ts
        self.start(sim_ts)
        off = sim_ts - self._sim_anchor
        if self.cfg.loop:
            off %= self.span
        return self.rec_anchor + off

    def sample(self, sim_ts: float) -> dict | None:
        """Interpolated recorded values at the mapped time, or None in a gap/outside."""
        t = self.recorded_ts(sim_ts)
        i = bisect_right(self.ts, t)
        if i == 0 or i >= len(self.ts):
            return None
        a, b = self.rows[i - 1], self.rows[i]
        if b["ts"] - a["ts"] > self.cfg.max_gap:
            return None
        f = (t - a["ts"]) / (b["ts"] - a["ts"])
        out = {}
        for c in CANONICAL:
            va, vb = a.get(c), b.get(c)
            out[c] = None if va is None or vb is None else va + (vb - va) * f
        return out

    def solar_w(self, sim_ts: float) -> float | None:
        if "solar" not in self.cfg.use:
            return None
        s = self.sample(sim_ts)
        v = None if s is None else s["solar_w"]
        return None if v is None else max(0.0, v) * self.cfg.scale_solar

    def load_w(self, sim_ts: float) -> float | None:
        if "load" not in self.cfg.use:
            return None
        s = self.sample(sim_ts)
        v = None if s is None else s["home_w"]
        return None if v is None else max(0.0, v) * self.cfg.scale_load

    def battery_w(self, sim_ts: float) -> float | None:
        """Recorded battery power (+ = discharge) when ``battery`` is replayed."""
        if "battery" not in self.cfg.use:
            return None
        s = self.sample(sim_ts)
        return None if s is None else s["battery_w"]

    def dispatch_w(self, sim_ts: float) -> float | None:
        """The inferred dispatch setpoint (+ = discharge) in force at ``sim_ts``, if any."""
        if not self.dispatches:
            return None
        t = self.recorded_ts(sim_ts)
        i = bisect_right(self._d_starts, t) - 1
        if i >= 0 and t < self.dispatches[i]["end"]:
            return self.dispatches[i]["setpoint_w"]
        return None

    def state(self, sim_ts: float) -> dict | None:
        """The recorded mode and reserves in force at ``sim_ts`` (``use: mode``)."""
        if "mode" not in self.cfg.use or not self.state_ts:
            return None
        t = self.recorded_ts(sim_ts)
        i = bisect_right(self.state_ts, t) - 1
        if i < 0:
            return None
        r = self.state_rows[i]
        mode = MODE_BY_NAME.get(r["mode_name"])
        if mode is None:
            return None
        out = {"native_mode": mode}
        for k, dest in (("self_reserve_pct", "self_reserve"), ("tou_reserve_pct", "tou_reserve")):
            v = _num(r.get(k))
            if v is not None:
                out[dest] = int(v)
        return out

    def ambient_c(self, sim_ts: float) -> float | None:
        if "ambient" not in self.cfg.use:
            return None
        s = self.sample(sim_ts)
        return None if s is None else s["ambient_temp_c"]


def native_battery_w(row: dict, units: int = 1) -> float | None:
    """What the native mode would do with this sample's load and solar (+ = discharge).

    Self-Consumption (and TOU, whose local behaviour is not decoded) covers home − solar,
    bounded by the rate, the reserve and a full battery. Emergency Backup only charges from
    surplus. Returns None when a needed column is missing."""
    home, solar, soc = row.get("home_w"), row.get("solar_w"), row.get("soc")
    if home is None or solar is None or soc is None:
        return None
    rate = RATE_W * max(1, units)
    net = max(-rate, min(rate, home - solar))
    mode = MODE_BY_NAME.get(row.get("mode_name") or "", 2)
    reserve = 100.0 if mode == 1 else _num(
        row.get("tou_reserve_pct" if mode == 3 else "self_reserve_pct"))
    if mode == 1:
        net = min(0.0, net)
    if net > 0 and reserve is not None and soc <= reserve + 0.5:
        net = 0.0
    if net < 0 and soc >= 99.5:
        net = 0.0
    return net


def infer_dispatches(rows: list[dict], cfg: ReplayConfig) -> list[dict]:
    """Stretches where the recorded battery departs from the native mode by more than
    ``dispatch_threshold_w`` for at least ``dispatch_min_s``: VPP, schedules, force charge.

    Each becomes {start, end, setpoint_w} in recorded time; a stretch is split where the
    battery moves more than 800 W from the running setpoint. The setpoint is the median
    recorded power, so the replay behaves like a command, not a trace: the emulator's own
    limits (reserve, full) end it if the real one ran into them."""
    out: list[dict] = []
    cur: dict | None = None
    gap = 60.0

    def close():
        if cur and cur["last"] - cur["start"] >= cfg.dispatch_min_s:
            vals = sorted(cur["vals"])
            out.append({"start": cur["start"], "end": cur["last"] + 1e-6,
                        "setpoint_w": vals[len(vals) // 2], "samples": len(vals)})

    for r in rows:
        b = r.get("battery_w")
        nat = native_battery_w(r, cfg.apower_units)
        flagged = b is not None and nat is not None and abs(b - nat) > cfg.dispatch_threshold_w
        if flagged:
            if cur and r["ts"] - cur["last"] <= gap and abs(b - cur["med"]) <= 800:
                cur["vals"].append(b)
                cur["last"] = r["ts"]
                if len(cur["vals"]) % 5 == 0:
                    v = sorted(cur["vals"])
                    cur["med"] = v[len(v) // 2]
            else:
                close()
                cur = {"start": r["ts"], "last": r["ts"], "vals": [b], "med": b}
        elif cur and r["ts"] - cur["last"] > gap:
            close()
            cur = None
    close()
    return out


def compare(rows: list[dict], gateway: str | None = None) -> dict:
    """Score emulated vs recorded: mean absolute error and bias for battery W, grid W and SoC,
    plus energy totals, over recording rows that carry ``rec_*`` columns."""
    sel = [r for r in rows if (gateway is None or r["gateway"] == gateway)
           and r.get("rec_battery_w") is not None]
    if not sel:
        return {}
    out: dict = {"samples": len(sel)}

    def score(subset):
        res = {}
        for emu, rec in (("battery_w", "rec_battery_w"), ("grid_w", "rec_grid_w"), ("soc", "rec_soc")):
            pairs = [(r[emu], r[rec]) for r in subset if r.get(rec) is not None]
            if pairs:
                err = [e - c for e, c in pairs]
                res[emu] = {"mae": round(sum(abs(x) for x in err) / len(err), 2),
                            "bias": round(sum(err) / len(err), 2)}
        return res

    out.update(score(sel))
    if any(r.get("rec_dispatch") is not None for r in sel):
        nat = [r for r in sel if r.get("rec_dispatch") is None]
        dis = [r for r in sel if r.get("rec_dispatch") is not None]
        out["native"] = {"samples": len(nat), **score(nat)}
        out["dispatched"] = {"samples": len(dis), **score(dis)}
    if len(sel) > 1:
        dt = (sel[1]["t"] - sel[0]["t"]) / 3600.0
        for emu, rec in (("grid_w", "rec_grid_w"),):
            imp_e = sum(max(0.0, r[emu]) for r in sel) * dt / 1000
            imp_r = sum(max(0.0, r[rec] or 0.0) for r in sel) * dt / 1000
            exp_e = sum(max(0.0, -r[emu]) for r in sel) * dt / 1000
            exp_r = sum(max(0.0, -(r[rec] or 0.0)) for r in sel) * dt / 1000
            out["grid_kwh"] = {"import_emulated": round(imp_e, 2), "import_recorded": round(imp_r, 2),
                               "export_emulated": round(exp_e, 2), "export_recorded": round(exp_r, 2)}
    return out


EXPORT_COLUMNS = ("timestamp", "battery_w", "grid_w", "solar_w", "home_w", "soc",
                  "ambient_temp_c", "cabinet_temp_c", "mode_name", "self_reserve_pct",
                  "tou_reserve_pct", "grid_mode")


def to_bridge_csv(rows: list[dict], path: str | Path, gateway: str | None = None) -> Path:
    """Write scenario recording rows in the Modbus bridge's metrics export format, so emulator
    runs can seed a replay or be imported into the bridge (POST /api/metrics/import)."""
    from .site import MODE_NAMES
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        w.writeheader()
        for r in rows:
            if gateway is not None and r["gateway"] != gateway:
                continue
            w.writerow({
                "timestamp": datetime.fromtimestamp(r["ts"], tz=timezone.utc).isoformat(),
                "battery_w": round(r["battery_w"], 1), "grid_w": round(r["grid_w"], 1),
                "solar_w": round(r["solar_w"], 1), "home_w": round(r["load_w"], 1),
                "soc": round(r["soc"], 2), "ambient_temp_c": r.get("ambient_c", ""),
                "cabinet_temp_c": "", "mode_name": MODE_NAMES.get(r["native_mode"], ""),
                "self_reserve_pct": "", "tou_reserve_pct": "",
                "grid_mode": "Grid Following" if r["grid_connected"] else "Grid Forming"})
    return path
