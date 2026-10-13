"""Generator and automatic generator start (AGS) behind the aGate's generator input.

Configuration is the Direct Connect ``1901`` block, with the field names and defaults from
the reference aGate (V12R02B30D06, no generator fitted, read 2026-10-07):

    genEn 0, manuSw 0, genModel "", genRatedPower 0, genOptiPPoint 70, startDelTime 1800,
    genStartElec 20, genCloseElec 80, gridVoltCheck 3, mode 2,
    charge1En 1 11:00–23:59 (charge2/3 off), oilmanoEn 0, manoFre 7, manoDate 0,
    manoStartTime "", manoTime 5, manoManExit 0

Plus hardware facts that are not in the protocol (``installed``, start/warm-up/cooldown
times, minimum run, fuel).

Semantics: decoding from franklinwh-local-bridge ``generator.py``, the rest interpreted:

* ``genStat`` 0 standby/off · 1 running · 2 cooldown · 3 fault.
* ``manuSw`` 0 unset · 1 auto-schedule · 2 manual.
* ``charge1..3`` are the generator's **operating windows** (FranklinWH: "up to 3
  non-overlapping operating periods"). Auto starts only inside an enabled window; with no
  window enabled, any time.
* **AGS is SoC-based only** (no PV-loss trigger on the device): while islanded, once the
  outage has lasted ``startDelTime`` and SoC ≤ ``genStartElec``, it starts; it stops at
  SoC ≥ ``genCloseElec`` after the minimum run. *Interpretation:* ``startDelTime`` as the
  outage age before an auto start is allowed is not confirmed.
* The aGate never parallels a generator with the grid. On-grid (an exercise run, or a
  manual start) it runs **unloaded** with its relay open; if the grid returns while it
  carries the house, it hands back and cools down.
* Loaded, it serves the house and charges the battery so it runs near ``genOptiPPoint`` %
  of rated (and up to 100 % when the house needs it). The battery covers anything above
  rated.
* Exercise (``oilmanoEn``): every ``manoFre`` days at ``manoStartTime`` for ``manoTime``
  minutes. *Interpretation:* ``manoDate`` is taken as a day offset in the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

STATE_OFF, STATE_RUNNING, STATE_COOLDOWN, STATE_FAULT = 0, 1, 2, 3
STATE_NAMES = {0: "Standby / OFF", 1: "Running / ON", 2: "Cooldown", 3: "Fault"}
MANUAL, AUTO = 2, 1

# 1901 fields in the order the reference aGate sends them (writable per franklinwh_local).
BLOCK_DEFAULTS: dict = {
    "genEn": 0, "gridVoltCheck": 3, "mode": 2, "genModel": "", "genRatedPower": 0,
    "genOptiPPoint": 70, "startDelTime": 1800, "genStartElec": 20, "genCloseElec": 80,
    "manuSw": 0,
    "charge1En": 1, "charge1StartTime": "11:00", "charge1EndTime": "23:59",
    "charge2En": 0, "charge2StartTime": "00:00", "charge2EndTime": "00:00",
    "charge3En": 0, "charge3StartTime": "00:00", "charge3EndTime": "00:00",
    "oilmanoEn": 0, "manoFre": 7, "manoDate": 0, "manoStartTime": "", "manoTime": 5,
    "manoManExit": 0,
}


@dataclass
class GeneratorHardware:
    installed: bool = False
    crank_s: float = 8.0          # cranking until the engine fires
    warmup_s: float = 30.0        # running, relay still open
    cooldown_s: float = 120.0     # unloaded run-on after a stop
    min_run_s: float = 900.0      # auto runs at least this long once loaded
    fuel_l: float | None = None   # None = unlimited
    idle_lph: float = 0.6         # Willans line: fuel l/h = idle + per_kwh × kW
    per_kwh_l: float = 0.28


class Generator:
    def __init__(self, cfg: dict | None):
        cfg = dict(cfg or {})
        hw_keys = set(GeneratorHardware.__dataclass_fields__)
        self.hw = GeneratorHardware(**{k: cfg.pop(k) for k in list(cfg) if k in hw_keys})
        aliases = {"rated_w": "genRatedPower", "model": "genModel", "enabled": "genEn",
                   "start_below_soc": "genStartElec", "stop_above_soc": "genCloseElec",
                   "start_delay_s": "startDelTime", "optimal_pct": "genOptiPPoint",
                   "mode": "manuSw"}
        block = dict(BLOCK_DEFAULTS)
        if self.hw.installed:                    # sensible defaults for a fitted unit
            block.update({"genEn": 1, "genRatedPower": 7000, "genModel": "emulated", "manuSw": AUTO})
        for k, v in cfg.items():
            key = aliases.get(k, k)
            if key == "manuSw" and isinstance(v, str):
                v = {"auto": AUTO, "manual": MANUAL}[v]
            if key == "genEn":
                v = int(bool(v))
            if key not in block:
                raise ValueError(f"generator: unknown setting {k!r}")
            block[key] = v
        self.block = block
        self.state = STATE_OFF
        self.fault: str | None = None
        self.manual_on = False
        self.fuel_l = self.hw.fuel_l
        self.relay_closed = False
        self._phase_since: float | None = None   # start of crank/warm-up/run/cooldown
        self._phase = "off"                      # off | crank | warmup | run | cooldown
        self._loaded_since: float | None = None
        self._exercise_until: float | None = None
        self._last_exercise_day: str | None = None
        self.forced_fault: str | None = None     # "fail-start" | "trip" (control)
        self.energy_wh = 0.0
        self.starts = 0

    # -- config ------------------------------------------------------------------------------
    @property
    def rated_w(self) -> float:
        return float(self.block.get("genRatedPower") or 0)

    @property
    def enabled(self) -> bool:
        return bool(self.block.get("genEn"))

    @property
    def usable(self) -> bool:
        return self.hw.installed and self.enabled and self.rated_w > 0

    def windows(self) -> list[tuple[int, int]]:
        out = []
        for n in (1, 2, 3):
            if self.block.get(f"charge{n}En"):
                out.append((_mins(self.block[f"charge{n}StartTime"]),
                            _mins(self.block[f"charge{n}EndTime"])))
        return out

    def in_window(self, minute_of_day: int) -> bool:
        wins = self.windows()
        if not wins:
            return True
        for a, b in wins:
            if (a <= minute_of_day <= b) if a <= b else (minute_of_day >= a or minute_of_day <= b):
                return True
        return False

    # -- control -------------------------------------------------------------------------------
    def start_manual(self) -> None:
        self.manual_on = True

    def stop_manual(self) -> None:
        self.manual_on = False

    def clear_fault(self) -> None:
        self.fault = None
        self.forced_fault = None
        if self.state == STATE_FAULT:
            self.state, self._phase, self._phase_since = STATE_OFF, "off", None

    def refuel(self, litres: float | None) -> None:
        self.fuel_l = litres
        if self.fault == "out-of-fuel":
            self.clear_fault()

    # -- dynamics -----------------------------------------------------------------------------
    def _want_run(self, ts: float, dt: float, local_dt, islanded: bool, islanded_for: float,
                  soc: float) -> tuple[bool, str, float]:
        """(run wanted, why, when within this step it became so)."""
        b = self.block
        t0 = ts - dt
        if self._exercise_until is not None:
            if ts < self._exercise_until:
                return True, "exercise", t0
            end, self._exercise_until = self._exercise_until, None
            if not self.manual_on and b.get("manuSw") != MANUAL and not islanded:
                return False, "exercise-done", max(t0, end)
        if b.get("oilmanoEn") and b.get("manoStartTime"):
            day = local_dt.strftime("%Y-%m-%d")
            freq = max(1, int(b.get("manoFre") or 7))
            m = _mins(b["manoStartTime"])
            start = local_dt.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
            crossed = ts - dt < start.timestamp() <= ts      # the start time fell in this step
            if ((local_dt.toordinal() - int(b.get("manoDate") or 0)) % freq == 0
                    and crossed and self._last_exercise_day != day):
                self._last_exercise_day = day
                self._exercise_until = start.timestamp() + 60 * float(b.get("manoTime") or 5)
                return True, "exercise", start.timestamp()
        if b.get("manuSw") == MANUAL:
            return self.manual_on, "manual", t0
        if self.manual_on:
            return True, "manual", t0
        # AUTO (or unset with the feature enabled): SoC-based, islanded only.
        running = self._phase in ("crank", "warmup", "run")
        if not islanded:
            return False, "grid", t0
        minute = local_dt.hour * 60 + local_dt.minute
        if running:
            loaded_for = 0.0 if self._loaded_since is None else ts - self._loaded_since
            if soc >= float(b.get("genCloseElec", 80)) and loaded_for >= self.hw.min_run_s:
                return False, "soc-reached", t0
            return True, "auto", t0
        delay = float(b.get("startDelTime") or 0)
        if (islanded_for >= delay and soc <= float(b.get("genStartElec", 20))
                and self.in_window(minute)):
            return True, "auto", max(t0, ts - islanded_for + delay)
        return False, "idle", t0

    def update(self, ts: float, dt: float, local_dt, islanded: bool, islanded_for: float,
               soc: float) -> None:
        """Advance the start/stop state machine over ``dt`` ending at ``ts``."""
        if not self.usable:
            self.state, self._phase, self.relay_closed = STATE_OFF, "off", False
            return
        if self.fault:
            self.state, self.relay_closed = STATE_FAULT, False
            self._phase = "off"
            return
        want, why, at = self._want_run(ts, dt, local_dt, islanded, islanded_for, soc)
        ph = self._phase
        if want and ph in ("off", "cooldown"):
            if self.fuel_l is not None and self.fuel_l <= 0:
                self._set_fault("out-of-fuel")
                return
            self._phase, self._phase_since = "crank", at
            self.starts += 1
        elif not want and ph in ("crank", "warmup", "run"):
            self._phase, self._phase_since = "cooldown", at
            self._loaded_since = None
        # Chain stage transitions inside one step, each from when the previous one ended.
        while True:
            ph, since = self._phase, (self._phase_since if self._phase_since is not None else ts)
            if ph == "crank":
                if self.forced_fault == "fail-start":
                    if ts - since >= 3 * self.hw.crank_s:   # three cranking attempts
                        self._set_fault("fail-to-start")
                        return
                    break
                if ts - since < self.hw.crank_s:
                    break
                self._phase, self._phase_since = "warmup", since + self.hw.crank_s
            elif ph == "warmup":
                if ts - since < self.hw.warmup_s:
                    break
                self._phase, self._phase_since = "run", since + self.hw.warmup_s
            elif ph == "cooldown":
                if ts - since < self.hw.cooldown_s:
                    break
                self._phase, self._phase_since = "off", None
            else:
                break
        ph = self._phase
        self.state = (STATE_RUNNING if ph in ("crank", "warmup", "run")
                      else STATE_COOLDOWN if ph == "cooldown" else STATE_OFF)
        # The aGate never parallels the generator with the grid.
        self.relay_closed = ph == "run" and islanded
        if self.relay_closed and self._loaded_since is None:
            self._loaded_since = self._phase_since if self._phase_since is not None else ts
        elif not self.relay_closed:
            self._loaded_since = None
        if self.forced_fault == "trip" and self.relay_closed:
            self._set_fault("tripped")

    def _set_fault(self, why: str) -> None:
        self.fault = why
        self.state, self._phase, self.relay_closed = STATE_FAULT, "off", False
        self._loaded_since = None

    def output_w(self, net_load_w: float, max_charge_w: float) -> float:
        """Power the generator delivers when its relay is closed: the house's deficit
        (``net_load_w`` = load − solar, may be negative), plus battery charging up to the
        optimal operating point, within the battery's remaining charge headroom; at most
        rated."""
        if not self.relay_closed:
            return 0.0
        rated = self.rated_w
        deficit = max(0.0, net_load_w)
        headroom = max(0.0, max_charge_w - max(0.0, -net_load_w))   # after any PV surplus
        target = max(deficit, rated * float(self.block.get("genOptiPPoint") or 70) / 100.0)
        charge = max(0.0, min(headroom, target - deficit))
        return min(rated, deficit + charge)

    def burn(self, gen_w: float, dt: float) -> None:
        """Account energy and fuel over ``dt`` at ``gen_w``."""
        hours = dt / 3600.0
        self.energy_wh += gen_w * hours
        if self.fuel_l is not None and self.state == STATE_RUNNING:
            self.fuel_l = max(0.0, self.fuel_l - (self.hw.idle_lph + self.hw.per_kwh_l
                                                  * gen_w / 1000.0) * hours)
            if self.fuel_l <= 0:
                self._set_fault("out-of-fuel")

    def status(self) -> dict:
        return {"installed": self.hw.installed, "enabled": self.enabled,
                "state": self.state, "state_name": STATE_NAMES[self.state],
                "phase": self._phase, "relay_closed": self.relay_closed,
                "fault": self.fault, "manual_on": self.manual_on,
                "mode": self.block.get("manuSw"), "rated_w": self.rated_w,
                "fuel_l": None if self.fuel_l is None else round(self.fuel_l, 2),
                "energy_kwh": round(self.energy_wh / 1000.0, 3), "starts": self.starts}


def _mins(hhmm: str) -> int:
    h, m = str(hhmm or "00:00").split(":")[:2]
    return int(h) * 60 + int(m)
