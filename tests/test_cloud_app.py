"""#333, #337 (#316 step 3): the official app's calls. Each reply has the fields the app's 42
HTTP Toolkit captures show (names and types merged over every capture), and its values come
from the site."""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator import cloud_energy, models
from franklinwh_emulator.cloud_app import APP_CALLS
from franklinwh_emulator.gateway import Fleet

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU"}

# path -> (message, envelope extras, result type, result keys); from the captures (aggregates).
CAPTURED = {
    "terminal/chargePowerDetails": ("Query success!", {"total"}, dict, {
        "batterySoc", "touMinSoc", "currentStateDuration", "highEnergyConsumption",
        "averageEnergyConsumption", "currentTime", "highEnergyTime", "averageTime"}),
    "terminal/nps/getNpsShowTip": ("Operate success!", set(), dict, {
        "joinFlag", "showTipFlag", "firstFlag", "expired", "activityId", "tipContent", "showStatisticsFlag"}),
    "terminal/selectProgramFlag": ("Operate success!", set(), dict, {
        "flag", "postcode", "province", "city", "programList", "address1", "address2", "userPhoneNumber",
        "firstName", "lastName", "registerGuideDesc", "registerStartDate", "registerEndDate",
        "showRegisterBanner", "vppBannerDetail", "programIdList", "programId", "programName",
        "partnerName", "partnerId"}),
    "common/selectDeviceRunLogList": ("Query success!", {"total"}, list, {"alarmCode", "logName", "enLogName"}),
    "terminal/bill/electric/selectBenefitInfo": ("Operate success!", set(), dict, {
        "fuelConversion", "evMileageConversion", "treeConversion", "carbonReduction", "batFeedEarnList",
        "batLoadEarnModList", "batLoadEarnList", "solarFeedEarnList", "solarLoadEarnList",
        "solarToHouseList", "solarToGridList", "apowerToHouseList", "apowerToGridList", "dayTimeList",
        "currency", "priceFlag"}),
    "terminal/getDeviceInfoV2": ("Operate success!", set(), dict, {
        "gatewayId", "date", "deviceTime", "countryId", "provinceId", "offGirdFlag", "sysHdVersion",
        "sysHdVersionInt", "realSysHdVersion", "protocolVer", "installerId", "serviceVoltageFlag",
        "offMessage", "genEn", "chooseFinancierId", "firstName", "lastName", "fhpSn", "gridPhaseConSet",
        "activeStatus", "emsDeviceVer", "solarFlag", "solarTipMsg", "v2lModeEnable", "v2lRunState",
        "newHomeFlag", "sleepStatus", "zoneInfo", "peHwVerList", "fixedPowerTotal", "fixedPowerAverage",
        "totalCap", "apowerList", "blackSleepFlag", "mpptEnFlag", "apbox20Num", "equipType",
        "msaInstallStartDetectTime"}),
    "terminal/message/selectMessage": ("Operate success!", set(), type(None), set()),
    "terminal/tou/getEntranceInfo": ("Operate success!", set(), dict, {
        "sgipEntrance", "bbEntrance", "pcsEntrance", "tariffSettingFlag", "sdcpFlag", "solarFlag",
        "gridFlag", "globalGridDischargeMax", "globalGridChargeMax", "slSettingFlag", "needCtTest",
        "gridFeedMaxFlag", "gridMaxFlag", "backupSolution", "gridFeedMax", "gridMax", "peakDemandGridMax",
        "bbDischargePower", "bonusEnable", "ahubAddressingFlag", "chargingPowerLimited", "ja12Entrance"}),
    "terminal/updateTerUserFcmToken": ("UpdateSuccess!", {"total"}, str, set()),
    "terminal/weather/getCurrentBriefWeather": ("Operate success!", set(), dict, {
        "secondaryType", "description", "isDayTime", "dayIconUrl", "nightIconUrl", "thunderFlag",
        "rainFlag", "snowyFlag", "tempC", "tempF", "sunriseTime", "sunsetTime", "shortForecast",
        "probabilityOfPrecipitation"}),
    "terminal/appObtainAdvertiseLst": ("Operate success!", set(), list, set()),
    "terminal/feedback/whetherPopUp": ("Operate success!", set(), dict, {"upFlag", "generalTitle", "ratingTitle"}),
    "terminal/getAppGlobalConfig": ("Operate success!", set(), dict, {"entrance", "tip", "isAppVersion1User"}),
    "terminal/getShowTip": ("Operate success!", set(), dict, {"activityId", "type", "tipContent", "showTipFlag"}),
    "common/getPageByTypeList": ("Operate success!", set(), list, set()),
    "common/getPowerCapConfigList": ("Query success!", {"total"}, list, {
        "id", "modelName", "peHwVersion", "ratedCap", "chargePower", "dischargePower", "derateFlag"}),
    "terminal/tou/notify/ai/cache": ("success", {"traceId"}, type(None), set()),
    # #337
    "terminal/getPersonalInfo": ("Query success!", {"total"}, dict, {
        "userName", "contactNumber", "userAddress", "region", "lat", "lon", "firstName", "lastName",
        "zipCode", "country", "province", "city", "addressSecondLast", "alphaCode", "addressFinish", "avatar"}),
    "terminal/backupHistorySummary": ("Operate success!", set(), dict, {
        "longestDuration", "totalBackup", "totalDuration", "lastRecord"}),
    "terminal/recommend/recommendFlag": ("Operate success!", set(), dict, {
        "enableRecommend", "recommendUrl", "recommendCodeMarkdown", "recommendText", "recommendImage"}),
    "terminal/selectSimBtn": ("Operate success!", set(), list, {"dictLabel", "dictValue", "isDefault"}),
    "terminal/v2/loginOut": ("Operate success!", set(), type(None), set()),
    "terminal/selectDeviceOverallInfo": ("Query success!", {"total"}, dict, {"apowerCount", "totalPower"}),
    "terminal/tou/getBonusInfo": ("Query success!", {"total"}, dict, {
        "bonusEnable", "touEleCompanyFullName", "extraDTO", "bbDefaultVo", "batteryRatedCapacity",
        "alertMessage", "sendStatus", "switchBonusFlag", "pcsVo", "apowerCount"}),
    "terminal/site/get/DeviceDetail": ("Operate success!", set(), dict, {
        "gatewayName", "address1", "address2", "country", "province", "city", "postCode", "alphaCode",
        "completeAddress"}),
    "terminal/selectGatewayAlarm": ("Query success!", {"total"}, list, set()),
    "terminal/obtainApowersInfo": ("Query success!", {"total"}, list, {
        "apowerSn", "ratedPower", "ratedCapacity", "status", "remainingPower", "fpgaVer", "dcdcVer",
        "invVer", "bmsVer", "blVer", "thVer", "peHwVer", "mpptAppVer", "soc"}),
    "terminal/obtainAgateInfo": ("Query success!", {"total"}, dict, {
        "protocolVer", "sysHdVersion", "emsDeviceVer", "ibgVersion", "ibgMainVersion", "connType",
        "slVersion", "slVersionApp", "slVersionBoot", "awsVersion", "appVersion", "meterVersion",
        "msaModel", "msaSn"}),
}
# Replies in another envelope: path -> (HTTP status, the reply's keys).
OWN_ENVELOPE = {
    "newApi/api-user/app/resource/getUserResources/v2": (200, {"code", "msg", "data", "success"}),
    "terminal/tou/calculate/expected/earnings": (200, {"code", "message", "result", "traceId"}),
    "terminal/sim/cardShow": (404, {"message", "status"}),
}
ALARM_KEYS = {"id", "gatewayId", "alarmEqSn", "alarmCode", "commonAlarmCode", "level", "time", "logName",
              "commonLogName", "logNameEn", "commonLogNameEn", "createTime", "alarmExplanationZh",
              "alarmExplanationEn", "alarmExplanation", "pictureUrl", "plan"}
# sendMqtt cmdType -> (request dataArea, reply cmdType, reply dataArea keys).
MQTT = {
    317: ({"optType": 0, "paraType": 6}, 318, {"optType", "paraType", "result", "commSetPara", "num"}),
    327: ({"opt": 0}, 328, {"opt", "result", "lightStat", "timeEn", "lightOpenTime", "lightCloseTime"}),
    337: ({"opt": 0}, 338, {"opt", "result", "reason", "wifi_SSID", "wifi_Pw", "ap_SSID", "ap_Pw", "wifi_Safety"}),
}
COMM_KEYS = {"opt", "result", "reason", "currentNetType", "wifiDHCP", "wifiMAC", "wifiStaticIP", "wifiDNS",
             "wifiGateWay", "eth0DHCP", "eth0MAC", "eth0StaticIP", "eth0DNS", "eth0GateWay", "eth1DHCP",
             "eth1MAC", "eth1StaticIP", "eth1DNS", "eth1GateWay", "operatorMAC", "operatorDNS",
             "operatorRSSI", "awsStatus"}
SYS_KEYS = {"electricSys", "ratedGridVolt", "ratedGridHz", "electricSupply", "airSwitchCur", "gridPhaseConSet",
            "gridPhaseSeqSet", "solarInstallState", "genRatePower", "solarInvManu", "solarInvRatePower",
            "fhpRatePower", "kwRatePower", "gridExportEnable", "gridSoftLimit", "gridHardLimit",
            "isThreePhaseInstall"}


@pytest.fixture(scope="module")
def fleet():
    f = Fleet.from_dict({"gateways": [
        {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "location": AU, "weather": {"model": "bell"},
         "setup": "in-service", "history_days": 2, "apower_units": 2}]})
    f.start(control_port=0, cloud_port=0)
    yield f
    f.stop()


def _token(f) -> str:
    form = {"account": "owner@example.com", "password": hashlib.md5(b"emulator").hexdigest()}
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/terminal/initialize/"
                                 "appUserOrInstallerLogin", data=urllib.parse.urlencode(form).encode())
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)["result"]["token"]


def _call(f, method, path, token, body=None, status=None, **q) -> dict:
    sn = f["home"].serial
    q = {"gatewayId": sn, "equipNo": sn, "lang": "en_US", **q}
    data = json.dumps(body or {"gatewayId": sn}).encode() if method == "POST" else None
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/{path}?{urllib.parse.urlencode(q)}",
                                 data=data, method=method, headers={"loginToken": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            code, d = r.status, json.load(r)
    except urllib.error.HTTPError as e:
        code, d = e.code, json.load(e)
    if status is not None:
        assert code == status, path
    return d


def _mqtt(f, token, cmd_type, area) -> dict:
    sn = f["home"].serial
    body = {"cmdType": cmd_type, "equipNo": sn, "type": 0, "timeStamp": 0, "snno": 1, "len": 0, "crc": "",
            "dataArea": json.dumps(area)}
    d = _call(f, "POST", "terminal/sendMqtt", token, body=body, status=200)
    assert d["code"] == 200, d
    return d


def _monitor(f) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}/monitor", timeout=5) as r:
        return json.load(r)


def test_every_start_up_call_has_the_captured_fields(fleet):
    assert set(p for _, p in APP_CALLS) == set(CAPTURED) | set(OWN_ENVELOPE)
    token = _token(fleet)
    for method, path in APP_CALLS:
        if path in OWN_ENVELOPE:
            status, keys = OWN_ENVELOPE[path]
            assert set(_call(fleet, method, path, token, status=status)) == keys, path
            continue
        message, extras, kind, keys = CAPTURED[path]
        d = _call(fleet, method, path, token, status=200)
        assert set(d) == {"code", "message", "result", "success"} | extras, path
        assert (d["code"], d["message"], d["success"]) == (200, message, True), path
        assert isinstance(d["result"], kind), path
        if keys:
            item = d["result"][0] if kind is list else d["result"]
            assert set(item) == keys, (path, sorted(set(item) ^ keys))


def test_values_come_from_the_site(fleet):
    gw, token = fleet["home"], _token(fleet)
    site = gw.site
    info = _call(fleet, "GET", "terminal/getDeviceInfoV2", token)["result"]
    unit = models.APOWERS[2]
    assert (info["gatewayId"], info["sysHdVersionInt"], info["sysHdVersion"]) == (gw.serial, 102, "102")
    assert info["zoneInfo"] == "Australia/Sydney" and info["countryId"] == 3
    assert info["peHwVerList"] == [2, 2] and info["totalCap"] == pytest.approx(2 * unit.kwh)
    assert [a["rateBatCap"] for a in info["apowerList"]] == [int(unit.kwh * 1000)] * 2
    assert info["fixedPowerTotal"] == pytest.approx(2 * unit.kw)
    battery = _call(fleet, "GET", "terminal/chargePowerDetails", token)["result"]
    shown = fleet.cloud.composite(gw)["runtimeData"]["soc"]          # docs/FINDINGS.md: the same
    assert battery["batterySoc"] == pytest.approx(shown, abs=0.5)
    assert battery["touMinSoc"] == round(site.active_reserve())
    weather = _call(fleet, "GET", "terminal/weather/getCurrentBriefWeather", token)["result"]
    assert weather["tempC"] == pytest.approx(site.ambient_c(site.clock.now()), abs=0.5)
    # each rounded to 0.1 from the unrounded temperature: F from the rounded C can be 0.14 off (#366)
    assert weather["tempF"] == pytest.approx(weather["tempC"] * 9 / 5 + 32, abs=0.15)
    day = sorted(site.history)[-2]
    benefit = _call(fleet, "GET", "terminal/bill/electric/selectBenefitInfo", token, dayTime=day, type=1)["result"]
    kwh = cloud_energy.day_kwh(gw, day)
    assert benefit["dayTimeList"] == [day] and benefit["priceFlag"] is False
    assert benefit["solarToHouseList"] == [round(kwh["sun_home"], 2)]
    assert benefit["apowerToHouseList"] == [round(kwh["bat_home"], 2)]
    entrance = _call(fleet, "GET", "terminal/tou/getEntranceInfo", token)["result"]
    pc = fleet.cloud.power_control(gw)
    assert {k: entrance[k] for k in ("gridFeedMaxFlag", "gridMaxFlag", "gridFeedMax", "gridMax")} == \
        {k: pc[k] for k in ("gridFeedMaxFlag", "gridMaxFlag", "gridFeedMax", "gridMax")}
    caps = _call(fleet, "GET", "common/getPowerCapConfigList", token)
    assert caps["total"] == len(models.APOWERS) == len(caps["result"])
    logs = _call(fleet, "GET", "common/selectDeviceRunLogList", token)["result"]
    assert logs[0] == {"alarmCode": "0", "logName": "Ground fault", "enLogName": "Ground fault"}
    assert {"alarmCode": "1", "logName": "DC over volt", "enLogName": "DC over volt"} in logs


def test_no_token_unknown_gateway_and_the_monitor(fleet):
    d = _call(fleet, "GET", "terminal/getDeviceInfoV2", None)
    assert d["code"] == 401 and "result" not in d
    token = _token(fleet)
    assert _call(fleet, "GET", "terminal/chargePowerDetails", token, gatewayId="nope", equipNo="nope")["code"] == 400
    assert _call(fleet, "GET", "common/getPowerCapConfigList", token, gatewayId="")["code"] == 200   # names none
    for method, path in APP_CALLS:
        _call(fleet, method, path, token)
    assert _monitor(fleet)["cloud"]["unserved"] == {}


def test_sendmqtt_317_327_337_have_the_captured_fields(fleet):
    token = _token(fleet)
    for cmd_type, (area, reply, keys) in MQTT.items():
        r = _mqtt(fleet, token, cmd_type, area)["result"]
        assert r["cmdType"] == reply
        assert set(json.loads(r["dataArea"])) == keys, cmd_type
    net = json.loads(_mqtt(fleet, token, 317, {"optType": 0, "paraType": 6})["result"]["dataArea"])
    assert set(net["commSetPara"]) == COMM_KEYS and net["commSetPara"]["currentNetType"] == 3
    sys_ = json.loads(_mqtt(fleet, token, 317, {"optType": 0, "paraType": 12})["result"]["dataArea"])
    assert set(sys_["sysConfigPara"]) == SYS_KEYS and sys_["num"] == 17
    assert sys_["sysConfigPara"]["fhpRatePower"] == int(models.APOWERS[2].kw * 1000)


def test_sendmqtt_writes_are_kept(fleet):
    token = _token(fleet)
    area = lambda t, a: json.loads(_mqtt(fleet, token, t, a)["result"]["dataArea"])
    assert area(337, {"opt": 1, "wifi_SSID": "lab", "wifi_Pw": "secret"})["wifi_SSID"] == "lab"
    assert area(337, {"opt": 0})["wifi_Pw"] == "secret"
    assert area(327, {"opt": 1, "lightStat": [1, 1], "timeEn": 1})["lightStat"] == [1, 1]
    assert area(327, {"opt": 0})["timeEn"] == 1
    net = area(317, {"optType": 1, "paraType": 6, "commSetPara": {"currentNetType": 4}})
    assert net["optType"] == 1 and net["commSetPara"]["opt"] == 1
    assert area(317, {"optType": 0, "paraType": 6})["commSetPara"]["currentNetType"] == 4
    area(317, {"optType": 1, "paraType": 6, "commSetPara": {"currentNetType": 3}})


def test_batch_two_values_come_from_the_site(fleet):
    gw, token = fleet["home"], _token(fleet)
    unit = models.APOWERS[2]
    overall = _call(fleet, "GET", "terminal/selectDeviceOverallInfo", token)["result"]
    assert overall == {"apowerCount": 2, "totalPower": pytest.approx(2 * unit.kwh)}
    aps = _call(fleet, "GET", "terminal/obtainApowersInfo", token)["result"]
    assert [a["ratedPower"] for a in aps] == [unit.kw] * 2 and [a["peHwVer"] for a in aps] == ["2", "2"]
    ag = _call(fleet, "GET", "terminal/obtainAgateInfo", token)["result"]
    assert (ag["sysHdVersion"], ag["ibgVersion"]) == (102, gw.profile.dc_firmware)
    detail = _call(fleet, "GET", "terminal/site/get/DeviceDetail", token)["result"]
    assert (detail["gatewayName"], detail["country"], detail["alphaCode"], detail["address1"]) == (
        "home", "Australia", "AU", "")
    bonus = _call(fleet, "GET", "terminal/tou/getBonusInfo", token)["result"]
    assert bonus["apowerCount"] == 2 and bonus["bbDefaultVo"]["dischargePower"] == 2.5
    earn = _call(fleet, "POST", "terminal/tou/calculate/expected/earnings", token,
                 body={"template": {"gatewayId": gw.serial}, "strategyList": [], "nemType": 1})
    assert (earn["result"]["gatewayId"], earn["result"]["scene"]) == (gw.serial, 1)
    assert _call(fleet, "GET", "terminal/sim/cardShow", token, status=404) == {"message": "", "status": 404}
    assert _call(fleet, "GET", "newApi/api-user/app/resource/getUserResources/v2", token)["msg"] == "请求成功"


def test_alarms_and_backups_follow_the_site(fleet):
    gw, token = fleet["home"], _token(fleet)
    site = gw.site
    assert _call(fleet, "GET", "terminal/selectGatewayAlarm", token)["result"] == []
    assert _call(fleet, "GET", "terminal/backupHistorySummary", token)["result"]["lastRecord"] is None
    site.force_alarm("OVER_TEMP")
    try:
        alarms = _call(fleet, "GET", "terminal/selectGatewayAlarm", token)
        assert alarms["total"] == 1 and set(alarms["result"][0]) == ALARM_KEYS
        assert (alarms["result"][0]["alarmCode"], alarms["result"][0]["logNameEn"]) == ("7", "Over temp")
    finally:
        site.clear_alarm()
    t0 = site.clock.now()
    site.relay_events.extend([{"ts": t0 - 600, "event": "open", "reason": "outage", "stage": None},
                              {"ts": t0 - 300, "event": "close", "reason": None, "stage": None}])
    try:
        b = _call(fleet, "GET", "terminal/backupHistorySummary", token)["result"]
        assert (b["totalBackup"], b["totalDuration"], b["longestDuration"]) == (1, 300, 300)
        assert b["lastRecord"]["second"] == 300 and b["lastRecord"]["chargeType"] == 2
    finally:
        del site.relay_events[-2:]
