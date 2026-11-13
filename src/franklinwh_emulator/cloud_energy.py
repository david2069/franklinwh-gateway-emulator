"""FranklinWH Cloud API energy history (#301): ``getFhpPowerByDay`` and ``getFhpElectricData``
from the site's own quarter-hour history (``Site.history``, the one Direct Connect 1303
reads; up to ~105 days). Days with no history give zeros; #296 is the design for filling
older days.

Field names and the period rules follow franklinwh-cloud (``const/energy_fields.py``, its
OpenAPI file, and the live probe in tests/results/2026-10-08_DEF-ENERGY-PERIOD-PARAM_live_probe
.txt): type 1 (day) reads ``dayTime``; types 2-4 read ``startDate``, a week must start on its
Monday, a month or year accepts any day inside it; type 5 (total) ignores the date. Anything
else gives empty arrays, as the cloud does.

Not established, so the emulator's own choice:
* the splits (solar to home first, then battery, then grid; generator and V2L to home, then
  battery; battery to home before grid; grid to home before battery);
* ``getFhpPowerByDay`` samples every 15 minutes (the history's resolution); the cloud's
  samples are 5-minute.
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gateway import Gateway

# getFhpElectricData: (array, split key) in the order franklinwh-cloud lists them.
ELECTRIC = (("kwhSuArray", "sun"), ("kwhGenArray", "gen"), ("kwhUtiInArray", "imp"),
            ("kwhUtiOutArray", "exp"), ("kwhFhpChgArray", "chg"), ("kwhFhpDiArray", "dis"),
            ("kwhLoadArray", "load"), ("kwhGridLoadArray", "grid_home"),
            ("kwhSolarLoadArray", "sun_home"), ("kwhFhpLoadArray", "bat_home"),
            ("kwhGenLoadArray", "gen_home"), ("gridChBatArray", "grid_bat"),
            ("batOutGridArray", "bat_grid"), ("genChBatArray", "gen_bat"),
            ("soChBatArray", "sun_bat"), ("soOutGridArray", "sun_grid"),
            ("remoteSolarWhTotalArray", None))
# Never non-zero in franklinwh-cloud's capture corpus: null per bucket.
ELECTRIC_NULL = ("proximalSolarWhArray", "mpptWhArray", "meterkitPvWhArray", "secondaryPvWhArray",
                 "mpanPv1Wh", "mpanPv2Wh", "apbox20PvWh", "kwhV2lArray", "kwhV2lToFhpArray",
                 "kwhV2lToHomeArray")
# getFhpPowerByDay: ("Gird" is the API's spelling).
POWER = (("powerSolarHomeArray", "sun_home"), ("powerSolarGirdArray", "sun_grid"),
         ("powerSolarFhpArray", "sun_bat"), ("powerGirdFhpArray", "grid_bat"),
         ("powerGirdHomeArray", "grid_home"), ("powerFhpGirdArray", "bat_grid"),
         ("powerFhpHomeArray", "bat_home"), ("powerGenFhpArray", "gen_bat"),
         ("powerGenHomeArray", "gen_home"), ("powerV2lFhpArray", "v2l_bat"),
         ("powerV2lHomeArray", "v2l_home"))
SLOT_S = 900
IDLE_W = 50.0                     # below this the battery counts as idle (runStatus 0)


def split(slot: dict) -> dict[str, float]:
    """A quarter-hour's average powers (W) split into flows."""
    s = slot.get("s") or 0.0
    if not s:
        return {}
    avg = {k: slot.get(k, 0.0) / s for k in ("uti", "fhp", "load", "gen", "v2l")}
    load, uti, fhp, gen, v2l = avg["load"], avg["uti"], avg["fhp"], avg["gen"], avg["v2l"]
    sun = max(0.0, load - uti - fhp - gen - v2l)
    imp, exp_, dis, chg = max(0.0, uti), max(0.0, -uti), max(0.0, fhp), max(0.0, -fhp)
    left = max(0.0, load)
    f: dict[str, float] = {}
    for key, src in (("sun_home", sun), ("gen_home", gen), ("v2l_home", v2l),
                     ("bat_home", dis), ("grid_home", imp)):
        f[key] = min(src, left)
        left -= f[key]
    need = chg
    for key, src in (("sun_bat", sun - f["sun_home"]), ("gen_bat", gen - f["gen_home"]),
                     ("v2l_bat", v2l - f["v2l_home"]), ("grid_bat", imp - f["grid_home"])):
        f[key] = max(0.0, min(src, need))
        need -= f[key]
    f["sun_grid"] = max(0.0, sun - f["sun_home"] - f["sun_bat"])
    f["bat_grid"] = max(0.0, dis - f["bat_home"])
    f.update(sun=sun, gen=gen, imp=imp, exp=exp_, chg=chg, dis=dis, load=load)
    return f


def _day_kwh(gw: "Gateway", date: str) -> dict[str, float]:
    tot: dict[str, float] = {}
    for slot in gw.site.history.get(date, []):
        hours = (slot.get("s") or 0.0) / 3600
        for k, w in split(slot).items():
            tot[k] = tot.get(k, 0.0) + w * hours / 1000
    return tot


def power_by_day(gw: "Gateway", q: dict) -> dict:
    site = gw.site
    site.step()
    date = q.get("dayTime", "")
    out = {k: [] for k in ("deviceTimeArray", "socArray", "kwhTotalArray", "runStatusArray",
                           *(a for a, _ in POWER))}
    for i, slot in enumerate(site.history.get(date, [])):
        s = slot.get("s") or 0.0
        if not s:
            continue
        f = split(slot)
        soc = slot["soc"] / s if "soc" in slot else site.soc
        fhp = slot.get("fhp", 0.0) / s
        out["deviceTimeArray"].append(f"{date} {i // 4:02d}:{i % 4 * 15:02d}:00")
        out["socArray"].append(round(soc, 1))
        out["kwhTotalArray"].append(round(soc / 100 * site.capacity_wh / 1000, 2))
        out["runStatusArray"].append(0 if abs(fhp) < IDLE_W else (2 if fhp > 0 else 1))
        for array, key in POWER:
            out[array].append(round(f[key] / 1000, 3))
    return out


def _periods(gw: "Gateway", q: dict) -> list[tuple[str, list[str]]]:
    """(bucket label, the dates in it) for the request, or [] where the cloud gives empty
    arrays."""
    try:
        kind = int(q.get("type", 1))
        raw = q.get("dayTime" if kind == 1 else "startDate", "")
        if kind == 5:
            raw = q.get("startDate") or q.get("dayTime") or ""
            day = None
        else:
            day = dt.date.fromisoformat(raw)
    except ValueError:
        return []
    if kind == 1:
        return [(day.isoformat(), [day.isoformat()])]
    if kind == 2:
        if day.weekday() != 0:
            return []
        days = [(day + dt.timedelta(n)).isoformat() for n in range(7)]
        return [(d, [d]) for d in days]
    if kind == 3:
        n = calendar.monthrange(day.year, day.month)[1]
        return [(d, [d]) for d in (dt.date(day.year, day.month, i).isoformat()
                                   for i in range(1, n + 1))]
    if kind in (4, 5):
        if kind == 4:
            buckets = [f"{day.year}-{m:02d}" for m in range(1, 13)]
        else:
            first = int(str(gw.commissioned)[:4])
            now = int(gw.site.local_date(gw.site.clock.now())[:4])
            buckets = [str(y) for y in range(min(first, now), now + 1)]
        return [(b, [d for d in gw.site.history if d.startswith(b)]) for b in buckets]
    return []


def electric_data(gw: "Gateway", q: dict) -> dict:
    gw.site.step()
    periods = _periods(gw, q)
    out: dict[str, list] = {"deviceTimeArray": [label for label, _ in periods]}
    totals = []
    for _, dates in periods:
        tot: dict[str, float] = {}
        for d in dates:
            for k, v in _day_kwh(gw, d).items():
                tot[k] = tot.get(k, 0.0) + v
        totals.append(tot)
    for array, key in ELECTRIC:
        out[array] = [round(t.get(key, 0.0), 2) if key else 0.0 for t in totals]
    for array in ELECTRIC_NULL:
        out[array] = [None] * len(periods)
    return out
