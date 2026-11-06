"""V2L: an EV supplying the home through the aGate (#287).

What is known (franklinwh-cloud docs/GENERATOR_V2L_API.md, defect_list.md; none of it is
FranklinWH documentation, all of it from captured traffic):

* V2L needs the **generator input** and the **smart circuits**; the EV sits on a smart
  circuit (the owner's site: aGate 3, smart circuit 1, board E of the design).
  franklinwh-cloud's device catalogue lists no V2L port on AU aGates, while its cookbook and
  the owner's site have one; modelled as the owner has it.
* It supplies **off-grid** only, and only while a generator on the same input is not running
  (the reported conditions: off-grid, generator configured, generator off).
* Direct Connect ``1411`` carries the ``CarSW*`` fields. An AU gateway reports ``CarSWPower``
  negative about half the time; the sign convention is not established.
* Modbus has no V2L registers.

Not documented, so settings to confirm (defaults marked): the EV's battery size, its SoC, the
most V2L can supply, and the SoC at which it stops. V2L covers the house's shortfall (load
less solar and generator), not battery charging, and the EV on that circuit doesn't charge
while it supplies: plausible, unverified.
"""

from __future__ import annotations

from dataclasses import dataclass

KEYS = {"enabled", "circuit", "ev_kwh", "ev_soc", "max_w", "min_soc"}
DISABLED, READY, SUPPLYING = "Disabled", "Ready", "Supplying"


@dataclass
class V2L:
    enabled: bool = True
    circuit: int = 1
    ev_kwh: float = 60.0                # please confirm: the EV's usable battery
    ev_soc: float = 80.0                # please confirm: the EV's state of charge now, %
    max_w: float = 2200.0               # please confirm: the most V2L supplies (board E shows 2.2 kW)
    min_soc: float = 20.0               # please confirm: V2L stops at this EV SoC, %
    energy_wh: float = 0.0              # supplied so far
    power_w: float = 0.0                # supplying now

    @classmethod
    def from_dict(cls, d: dict | None) -> "V2L | None":
        if d is None:
            return None
        if not isinstance(d, dict):
            raise ValueError("v2l is a mapping, e.g. {circuit: 1, ev_kwh: 60, ev_soc: 80}")
        bad = set(d) - KEYS
        if bad:
            raise ValueError(f"v2l: unknown keys {sorted(bad)}; known {sorted(KEYS)}")
        v = cls(**d)
        v.check()
        return v

    def check(self) -> None:
        if self.ev_kwh <= 0 or self.max_w < 0:
            raise ValueError("v2l: ev_kwh is more than 0 and max_w 0 or more")
        for k in ("ev_soc", "min_soc"):
            if not 0 <= getattr(self, k) <= 100:
                raise ValueError(f"v2l: {k} is 0..100 %")

    def state(self, islanded: bool, generator_running: bool) -> str:
        if not self.enabled:
            return DISABLED
        return SUPPLYING if self.power_w > 0 else READY

    def available_w(self, islanded: bool, generator_running: bool) -> float:
        """What V2L can give now: off-grid, generator not running, EV above its floor."""
        if not (self.enabled and islanded) or generator_running or self.ev_soc <= self.min_soc:
            return 0.0
        return self.max_w

    def commit(self, w: float, dt: float) -> None:
        self.power_w = w
        if w > 0 and dt > 0:
            wh = w * dt / 3600.0
            self.energy_wh += wh
            self.ev_soc = max(self.min_soc, self.ev_soc - wh / (self.ev_kwh * 1000.0) * 100.0)

    def status(self, islanded: bool, generator_running: bool) -> dict:
        return {"enabled": self.enabled, "state": self.state(islanded, generator_running),
                "circuit": self.circuit, "ev_soc": round(self.ev_soc, 1), "ev_kwh": self.ev_kwh,
                "power_w": round(self.power_w, 1), "max_w": self.max_w, "min_soc": self.min_soc,
                "energy_kwh": round(self.energy_wh / 1000.0, 3)}
