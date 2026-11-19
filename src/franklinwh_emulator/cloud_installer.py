"""The FranklinWH installer app's calls (#343, #316 step 3, batch 3): the ``hes-gateway/manage/``
paths in 1 to 13 of the 50 HTTP Toolkit captures, and ``manage/sendMqtt``.

As cloud_app.py and cloud_more.py: shapes are the captures' field names and types (aggregates
only), message text and envelope keys as captured, values the emulator's own. The emulator has one
account, so the installer sees the fleet's gateways as the owner does. Many calls are the user
app's twins and answer as those do, in the envelope the captures show for the installer app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import cloud_app, cloud_more
from .cloud_app import QUERY, _env, site_address
from .cloud_more import COUNTRIES, _country_id, _page, _province_id, _provinces, _when

if TYPE_CHECKING:
    from .gateway import Gateway

IDLE_KW = 0.05                    # below this the battery counts as standby (selectAppGatewayStatus)


def composite(api, q, body) -> dict:
    return _env(api.composite(api._gateway(q)), QUERY, total=0)


def tou_list(api, q, body) -> dict:
    return _env(api.tou_list(api._gateway(q)))


def agate_info(api, q, body) -> dict:
    """obtainAgateInfo without msaSn, as captured."""
    res = dict(cloud_app.agate_info(api, q, body)["result"])
    del res["msaSn"]
    return _env(res, QUERY, total=0)


def personal_info(api, q, body) -> dict:
    """The installer's profile: the account's login, no name or phone number kept."""
    return _env({"userName": "", "contactNumber": "", "loginName": api.account, "avatar": None})


def sim_buttons(api, q, body) -> dict:
    """The SIM page's buttons, as the user app's (this call names no gateway)."""
    return _env([{"dictLabel": label, "dictValue": "0", "isDefault": "Y"}
                 for label in ("userSimCardManagement", "installerAuthCode", "installerActivateAuthCode")])


def global_config(api, q, body) -> dict:
    return _env({"entrance": None, "tip": None, "isAppVersion1User": None})


def program_flag(api, q, body) -> dict:
    api._gateway(q)
    return _env({"flag": 0})


def communication(api, q, body) -> dict:
    """The smart-circuit and mode block, as sendMqtt 311 reads it (its key set is the captures')."""
    return _env(api.switch_block(api._gateway(q), {}), QUERY, total=0)


def _real_version(gw: "Gateway") -> str:
    return "FHP1.2" if gw.site.model.agate.code == 102 else ""         # as getDeviceInfoV2


def _status(api, gw: "Gateway") -> str:
    """One of selectAppGatewayStatus's buckets for a gateway."""
    if api.gateway_entry(gw)["status"] == 0:
        return "offline"
    rt = api.composite(gw)["runtimeData"]
    if gw.site._last.alarms:
        return "fault"
    if rt["offGridFlag"] or not gw.site._last.grid_connected:
        return "offGrid"
    p = float(rt["p_fhp"])
    return "standby" if abs(p) < IDLE_KW else ("discharging" if p > 0 else "charging")


def gateway_list(api, q, body) -> dict:
    """The installer's gateways (the account's), filtered by countryId and stateId when given,
    paged. runStatus is the composite's run_status; emsModel 0 and simCardStatus 0 (their
    meanings aren't established)."""
    items = []
    for gw in api.gateways.values():
        cid, pid = _country_id(gw), _province_id(api, gw)
        if q.get("countryId") and str(cid) != str(q["countryId"]):
            continue
        if q.get("stateId") and str(pid) != str(q["stateId"]):
            continue
        rt = api.composite(gw)["runtimeData"]
        addr = site_address(gw)
        _, fw = api.payloads(gw).dc_firmware()
        items.append({"id": gw.serial, "account": api.account, "runStatus": int(rt["run_status"]), "emsModel": 0,
                      "status": api.gateway_entry(gw)["status"], "protocolVer": fw["protocolVer"],
                      "address": addr["completeAddress"], "region": "", "simCardStatus": 0,
                      "sysHdVersion": str(gw.site.model.agate.code), "realSysHdVersion": _real_version(gw),
                      "countryId": cid, "provinceId": pid, "userName": None, "firstName": None, "lastName": None,
                      "deviceTime": _when(gw), "zoneInfo": gw.site.location.timezone, "addressSecondLast": "",
                      "city": addr["city"], "province": addr["province"], "country": addr["country"],
                      "deviceAddress": addr["completeAddress"], "newHomeFlag": None, "sleepStatus": None,
                      "benefitShowFlag": 0, "jobCode": None, "isJoinJA12": False})
    return _env(_page(q, items), QUERY, total=len(items))


def gateway_status(api, q, body) -> dict:
    """How many of the installer's gateways are in each state: offline, fault (an alarm),
    off-grid, then charging, discharging or standby by the aPower power. incomplete, shutdown,
    newHome and debugPause are 0 (the emulator has none)."""
    counts = dict.fromkeys(("incomplete", "fault", "offline", "charging", "discharging", "standby", "offGrid",
                            "shutdown", "newHome", "debugPause"), 0)
    for gw in api.gateways.values():
        counts[_status(api, gw)] += 1
    return _env({"all": len(api.gateways), **counts}, QUERY, total=0)


def countries(api, q, body) -> dict:
    """The countries the fleet's gateways are in (cloud ids 2 and 3), with the first gateway's
    city-level latitude and longitude; countryCode ISO alpha-3, as obtainGeographyList."""
    seen: dict[int, "Gateway"] = {}
    for gw in api.gateways.values():
        cid = _country_id(gw)
        if cid in COUNTRIES:
            seen.setdefault(cid, gw)
    items = [{"id": cid, "country": COUNTRIES[cid][2], "countryCode": COUNTRIES[cid][1],
              "latitude": str(gw.site.location.latitude or ""), "longitude": str(gw.site.location.longitude or ""),
              "sorted": n} for n, (cid, gw) in enumerate(sorted(seen.items()), 1)]
    return _env(items, QUERY, total=len(items))


def states(api, q, body) -> dict:
    try:
        cid = int(q.get("countryId") or 0)
    except ValueError:
        cid = 0
    items = [{"countryId": p["countryId"], "province": p["provinceEn"], "cityLat": p["latitude"],
              "cityLon": p["longitude"], "stateLat": p["latitude"], "stateLon": p["longitude"],
              "stateId": p["provinceId"]} for p in _provinces(api, cid)]
    return _env(items, QUERY, total=len(items))


def gateway_install(api, q, body) -> dict:
    """The install record: the aGate's versions; the address and step null, as every capture
    (status 1, the emulator's: installed)."""
    gw = api._gateway(q)
    _, fw = api.payloads(gw).dc_firmware()
    nulls = ("gatewayId", "currentStep", "address", "region", "addressSecondLast", "country", "province", "city",
             "deviceAddress", "alphaCode", "jobCode", "completeInstallFlag")
    return _env({**dict.fromkeys(nulls), "protocolVer": fw["protocolVer"],
                 "sysHdVersion": str(gw.site.model.agate.code), "realSysHdVersion": _real_version(gw), "status": 1},
                QUERY, total=0)


def upgrade_result(api, q, body) -> dict:
    """No upgrade running: the firmware the aGate has (Direct Connect 1102's version)."""
    gw = api._gateway(q)
    ibg, _ = api.payloads(gw).dc_firmware()
    return _env({"maint": "", "upgradeType": 0, "time": 0, "optUpgradeType": 0, "totalStepsId": None,
                 "finishStepsId": None, "currStepInfo": None, "firmwareNameList": [ibg], "currFirmwareIndex": None,
                 "onlineStatus": api.gateway_entry(gw)["status"]})


def upgrade_steps(api, q, body) -> dict:
    """No upgrade running, so no steps (their names aren't known)."""
    return _env([], QUERY, total=0)


def no_power_alarms(api, q, body) -> dict:
    """Empty, as every capture."""
    api._gateway(q)
    return _env([], QUERY, total=0)


def unread_count(api, q, body) -> dict:
    return _env(0, QUERY, total=0)


def fcm_token(api, q, body) -> dict:
    """The phone's push token: accepted and not kept."""
    return _env("", "UpdateSuccess!", total=0)


def no_picture_tip(api, q, body) -> dict:
    api._gateway(q)
    return _env(False)


def order_count(api, q, body) -> dict:
    """No service orders."""
    api._gateway(q)
    return _env(0)


def send_mqtt(api, q, body) -> dict:
    """The installer app's relay: the same commands as terminal/sendMqtt."""
    return api.send_mqtt(body)


CALLS = {
    ("GET", "manage/selectAppGatewayList"): gateway_list,
    ("GET", "manage/getDeviceUpgradeResult"): upgrade_result,
    ("GET", "manage/tipForUploadingPictures"): no_picture_tip,
    ("GET", "manage/selectAppGatewayStatus"): gateway_status,
    ("GET", "manage/getPersonalInfo"): personal_info,
    ("GET", "manage/selectSimBtn"): sim_buttons,
    ("POST", "manage/v2/loginOut"): cloud_app.logout,
    ("GET", "manage/getAppGlobalConfig"): global_config,
    ("POST", "manage/tou/getGatewayTouListV2"): tou_list,
    ("GET", "manage/getEquipmentLocationDetail"): cloud_more.location_detail,
    ("GET", "manage/getDeviceInfoV2"): cloud_app.device_info,
    ("GET", "manage/getDeviceCompositeInfo"): composite,
    ("GET", "manage/tou/getEntranceInfo"): cloud_app.entrance_info,
    ("GET", "manage/selectCountriesList"): countries,
    ("GET", "manage/selectIotGatewayInstall"): gateway_install,
    ("GET", "manage/getOffGridFlag"): cloud_more.off_grid_flag,
    ("GET", "manage/selectDeviceOverallInfo"): cloud_app.overall_info,
    ("GET", "manage/message/selectMessage"): cloud_app.no_messages,
    ("GET", "manage/selectProgramFlag"): program_flag,
    ("GET", "manage/obtainApowersInfo"): cloud_app.apowers_info,
    ("GET", "manage/selectStatesList"): states,
    ("GET", "manage/app/message/selectPushUnreadCount"): unread_count,
    ("GET", "manage/listUpgradeSteps"): upgrade_steps,
    ("POST", "manage/app/message/fcmToken/updateManage"): fcm_token,
    ("GET", "manage/obtainAgateInfo"): agate_info,
    ("GET", "manage/getCommunicationOptimization"): communication,
    ("GET", "manage/solar/selectAnyOneSolarOpen"): cloud_more.solar_open,
    ("GET", "manage/selectGatewayPowerAlarm"): no_power_alarms,
    ("GET", "manage/getHotSpotInfo/v2"): cloud_more.hotspot,
    ("GET", "manage/component/progressOrderCount"): order_count,
    ("POST", "manage/sendMqtt"): send_mqtt,
}
