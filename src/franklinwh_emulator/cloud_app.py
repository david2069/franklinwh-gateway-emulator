"""The official FranklinWH app's start-up calls (#333, #316 step 3): the 17 the app makes in 34 or
more of the 42 HTTP Toolkit captures that the Cloud API didn't serve.

Shapes: field names and types merged over every capture (aggregates only; no values were
taken), checked against franklinwh-cloud's ``docs/franklinwh_openapi.json``. The message text
and the envelope's extra keys (``total``, ``traceId``) are as captured. Values are the
emulator's own: from the site where it has them, ``None`` where the captures show null and the
meaning isn't established, and "none" for the account's extras (no VPP programme, survey,
advert, message or help page). ``APP_CALLS`` maps (method, path below the cloud's prefix) to a
handler ``(api, q, body) -> reply``; cloud.py routes them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import cloud_energy, models
from .site import ALARMS
from .weather import sun_position

if TYPE_CHECKING:
    from .cloud import CloudApi

OPERATE = "Operate success!"
QUERY = "Query success!"
# The weather's sky as the app's brief weather words it (emulator wording; flags as named).
SKY_TEXT = {"clear": "Clear", "broken": "Partly cloudy", "overcast": "Cloudy", "storm": "Thunderstorm"}


def _env(result, message: str = OPERATE, **extra) -> dict:
    return {"code": 200, "message": message, "result": result, "success": True, **extra}


def charge_power_details(api: "CloudApi", q: dict, body: bytes) -> dict:
    """The battery card. ``batterySoc`` is the displayed SoC, the composite's ``runtimeData.soc``
    (2,191 of 2,343 capture pairs), and ``touMinSoc`` the active mode's reserve (1,367 of 1,568):
    docs/FINDINGS.md. The duration and consumption figures are null, as some captures show
    (their meaning isn't established)."""
    gw = api._gateway(q)
    soc = api.composite(gw)["runtimeData"]["soc"]
    return _env({"batterySoc": soc, "touMinSoc": int(round(gw.site.active_reserve())),
                 "currentStateDuration": None, "highEnergyConsumption": None,
                 "averageEnergyConsumption": None, "currentTime": None, "highEnergyTime": None,
                 "averageTime": None}, QUERY, total=0)


def nps_show_tip(api, q, body) -> dict:
    return _env({"joinFlag": 0, "showTipFlag": 0, "firstFlag": 0, "expired": 0, "activityId": 0,
                 "tipContent": None, "showStatisticsFlag": 0})


def program_flag(api, q, body) -> dict:
    """Not in a VPP programme."""
    api._gateway(q)
    keys = ("postcode", "province", "city", "programList", "address1", "address2",
            "userPhoneNumber", "firstName", "lastName", "registerGuideDesc", "registerStartDate",
            "registerEndDate", "vppBannerDetail", "programIdList", "programId", "programName",
            "partnerName", "partnerId")
    return _env({"flag": 0, **{k: None for k in keys}, "showRegisterBanner": False})


def _alarm_text(name: str) -> str:
    words = name.lower().split("_")
    words = [w.upper() if w in ("dc", "ac", "hw") else w for w in words]
    return " ".join(words)[:1].upper() + " ".join(words)[1:]


def run_log_list(api, q, body) -> dict:
    """The alarm names the app's run log can show: the emulator's own alarms."""
    items = [{"alarmCode": str(bit), "logName": _alarm_text(name), "enLogName": _alarm_text(name)}
             for name, bit in ALARMS.items()]
    return _env(items, QUERY, total=len(items))


def benefit_info(api, q, body) -> dict:
    """The day's solar and aPower energy to the home and the grid (kWh, the Cloud API's daily
    split); no prices, so the earnings are 0 and ``priceFlag`` false."""
    gw = api._gateway(q)
    gw.site.step()
    day = q.get("dayTime") or gw.site.local_date(gw.site.clock.now())
    kwh = cloud_energy.day_kwh(gw, day)
    one = lambda key: [round(kwh.get(key, 0.0), 2)]
    return _env({"fuelConversion": None, "evMileageConversion": None, "treeConversion": None,
                 "carbonReduction": None,
                 **{k: [0.0] for k in ("batFeedEarnList", "batLoadEarnModList", "batLoadEarnList",
                                       "solarFeedEarnList", "solarLoadEarnList")},
                 "solarToHouseList": one("sun_home"), "solarToGridList": one("sun_grid"),
                 "apowerToHouseList": one("bat_home"), "apowerToGridList": one("bat_grid"),
                 "dayTimeList": [day], "currency": None, "priceFlag": False})


def device_info(api, q, body) -> dict:
    """The aGate and its aPowers from the site's models (ratedPwr W, rateBatCap Wh, totalCap
    kWh, fixedPower* kW, as franklinwh-cloud reads them)."""
    gw = api._gateway(q)
    site, pay = gw.site, api.payloads(gw)
    site.step()
    _, fw = pay.dc_firmware()
    now = site.local_datetime(site.clock.now())
    units = site.unit_models
    agate = site.model.agate
    comp = api.composite(gw)["runtimeData"]
    return _env({
        "gatewayId": gw.serial, "date": now.strftime("%Y-%m-%d"),
        "deviceTime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "countryId": api.power_control(gw)["countryId"], "provinceId": 0, "offGirdFlag": comp["offGridFlag"],
        "sysHdVersion": str(agate.code), "sysHdVersionInt": agate.code,
        "realSysHdVersion": "FHP1.2" if agate.code == 102 else "",   # the reference aGate's only
        "protocolVer": fw["protocolVer"], "installerId": 0, "serviceVoltageFlag": None,
        "offMessage": None, "genEn": comp["genEn"], "chooseFinancierId": None,
        "firstName": None, "lastName": None, "fhpSn": pay.fhp_sn(1), "gridPhaseConSet": None,
        "activeStatus": 1, "emsDeviceVer": 0, "solarFlag": True, "solarTipMsg": "",
        "v2lModeEnable": comp["v2lModeEnable"], "v2lRunState": None, "newHomeFlag": None,
        "sleepStatus": None, "zoneInfo": site.location.timezone,
        "peHwVerList": [u.code for u in units],
        "fixedPowerTotal": round(sum(u.kw for u in units), 2),
        "fixedPowerAverage": round(sum(u.kw for u in units) / len(units), 2),
        "totalCap": round(sum(u.kwh for u in units), 2),
        "apowerList": [{"id": pay.fhp_sn(i + 1), "ratedPwr": int(u.kw * 1000),
                        "rateBatCap": int(u.kwh * 1000)} for i, u in enumerate(units)],
        "blackSleepFlag": False, "mpptEnFlag": any(u.mppt_count for u in units),
        "apbox20Num": None, "equipType": None, "msaInstallStartDetectTime": None})


def no_messages(api, q, body) -> dict:
    api._gateway(q)
    return _env(None)


def entrance_info(api, q, body) -> dict:
    """Which settings pages the app offers; the grid limits as getPowerControlSetting has them."""
    pc = api.power_control(api._gateway(q))
    return _env({"sgipEntrance": 0, "bbEntrance": 0, "pcsEntrance": 0, "tariffSettingFlag": False,
                 "sdcpFlag": False, "solarFlag": pc["solarFlag"], "gridFlag": bool(pc["gridFlag"]),
                 "globalGridDischargeMax": pc["globalGridDischargeMax"],
                 "globalGridChargeMax": pc["globalGridChargeMax"], "slSettingFlag": 0,
                 "needCtTest": False, "gridFeedMaxFlag": pc["gridFeedMaxFlag"],
                 "gridMaxFlag": pc["gridMaxFlag"], "backupSolution": None,
                 "gridFeedMax": pc["gridFeedMax"], "gridMax": pc["gridMax"],
                 "peakDemandGridMax": None, "bbDischargePower": None, "bonusEnable": None,
                 "ahubAddressingFlag": None, "chargingPowerLimited": False, "ja12Entrance": 0})


def fcm_token(api, q, body) -> dict:
    """The phone's push token: accepted and not kept."""
    return _env("", "UpdateSuccess!", total=0)


def brief_weather(api, q, body) -> dict:
    """The site's weather: the sky (a weather event's, else the model's), the ambient
    temperature and whether the sun is up. Icons and the forecast are null."""
    gw = api._gateway(q)
    site = gw.site
    ts = site.step().ts
    sky = None
    if site.weather is not None:
        event = site.weather_events.active(ts)
        sky = event["sky"] if event else site.weather.cfg.sky
    temp = site.ambient_c(ts)
    loc = site.location
    sun = None if loc.latitude is None or loc.longitude is None else \
        sun_position(ts, loc.latitude, loc.longitude)[0] < 90.0
    return _env({"secondaryType": None, "description": SKY_TEXT.get(sky),
                 "isDayTime": sun, "dayIconUrl": None, "nightIconUrl": None,
                 "thunderFlag": int(sky == "storm"), "rainFlag": int(sky == "storm"), "snowyFlag": 0,
                 "tempC": round(temp, 1), "tempF": round(temp * 9 / 5 + 32, 1),
                 "sunriseTime": None, "sunsetTime": None, "shortForecast": None,
                 "probabilityOfPrecipitation": None})


def no_adverts(api, q, body) -> dict:
    api._gateway(q)
    return _env([])


def feedback_popup(api, q, body) -> dict:
    api._gateway(q)
    return _env({"upFlag": False, "generalTitle": None, "ratingTitle": None})


def global_config(api, q, body) -> dict:
    """No feature entrances listed: their keys aren't known."""
    return _env({"entrance": [], "tip": None, "isAppVersion1User": False})


def show_tip(api, q, body) -> dict:
    api._gateway(q)
    try:
        kind = int(q.get("type", 0))
    except ValueError:
        kind = 0
    return _env({"activityId": 0, "type": kind, "tipContent": None, "showTipFlag": 0})


def no_pages(api, q, body) -> dict:
    api._gateway(q)
    return _env([])


def power_cap_configs(api, q, body) -> dict:
    """The aPower models the emulator knows (models.APOWERS): ratedCap Wh, powers W."""
    items = [{"id": m.code + 1, "modelName": m.name, "peHwVersion": m.code,
              "ratedCap": int(m.kwh * 1000), "chargePower": int(m.charge_w),
              "dischargePower": int(m.kw * 1000), "derateFlag": 0}
             for m in models.APOWERS.values()]
    return _env(items, QUERY, total=len(items))


def ai_cache(api, q, body) -> dict:
    import json
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    api._gateway({"gatewayId": (req.get("gatewayId") if isinstance(req, dict) else None)
                  or q.get("gatewayId")})
    return _env(None, "success", traceId=None)


APP_CALLS = {
    ("GET", "terminal/chargePowerDetails"): charge_power_details,
    ("POST", "terminal/nps/getNpsShowTip"): nps_show_tip,
    ("GET", "terminal/selectProgramFlag"): program_flag,
    ("GET", "common/selectDeviceRunLogList"): run_log_list,
    ("GET", "terminal/bill/electric/selectBenefitInfo"): benefit_info,
    ("GET", "terminal/getDeviceInfoV2"): device_info,
    ("GET", "terminal/message/selectMessage"): no_messages,
    ("GET", "terminal/tou/getEntranceInfo"): entrance_info,
    ("POST", "terminal/updateTerUserFcmToken"): fcm_token,
    ("GET", "terminal/weather/getCurrentBriefWeather"): brief_weather,
    ("GET", "terminal/appObtainAdvertiseLst"): no_adverts,
    ("GET", "terminal/feedback/whetherPopUp"): feedback_popup,
    ("GET", "terminal/getAppGlobalConfig"): global_config,
    ("GET", "terminal/getShowTip"): show_tip,
    ("GET", "common/getPageByTypeList"): no_pages,
    ("GET", "common/getPowerCapConfigList"): power_cap_configs,
    ("POST", "terminal/tou/notify/ai/cache"): ai_cache,
}
