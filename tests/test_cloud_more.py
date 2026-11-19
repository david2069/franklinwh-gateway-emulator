"""#341 (#316 step 3, batch 3): the official app's remaining low-frequency calls, the assistant
and sendMqtt 315, 335, 339 and 341. Each reply has the fields the app's 50 HTTP Toolkit captures
show (names and types merged over every capture), and its values come from the site."""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator import cloud_energy
from franklinwh_emulator.cloud_more import CALLS, SMART_CALLS
from franklinwh_emulator.gateway import Fleet

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU", "state": "NSW",
      "city": "Sydney", "postcode": "2000"}
US = {"latitude": 33.45, "longitude": -112.07, "timezone": "America/Phoenix", "country": "US", "state": "AZ",
      "city": "Phoenix"}

# (method, path below /hes-gateway/) -> (message, envelope extras, result type, result keys, or a
# list item's); from the captures (aggregates).
CAPTURED = {
    ("GET", "common/getDeviceRealTimeData"): ("Query success!", {"total"}, dict, {"batterySoc"}),
    ("GET", "common/getMaintenanceInfo"): ("Query success!", {"total"}, type(None), set()),
    ("GET", "common/obtainGeographyList"): ("Query success!", {"total"}, dict, {"cityList", "countryList", "provinceList"}),
    ("GET", "common/v2/obtainUrlByKey"): ("Operate success!", set(), dict, {"description", "keyVal", "url"}),
    ("GET", "terminal/chargePowerTotal"): ("Query success!", {"total"}, dict, {"hour", "minute", "second"}),
    ("GET", "terminal/countLoad"): ("Query success!", {"total"}, dict, {
        "endDate", "kwhFhpLoad", "kwhFhpLoadRate", "kwhGenLoad", "kwhGenLoadRate", "kwhGridLoad", "kwhGridLoadRate",
        "kwhSolarLoad", "kwhSolarLoadRate", "startDate", "status"}),
    ("GET", "terminal/electricStatistics"): ("Query success!", {"total"}, dict, {
        "electricVo", "endDate", "genStat", "opt", "startDate", "year"}),
    ("GET", "terminal/getCompliance"): ("Operate success!", set(), dict, {
        "countryId", "gridConnFuntionPara", "num", "optType", "paraType", "provinceId"}),
    ("GET", "terminal/getDeviceInfo"): ("Operate success!", set(), dict, {
        "activeStatus", "apbox20Num", "apowerList", "chooseFinancierId", "countryId", "date", "deviceTime",
        "emsDeviceVer", "fhpSn", "firstName", "fixedPowerAverage", "fixedPowerTotal", "gatewayId", "genEn",
        "gridPhaseConSet", "installerId", "lastName", "mpanGridCurLim", "mpanGridEnb", "mpptEnFlag", "msaModel",
        "newHomeFlag", "offGirdFlag", "offGridReason", "offMessage", "peHwVerList", "protocolVer", "provinceId",
        "realSysHdVersion", "sleepStatus", "solarFlag", "solarTipMsg", "sysHdVersion", "sysHdVersionInt",
        "totalCap", "v2lModeEnable", "v2lRunState"}),
    ("GET", "terminal/getEquipmentLocationDetail"): ("Query success!", {"total"}, dict, {
        "address", "addressSecondLast", "alertMessage", "alphaCode", "city", "completeAddress", "country", "dst",
        "gatewayId", "gatewayName", "latitude", "longitude", "postCode", "province", "region", "timezone",
        "zoneInfo"}),
    ("GET", "terminal/getGatewaySystemSetting"): ("Query success!", {"total"}, dict, {
        "apowerNumber", "countryId", "electricSupply", "gatewayId", "gridExportEnable", "gridHardLimit",
        "gridSoftLimit", "isThreePhaseInstall", "offGridFlag", "pcsSetFlag", "ratedGridHz", "ratedGridVolt",
        "rsdEnable", "threePhPvEnb"}),
    ("GET", "terminal/getHotSpotInfo"): ("Query success!", {"total"}, str, set()),
    ("GET", "terminal/getHotSpotInfo/v2"): ("Query success!", {"total"}, dict, {"gatewayId", "id", "password", "ssid"}),
    ("GET", "terminal/getOffGridFlag"): ("Query success!", {"total"}, dict, {"offGridFlag"}),
    ("GET", "terminal/getWarnFlagNotice"): ("Query success!", {"total"}, dict, {"warnFlag"}),
    ("GET", "terminal/initialize/selectTerStartupPage"): ("Query success!", {"total"}, type(None), set()),
    ("GET", "terminal/iotChargePowers"): ("Query success!", {"total"}, list, {
        "chargeDate", "chargeHour", "chargeTime", "chargeType", "chour", "cminute", "csecond", "endSoc", "endTime",
        "energy", "gatewayId", "id", "second", "startSoc", "startTime"}),
    ("GET", "terminal/iotDayPowerStatistics"): ("Query success!", {"total"}, dict, {
        "date", "genStat", "iotDayPowerVo", "last", "next"}),
    ("GET", "terminal/listAppUserDevice"): ("Query success!", {"total"}, list, {
        "account", "activeStatus", "activeTime", "address", "createTime", "deviceTime", "dst", "id", "installTime",
        "name", "protocolVer", "status", "sysHdVersion", "timeZone", "version", "zoneInfo"}),
    ("GET", "terminal/listDeviceMessagesByType"): ("Query success!", {"total"}, list, {
        "citys", "content", "countrys", "creationTime", "deviceSendTime", "event", "gatewayId", "id",
        "importFileAddress", "importFileName", "installerContent", "msgType", "notice", "provinces", "pushLink",
        "pushType", "readStatus", "receiveMode", "receiveNum", "sendMode", "startTime", "status", "subType",
        "title", "toUrlParams", "toUrlType", "turnoverTime", "type", "userMessageId"}),
    ("GET", "terminal/newCompliance/getComplianceDetailById"): ("Operate success!", set(), dict, {
        "alertMessage", "catalog", "complianceRuleType", "complianceRuleTypeName", "country", "countryId",
        "fromTemplateId", "province", "provinceId", "systemId", "templateFromType"}),
    ("GET", "terminal/newCompliance/getComplianceNameList"): ("Operate success!", set(), dict, {
        "countryId", "currentId", "list"}),
    ("GET", "terminal/selectEventClassification"): ("Query success!", {"total"}, dict, {
        "blockTime", "blockTimeFlag", "classificationList", "classificationsId", "weekDate"}),
    ("GET", "terminal/selectIotUserRuntimeDataLog"): ("Query success!", {"total"}, dict, {
        "createTime", "dataArea", "gatewayId", "id", "updateTime"}),
    ("GET", "terminal/selectTerPushMessageList"): ("Query success!", {"total"}, list, set()),
    ("GET", "terminal/selectTerPushMessageListApp"): ("Query success!", {"total"}, list, set()),
    ("GET", "terminal/selectTerPushMessageUnread"): ("Query success!", {"total"}, int, set()),
    ("GET", "terminal/selectWorkingHours"): ("Query success!", {"total"}, dict, {
        "currentStateDuration", "currentTime", "day", "gatewayId", "id", "income", "mapList", "open", "powerFhp",
        "powerGen", "powerLoad", "powerSun", "powerUti", "recTime", "spontaneous"}),
    ("GET", "terminal/site/get/SiteDetail"): ("Operate success!", set(), dict, {
        "address1", "address2", "alphaCode", "city", "completeAddress", "country", "postCode", "province",
        "siteName"}),
    ("GET", "terminal/site/list/siteAndDeviceInfo"): ("Query success!", {"total"}, list, {
        "basicDeviceInfoVOList", "completeAddress", "siteId", "siteName"}),
    ("GET", "terminal/solar/selectAnyOneSolarOpen"): ("Operate success!", set(), dict, {
        "dspNemPvEn", "gridSplitCtEn", "installApboxSolar", "installProximalsolar", "installPv1Port",
        "installPv2Port", "isThreePhaseInstall", "mpanPv1En", "mpanPv2En", "mpptEnFlag", "mpptExportEn",
        "offGridFlag", "offGridReason", "pvSplitCtEn", "remoteSolarEn", "remoteSolarMode", "sysHdVersion"}),
    ("GET", "terminal/span/getSpanSetting"): ("Operate success!", set(), dict, {"spanFlag"}),
    ("GET", "terminal/system/getSystemSetting"): ("Query success!", {"total"}, dict, {
        "apowerNumber", "countryId", "electricSupply", "gatewayId", "gridExportEnable", "gridHardLimit",
        "gridSoftLimit", "isThreePhaseInstall", "offGridFlag", "pcsSetFlag", "ratedGridHz", "ratedGridVolt",
        "rsdEnable", "threePhPvEnb"}),
    ("GET", "terminal/tou/getTariffListByCompanyId"): ("Operate success!", set(), dict, {"dataList", "postCodeHave"}),
    ("GET", "terminal/tou/getVppTipForUpdateTou"): ("", set(), type(None), set()),
    ("GET", "terminal/v2/queryTerminalUserInfo"): ("Operate success!", set(), dict, {"account", "receiveLetterFlag"}),
    ("GET", "terminal/v2/warrantyInfo"): ("Operate success!", set(), dict, {
        "deviceExpirationList", "equipmentSupplierPhone", "expirationTime", "installerCompany",
        "installerCompanyEmail", "installerCompanyPhone", "remainThroughput", "throughput", "warrantyLink"}),
    ("POST", "terminal/getProvincesListBYGatewayId"): ("Operate success!", set(), dict, {
        "iotGatewayRegionVo", "worldProvincesList"}),
    ("POST", "terminal/setEventPushToUser"): ("Operate success!", set(), type(None), set()),
    ("POST", "terminal/tou/getCustomEnergyDispatchList"): ("Operate success!", set(), dict, {"dispatchList", "strategyList"}),
    ("POST", "terminal/tou/getGatewayTouList"): ("Operate success!", set(), dict, {
        "backupForeverFlag", "currendId", "gridChargeEn", "itcFlag", "list", "nextWorkMode", "sgipFlag", "stopMode",
        "stromEn", "tariffSettingFlag", "timerEndTime", "timerEndTimeZero", "timerStartTime", "timerStartTimeZero",
        "todayVppVo", "touAlertMessage", "touSendStatus", "vppSocVo", "zoneInfo"}),
    ("POST", "terminal/tou/getRecommendEnergyDispatchList"): ("Operate success!", set(), dict, {"strategyList"}),
    ("POST", "terminal/tou/getTouCompanyListPageV2"): ("Operate success!", set(), dict, {"dataList", "postCodeHave"}),
    ("POST", "terminal/updateTerPushMessageReadByType"): ("Query success!", {"total"}, int, set()),
}
# Replies in another envelope: (method, path) -> (HTTP status, the whole reply's keys, code, success).
OWN_ENVELOPE = {
    ("GET", "newApi/api-user/app/resource/getUserResources"): (200, {"code", "msg", "data", "success"}, 200, True),
    ("GET", "terminal/site/check/OverlapSite"): (200, {"code", "message", "result", "success"}, 4091, False),
    ("POST", "terminal/site/update/DeviceDetail"): (200, {"code", "message", "result", "success"}, 4094, False),
    ("POST", "terminal/site/update/SiteDetail"): (200, {"code", "message", "result", "success"}, 4094, False),
    ("GET", "terminal/tou/getTariffDetailByIdV2"): (200, {"code", "message", "result", "success"}, 400, False),
    ("GET", "terminal/selectTerPushMessageV2"): (200, {"code", "message", "result", "success"}, 400, False),
}
SMART = {
    ("GET", "/api-smart/terminal/recommendQuestion"): (list, {"code", "msg", "data", "success"}),
    ("POST", "/api-smart/terminal/askQuestion"): (dict, {"code", "msg", "data", "success"}),
    ("POST", "/api-smart/terminal/feedback"): (None, {"code", "msg", "success"}),
}
ASK_KEYS = {"action", "function", "scene", "subScene", "content", "hint", "jumpScheme", "questionId", "component",
            "sessionId"}
# Nested shapes, as captured.
SITE_DEVICE = {"gatewayName", "gatewayId", "completeAddress", "status", "sysHdVersion", "agateNum", "apowerNum"}
CONTENT = {"name", "key", "type", "valueType", "defaultValue", "minValue", "maxValue", "accuracy", "rateUnit", "unit",
           "value", "description", "rank", "needSend", "values", "hide", "enableCondition", "validCondition",
           "sendRule", "showUnit"}
ELECTRIC_VO = {"id", "gatewayId", "year", "day", "weekDay", "monthDay", "kwhSun", "kwhGen", "kwhUtiIn", "kwhUtiOut",
               "kwhFhpChg", "kwhFhpDi", "kwhLoad", "kwhSunTotal", "kwhGenTotal", "kwhUtiInTotal", "kwhUtiOutTotal",
               "kwhFhpChgTotal", "kwhFhpDiTotal", "kwhLoadTotal", "mapList", "weekList"}
ELECTRIC_ROW = {"soOutGrid", "kwhSolarLoad", "soChBat", "genChBat", "kwhSun", "monthDay", "kwhGen", "gridChBat",
                "kwhLoad", "kwhUtiIn", "kwhUtiOut", "kwhFhpLoad", "kwhFhpDi", "batOutGrid", "kwhGenLoad",
                "kwhGridLoad", "kwhFhpChg"}
DAY_POINT = {"mode", "powerFhp", "soc", "recTime", "powerGen", "powerLoad", "kwhTotal", "powerUti", "powerSun"}
HOURS_POINT = {"powerSunColor", "powerFhpColor", "powerGenColor", "powerFhp", "recTime", "powerGen", "powerLoad",
               "powerUti", "powerSun", "powerLoadColor", "powerUtiColor"}
EXPIRY = {"sn", "model", "type", "expirationTime", "subModuleExpirationTime"}
DISPATCH = {"id", "dispatchCode", "title", "recommendScene", "recommendLevel", "content", "remark", "url",
            "descriptionUrl", "waveType", "solarPriority", "loadPriority", "mainIndex", "webUrls", "recommendFlag"}
REGION = {"countryZh", "countryEn", "provinceZh", "provinceEn", "countryId", "provinceId", "latitude", "longitude"}
PROVINCE = {"provinceZh", "provinceEn", "countryId", "latitude", "longitude", "provinceAlias", "region",
            "provinceCode", "provinceId"}
COUNTRY = {"id", "nameEn", "nameZh", "latitude", "longitude", "countryCode", "provinceList", "sorted", "alphaCode"}
GEO_PROVINCE = {"id", "nameEn", "nameZh", "latitude", "longitude", "cityList"}
CLASS = {"id", "name", "eventsId", "explain"}
# sendMqtt cmdType -> (request dataArea, reply cmdType, reply dataArea keys).
MQTT = {
    315: ({"opt": 0, "paraType": 0, "reboot": 0}, 316, {
        "paraType", "opt", "reboot", "cleanUnlockAlarm", "cleanLockAlarm", "cleanAlarmFlag", "reset", "result"}),
    335: ({"wifi_ScanTime": 5}, 336, {"result", "reason", "wifi_Info"}),
    339: ({"opt": 0}, 340, {"opt", "result", "reason", "routerStatus", "netStatus", "awsStatus",
                            "EthConnectRouterStatus", "wifiConnectRouterStatus", "4GConnectBSStatus",
                            "WifiSignalStrength", "4GSignalStrength", "currentNetType"}),
    341: ({"opt": 0}, 342, {"opt", "result", "reason", "ethernet0NetSwitch", "ethernet1NetSwitch", "wifiNetSwitch",
                            "4GNetSwitch"}),
}


@pytest.fixture(scope="module")
def fleet():
    f = Fleet.from_dict({"gateways": [
        {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "location": AU, "weather": {"model": "bell"},
         "setup": "in-service", "history_days": 2, "apower_units": 2, "commissioned": "2025-03-01",
         "service": {"main_breaker_a": 63}},
        {"id": "us", "port": 0, "dc_port": 0, "seed": 4, "location": US, "ac_type": 1, "weather": {"model": "bell"}}]})
    f.start(control_port=0, cloud_port=0)
    yield f
    f.stop()


def _token(f) -> str:
    form = {"account": "owner@example.com", "password": hashlib.md5(b"emulator").hexdigest()}
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/terminal/initialize/"
                                 "appUserOrInstallerLogin", data=urllib.parse.urlencode(form).encode())
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)["result"]["token"]


def _call(f, method, path, token, body=None, status=200, gw="home", **q) -> dict:
    sn = f[gw].serial
    q = {"gatewayId": sn, "equipNo": sn, "lang": "en_US", **q}
    data = json.dumps(body or {"gatewayId": sn}).encode() if method == "POST" else None
    url = path if path.startswith("/") else f"/hes-gateway/{path}"
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}{url}?{urllib.parse.urlencode(q)}",
                                 data=data, method=method, headers={"loginToken": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            code, d = r.status, json.load(r)
    except urllib.error.HTTPError as e:
        code, d = e.code, json.load(e)
    assert code == status, (path, code, d)
    return d


def _mqtt(f, token, cmd_type, area) -> dict:
    sn = f["home"].serial
    body = {"cmdType": cmd_type, "equipNo": sn, "type": 0, "timeStamp": 0, "snno": 1, "len": 0, "crc": "",
            "dataArea": json.dumps(area)}
    r = _call(f, "POST", "terminal/sendMqtt", token, body=body)["result"]
    return r


def _monitor(f) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}/monitor", timeout=5) as r:
        return json.load(r)


def _day(f) -> str:
    return sorted(f["home"].site.history)[-2]


def test_every_call_has_the_captured_fields(fleet):
    assert set(CALLS) == set(CAPTURED) | set(OWN_ENVELOPE)
    token = _token(fleet)
    site = fleet["home"].site
    t0 = site.clock.now()
    site.relay_events.extend([{"ts": t0 - 600, "event": "open", "reason": "outage", "stage": None},
                              {"ts": t0 - 300, "event": "close", "reason": None, "stage": None}])
    fleet["home"].events.add("grid", "Grid outage (emulated)")
    try:
        for (method, path), (message, extras, kind, keys) in CAPTURED.items():
            d = _call(fleet, method, path, token, siteId=1, systemId=1, startDate=_day(fleet),
                      endDate=_day(fleet), day=_day(fleet), date=_day(fleet), key="privacy")
            assert set(d) == {"code", "message", "result", "success"} | extras, path
            assert (d["code"], d["message"], d["success"]) == (200, message, True), path
            assert isinstance(d["result"], kind), (path, d["result"])
            if keys:
                item = d["result"][0] if kind is list else d["result"]
                assert set(item) == keys, (path, sorted(set(item) ^ keys))
    finally:
        del site.relay_events[-2:]
    for (method, path), (status, keys, code, ok) in OWN_ENVELOPE.items():
        d = _call(fleet, method, path, token, status=status, tariffId=1, id=1)
        assert set(d) == keys and (d["code"], d["success"]) == (code, ok), (path, d)
    assert _call(fleet, "GET", "terminal/site/check/OverlapSite", token)["result"] == {
        "alertMessage": None, "alertTitle": None}
    assert _call(fleet, "POST", "terminal/site/update/SiteDetail", token)["message"] == "Successful."
    assert _call(fleet, "GET", "newApi/api-user/app/resource/getUserResources", token)["data"] == {
        "resources": None, "roleTypes": None}


def test_the_assistant(fleet):
    assert set(SMART_CALLS) == set(SMART)
    token = _token(fleet)
    for (method, path), (kind, keys) in SMART.items():
        d = _call(fleet, method, path, token, body={"sessionId": "s1", "content": "hello", "action": 1})
        assert set(d) == keys and (d["code"], d["msg"], d["success"]) == (200, "Request Success", True), path
        if kind is not None:
            assert isinstance(d["data"], kind), path
    ask = _call(fleet, "POST", "/api-smart/terminal/askQuestion", token, body={"sessionId": "s1", "action": 1})["data"]
    assert set(ask) == ASK_KEYS and ask["sessionId"] == "s1" and ask["action"] == 1


def test_nested_shapes(fleet):
    token = _token(fleet)
    day = _day(fleet)
    sites = _call(fleet, "GET", "terminal/site/list/siteAndDeviceInfo", token)
    assert set(sites["result"][0]["basicDeviceInfoVOList"][0]) == SITE_DEVICE
    detail = _call(fleet, "GET", "terminal/newCompliance/getComplianceDetailById", token,
                   systemId=_call(fleet, "GET", "terminal/newCompliance/getComplianceNameList", token)["result"]["currentId"])
    content = detail["result"]["catalog"][0]["parameters"][0]["content"]
    assert content and all(set(c) == CONTENT for c in content)
    vo = _call(fleet, "GET", "terminal/electricStatistics", token, startDate=day, endDate=day, opt=1)["result"]["electricVo"]
    assert set(vo) == ELECTRIC_VO and set(vo["mapList"][0]) == ELECTRIC_ROW and set(vo["weekList"][0]) == ELECTRIC_ROW
    power = _call(fleet, "GET", "terminal/iotDayPowerStatistics", token, date=day)["result"]["iotDayPowerVo"]
    assert set(power) == {"date", "mapList", "genStat", "deviceTime"} and set(power["mapList"][0]) == DAY_POINT
    hours = _call(fleet, "GET", "terminal/selectWorkingHours", token, day=day)["result"]
    assert set(hours["mapList"][0]) == HOURS_POINT
    expiry = _call(fleet, "GET", "terminal/v2/warrantyInfo", token)["result"]["deviceExpirationList"]
    assert all(set(e) == EXPIRY for e in expiry)
    blocks = [{"startHourTime": "00:00", "endHourTime": "12:00", "waveType": 0}]
    custom = _call(fleet, "POST", "terminal/tou/getCustomEnergyDispatchList", token,
                   body={"gatewayId": fleet["home"].serial, "strategyList": blocks})["result"]
    assert all(set(d) == DISPATCH for d in custom["dispatchList"])
    assert set(custom["strategyList"][0]) == {"startHourTime", "endHourTime", "waveType", "recommendDispatchList"}
    season = [{"id": None, "seasonName": "All", "month": "1,2,3,4,5,6,7,8,9,10,11,12", "templateId": None,
               "dayTypeVoList": [{"dayType": 3, "detailVoList": blocks}]}]
    rec = _call(fleet, "POST", "terminal/tou/getRecommendEnergyDispatchList", token,
                body={"gatewayId": fleet["home"].serial, "strategyList": season})["result"]["strategyList"][0]
    point = rec["dayTypeVoList"][0]["detailVoList"][0]
    assert set(rec) == {"id", "seasonName", "dayTypeVoList"} and set(point["dispatch"]) == DISPATCH
    region = _call(fleet, "POST", "terminal/getProvincesListBYGatewayId", token)["result"]
    assert set(region["iotGatewayRegionVo"]) == REGION and set(region["worldProvincesList"][0]) == PROVINCE
    geo = _call(fleet, "GET", "common/obtainGeographyList", token)["result"]
    assert set(geo["countryList"][0]) == COUNTRY
    geo = _call(fleet, "GET", "common/obtainGeographyList", token, countryId=3)["result"]
    assert set(geo["provinceList"][0]) == GEO_PROVINCE
    classes = _call(fleet, "GET", "terminal/selectEventClassification", token)["result"]["classificationList"]
    assert all(set(c) == CLASS for c in classes)


def test_values_come_from_the_site_and_the_fleet(fleet):
    gw, token = fleet["home"], _token(fleet)
    site = gw.site
    sites = _call(fleet, "GET", "terminal/site/list/siteAndDeviceInfo", token)
    assert sites["total"] == 2 and [s["siteName"] for s in sites["result"]] == ["home", "us"]
    dev = sites["result"][0]["basicDeviceInfoVOList"][0]
    assert (dev["gatewayId"], dev["apowerNum"], dev["sysHdVersion"]) == (gw.serial, 2, 102)
    site_detail = _call(fleet, "GET", "terminal/site/get/SiteDetail", token, siteId=2)["result"]
    assert (site_detail["siteName"], site_detail["province"], site_detail["alphaCode"]) == ("us", "AZ", "US")
    assert _call(fleet, "GET", "terminal/site/get/SiteDetail", token, siteId=9)["code"] == 400
    devices = _call(fleet, "GET", "terminal/listAppUserDevice", token)["result"]
    assert [d["id"] for d in devices] == [gw.serial, fleet["us"].serial]
    assert devices[0]["activeTime"] == "2025-03-01 00:00:00" and devices[0]["account"] == "owner@example.com"
    loc = _call(fleet, "GET", "terminal/getEquipmentLocationDetail", token)["result"]
    assert (loc["latitude"], loc["longitude"], loc["zoneInfo"], loc["city"]) == ("-33.87", "151.21",
                                                                                 "Australia/Sydney", "Sydney")
    assert loc["timezone"] == 10.0 and loc["dst"] in (0, 1)
    names = _call(fleet, "GET", "terminal/newCompliance/getComplianceNameList", token)["result"]
    current = dict((n["id"], n["name"]) for n in names["list"])[names["currentId"]]
    assert current == site.grid.name and names["countryId"] == 3
    params = _call(fleet, "GET", "terminal/getCompliance", token, paraType=7)["result"]
    assert params["num"] == len(params["gridConnFuntionPara"]) == 89 and params["paraType"] == 7
    assert params["gridConnFuntionPara"]["ComplianceRuleType"] == names["currentId"]
    geo = _call(fleet, "GET", "common/obtainGeographyList", token)["result"]
    assert [(c["id"], c["alphaCode"]) for c in geo["countryList"]] == [(2, "US"), (3, "AU")]
    assert [p["nameEn"] for p in _call(fleet, "GET", "common/obtainGeographyList", token, countryId=2)
            ["result"]["provinceList"]] == ["AZ"]
    region = _call(fleet, "POST", "terminal/getProvincesListBYGatewayId", token)["result"]["iotGatewayRegionVo"]
    assert (region["countryEn"], region["provinceEn"], region["provinceId"]) == ("Australia", "NSW", 1)
    sys_ = _call(fleet, "GET", "terminal/getGatewaySystemSetting", token)["result"]
    assert (sys_["electricSupply"], sys_["apowerNumber"], sys_["countryId"]) == (63, 2, 3)
    v1 = _call(fleet, "GET", "terminal/getDeviceInfo", token)["result"]
    v2 = _call(fleet, "GET", "terminal/getDeviceInfoV2", token)["result"]
    assert {k: v1[k] for k in ("gatewayId", "sysHdVersion", "totalCap", "peHwVerList")} == \
        {k: v2[k] for k in ("gatewayId", "sysHdVersion", "totalCap", "peHwVerList")}
    soc = _call(fleet, "GET", "common/getDeviceRealTimeData", token)["result"]["batterySoc"]
    assert soc == pytest.approx(fleet.cloud.composite(gw)["runtimeData"]["soc"], abs=0.5)
    rt = _call(fleet, "GET", "terminal/selectIotUserRuntimeDataLog", token)["result"]["dataArea"]
    assert len(rt) == 59 and rt["soc"] == pytest.approx(soc, abs=0.5)
    warranty = _call(fleet, "GET", "terminal/v2/warrantyInfo", token)["result"]
    assert warranty["expirationTime"] == "2037-03-01"                       # aGate: 12 years
    assert [e["type"] for e in warranty["deviceExpirationList"]] == [1, 2, 2]
    assert warranty["throughput"] == int(2 * site.components.parts["apower"].throughput_mwh)
    spot = _call(fleet, "GET", "terminal/getHotSpotInfo/v2", token)["result"]
    assert spot["ssid"] == _call(fleet, "GET", "terminal/getHotSpotInfo", token)["result"] == "aGate_" + gw.serial[-4:]


def test_energy_follows_the_history(fleet):
    gw, token = fleet["home"], _token(fleet)
    day = _day(fleet)
    kwh = cloud_energy.day_kwh(gw, day)
    vo = _call(fleet, "GET", "terminal/electricStatistics", token, startDate=day, endDate=day, opt=1)["result"]["electricVo"]
    assert vo["day"] == [day] and vo["kwhSun"] == [round(kwh["sun"], 2)] and vo["kwhLoadTotal"] == round(kwh["load"], 2)
    load = _call(fleet, "GET", "terminal/countLoad", token, date=day, type=1)["result"]
    assert load["kwhSolarLoad"] == round(kwh.get("sun_home", 0.0), 2) and (load["startDate"], load["endDate"]) == (day, day)
    assert load["kwhSolarLoadRate"].endswith("%")
    slots = sum(1 for s in gw.site.history[day] if s.get("s"))
    assert len(_call(fleet, "GET", "terminal/selectWorkingHours", token, day=day)["result"]["recTime"]) == slots
    stats = _call(fleet, "GET", "terminal/iotDayPowerStatistics", token, date=day)["result"]
    assert len(stats["iotDayPowerVo"]["mapList"]) == slots and stats["next"]["date"] > day > stats["last"]["date"]
    assert _call(fleet, "GET", "terminal/electricStatistics", token, startDate="bad")["code"] == 400


def test_backups_messages_and_event_settings(fleet):
    gw, token = fleet["home"], _token(fleet)
    site = gw.site
    t0 = site.clock.now()
    site.relay_events.extend([{"ts": t0 - 7300, "event": "open", "reason": "outage", "stage": None},
                              {"ts": t0 - 100, "event": "close", "reason": None, "stage": None}])
    try:
        powers = _call(fleet, "GET", "terminal/iotChargePowers", token)
        assert powers["total"] == 1 and (powers["result"][0]["chour"], powers["result"][0]["cminute"]) == (2, 0)
        assert _call(fleet, "GET", "terminal/chargePowerTotal", token)["result"] == {"hour": 2, "minute": 0, "second": 0}
    finally:
        del site.relay_events[-2:]
    gw.events.add("grid", "Grid outage (emulated)")
    msgs = _call(fleet, "GET", "terminal/listDeviceMessagesByType", token, eventTypes="1")["result"]
    assert msgs[0]["content"] == "Grid outage (emulated)" and {m["event"] for m in msgs} == {1}
    assert all(m["event"] != 1 for m in _call(fleet, "GET", "terminal/listDeviceMessagesByType", token,
                                              eventTypes="5")["result"])
    _call(fleet, "POST", "terminal/setEventPushToUser", token,
          body={"classificationsId": "1,3", "blockTime": "22:00-07:00", "blockTimeFlag": 1, "userId": 1})
    ev = _call(fleet, "GET", "terminal/selectEventClassification", token)["result"]
    assert (ev["classificationsId"], ev["blockTime"], ev["blockTimeFlag"]) == ("1,3", "22:00-07:00", 1)


def test_sendmqtt_315_335_339_341(fleet, monkeypatch):
    gw, token = fleet["home"], _token(fleet)
    for cmd_type, (area, reply, keys) in MQTT.items():
        r = _mqtt(fleet, token, cmd_type, area)
        assert r["cmdType"] == reply and set(json.loads(r["dataArea"])) == keys, cmd_type
    area = lambda t, a: json.loads(_mqtt(fleet, token, t, a)["dataArea"])
    scan = area(335, {"wifi_ScanTime": 5})["wifi_Info"]
    assert scan[0]["wifi_SSID"] == area(337, {"opt": 0})["wifi_SSID"]
    assert area(339, {"opt": 0})["wifiConnectRouterStatus"] == 1
    assert area(341, {"opt": 1, "wifiNetSwitch": 0})["wifiNetSwitch"] == 0
    assert area(341, {"opt": 0})["wifiNetSwitch"] == 0 and area(339, {"opt": 0})["wifiConnectRouterStatus"] == 0
    area(341, {"opt": 1, "wifiNetSwitch": 1})
    reboots = []
    monkeypatch.setattr(gw, "reboot", lambda *a, **k: reboots.append(1))
    area(315, {"opt": 0, "paraType": 0, "reboot": 1})
    assert reboots == []                                          # a read never reboots
    assert area(315, {"opt": 1, "paraType": 0, "reboot": 1})["reboot"] == 1 and reboots == [1]


def test_no_token_and_nothing_unserved(fleet):
    assert _call(fleet, "GET", "terminal/v2/warrantyInfo", None)["code"] == 401
    assert _call(fleet, "GET", "/api-smart/terminal/recommendQuestion", None)["code"] == 401
    token = _token(fleet)
    assert _call(fleet, "GET", "terminal/getOffGridFlag", token, gatewayId="nope", equipNo="nope")["code"] == 400
    for method, path in [*CALLS, *SMART_CALLS]:
        _call(fleet, method, path, token)
    for cmd_type, (area, _, _) in MQTT.items():
        _mqtt(fleet, token, cmd_type, area)
    assert _monitor(fleet)["cloud"]["unserved"] == {}
