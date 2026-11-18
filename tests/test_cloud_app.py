"""#333 (#316 step 3): the official app's start-up calls. Each reply has the fields the app's 42
HTTP Toolkit captures show (names and types merged over every capture), and its values come
from the site."""

import hashlib
import json
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
}


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


def _call(f, method, path, token, **q) -> dict:
    sn = f["home"].serial
    q = {"gatewayId": sn, "equipNo": sn, "lang": "en_US", **q}
    data = json.dumps({"gatewayId": sn}).encode() if method == "POST" else None
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/{path}?{urllib.parse.urlencode(q)}",
                                 data=data, method=method, headers={"loginToken": token} if token else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def _monitor(f) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}/monitor", timeout=5) as r:
        return json.load(r)


def test_every_start_up_call_has_the_captured_fields(fleet):
    assert set(p for _, p in APP_CALLS) == set(CAPTURED)
    token = _token(fleet)
    for method, path in APP_CALLS:
        message, extras, kind, keys = CAPTURED[path]
        d = _call(fleet, method, path, token)
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
    assert weather["tempF"] == pytest.approx(weather["tempC"] * 9 / 5 + 32, abs=0.1)
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
