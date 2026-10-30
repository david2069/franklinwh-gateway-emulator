"""The TOU schedule: time blocks with a dispatch code and a tariff period, as the FranklinWH cloud
API stores them (franklinwh-cloud docs/TOU_SCHEDULE_GUIDE.md).

A block is ``{"name", "startHourTime", "endHourTime", "waveType", "dispatchId", ...}``. In TOU
mode the block covering the site's local time decides what the battery does (``target_w``).
One 24-hour schedule applies to every day; gaps are filled with SELF, off-peak, as the
franklinwh-cloud library fills them. The guide's limit fields (``maxChargeSoc``,
``minDischargeSoc``, ``gridChargeMax``, …) are kept and shown but not applied: the guide says
``minDischargeSoc`` is ignored by the aGate and the others aren't verified in all firmware.
"""

from __future__ import annotations

import re
from typing import Any

# dispatchId → (code, what the battery does), from the guide's dispatch code table.
DISPATCH = {
    1: ("HOME", "battery covers home loads; surplus solar goes to the grid"),
    2: ("STANDBY", "battery idle; solar to the home, excess to the grid"),
    3: ("SOLAR", "charge the battery from surplus solar only"),
    6: ("SELF", "self-consumption: solar, then battery, then grid"),
    7: ("GRID_EXPORT", "discharge the battery to the grid at its rating"),
    8: ("GRID_CHARGE", "charge the battery from grid and solar at its rating"),
}
WAVES = {0: "Off-Peak", 1: "Mid-Peak", 2: "On-Peak", 4: "Super Off-Peak"}
# Optional block fields from the guide's schema: stored and shown, never applied.
LIMIT_FIELDS = ("gridDischargeMax", "gridChargeMax", "chargeMax", "chargePower", "gridFeedMax",
                "dischargePower", "dischargeMax", "solarCutoff", "gridMax", "maxChargeSoc",
                "minDischargeSoc", "heatEnable", "powerOffApower", "offGrid", "gcaoMax", "rampTime")
REQUIRED = ("name", "startHourTime", "endHourTime", "waveType", "dispatchId")
_HHMM = re.compile(r"^(([01]?[0-9]|2[0-3]):[0-5][0-9]|24:00)$")

# Today's TOU behaviour as a schedule: charge from surplus solar off-peak, self-consumption in
# the 17:00–21:00 peak (the emulator's model since it began; not a FranklinWH default).
DEFAULT_SCHEDULE = [
    {"name": "Off-Peak", "startHourTime": "00:00", "endHourTime": "17:00", "waveType": 0, "dispatchId": 3},
    {"name": "On-Peak", "startHourTime": "17:00", "endHourTime": "21:00", "waveType": 2, "dispatchId": 6},
    {"name": "Off-Peak", "startHourTime": "21:00", "endHourTime": "24:00", "waveType": 0, "dispatchId": 3},
]


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_schedule(blocks: Any) -> list[dict]:
    """Validate blocks; return them sorted, with gaps filled by SELF off-peak."""
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("tou schedule: a list of at least one block")
    out = []
    for i, b in enumerate(blocks):
        where = f"tou block {i + 1}"
        if not isinstance(b, dict):
            raise ValueError(f"{where}: must be a mapping")
        missing = [k for k in REQUIRED if k not in b]
        if missing:
            raise ValueError(f"{where}: missing {missing}")
        unknown = sorted(set(b) - set(REQUIRED) - set(LIMIT_FIELDS) - {"id", "strategyId", "dispatch",
                                                                      "briefDescribe", "solarPriority",
                                                                      "loadPriority", "useModeFlag"})
        if unknown:
            raise ValueError(f"{where}: unknown {unknown}")
        for k in ("startHourTime", "endHourTime"):
            if not isinstance(b[k], str) or not _HHMM.match(b[k]):
                raise ValueError(f"{where}: {k} must be HH:MM (00:00-24:00), not {b[k]!r}")
        start, end = _minutes(b["startHourTime"]), _minutes(b["endHourTime"])
        if end <= start:
            raise ValueError(f"{where}: ends ({b['endHourTime']}) before it starts ({b['startHourTime']})")
        if int(b["waveType"]) not in WAVES:
            raise ValueError(f"{where}: waveType must be one of {sorted(WAVES)} ({WAVES})")
        if int(b["dispatchId"]) not in DISPATCH:
            raise ValueError(f"{where}: dispatchId must be one of {sorted(DISPATCH)}")
        out.append({**b, "waveType": int(b["waveType"]), "dispatchId": int(b["dispatchId"]),
                    "_start": start, "_end": end})
    out.sort(key=lambda b: b["_start"])
    for a, b in zip(out, out[1:]):
        if b["_start"] < a["_end"]:
            raise ValueError(f"tou: {a['name']!r} ({a['startHourTime']}-{a['endHourTime']}) overlaps "
                             f"{b['name']!r} ({b['startHourTime']}-{b['endHourTime']})")
    filled, t = [], 0
    for b in out + [{"_start": 1440, "_end": 1440}]:
        if b["_start"] > t:
            filled.append({"name": "Off-Peak", "startHourTime": _hhmm(t), "endHourTime": _hhmm(b["_start"]),
                           "waveType": 0, "dispatchId": 6, "_start": t, "_end": b["_start"], "filled": True})
        if "name" in b:
            filled.append(b)
        t = b["_end"]
    return filled


def parse_rates(rates: Any) -> dict[int, dict]:
    """Prices per tariff period: ``{waveType: {"buy": $/kWh, "sell": $/kWh}}`` (display only)."""
    out = {}
    for k, v in (rates or {}).items():
        wave = int(k)
        if wave not in WAVES:
            raise ValueError(f"tou rates: waveType must be one of {sorted(WAVES)}, not {k!r}")
        bad = set(v or {}) - {"buy", "sell"}
        if bad:
            raise ValueError(f"tou rates {k}: unknown {sorted(bad)}; use buy, sell")
        out[wave] = {"buy": v.get("buy"), "sell": v.get("sell")}
    return out


def block_at(schedule: list[dict], minute: float) -> dict:
    for b in schedule:
        if b["_start"] <= minute < b["_end"]:
            return b
    return schedule[-1]


def target_w(dispatch_id: int, net_w: float, rated_w: float) -> float:
    """The battery power a dispatch code asks for (+ = discharge). ``net_w`` = load - solar."""
    if dispatch_id == 1:                       # HOME: cover the deficit, don't absorb surplus
        return max(0.0, net_w)
    if dispatch_id == 2:                       # STANDBY
        return 0.0
    if dispatch_id == 3:                       # SOLAR: charge from surplus only
        return min(0.0, net_w)
    if dispatch_id == 7:                       # GRID_EXPORT
        return rated_w
    if dispatch_id == 8:                       # GRID_CHARGE
        return -rated_w
    return net_w                               # 6 SELF


def public(block: dict) -> dict:
    """A block as the API shows it (internal keys dropped, names added)."""
    out = {k: v for k, v in block.items() if not k.startswith("_")}
    out["dispatch"] = DISPATCH[block["dispatchId"]][0]
    out["wave"] = WAVES[block["waveType"]]
    return out
