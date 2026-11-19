"""#343 (#316 step 3, batch 3): the installer app's manage/ calls. Each reply has the fields the
50 HTTP Toolkit captures show (names and types merged over every capture), and its values come
from the site and the fleet."""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from franklinwh_emulator.cloud_installer import CALLS
from franklinwh_emulator.gateway import Fleet

AU = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney", "country": "AU", "state": "NSW",
      "city": "Sydney"}
US = {"latitude": 33.45, "longitude": -112.07, "timezone": "America/Phoenix", "country": "US", "state": "AZ",
      "city": "Phoenix"}

# (method, path below /hes-gateway/) -> (message, envelope extras, result type, result keys, or a
# list item's); from the captures (aggregates).
CAPTURED = {
    ("GET", "manage/app/message/selectPushUnreadCount"): ("Query success!", {"total"}, int, set()),
    ("GET", "manage/getAppGlobalConfig"): ("Operate success!", set(), dict, {"entrance", "isAppVersion1User", "tip"}),
    ("GET", "manage/getCommunicationOptimization"): ("Query success!", {"total"}, dict, {
        "BBBackupSoc", "CarSwConsSupEnable", "CarSwConsSupEnerge", "CarSwConsSupStartTime", "GridChargeEn",
        "Sw1AtuoEn", "Sw1Freq", "Sw1Mode", "Sw1MsgType", "Sw1Name", "Sw1ProLoad", "Sw1SocLowSet", "Sw1Time",
        "Sw1TimeEn", "Sw1TimeSet", "Sw2AtuoEn", "Sw2Freq", "Sw2Mode", "Sw2MsgType", "Sw2Name", "Sw2ProLoad",
        "Sw2SocLowSet", "Sw2Time", "Sw2TimeEn", "Sw2TimeSet", "Sw3AtuoEn", "Sw3Freq", "Sw3Mode", "Sw3MsgType",
        "Sw3Name", "Sw3ProLoad", "Sw3SocLowSet", "Sw3Time", "Sw3TimeEn", "Sw3TimeSet", "SwMerge", "backupMaxSoc",
        "custom", "genStartSoc", "genStat", "genStopSoc", "gridVoltCheck", "modeChoose", "opt", "order", "result",
        "runingMode", "selfMinSoc", "stopMode", "stromEn", "touMinSoc"}),
    ("GET", "manage/getDeviceCompositeInfo"): ("Query success!", {"total"}, dict, {
        "currentAlarmVOList", "currentWorkMode", "deviceStatus", "runtimeData", "solarHaveVo", "valid"}),
    ("GET", "manage/getDeviceInfoV2"): ("Operate success!", set(), dict, {
        "activeStatus", "apbox20Num", "apowerList", "blackSleepFlag", "chooseFinancierId", "countryId", "date",
        "deviceTime", "emsDeviceVer", "equipType", "fhpSn", "firstName", "fixedPowerAverage", "fixedPowerTotal",
        "gatewayId", "genEn", "gridPhaseConSet", "installerId", "lastName", "mpptEnFlag",
        "msaInstallStartDetectTime", "newHomeFlag", "offGirdFlag", "offMessage", "peHwVerList", "protocolVer",
        "provinceId", "realSysHdVersion", "serviceVoltageFlag", "sleepStatus", "solarFlag", "solarTipMsg",
        "sysHdVersion", "sysHdVersionInt", "totalCap", "v2lModeEnable", "v2lRunState", "zoneInfo"}),
    ("GET", "manage/getDeviceUpgradeResult"): ("Operate success!", set(), dict, {
        "currFirmwareIndex", "currStepInfo", "finishStepsId", "firmwareNameList", "maint", "onlineStatus",
        "optUpgradeType", "time", "totalStepsId", "upgradeType"}),
    ("GET", "manage/getEquipmentLocationDetail"): ("Query success!", {"total"}, dict, {
        "address", "addressSecondLast", "alertMessage", "alphaCode", "city", "completeAddress", "country", "dst",
        "gatewayId", "gatewayName", "latitude", "longitude", "postCode", "province", "region", "timezone",
        "zoneInfo"}),
    ("GET", "manage/getHotSpotInfo/v2"): ("Query success!", {"total"}, dict, {"gatewayId", "id", "password", "ssid"}),
    ("GET", "manage/getOffGridFlag"): ("Query success!", {"total"}, dict, {"offGridFlag"}),
    ("GET", "manage/getPersonalInfo"): ("Operate success!", set(), dict, {"avatar", "contactNumber", "loginName",
                                                                         "userName"}),
    ("GET", "manage/listUpgradeSteps"): ("Query success!", {"total"}, list, set()),   # captured items: no upgrade here
    ("GET", "manage/message/selectMessage"): ("Operate success!", set(), type(None), set()),
    ("GET", "manage/obtainAgateInfo"): ("Query success!", {"total"}, dict, {
        "appVersion", "awsVersion", "connType", "emsDeviceVer", "ibgMainVersion", "ibgVersion", "meterVersion",
        "msaModel", "protocolVer", "slVersion", "slVersionApp", "slVersionBoot", "sysHdVersion"}),
    ("GET", "manage/obtainApowersInfo"): ("Query success!", {"total"}, list, {
        "apowerSn", "blVer", "bmsVer", "dcdcVer", "fpgaVer", "invVer", "mpptAppVer", "peHwVer", "ratedCapacity",
        "ratedPower", "remainingPower", "soc", "status", "thVer"}),
    ("GET", "manage/selectAppGatewayList"): ("Query success!", {"total"}, list, {
        "account", "address", "addressSecondLast", "benefitShowFlag", "city", "country", "countryId",
        "deviceAddress", "deviceTime", "emsModel", "firstName", "id", "isJoinJA12", "jobCode", "lastName",
        "newHomeFlag", "protocolVer", "province", "provinceId", "realSysHdVersion", "region", "runStatus",
        "simCardStatus", "sleepStatus", "status", "sysHdVersion", "userName", "zoneInfo"}),
    ("GET", "manage/selectAppGatewayStatus"): ("Query success!", {"total"}, dict, {
        "all", "charging", "debugPause", "discharging", "fault", "incomplete", "newHome", "offGrid", "offline",
        "shutdown", "standby"}),
    ("GET", "manage/selectCountriesList"): ("Query success!", {"total"}, list, {
        "country", "countryCode", "id", "latitude", "longitude", "sorted"}),
    ("GET", "manage/selectDeviceOverallInfo"): ("Query success!", {"total"}, dict, {"apowerCount", "totalPower"}),
    ("GET", "manage/selectGatewayPowerAlarm"): ("Query success!", {"total"}, list, set()),
    ("GET", "manage/selectIotGatewayInstall"): ("Query success!", {"total"}, dict, {
        "address", "addressSecondLast", "alphaCode", "city", "completeInstallFlag", "country", "currentStep",
        "deviceAddress", "gatewayId", "jobCode", "protocolVer", "province", "realSysHdVersion", "region", "status",
        "sysHdVersion"}),
    ("GET", "manage/selectProgramFlag"): ("Operate success!", set(), dict, {"flag"}),
    ("GET", "manage/selectSimBtn"): ("Operate success!", set(), list, {"dictLabel", "dictValue", "isDefault"}),
    ("GET", "manage/selectStatesList"): ("Query success!", {"total"}, list, {
        "cityLat", "cityLon", "countryId", "province", "stateId", "stateLat", "stateLon"}),
    ("GET", "manage/solar/selectAnyOneSolarOpen"): ("Operate success!", set(), dict, {
        "dspNemPvEn", "gridSplitCtEn", "installApboxSolar", "installProximalsolar", "installPv1Port",
        "installPv2Port", "isThreePhaseInstall", "mpanPv1En", "mpanPv2En", "mpptEnFlag", "mpptExportEn",
        "offGridFlag", "offGridReason", "pvSplitCtEn", "remoteSolarEn", "remoteSolarMode", "sysHdVersion"}),
    ("GET", "manage/tipForUploadingPictures"): ("Operate success!", set(), bool, set()),
    ("GET", "manage/tou/getEntranceInfo"): ("Operate success!", set(), dict, {
        "ahubAddressingFlag", "backupSolution", "bbDischargePower", "bbEntrance", "bonusEnable",
        "chargingPowerLimited", "globalGridChargeMax", "globalGridDischargeMax", "gridFeedMax", "gridFeedMaxFlag",
        "gridFlag", "gridMax", "gridMaxFlag", "ja12Entrance", "needCtTest", "pcsEntrance", "peakDemandGridMax",
        "sdcpFlag", "sgipEntrance", "slSettingFlag", "solarFlag", "tariffSettingFlag"}),
    ("GET", "manage/component/progressOrderCount"): ("Operate success!", set(), int, set()),
    ("POST", "manage/app/message/fcmToken/updateManage"): ("UpdateSuccess!", {"total"}, str, set()),
    ("POST", "manage/tou/getGatewayTouListV2"): ("Operate success!", set(), dict, {
        "backupForeverFlag", "currendId", "gridChargeEn", "itcFlag", "list", "nextWorkMode", "sgipFlag", "stopMode",
        "stromEn", "tariffSettingFlag", "timerEndTime", "timerEndTimeZero", "timerStartTime", "timerStartTimeZero",
        "todayVppVo", "touAlertMessage", "touSendStatus", "vppSocVo", "zoneInfo"}),
    ("POST", "manage/v2/loginOut"): ("Operate success!", set(), type(None), set()),
}


@pytest.fixture(scope="module")
def fleet():
    f = Fleet.from_dict({"gateways": [
        {"id": "home", "port": 0, "dc_port": 0, "seed": 3, "location": AU, "weather": {"model": "bell"},
         "setup": "in-service", "apower_units": 2},
        {"id": "us", "port": 0, "dc_port": 0, "seed": 4, "location": US, "ac_type": 1, "weather": {"model": "bell"}},
        {"id": "us2", "port": 0, "dc_port": 0, "seed": 5, "location": {**US, "state": "TX", "city": "Houston"},
         "ac_type": 1, "weather": {"model": "bell"}}]})
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
    q = {"gatewayId": sn, "lang": "en_US", **q}
    data = json.dumps(body or {"gatewayId": sn}).encode() if method == "POST" else None
    req = urllib.request.Request(f"http://127.0.0.1:{f.cloud.port}/hes-gateway/{path}?{urllib.parse.urlencode(q)}",
                                 data=data, method=method, headers={"loginToken": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            code, d = r.status, json.load(r)
    except urllib.error.HTTPError as e:
        code, d = e.code, json.load(e)
    assert code == status, (path, code, d)
    return d


def _monitor(f) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{f.control.port}/monitor", timeout=5) as r:
        return json.load(r)


def test_every_installer_call_has_the_captured_fields(fleet):
    assert set(CALLS) == set(CAPTURED) | {("POST", "manage/sendMqtt")}
    token = _token(fleet)
    for (method, path), (message, extras, kind, keys) in CAPTURED.items():
        d = _call(fleet, method, path, token, countryId=3 if path == "manage/selectStatesList" else "")
        assert set(d) == {"code", "message", "result", "success"} | extras, path
        assert (d["code"], d["message"], d["success"]) == (200, message, True), path
        assert isinstance(d["result"], kind), (path, d["result"])
        if keys:
            item = d["result"][0] if kind is list else d["result"]
            assert set(item) == keys, (path, sorted(set(item) ^ keys))


def test_the_twins_answer_as_the_user_app(fleet):
    token = _token(fleet)
    for path in ("getDeviceInfoV2", "tou/getEntranceInfo", "getEquipmentLocationDetail", "getOffGridFlag",
                 "selectDeviceOverallInfo", "getHotSpotInfo/v2", "solar/selectAnyOneSolarOpen"):
        installer = _call(fleet, "GET", f"manage/{path}", token)["result"]
        user = _call(fleet, "GET", f"terminal/{path}", token)["result"]
        for k in ("date", "deviceTime"):
            installer.pop(k, None), user.pop(k, None)
        assert installer == user, path
    agate = _call(fleet, "GET", "manage/obtainAgateInfo", token)["result"]
    assert agate == {k: v for k, v in _call(fleet, "GET", "terminal/obtainAgateInfo", token)["result"].items()
                     if k != "msaSn"}
    sn = fleet["home"].serial
    body = {"cmdType": 327, "equipNo": sn, "type": 0, "timeStamp": 0, "snno": 1, "len": 0, "crc": "",
            "dataArea": json.dumps({"opt": 0})}
    led = _call(fleet, "POST", "manage/sendMqtt", token, body=body)["result"]
    assert led["cmdType"] == 328 and json.loads(led["dataArea"])["lightStat"] == [2, 2]


def test_values_come_from_the_fleet(fleet):
    token = _token(fleet)
    gws = _call(fleet, "GET", "manage/selectAppGatewayList", token, countryId="")
    assert gws["total"] == 3 and [g["id"] for g in gws["result"]] == [fleet[n].serial for n in ("home", "us", "us2")]
    us = _call(fleet, "GET", "manage/selectAppGatewayList", token, countryId=2)["result"]
    assert [(g["city"], g["province"], g["countryId"]) for g in us] == [("Phoenix", "AZ", 2), ("Houston", "TX", 2)]
    tx = _call(fleet, "GET", "manage/selectAppGatewayList", token, countryId=2, stateId=2)["result"]
    assert [g["city"] for g in tx] == ["Houston"]
    assert len(_call(fleet, "GET", "manage/selectAppGatewayList", token, countryId="", pageSize=2, pageNum=2)
               ["result"]) == 1
    status = _call(fleet, "GET", "manage/selectAppGatewayStatus", token)["result"]
    assert status["all"] == 3 == sum(v for k, v in status.items() if k != "all")
    countries = _call(fleet, "GET", "manage/selectCountriesList", token)["result"]
    assert [(c["id"], c["country"]) for c in countries] == [(2, "United States"), (3, "Australia")]
    states = _call(fleet, "GET", "manage/selectStatesList", token, countryId=2)["result"]
    assert [(s["stateId"], s["province"]) for s in states] == [(1, "AZ"), (2, "TX")]
    me = _call(fleet, "GET", "manage/getPersonalInfo", token)["result"]
    assert me["loginName"] == "owner@example.com" and me["userName"] == ""
    upgrade = _call(fleet, "GET", "manage/getDeviceUpgradeResult", token)["result"]
    assert upgrade["firmwareNameList"] == [fleet["home"].profile.dc_firmware] and upgrade["onlineStatus"] == 1
    install = _call(fleet, "GET", "manage/selectIotGatewayInstall", token)["result"]
    assert (install["sysHdVersion"], install["realSysHdVersion"], install["gatewayId"]) == ("102", "FHP1.2", None)
    comm = _call(fleet, "GET", "manage/getCommunicationOptimization", token)["result"]
    assert comm["order"] == fleet["home"].serial and comm["opt"] == 0


def test_status_follows_the_site(fleet):
    gw, token = fleet["us"], _token(fleet)
    gw.site.force_alarm("OVER_TEMP")
    try:
        assert _call(fleet, "GET", "manage/selectAppGatewayStatus", token)["result"]["fault"] == 1
    finally:
        gw.site.clear_alarm()
    assert _call(fleet, "GET", "manage/selectAppGatewayStatus", token)["result"]["fault"] == 0


def test_no_token_and_nothing_unserved(fleet):
    assert _call(fleet, "GET", "manage/selectAppGatewayList", None)["code"] == 401
    token = _token(fleet)
    assert _call(fleet, "GET", "manage/getDeviceInfoV2", token, gatewayId="nope")["code"] == 400
    for method, path in CAPTURED:
        _call(fleet, method, path, token)
    assert _monitor(fleet)["cloud"]["unserved"] == {}
