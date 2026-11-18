"""FranklinWH Cloud API TOU schedules (#305): ``getTouDispatchDetail`` and ``saveTouDispatch``.

The cloud keeps seasons (``strategyList``: months, then day types, then 24-hour blocks); the
site runs one 24-hour schedule (tou.py). The cloud copy is kept here and the site is given the
blocks and rates for its local date: at the save, and again at a cloud call once the date
moves into another season or day type. A schedule changed elsewhere (the control API) reads
back as one everyday season.

Shapes: franklinwh-cloud ``tests/fixtures/tou_dispatch_detail_multi_season.json`` (a
getTouDispatchDetail response) and ``tou_save_multi_season.json`` (a save body); the rules
(``dayType`` 3 every day, or 1 weekdays and 2 weekends) from its docs/TOU_SCHEDULE_GUIDE.md.
As on current firmware, a save doesn't switch the mode (franklinwh-cloud
DEF-TOU-SAVE-NO-LONGER-SWITCHES-MODE). Values outside those sources (template ids, the
default tariff name) are the emulator's own.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import TYPE_CHECKING

from . import tou

if TYPE_CHECKING:
    from .gateway import Gateway

# waveType -> the rate fields' suffix (franklinwh-cloud RATE_FIELD_MAP + WaveType). Sharp has
# no waveType: kept, not applied.
RATE_SUFFIX = {0: "Valley", 1: "Shoulder", 2: "Peak", 4: "SuperOffPeak"}
RATE_SUFFIXES = ("Peak", "Sharp", "Shoulder", "Valley", "SuperOffPeak")
DAY_NAMES = {1: "Weekdays", 2: "Weekends", 3: "Everyday"}
BLOCK_KEYS = tou.REQUIRED + tou.LIMIT_FIELDS


class TouError(ValueError):
    pass


def _rates_fields(rates: dict) -> dict:
    out = {f"eleticRate{s}": None for s in RATE_SUFFIXES}
    out.update({f"eleticSell{s}": None for s in RATE_SUFFIXES})
    for wave, r in rates.items():
        suffix = RATE_SUFFIX.get(int(wave))
        if suffix:
            out[f"eleticRate{suffix}"] = r.get("buy")
            out[f"eleticSell{suffix}"] = r.get("sell")
    return out


def _site_rates(day: dict) -> dict:
    out = {}
    for wave, suffix in RATE_SUFFIX.items():
        buy, sell = _num(day.get(f"eleticRate{suffix}")), _num(day.get(f"eleticSell{suffix}"))
        if buy is not None or sell is not None:
            out[wave] = {"buy": buy, "sell": sell}
    return out


def _num(v):
    """A rate as sent: a number, or None for null (also franklinwh-cloud's string "null")."""
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class TouStore:
    """One gateway's cloud TOU copy."""

    def __init__(self, gw: "Gateway"):
        self.gw = gw
        self.saves = 0
        self.template: dict = {}
        self.strategies: list[dict] = []
        self.applied: tuple | None = None        # (season index, day type) last given to the site
        self.site_blocks: list | None = None     # the site's blocks when we last looked
        self._from_site()

    # -- the site's schedule as one everyday season ---------------------------------------
    def _blocks(self) -> list[dict]:
        return [{k: b[k] for k in BLOCK_KEYS if k in b} for b in self.gw.site.tou_schedule]

    def _from_site(self) -> None:
        site = self.gw.site
        blocks = self._blocks()
        details = [{"id": i + 1, "strategyId": 1, "briefDescribe": None,
                    "solarPriority": None, "loadPriority": None, "useModeFlag": 0,
                    "dispatch": None, **b} for i, b in enumerate(blocks)]
        self.strategies = [{"id": 1, "seasonName": "All year",
                            "month": ",".join(str(m) for m in range(1, 13)), "templateId": None,
                            "fromType": 0,
                            "dayTypeVoList": [{"id": 1, "dayName": DAY_NAMES[3], "dayType": 3,
                                               "ccociateType": 0, "eleticRateGridFee": None,
                                               "ladderRate": None,
                                               **_rates_fields(site.tou_rates),
                                               "detailVoList": details}]}]
        self.template = self._template()
        self.site_blocks = blocks
        self.applied = (0, 3)

    def _template(self, sent: dict | None = None) -> dict:
        site = self.gw.site
        base = {"id": 1000 + self.saves, "gatewayId": self.gw.serial, "templateId": None,
                "templateFromType": 0, "electricCompany": "Custom", "eleCompanyFullName": "Custom",
                "eletricCompanyId": None, "sdcpCompanyFlag": None, "derSchdule": "Other",
                "gridType": None, "name": "Custom", "electricityType": 1, "status": 1,
                "sourceType": 0, "countryId": None, "countryEn": "", "countryZh": "",
                "provinceId": None, "provinceEn": "", "provinceZh": "", "env": None,
                "mookRunCount": 0, "priority": 0, "solarChargeMin": None, "workMode": 1,
                "updateTime": None, "displayFlag": 1, "templateType": 0, "ptoDate": None,
                "nbtType": 0, "zoneInfo": site.location.timezone}
        for k, v in (sent or {}).items():
            if k in base and k not in ("id", "gatewayId") and v not in ("null", None):
                base[k] = v
        return base

    # -- reads -----------------------------------------------------------------------------
    def sync(self) -> None:
        """Follow a schedule set elsewhere; give the site today's season and day type."""
        if self._blocks() != self.site_blocks:
            self._from_site()
            return
        want = self._pick(self._today())
        if want is not None and want != self.applied:
            self._apply(*want)

    def _today(self) -> dt.date:
        site = self.gw.site
        return dt.date.fromisoformat(site.local_date(site.clock.now()))

    def _pick(self, day: dt.date) -> tuple[int, int] | None:
        weekend = day.weekday() >= 5
        for i, season in enumerate(self.strategies):
            if str(day.month) not in str(season.get("month", "")).split(","):
                continue
            types = {int(d.get("dayType", 3)) for d in season.get("dayTypeVoList") or []}
            if 3 in types:
                return i, 3
            return i, (2 if weekend else 1)
        return None

    def _apply(self, season: int, day_type: int) -> None:
        day = next(d for d in self.strategies[season]["dayTypeVoList"]
                   if int(d.get("dayType", 3)) == day_type)
        blocks = [{k: b[k] for k in BLOCK_KEYS if k in b and b[k] not in (None, "null")}
                  for b in day["detailVoList"]]
        self.gw.site.set_tou_schedule(blocks, _site_rates(day))
        self.site_blocks = self._blocks()
        self.applied = (season, day_type)

    def detail(self) -> dict:
        self.sync()
        site = self.gw.site
        return {"template": copy.deepcopy(self.template), "strategyList": copy.deepcopy(self.strategies),
                "detailDefaultVo": {
                    "gridMax": -1, "gridChargeMax": -1, "dischargeMax": -1, "gridDischargeMax": -1,
                    "fixedPower": None, "showAdvance": 0, "sgipRiskMax": None,
                    "peakDemandGridMax": None, "sgipProvinceIdList": [], "sgipDerScheduleList": [],
                    "advanceHiddenDerScheduleList": [], "priorityList": [],
                    "fixedPowerTotal": None, "fixedPowerAverage": None,
                    "touDispatchList": [{"id": did, "dispatchCode": code, "title": code.title(),
                                         "recommendScene": None, "recommendLevel": 0,
                                         "content": text, "remark": None, "solarPriority": None,
                                         "loadPriority": None, "mainIndex": n, "url": None,
                                         "descriptionUrl": None, "waveType": None, "webUrls": None,
                                         "recommendFlag": 0}
                                        for n, (did, (code, text)) in enumerate(tou.DISPATCH.items())]},
                "extraDTO": None, "advancedSettings": None, "iotGatewayRegionVo": None,
                "bbDefaultVo": None, "alertMessage": None, "sendStatus": 0,
                "batteryRatedCapacity": round(site.rated_capacity_wh / 1000, 1),
                "onlineFlag": 0 if self.gw.faults.offline else 1, "vppFlagTip": None,
                "tariffSettingFlag": False, "nemType": 0, "batterySavingsFlag": 0,
                "weeklyParticipationSchedule": None, "ptoDate": None,
                "apowerCount": max(1, site.config.apower_units)}

    # -- the save --------------------------------------------------------------------------
    def save(self, body: dict) -> int:
        strategies = body.get("strategyList")
        if not isinstance(strategies, list) or not strategies:
            raise TouError("strategyList: at least one season")
        months_seen: set[int] = set()
        clean = []
        for si, season in enumerate(strategies):
            where = f"season {si + 1}"
            try:
                months = {int(m) for m in str(season.get("month", "")).split(",") if m.strip()}
            except ValueError:
                raise TouError(f"{where}: month must be a list of months, e.g. \"1,2,3\"")
            if not months or not months <= set(range(1, 13)) or months & months_seen:
                raise TouError(f"{where}: months 1-12, each in one season")
            months_seen |= months
            days = season.get("dayTypeVoList") or []
            types = sorted(int(d.get("dayType", 3)) for d in days)
            if types not in ([3], [1, 2]):
                raise TouError(f"{where}: dayType 3 (every day), or 1 and 2 (weekdays, weekends)")
            out_days = []
            for d in days:
                try:
                    parsed = tou.parse_schedule([{k: b[k] for k in BLOCK_KEYS
                                                  if k in b and b[k] not in (None, "null")}
                                                 for b in d.get("detailVoList") or []])
                except ValueError as e:
                    raise TouError(f"{where} dayType {d.get('dayType')}: {e}")
                details = [{"id": i + 1, "strategyId": si + 1, **tou.public(b)}
                           for i, b in enumerate(parsed)]
                for b in details:
                    b.pop("wave", None)
                    b["dispatch"] = None
                dtype = int(d.get("dayType", 3))
                out_days.append({"id": len(out_days) + 1, "dayName": DAY_NAMES[dtype], "dayType": dtype,
                                 "ccociateType": 0, "eleticRateGridFee": _num(d.get("eleticRateGridFee")),
                                 "ladderRate": None,
                                 **{k: _num(d.get(k)) for k in _rates_fields({})},
                                 "detailVoList": details})
            clean.append({"id": si + 1, "seasonName": season.get("seasonName") or f"Season {si + 1}",
                          "month": ",".join(str(m) for m in sorted(months)), "templateId": None,
                          "fromType": 0, "dayTypeVoList": out_days})
        self.saves += 1
        self.strategies = clean
        self.template = self._template(body.get("template") if isinstance(body.get("template"), dict) else None)
        self.applied = None
        want = self._pick(self._today())
        if want is not None:
            self._apply(*want)
        return self.template["id"]
