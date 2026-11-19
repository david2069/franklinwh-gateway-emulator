"""The official FranklinWH app's calls (#316 step 3) that the Cloud API didn't serve: the 17 it
makes at start-up, in 34 or more of the 42 HTTP Toolkit captures (#333), then the next 14 (19 to 30
captures) and sendMqtt 317, 327 and 337 (34 to 37) (#337).

Shapes: field names and types merged over every capture (aggregates only; no values were
taken), checked against franklinwh-cloud's ``docs/franklinwh_openapi.json``. The message text
and the envelope's extra keys (``total``, ``traceId``) are as captured. Values are the
emulator's own: from the site where it has them, ``None`` where the captures show null and the
meaning isn't established, and "none" for the account's extras (no VPP programme, survey,
advert, message or help page). ``APP_CALLS`` maps (method, path below the cloud's prefix) to a
handler ``(api, q, body) -> reply``; cloud.py routes them. ``MQTT_CALLS`` maps a sendMqtt cmdType
to ``(api, gw, dataArea) -> reply dataArea``. Network addresses are documentation placeholders
(192.0.2.x, AA:BB:CC:…), never a real network's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import hashlib
import json

from . import cloud_energy, models
from .site import ALARMS
from .weather import sun_position

if TYPE_CHECKING:
    from .cloud import CloudApi
    from .gateway import Gateway

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
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    api._gateway({"gatewayId": (req.get("gatewayId") if isinstance(req, dict) else None)
                  or q.get("gatewayId")})
    return _env(None, "success", traceId=None)


# -- #337: the next 14 calls ------------------------------------------------------------------
COUNTRY_NAME = {"AU": "Australia", "US": "United States", "CA": "Canada", "NZ": "New Zealand"}
NET_WIFI = 3          # currentNetType / connType: 1 Eth0, 2 Eth1, 3 Wi-Fi, 4 4G (franklinwh-cloud)


def personal_info(api, q, body) -> dict:
    """The account's profile: nothing filled in (the emulator keeps no names or address)."""
    keys = ("userName", "contactNumber", "userAddress", "region", "lat", "lon", "firstName", "lastName",
            "zipCode", "country", "province", "city", "addressSecondLast")
    return _env({**{k: None for k in keys}, "alphaCode": "", "addressFinish": " ", "avatar": None},
                QUERY, total=0)


def user_resources(api, q, body) -> dict:
    """The newApi service's own envelope (``msg``, ``data``), its message as captured."""
    return {"code": 200, "msg": "请求成功", "data": {"resources": None, "roleResources": []}, "success": True}


def backup_records(gw: "Gateway") -> list[dict]:
    """Backups, oldest first: the grid relay's openings (site.relay_events), chargeType 2 and
    energy 0.0 as in every capture. The SoC at either end isn't recorded, so it is null."""
    site = gw.site
    now = site.step().ts
    spans, start = [], None
    for e in site.relay_events:
        if e["event"] == "open" and start is None:
            start = e["ts"]
        elif e["event"] == "close" and start is not None:
            spans.append((start, e["ts"]))
            start = None
    if start is not None:
        spans.append((start, now))                  # still islanded
    when = lambda t: site.local_datetime(t).strftime("%Y-%m-%d %H:%M:%S")
    return [{"id": n, "gatewayId": gw.serial, "chargeType": 2, "energy": 0.0,
             "startSoc": None, "endSoc": None, "chargeDate": when(a), "startTime": when(a),
             "endTime": when(b), "second": int(b - a), "chargeTime": None, "chargeHour": None,
             "chour": None, "cminute": None, "csecond": None}
            for n, (a, b) in enumerate(spans, 1)]


def backup_history(api, q, body) -> dict:
    """The backups' count, total and longest seconds, and the last one; with no backup yet,
    lastRecord is null."""
    records = backup_records(api._gateway(q))
    secs = [r["second"] for r in records]
    return _env({"longestDuration": max(secs, default=0), "totalBackup": len(records),
                 "totalDuration": sum(secs), "lastRecord": records[-1] if records else None})


def recommend_flag(api, q, body) -> dict:
    return _env({"enableRecommend": 0, "recommendUrl": None, "recommendCodeMarkdown": None,
                 "recommendText": None, "recommendImage": None})


def sim_buttons(api, q, body) -> dict:
    """The SIM page's buttons, labels and values as every capture has them."""
    api._gateway(q)
    return _env([{"dictLabel": label, "dictValue": "0", "isDefault": "Y"}
                 for label in ("userSimCardManagement", "installerAuthCode", "installerActivateAuthCode")])


def logout(api, q, body) -> dict:
    """Accepted; the token stays valid until it expires (the app drops it)."""
    return _env(None)


def overall_info(api, q, body) -> dict:
    """aPower count and total capacity (kWh)."""
    units = api._gateway(q).site.unit_models
    return _env({"apowerCount": len(units), "totalPower": round(sum(u.kwh for u in units), 2)}, QUERY, total=0)


def bonus_info(api, q, body) -> dict:
    """Battery bonus (BB) off. The bbDefaultVo numbers are every capture's; its start-time window
    is the emulator's (the captures' times weren't taken). The utility is the tariff's
    ("Custom", as the emulator's TOU names it)."""
    gw = api._gateway(q)
    units = gw.site.unit_models
    pc = api.power_control(gw)
    return _env({"bonusEnable": 0, "touEleCompanyFullName": "Custom", "extraDTO": None,
                 "bbDefaultVo": {"dischargeStartTimeMin": "16:00", "dischargeStartTimeMax": "20:00",
                                 "dischargeTimeInterval": 120, "rampTime": 300, "dischargePower": 2.5},
                 "batteryRatedCapacity": round(sum(u.kwh for u in units), 2), "alertMessage": None,
                 "sendStatus": None, "switchBonusFlag": 1,
                 "pcsVo": {"gridFeedMax": None, "gridFeedMaxFlag": pc["gridFeedMaxFlag"]},
                 "apowerCount": len(units)}, QUERY, total=0)


def expected_earnings(api, q, body) -> dict:
    """No prices, so no savings: scene 1 ("The data meets the requirements."), 0.0. This reply's
    envelope has ``traceId`` and no ``success``, as captured."""
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    tpl = (req.get("template") if isinstance(req, dict) else None) or {}
    sn = tpl.get("gatewayId") if isinstance(tpl, dict) else None
    if sn is not None:
        api._gateway({"gatewayId": sn})
    return {"code": 200, "message": "The data meets the requirements.", "traceId": None,
            "result": {"scene": 1, "gatewayId": sn, "historyRealSavings": 0.0, "historyRealSavings30": None,
                       "historyRealSavings365": None, "estimatedSavings": 0.0, "estimatedSavings30": None,
                       "estimatedSavings365": None, "estimatedHighOrNot30": None,
                       "estimatedHighOrNot365": None, "estimatedHighOrNot": 0}}


def site_address(gw: "Gateway") -> dict:
    """The site's address at city level (no street), from the fleet entry's location."""
    loc = gw.site.location
    city, state, post = loc.city or "", loc.state or "", loc.postcode or ""
    return {"country": COUNTRY_NAME.get(loc.country or "", loc.country or ""), "province": state,
            "city": city, "postCode": post, "alphaCode": loc.country or "",
            "completeAddress": ", ".join(x for x in (city, " ".join(x for x in (state, post) if x)) if x)}


def device_detail(api, q, body) -> dict:
    """The site's name and address at city level (no street): the fleet entry's id and
    location. alphaCode is the country code (the captures' list of codes isn't decoded)."""
    gw = api._gateway(q)
    return _env({"gatewayName": gw.config.id, "address1": "", "address2": None, **site_address(gw)})


def gateway_alarms(api, q, body) -> dict:
    """The alarms active now (the site's alarm bits), named as the run log names them; level 2,
    as every captured alarm. No Chinese names or explanations: English only, explanations empty."""
    gw = api._gateway(q)
    st = gw.site.step()
    when = gw.site.local_datetime(st.ts).strftime("%Y-%m-%d %H:%M:%S")
    items = [{"id": bit + 1, "gatewayId": gw.serial, "alarmEqSn": gw.serial, "alarmCode": str(bit),
              "commonAlarmCode": str(bit), "level": 2, "time": when, "logName": _alarm_text(name),
              "commonLogName": _alarm_text(name), "logNameEn": _alarm_text(name),
              "commonLogNameEn": _alarm_text(name), "createTime": None, "alarmExplanationZh": "",
              "alarmExplanationEn": "", "alarmExplanation": "", "pictureUrl": None, "plan": None}
             for name, bit in ALARMS.items() if st.alarms >> bit & 1]
    return _env(items, QUERY, total=len(items))


def apowers_info(api, q, body) -> dict:
    """Each aPower: rating (kW), capacity and energy left (kWh), SoC and the firmware's
    versions (Direct Connect 1102's)."""
    gw = api._gateway(q)
    pay = api.payloads(gw)
    soc = float(api.composite(gw)["runtimeData"]["soc"])
    _, fw = pay.dc_firmware()
    pa = fw["per_apower"]
    items = [{"apowerSn": pay.fhp_sn(i + 1), "ratedPower": float(u.kw), "ratedCapacity": float(u.kwh),
              "status": 0, "remainingPower": round(u.kwh * soc / 100, 1),
              "fpgaVer": pa["FPGA_VER"], "dcdcVer": pa["DCDC_VER"], "invVer": pa["INV_VER"],
              "bmsVer": pa["BMS_VER"], "blVer": pa["BL_VER"], "thVer": pa["TH_VER"],
              "peHwVer": str(u.code), "mpptAppVer": "", "soc": round(soc, 1)}
             for i, u in enumerate(gw.site.unit_models)]
    return _env(items, QUERY, total=len(items))


def agate_info(api, q, body) -> dict:
    """The aGate's versions (Direct Connect 1102's), on Wi-Fi."""
    gw = api._gateway(q)
    ibg, fw = api.payloads(gw).dc_firmware()
    return _env({"protocolVer": fw["protocolVer"], "sysHdVersion": gw.site.model.agate.code,
                 "emsDeviceVer": 0, "ibgVersion": ibg, "ibgMainVersion": "", "connType": NET_WIFI,
                 "slVersion": fw["SL_VER"], "slVersionApp": None, "slVersionBoot": None,
                 "awsVersion": fw["AWS_VER"], "appVersion": fw["APP_VER"], "meterVersion": fw["METER_VER"],
                 "msaModel": None, "msaSn": None}, QUERY, total=0)


def sim_card_show(api, q, body) -> dict:
    """HTTP 404 with this body, as 2,449 of 2,471 captured calls (no SIM card page)."""
    from .cloud import CloudError
    api._gateway(q)
    raise CloudError(404, "", http=404, body={"message": "", "status": 404})


# -- sendMqtt 317, 327, 337 (#337) -------------------------------------------------------------
def _block(api, gw: "Gateway", name: str, default) -> dict:
    return api._app.setdefault((gw.serial, name), default())


def _mac(gw: "Gateway", n: int) -> str:
    h = hashlib.sha256(gw.serial.encode()).hexdigest()
    return "AA:BB:CC:" + ":".join(h[i:i + 2].upper() for i in (0, 2)) + f":{n:02X}"


def _net(api, gw: "Gateway") -> dict:
    def default():
        host = 10 + int(hashlib.sha256(gw.serial.encode()).hexdigest(), 16) % 240
        out = {"currentNetType": NET_WIFI, "wifiDHCP": 1, "wifiMAC": _mac(gw, 1),
               "wifiStaticIP": f"192.0.2.{host}", "wifiDNS": "192.0.2.1", "wifiGateWay": "192.0.2.1"}
        for n, nic in ((2, "eth0"), (3, "eth1")):
            out.update({f"{nic}DHCP": int(nic == "eth1"), f"{nic}MAC": _mac(gw, n),
                        f"{nic}StaticIP": "0.0.0.0", f"{nic}DNS": "0.0.0.0", f"{nic}GateWay": "0.0.0.0"})
        return {**out, "operatorMAC": _mac(gw, 4), "operatorDNS": "0.0.0.0", "operatorRSSI": 20,
                "awsStatus": 1}
    return _block(api, gw, "net", default)


SYS_CONFIG_KEYS = ("electricSys", "ratedGridVolt", "ratedGridHz", "electricSupply", "airSwitchCur",
                   "gridPhaseConSet", "gridPhaseSeqSet", "solarInstallState", "genRatePower", "solarInvManu",
                   "solarInvRatePower", "fhpRatePower", "kwRatePower", "gridExportEnable", "gridSoftLimit",
                   "gridHardLimit", "isThreePhaseInstall")


def network_settings(api, gw, req) -> dict:
    """317 → 318. paraType 6: the network interfaces (commSetPara); optType 1 writes the keys sent
    and replies with them as they now stand. paraType 12: the install profile (sysConfigPara, 17
    keys), Direct Connect 1702's values, fhpRatePower one aPower's rating (W), as captured."""
    from .cloud import CloudError
    opt, para = int(req.get("optType", 0)), int(req.get("paraType", 6))
    if para == 6:
        net = _net(api, gw)
        if opt == 1:
            sent = req.get("commSetPara") or {}
            net.update({k: v for k, v in sent.items() if k in net})
        return {"optType": opt, "paraType": 6, "result": 0,
                "commSetPara": {"opt": opt, "result": 0, "reason": 0, **net}, "num": 0}
    if para == 12 and opt == 0:
        pay = api.payloads(gw)
        prof = {**api._strip(pay.install_profile({})), **pay.install_limits,
                "fhpRatePower": int(gw.site.unit_models[0].kw * 1000)}
        return {"optType": 0, "paraType": 12, "result": 0,
                "sysConfigPara": {k: prof.get(k, 0) for k in SYS_CONFIG_KEYS}, "num": len(SYS_CONFIG_KEYS)}
    raise CloudError(400, f"sendMqtt: cmdType 317 optType {opt} paraType {para} is not emulated yet",
                     unserved=f"sendMqtt cmdType 317 paraType {para}")


def led_settings(api, gw, req) -> dict:
    """327 → 328, the aPower LED: lightStat one per aPower (2 in most captures), the light's
    timer off. opt 1 writes the keys sent (rgb and bright are kept once sent)."""
    led = _block(api, gw, "led", lambda: {"lightStat": [2] * len(gw.site.unit_models), "timeEn": 0,
                                          "lightOpenTime": "18:00", "lightCloseTime": "06:00"})
    opt = int(req.get("opt", 0))
    if opt == 1:
        led.update({k: req[k] for k in ("lightStat", "timeEn", "lightOpenTime", "lightCloseTime", "rgb", "bright")
                    if k in req})
    return {"opt": opt, "result": 0, **led}


def wifi_settings(api, gw, req) -> dict:
    """337 → 338, the aGate's Wi-Fi and its own access point (placeholder names and passwords).
    opt 1 writes the keys sent."""
    wifi = _block(api, gw, "wifi", lambda: {"wifi_SSID": "emulator-wifi", "wifi_Pw": "emulator",
                                            "ap_SSID": "aGate_" + gw.serial[-4:], "ap_Pw": "emulator"})
    opt = int(req.get("opt", 0))
    if opt == 1:
        wifi.update({k: str(req[k]) for k in wifi if k in req})
    return {"opt": opt, "result": 0, "reason": 0, **wifi, "wifi_Safety": 1}


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
    ("GET", "terminal/getPersonalInfo"): personal_info,
    ("GET", "newApi/api-user/app/resource/getUserResources/v2"): user_resources,
    ("GET", "terminal/backupHistorySummary"): backup_history,
    ("GET", "terminal/recommend/recommendFlag"): recommend_flag,
    ("GET", "terminal/selectSimBtn"): sim_buttons,
    ("POST", "terminal/v2/loginOut"): logout,
    ("GET", "terminal/selectDeviceOverallInfo"): overall_info,
    ("GET", "terminal/tou/getBonusInfo"): bonus_info,
    ("POST", "terminal/tou/calculate/expected/earnings"): expected_earnings,
    ("GET", "terminal/site/get/DeviceDetail"): device_detail,
    ("GET", "terminal/selectGatewayAlarm"): gateway_alarms,
    ("GET", "terminal/obtainApowersInfo"): apowers_info,
    ("GET", "terminal/obtainAgateInfo"): agate_info,
    ("GET", "terminal/sim/cardShow"): sim_card_show,
}

MQTT_CALLS = {317: network_settings, 327: led_settings, 337: wifi_settings}
