"""The official FranklinWH app's less frequent calls (#341, #316 step 3, batch 3): the user app's
remaining ``terminal/`` and ``common/`` paths (1 to 18 of the 50 HTTP Toolkit captures), the
assistant (``/api-smart/``) and sendMqtt 315, 335, 339 and 341.

As cloud_app.py: shapes are the field names and types merged over every capture (aggregates
only; no values were taken), the message text and envelope keys as captured, and the values the
emulator's own: from the site or the fleet file where it has them, ``None`` where the captures
show null and the meaning isn't established, and "none" for what the emulator doesn't keep
(push messages, utilities and tariffs, an assistant). The emulator's own choices where a capture
shows a value but not its meaning are named on each handler.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import secrets
from typing import TYPE_CHECKING

from . import cloud_app, cloud_energy
from .cloud_app import OPERATE, QUERY, _block, _env, _net, backup_records, site_address
from .gridcodes import GRID_CODES

if TYPE_CHECKING:
    from .cloud import CloudApi
    from .gateway import Gateway

# Country ids the cloud uses (cloud.COUNTRY_ID: 2 the reference US captures, 3 the AU site's).
COUNTRIES = {2: ("US", "USA", "United States"), 3: ("AU", "AUS", "Australia")}
# Event classes for the app's notification settings: the emulator's event kinds (events.py),
# grouped; names and grouping are the emulator's own.
EVENT_CLASSES = {1: ("Grid", ("grid", "breaker", "export")),
                 2: ("Battery", ("battery", "soc", "reserve", "derate")),
                 3: ("Alarms", ("alarm",)),
                 4: ("Mode", ("mode", "force")),
                 5: ("System", ("reboot", "firmware", "commission", "debug", "modbus", "cloud", "client",
                                "weather", "episode"))}
# getCompliance's gridConnFuntionPara keys, as captured (3 captures, AU firmware).
COMPLIANCE_FLOAT = ("PF_Hyst_AS", "ES_F_LOW_AS", "PV_OLRT_AS", "PF_KUF_AS", "OF1_TRIP_T_AS", "QV_OLRT_AS",
                    "ES_F_HIGH_AS", "OF2_TRIP_T_AS", "CONST_Q_OLRT_AS", "PF_DBOF_AS", "PF_OLRT_AS",
                    "OV2_TRIP_T_AS", "CONST_PF_OLRT_AS")
COMPLIANCE_STR = ("UI_CAPABILITY_ER", "SURGE_LOC_CAT_EXPOSE_LEVEL")
COMPLIANCE_INT = (
    "QV_CURVE_V4_AS", "IEEE_1815_EVAL", "SUNSPEC_MODBUS_TCP_EVAL", "QV_CURVE_Q2_AS", "PV_MODE_ENABLE_AS",
    "OV1_TRIP_V_AS", "ES_V_HIGH_AS", "PV_CURVE_V2_AS", "QP_CURVE_P1_LOAD_AS", "PV_CURVE_P2_LOAD_AS",
    "EMC_IMMUN_LEVEL", "CONST_PF_AS", "PV_CURVE_P1_AS", "CONST_Q_AS", "PV_CURVE_V1_In_AS",
    "QP_CURVE_Q2_LOAD_AS", "CONST_Q_MODE_ENABLE_AS", "QP_MODE_ENABLE_AS", "UV1_TRIP_V_AS", "ES_RAMP_RATE_AS",
    "ComplianceRuleType", "QV_CURVE_V1_AS", "UF1_TRIP_T_AS", "QV_CURVE_Q3_AS", "PV_CURVE_V1_AS",
    "OF2_TRIP_F_AS", "QP_CURVE_Q2_GEN_AS", "OV2_TRIP_V_AS", "UV3_TRIP_T_AS", "PF_MODE_ENABLE_AS",
    "QP_CURVE_P1_GEN_AS", "QP_CURVE_P3_GEN_AS", "ES_V_LOW_AS", "UV2_TRIP_V_AS", "UF2_TRIP_F_AS",
    "PV_CURVE_P2_In_AS", "SUNSPEC_MODBUS_SERIAL_EVAL", "PV_CURVE_V2_In_AS", "QP_CURVE_P3_LOAD_AS",
    "PV_CURVE_P2_GEN_AS", "QV_CURVE_Q4_AS", "IEEE_2030_5_EVAL", "QV_CURVE_V2_AS", "QP_CURVE_P2_LOAD_AS",
    "PF_KOF_AS", "QV_VREF_AUTO_MODE_AS", "OF1_TRIP_F_AS", "QV_VREF_AS", "OV1_TRIP_T_AS", "UI_MODE_ENABLE_AS",
    "gridPowerLimit", "UF1_TRIP_F_AS", "CONST_PF_MODE_ENABLE_AS", "QP_CURVE_Q3_LOAD_AS", "PF_DBUF_AS",
    "OV_TRIP_U_AS", "ES_DELAY_AS", "gridFastEnable", "QP_CURVE_Q1_LOAD_AS", "UV1_TRIP_T_AS",
    "QV_CURVE_V3_AS", "NORMAL_RAMP_AS", "QP_OLRT_AS", "QV_CURVE_Q1_AS", "QP_CURVE_Q3_GEN_AS",
    "PV_CURVE_P1_In_AS", "UV3_TRIP_V_AS", "ES_PERMIT_SERVICE_AS", "QP_CURVE_P2_GEN_AS", "QV_MODE_ENABLE_AS",
    "QP_CURVE_Q1_GEN_AS", "UV2_TRIP_T_AS", "UF2_TRIP_T_AS", "QV_VREF_OLRT_AS")
# selectIotUserRuntimeDataLog's dataArea keys, as captured; the composite's runtimeData has most.
RUNTIME_LOG_KEYS = (
    "bms_work", "threePhasePvPower", "solarSupply", "genChBat", "remoteSolar1Power", "di", "soc", "gridChBat",
    "do", "remoteSolarEn", "p_uti", "sinHTemp", "mode", "batOutGrid", "electricity_type", "signal",
    "soOutGrid", "main_sw", "slaver_stat", "fhpSn", "p_sun", "report_type", "remoteSolar2Power",
    "kwh_fhp_chg", "pvRelay2", "kwh_sun", "name", "kwhGridLoad", "kwh_gen", "gridPhaseConSet", "kwh_uti_out",
    "kwh_load", "soChBat", "fhpSoc", "p_fhp", "sinLTemp", "fhpPower", "solarPower", "infi_status", "connType",
    "p_gen", "kwhGenLoad", "gridRelay2", "p_load", "pro_load", "genVoltage", "pe_stat", "kwh_uti_in",
    "run_status", "blackStartRelay", "kwhSolarLoad", "t_amb", "wifiSignal", "kwhFhpLoad", "p_use",
    "kwh_fhp_di", "genStat", "elecnet_state", "offgridreason")
NO_TARIFFS = {"dataList": {"records": [], "total": 0}, "postCodeHave": None}
ASSISTANT_OK = "Request Success"


def _when(gw: "Gateway", ts: float | None = None) -> str:
    site = gw.site
    return site.local_datetime(site.clock.now() if ts is None else ts).strftime("%Y-%m-%d %H:%M:%S")


def _tz(gw: "Gateway") -> tuple[int, float]:
    """(dst, the standard UTC offset in hours) now."""
    site = gw.site
    local = site.local_datetime(site.clock.now())
    dst = local.dst()
    on = bool(dst and dst.total_seconds())
    return int(on), round(site.utc_offset_hours(site.clock.now()) - (dst.total_seconds() / 3600 if on else 0), 2)


def _page(q: dict, items: list) -> list:
    try:
        num, size = max(1, int(q.get("pageNum") or 1)), max(1, int(q.get("pageSize") or 10))
    except ValueError:
        num, size = 1, 10
    return items[(num - 1) * size:num * size]


def _listed(items: list, message: str = QUERY) -> dict:
    return _env(items, message, total=len(items))


def _country_id(gw: "Gateway") -> int | None:
    from .cloud import COUNTRY_ID
    return COUNTRY_ID.get(gw.site.model.region)


def _provinces(api: "CloudApi", country_id: int | None) -> list[dict]:
    """The states the fleet's gateways are in for that country id, numbered 1.. by name, with
    the first gateway's (city-level) latitude and longitude."""
    seen: dict[str, "Gateway"] = {}
    for gw in api.gateways.values():
        st = gw.site.location.state
        if st and _country_id(gw) == country_id:
            seen.setdefault(st, gw)
    return [{"provinceId": n, "provinceEn": st, "provinceZh": st, "provinceCode": st, "provinceAlias": st,
             "countryId": country_id, "latitude": str(gw.site.location.latitude or ""),
             "longitude": str(gw.site.location.longitude or ""), "region": None}
            for n, (st, gw) in enumerate(sorted(seen.items()), 1)]


def _province_id(api, gw) -> int:
    st = gw.site.location.state
    return next((p["provinceId"] for p in _provinces(api, _country_id(gw)) if p["provinceEn"] == st), 0)


# -- the account's sites and devices ------------------------------------------------------------
def _sites(api) -> list["Gateway"]:
    """One site per gateway, numbered 1.. in fleet order (siteId)."""
    return list(api.gateways.values())


def site_and_device_info(api, q, body) -> dict:
    sites = []
    for n, gw in enumerate(_sites(api), 1):
        addr = site_address(gw)["completeAddress"]
        sites.append({"siteId": n, "siteName": gw.config.id, "completeAddress": addr,
                      "basicDeviceInfoVOList": [{
                          "gatewayName": gw.config.id, "gatewayId": gw.serial, "completeAddress": addr,
                          "status": api.gateway_entry(gw)["status"], "sysHdVersion": gw.site.model.agate.code,
                          "agateNum": 1, "apowerNum": len(gw.site.unit_models)}]})
    return _env(_page(q, sites), QUERY, total=len(sites))


def app_user_devices(api, q, body) -> dict:
    """The account's gateways; activeTime and createTime the commissioned date."""
    items = []
    for gw in api.gateways.values():
        entry, (dst, tz) = api.gateway_entry(gw), _tz(gw)
        ibg, fw = api.payloads(gw).dc_firmware()
        day = f"{gw.config.commissioned or '2024-01-01'} 00:00:00"
        items.append({"id": gw.serial, "account": api.account, "status": entry["status"], "name": gw.config.id,
                      "protocolVer": fw["protocolVer"], "version": ibg,
                      "address": site_address(gw)["completeAddress"], "activeStatus": 1, "activeTime": day,
                      "installTime": None, "createTime": day, "deviceTime": _when(gw), "dst": dst,
                      "timeZone": tz, "zoneInfo": gw.site.location.timezone,
                      "sysHdVersion": str(gw.site.model.agate.code)})
    return _listed(items)


def site_detail(api, q, body) -> dict:
    from .cloud import CloudError
    sites = _sites(api)
    try:
        gw = sites[int(q.get("siteId")) - 1]
    except (TypeError, ValueError, IndexError):
        raise CloudError(400, f"unknown site {q.get('siteId')!r}")
    return _env({"siteName": gw.config.id, "address1": "", "address2": None, **site_address(gw)})


def overlap_site(api, q, body) -> dict:
    """Code 4091, message null, success false, as every capture (its meaning isn't established)."""
    return {"code": 4091, "message": None, "result": {"alertMessage": None, "alertTitle": None}, "success": False}


def site_update(api, q, body) -> dict:
    """Code 4094 "Successful.", success false, as every capture; nothing is changed (whether the
    cloud applied it isn't established)."""
    return {"code": 4094, "message": "Successful.", "result": None, "success": False}


def location_detail(api, q, body) -> dict:
    """The fleet entry's location (city-level latitude and longitude); timezone the standard
    UTC offset in hours, dst whether daylight saving is on now."""
    gw = api._gateway(q)
    loc = gw.site.location
    dst, tz = _tz(gw)
    addr = site_address(gw)
    return _env({"gatewayId": gw.serial, "gatewayName": gw.config.id, "address": None, "dst": dst,
                 "latitude": "" if loc.latitude is None else str(loc.latitude),
                 "longitude": "" if loc.longitude is None else str(loc.longitude), "postCode": addr["postCode"],
                 "timezone": tz, "zoneInfo": loc.timezone, "country": addr["country"],
                 "province": addr["province"], "city": addr["city"], "region": None, "addressSecondLast": "",
                 "completeAddress": addr["completeAddress"], "alphaCode": addr["alphaCode"],
                 "alertMessage": None}, QUERY, total=0)


def terminal_user_info(api, q, body) -> dict:
    return _env({"account": api.account, "receiveLetterFlag": 0})


def user_resources_v1(api, q, body) -> dict:
    return {"code": 200, "msg": "请求成功", "data": {"resources": None, "roleTypes": None}, "success": True}


# -- geography and compliance -----------------------------------------------------------------
def geography(api, q, body) -> dict:
    """Without countryId: the countries the cloud's ids are known for; with it: that country's
    states among the fleet's gateways. countryCode is ISO alpha-3 (emulator's choice); nameZh
    repeats the English name."""
    cid = q.get("countryId")
    if not cid:
        countries = [{"id": i, "nameEn": name, "nameZh": name, "latitude": None, "longitude": None,
                      "countryCode": a3, "provinceList": None, "sorted": None, "alphaCode": a2}
                     for i, (a2, a3, name) in COUNTRIES.items()]
        return _env({"countryList": countries, "provinceList": None, "cityList": None}, QUERY, total=0)
    try:
        cid = int(cid)
    except ValueError:
        cid = None
    provinces = [{"id": p["provinceId"], "nameEn": p["provinceEn"], "nameZh": p["provinceZh"],
                  "latitude": None, "longitude": None, "cityList": None} for p in _provinces(api, cid)]
    return _env({"countryList": None, "provinceList": provinces, "cityList": None}, QUERY, total=0)


def provinces_by_gateway(api, q, body) -> dict:
    gw = api._gateway(q)
    cid = _country_id(gw)
    name = COUNTRIES.get(cid, ("", "", ""))[2]
    st, loc = gw.site.location.state or "", gw.site.location
    return _env({"iotGatewayRegionVo": {"countryZh": name, "countryEn": name, "provinceZh": st, "provinceEn": st,
                                        "countryId": cid, "provinceId": _province_id(api, gw),
                                        "latitude": "" if loc.latitude is None else str(loc.latitude),
                                        "longitude": "" if loc.longitude is None else str(loc.longitude)},
                 "worldProvincesList": _provinces(api, cid)})


def _codes_for(gw: "Gateway") -> list[tuple[int, str]]:
    """The emulator's grid codes at the site's frequency, numbered 1.. by name (systemId)."""
    hz = gw.site.grid.nominal_hz
    return [(n, name) for n, name in enumerate(sorted(GRID_CODES), 1) if GRID_CODES[name].nominal_hz == hz]


def compliance_names(api, q, body) -> dict:
    gw = api._gateway(q)
    codes = _codes_for(gw)
    current = next((n for n, name in codes if name == gw.site.grid.name), 0)
    return _env({"list": [{"name": name, "id": n} for n, name in codes], "currentId": current,
                 "countryId": _country_id(gw)})


def _item(rank: int, key: str, value) -> dict:
    return {"name": key, "key": key, "type": 0, "valueType": "string" if isinstance(value, str) else "number",
            "defaultValue": str(value), "minValue": None, "maxValue": None, "accuracy": None, "rateUnit": None,
            "unit": None, "value": value, "description": "", "rank": rank, "needSend": 0, "values": None,
            "hide": 0, "enableCondition": None, "validCondition": None, "sendRule": None, "showUnit": 0}


def compliance_detail(api, q, body) -> dict:
    """The grid code's settings (gridcodes.py, the emulator's), read-only: its numbers and its
    trip stages (threshold, seconds) as one catalog section."""
    from .cloud import CloudError
    gw = api._gateway(q)
    try:
        sid = int(q.get("systemId"))
        name = dict(_codes_for(gw))[sid]
    except (TypeError, ValueError, KeyError):
        raise CloudError(400, f"unknown systemId {q.get('systemId')!r}")
    code = GRID_CODES[name]
    content = [_item(0, "description", code.description)]
    for f in dataclasses.fields(code):
        v = getattr(code, f.name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            content.append(_item(len(content), f.name, v))
    for t in code.trips:
        content.append(_item(len(content), f"{t.name} {t.kind}", round(t.threshold, 4)))
        content.append(_item(len(content), f"{t.name} delay_s", t.delay_s))
    addr = site_address(gw)
    return _env({"complianceRuleType": sid, "complianceRuleTypeName": name, "countryId": _country_id(gw),
                 "country": addr["country"], "provinceId": None, "province": addr["province"] or None,
                 "systemId": sid, "fromTemplateId": None, "templateFromType": None,
                 "catalog": [{"headLine": code.description, "rank": 1,
                              "parameters": [{"sectionTitle": None, "rank": 1, "content": content}]}],
                 "alertMessage": None})


def compliance_params(api, q, body) -> dict:
    """The grid-connection parameter block, every captured key; the values are placeholders (0,
    0.0, "") apart from ComplianceRuleType, the site's grid code's systemId: what each key's
    number means isn't established."""
    gw = api._gateway(q)
    current = next((n for n, name in _codes_for(gw) if name == gw.site.grid.name), 0)
    para = {**{k: 0 for k in COMPLIANCE_INT}, **{k: 0.0 for k in COMPLIANCE_FLOAT},
            **{k: "" for k in COMPLIANCE_STR}, "ComplianceRuleType": current}
    try:
        para_type = int(q.get("paraType") or 0)
    except ValueError:
        para_type = 0
    return _env({"num": len(para), "optType": 0, "paraType": para_type, "gridConnFuntionPara": para,
                 "countryId": _country_id(gw), "provinceId": _province_id(api, gw)})


# -- device and settings ----------------------------------------------------------------------
def device_info_v1(api, q, body) -> dict:
    """getDeviceInfo, the older call: getDeviceInfoV2's fields less five, plus four as captured
    (names empty strings: the emulator keeps no names)."""
    res = dict(cloud_app.device_info(api, q, body)["result"])
    for k in ("serviceVoltageFlag", "zoneInfo", "blackSleepFlag", "equipType", "msaInstallStartDetectTime"):
        del res[k]
    res.update(chooseFinancierId=0, firstName="", lastName="", offGridReason=None, mpanGridEnb=None,
               mpanGridCurLim=None, msaModel=0)
    return _env(res)


def system_setting(api, q, body) -> dict:
    """The install profile as Direct Connect 1702 has it, with the off-grid flag and the
    aPower count; a firmware without the grid limits reads -1 for them."""
    gw = api._gateway(q)
    pay = api.payloads(gw)
    prof = {**api._strip(pay.install_profile({})), **pay.install_limits}
    flag = api.composite(gw)["runtimeData"]["offGridFlag"]
    return _env({"gatewayId": gw.serial, "electricSupply": prof["electricSupply"],
                 "ratedGridVolt": prof["ratedGridVolt"], "ratedGridHz": prof["ratedGridHz"], "offGridFlag": flag,
                 "gridExportEnable": prof["gridExportEnable"], "gridSoftLimit": prof.get("gridSoftLimit", -1),
                 "gridHardLimit": prof.get("gridHardLimit", -1), "apowerNumber": len(gw.site.unit_models),
                 "countryId": _country_id(gw), "isThreePhaseInstall": prof["isThreePhaseInstall"],
                 "threePhPvEnb": pay.solar_pv({})["threePhPvEnb"], "pcsSetFlag": False, "rsdEnable": 0},
                QUERY, total=0)


def solar_open(api, q, body) -> dict:
    """Which PV inputs are fitted: Direct Connect's solar block (remote PV through the aPbox,
    PV1 on the aGate) and the aPower S MPPT."""
    gw = api._gateway(q)
    pv = api.payloads(gw).solar_pv({})
    comp = api.composite(gw)
    return _env({"remoteSolarEn": pv["remoteSolarEn"], "remoteSolarMode": pv["remoteSolarMode"], "dspNemPvEn": 0,
                 "pvSplitCtEn": 0, "gridSplitCtEn": 0, "sysHdVersion": None,
                 "installProximalsolar": pv["installProximalsolar"],
                 "isThreePhaseInstall": int(gw.site.config.ac_type == 2), "installPv1Port": pv["installPV1port"],
                 "installPv2Port": pv["installPV2port"], "offGridFlag": comp["runtimeData"]["offGridFlag"],
                 "offGridReason": comp["solarHaveVo"]["offGridReason"],
                 "mpptEnFlag": any(u.mppt_count for u in gw.site.unit_models), "mpanPv1En": None,
                 "mpanPv2En": None, "mpptExportEn": None, "installApboxSolar": pv["remoteSolarEn"]})


def off_grid_flag(api, q, body) -> dict:
    return _env({"offGridFlag": api.composite(api._gateway(q))["runtimeData"]["offGridFlag"]}, QUERY, total=0)


def real_time_soc(api, q, body) -> dict:
    return _env({"batterySoc": float(api.composite(api._gateway(q))["runtimeData"]["soc"])}, QUERY, total=0)


def runtime_log(api, q, body) -> dict:
    """The latest runtime record: the composite's runtimeData, with Direct Connect 1412's
    readings for the keys it lacks (relays, temperatures, generator voltage) and 0 for the rest."""
    gw = api._gateway(q)
    rt = api.composite(gw)["runtimeData"]
    run = api.power_info(gw)
    extra = {"do": rt.get("doStatus", []), "sinHTemp": run.get("sinHTemp", 0), "sinLTemp": run.get("sinLTemp", 0),
             "genVoltage": run.get("genVoltage", 0), "pvRelay2": run.get("pvRelay2", 0),
             "gridRelay2": run.get("gridRelay2", 0), "blackStartRelay": run.get("blackStartRelay", 0)}
    area = {k: rt[k] if k in rt else extra.get(k, 0) for k in RUNTIME_LOG_KEYS}
    now = _when(gw)
    return _env({"id": f"{gw.serial}-runtime", "gatewayId": gw.serial, "dataArea": area, "createTime": now,
                 "updateTime": now}, QUERY, total=0)


def warranty(api, q, body) -> dict:
    """The site's component warranties (components.py, #115): the aGate's and each aPower's
    expiry, and the aPower throughput (MWh, all units) with what is left of it. No installer
    or supplier contact is kept, so those are empty."""
    gw = api._gateway(q)
    site = gw.site
    today = site.local_date(site.clock.now())
    comps = site.components

    def expires(name: str) -> str:
        start = str(comps.installed(name) or gw.config.commissioned or today)
        d = dt.date.fromisoformat(start)
        years = int(comps.parts[name].warranty_years)
        try:
            return d.replace(year=d.year + years).isoformat()
        except ValueError:                                    # 29 February
            return d.replace(year=d.year + years, day=28).isoformat()

    units = site.unit_models
    total = float(comps.parts["apower"].throughput_mwh) * len(units)
    pay = api.payloads(gw)
    devices = [{"sn": gw.serial, "model": site.model.agate.name, "type": 1, "expirationTime": expires("agate"),
                "subModuleExpirationTime": None}]
    devices += [{"sn": pay.fhp_sn(i + 1), "model": u.name, "type": 2, "expirationTime": expires("apower"),
                 "subModuleExpirationTime": None} for i, u in enumerate(units)]
    return _env({"expirationTime": expires("agate"), "installerCompany": "", "installerCompanyPhone": None,
                 "equipmentSupplierPhone": "", "warrantyLink": "", "throughput": int(total),
                 "remainThroughput": round(max(0.0, total - site.batt_discharge_wh / 1e6), 2),
                 "deviceExpirationList": devices, "installerCompanyEmail": None})


def hotspot(api, q, body) -> dict:
    """The aGate's own access point, as sendMqtt 337 keeps it."""
    gw = api._gateway(q)
    wifi = _wifi(api, gw)
    return _env({"id": 1, "gatewayId": gw.serial, "ssid": wifi["ap_SSID"], "password": wifi["ap_Pw"]},
                QUERY, total=0)


def hotspot_name(api, q, body) -> dict:
    """The older call: the access point's name (emulator's reading of the captured string)."""
    return _env(_wifi(api, api._gateway(q))["ap_SSID"], QUERY, total=0)


def _wifi(api, gw) -> dict:
    return cloud_app.wifi_settings(api, gw, {"opt": 0})


def span_setting(api, q, body) -> dict:
    """No SPAN panel."""
    api._gateway(q)
    return _env({"spanFlag": 0})


def warn_flag(api, q, body) -> dict:
    return _env({"warnFlag": 0}, QUERY, total=0)


def url_by_key(api, q, body) -> dict:
    """The emulator serves no web pages: the key, with an empty URL."""
    return _env({"keyVal": q.get("key", ""), "url": "", "description": ""})


def none_query(api, q, body) -> dict:
    return _env(None, QUERY, total=0)


def vpp_tip(api, q, body) -> dict:
    api._gateway(q)
    return _env(None, "")


# -- energy and history -----------------------------------------------------------------------
def _slots(gw: "Gateway", date: str):
    """(time of day, the quarter-hour's flows, SoC) for each recorded slot of the day."""
    site = gw.site
    for i, slot in enumerate(site.history.get(date, [])):
        s = slot.get("s") or 0.0
        if s:
            yield f"{i // 4:02d}:{i % 4 * 15:02d}", cloud_energy.split(slot), \
                (slot["soc"] / s if "soc" in slot else site.soc), {k: slot.get(k, 0.0) / s for k in ("uti", "fhp")}


def _day(q: dict, gw: "Gateway", key: str) -> str:
    gw.site.step()
    return q.get(key) or gw.site.local_date(gw.site.clock.now())


def working_hours(api, q, body) -> dict:
    """The day's power curve (kW, a point per recorded quarter-hour) and, per point, which flows
    are on; the colours are 0 (their meaning isn't established)."""
    gw = api._gateway(q)
    day = _day(q, gw, "day")
    rec, sun, gen, uti, fhp, load, flags = [], [], [], [], [], [], []
    for t, f, _, avg in _slots(gw, day):
        point = {"powerSun": round(f["sun"] / 1000, 3), "powerGen": round(f["gen"] / 1000, 3),
                 "powerUti": round(avg["uti"] / 1000, 3), "powerFhp": round(avg["fhp"] / 1000, 3),
                 "powerLoad": round(f["load"] / 1000, 3)}
        rec.append(t)
        for lst, k in ((sun, "powerSun"), (gen, "powerGen"), (uti, "powerUti"), (fhp, "powerFhp"),
                       (load, "powerLoad")):
            lst.append(point[k])
        flags.append({"recTime": t, **{k: abs(v) > 0.05 for k, v in point.items()},
                      **{k + "Color": 0 for k in point}})
    return _env({"id": None, "gatewayId": gw.serial, "day": day, "recTime": rec, "powerSun": sun, "powerGen": gen,
                 "powerUti": uti, "powerFhp": fhp, "powerLoad": load, "currentStateDuration": None,
                 "currentTime": None, "spontaneous": None, "income": None, "mapList": flags, "open": True},
                QUERY, total=0)


def day_power(api, q, body) -> dict:
    """The day's power points as strings (kW; SoC %; kWh stored); mode is the site's mode now
    (the history keeps none)."""
    gw = api._gateway(q)
    day = _day(q, gw, "date")
    site = gw.site
    d = dt.date.fromisoformat(day)
    points = [{"mode": str(site.native_mode), "powerFhp": f"{avg['fhp'] / 1000:.3f}", "soc": f"{soc:.1f}",
               "recTime": f"{day} {t}:00", "powerGen": f"{f['gen'] / 1000:.3f}",
               "powerLoad": f"{f['load'] / 1000:.3f}",
               "kwhTotal": f"{soc / 100 * site.capacity_wh / 1000:.2f}", "powerUti": f"{avg['uti'] / 1000:.3f}",
               "powerSun": f"{f['sun'] / 1000:.3f}"} for t, f, soc, avg in _slots(gw, day)]
    gen = int(site.generator.block.get("genStat", 0) or 0)
    return _env({"date": day, "next": {"date": (d + dt.timedelta(1)).isoformat()},
                 "last": {"date": (d - dt.timedelta(1)).isoformat()}, "genStat": gen,
                 "iotDayPowerVo": {"date": day, "mapList": points, "genStat": gen, "deviceTime": _when(gw)}},
                QUERY, total=0)


ELECTRIC_KEYS = (("kwhSun", "sun"), ("kwhGen", "gen"), ("kwhUtiIn", "imp"), ("kwhUtiOut", "exp"),
                 ("kwhFhpChg", "chg"), ("kwhFhpDi", "dis"), ("kwhLoad", "load"))
ELECTRIC_NULL = ("soOutGrid", "kwhSolarLoad", "soChBat", "genChBat", "gridChBat", "kwhFhpLoad", "batOutGrid",
                 "kwhGenLoad", "kwhGridLoad")


def electric_statistics(api, q, body) -> dict:
    """Daily kWh from startDate to endDate (at most 366 days), from the site's history."""
    from .cloud import CloudError
    gw = api._gateway(q)
    gw.site.step()
    try:
        start = dt.date.fromisoformat(q.get("startDate", ""))
        end = dt.date.fromisoformat(q.get("endDate") or q.get("startDate", ""))
    except ValueError:
        raise CloudError(400, "startDate and endDate are YYYY-MM-DD")
    days = [start + dt.timedelta(n) for n in range(min(366, max(0, (end - start).days + 1)))]
    kwh = [cloud_energy.day_kwh(gw, d.isoformat()) for d in days]
    vo = {"id": 1, "gatewayId": gw.serial, "year": start.year, "day": [d.isoformat() for d in days],
          "weekDay": [d.strftime("%a") for d in days], "monthDay": [d.day for d in days]}
    for name, key in ELECTRIC_KEYS:
        vo[name] = [round(k.get(key, 0.0), 2) for k in kwh]
        vo[name + "Total"] = round(sum(vo[name]), 2)
    rows = [{**{k: None for k in ELECTRIC_NULL}, **{name: vo[name][i] for name, _ in ELECTRIC_KEYS}}
            for i in range(len(days))]
    vo["mapList"] = [{**r, "monthDay": d.day} for r, d in zip(rows, days)]
    vo["weekList"] = [{**r, "monthDay": d.strftime("%a")} for r, d in zip(rows, days)]
    return _env({"opt": str(q.get("opt", "")), "startDate": start.isoformat(), "endDate": end.isoformat(),
                 "year": None, "electricVo": vo, "genStat": int(gw.site.generator.block.get("genStat", 0) or 0)},
                QUERY, total=0)


def count_load(api, q, body) -> dict:
    """Where the home's energy came from over the period (type 1 day, 2 week, 3 month, 4 year,
    as getFhpElectricData reads them), kWh and each source's share as a percentage string."""
    gw = api._gateway(q)
    day = _day(q, gw, "date")
    periods = cloud_energy._periods(gw, {"type": q.get("type", 1), "dayTime": day, "startDate": day})
    dates = [d for _, ds in periods for d in ds]
    tot: dict[str, float] = {}
    for d in dates:
        for k, v in cloud_energy.day_kwh(gw, d).items():
            tot[k] = tot.get(k, 0.0) + v
    parts = {"kwhSolarLoad": tot.get("sun_home", 0.0), "kwhGridLoad": tot.get("grid_home", 0.0),
             "kwhFhpLoad": tot.get("bat_home", 0.0), "kwhGenLoad": tot.get("gen_home", 0.0)}
    whole = sum(parts.values())
    out = {k: round(v, 2) for k, v in parts.items()}
    out.update({k + "Rate": f"{(100 * v / whole if whole else 0.0):.1f}%" for k, v in parts.items()})
    return _env({**out, "status": 1, "startDate": min(dates, default=day), "endDate": max(dates, default=day)},
                QUERY, total=0)


def charge_powers(api, q, body) -> dict:
    """Every backup (the grid relay's openings), newest first, paged; with the hours, minutes and
    seconds each lasted."""
    records = []
    for r in reversed(backup_records(api._gateway(q))):
        h, rest = divmod(r["second"], 3600)
        records.append({**r, "chour": h, "cminute": rest // 60, "csecond": rest % 60})
    return _env(_page(q, records), QUERY, total=len(records))


def charge_power_total(api, q, body) -> dict:
    """The backups' total time."""
    h, rest = divmod(sum(r["second"] for r in backup_records(api._gateway(q))), 3600)
    return _env({"hour": h, "minute": rest // 60, "second": rest % 60}, QUERY, total=0)


# -- messages and notifications ----------------------------------------------------------------
def event_settings(api) -> dict:
    return api._app.setdefault(("account", "events"), {
        "classificationsId": ",".join(str(i) for i in EVENT_CLASSES), "blockTime": "", "blockTimeFlag": 0,
        "weekDate": ""})


def event_classes(api, q, body) -> dict:
    items = [{"id": i, "name": name, "eventsId": ",".join(kinds), "explain": ""}
             for i, (name, kinds) in EVENT_CLASSES.items()]
    return _env({"classificationList": items, **event_settings(api)}, QUERY, total=0)


def set_event_push(api, q, body) -> dict:
    """Kept and read back by selectEventClassification; the emulator sends no pushes."""
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    st = event_settings(api)
    for k in ("classificationsId", "blockTime", "blockTimeFlag", "weekDate"):
        if isinstance(req, dict) and k in req:
            st[k] = req[k]
    return _env(None)


def device_messages(api, q, body) -> dict:
    """The gateway's event log (events.py) as device messages, newest first: eventTypes are
    EVENT_CLASSES ids (all when not given)."""
    gw = api._gateway(q)
    try:
        wanted = {int(x) for x in str(q.get("eventTypes") or "").split(",") if x.strip()} or set(EVENT_CLASSES)
    except ValueError:
        wanted = set(EVENT_CLASSES)
    kind_class = {k: i for i, (_, kinds) in EVENT_CLASSES.items() for k in kinds}
    items = []
    for e in reversed(gw.events.recent(0)):
        cls = kind_class.get(e["kind"], 5)
        if cls not in wanted:
            continue
        at = dt.datetime.fromisoformat(e["at"])
        ms = int(at.timestamp() * 1000)
        items.append({"id": e["seq"], "event": cls, "title": e["kind"], "type": cls, "subType": 0,
                      "gatewayId": gw.serial, "content": e["text"], "installerContent": "", "notice": "",
                      "sendMode": 0, "receiveMode": 0, "countrys": None, "provinces": None, "citys": None,
                      "importFileName": "", "importFileAddress": None, "receiveNum": 0, "status": 1,
                      "startTime": ms, "userMessageId": None, "readStatus": None, "toUrlType": None,
                      "toUrlParams": None, "turnoverTime": ms, "creationTime": ms,
                      "deviceSendTime": e["site_time"] or e["at"], "msgType": 0, "pushLink": None,
                      "pushType": None})
    return _env(_page(q, items), QUERY, total=len(items))


def no_push_list(api, q, body) -> dict:
    """No push messages: the emulator sends none."""
    return _env([], QUERY, total=0)


def no_push_count(api, q, body) -> dict:
    return _env(0, QUERY, total=0)


def push_message(api, q, body) -> dict:
    from .cloud import CloudError
    raise CloudError(400, f"no such message {q.get('id')!r}")


# -- TOU helpers ------------------------------------------------------------------------------
def _dispatches(api, gw: "Gateway") -> list[dict]:
    """The TOU editor's dispatches (cloud_tou), with the captured strings empty, not null."""
    return [{**d, "recommendScene": "", "remark": "", "url": "", "descriptionUrl": "", "solarPriority": "",
             "loadPriority": "", "recommendFlag": None}
            for d in api.tou(gw).detail()["detailDefaultVo"]["touDispatchList"]]


def _body(body: bytes) -> dict:
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        return {}
    return req if isinstance(req, dict) else {}


def custom_dispatch(api, q, body) -> dict:
    """The dispatches (cloud_tou's, the TOU editor's) and, for each block sent, the same list
    with the first, Home, as the recommendation (emulator's choice)."""
    req = _body(body)
    gw = api._gateway({"gatewayId": req.get("gatewayId") or q.get("gatewayId")})
    dispatches = _dispatches(api, gw)
    blocks = [b for b in req.get("strategyList") or [] if isinstance(b, dict)]
    return _env({"dispatchList": dispatches,
                 "strategyList": [{"startHourTime": b.get("startHourTime", ""), "endHourTime": b.get("endHourTime", ""),
                                   "waveType": b.get("waveType", 0),
                                   "recommendDispatchList": [{**d, "recommendFlag": int(n == 0)}
                                                             for n, d in enumerate(dispatches)]}
                                  for b in blocks]})


def recommend_dispatch(api, q, body) -> dict:
    """The schedule sent back with a dispatch on every block: Home (emulator's choice)."""
    req = _body(body)
    gw = api._gateway({"gatewayId": req.get("gatewayId") or q.get("gatewayId")})
    home = _dispatches(api, gw)[0]
    seasons = []
    for s in req.get("strategyList") or []:
        if not isinstance(s, dict):
            continue
        days = [{"dayType": d.get("dayType", 3),
                 "detailVoList": [{"waveType": b.get("waveType", 0), "startHourTime": b.get("startHourTime", ""),
                                   "endHourTime": b.get("endHourTime", ""), "dispatch": home}
                                  for b in d.get("detailVoList") or [] if isinstance(b, dict)]}
                for d in s.get("dayTypeVoList") or [] if isinstance(d, dict)]
        seasons.append({"id": None, "seasonName": s.get("seasonName", ""), "dayTypeVoList": days})
    return _env({"strategyList": seasons})


def gateway_tou_list(api, q, body) -> dict:
    """The older getGatewayTouList: getGatewayTouListV2's list."""
    return _env(api.tou_list(api._gateway(q)))


def no_tariffs(api, q, body) -> dict:
    """No utility or tariff catalogue (the emulator's TOU is entered by hand)."""
    return _env(NO_TARIFFS)


def tariff_detail(api, q, body) -> dict:
    from .cloud import CloudError
    raise CloudError(400, f"unknown tariff {q.get('tariffId')!r}: the emulator has no tariff catalogue")


# -- the assistant (/api-smart/) ---------------------------------------------------------------
def _smart(data) -> dict:
    """The assistant's envelope (``msg``, ``data``), its message as captured."""
    return {"code": 200, "msg": ASSISTANT_OK, "data": data, "success": True}


def recommend_questions(api, q, body) -> dict:
    """No assistant: no suggested questions."""
    return _smart([])


def ask_question(api, q, body) -> dict:
    """No assistant: a fixed answer, in the captured reply shape."""
    req = _body(body)
    return _smart({"action": int(req.get("action") or 0), "function": 0, "scene": 0, "subScene": 0,
                   "content": "The emulator has no assistant.", "hint": None, "jumpScheme": None,
                   "questionId": secrets.token_hex(8), "component": None,
                   "sessionId": str(req.get("sessionId") or secrets.token_hex(8))})


def assistant_feedback(api, q, body) -> dict:
    """Accepted and not kept; this reply has no data, as captured."""
    return {"code": 200, "msg": ASSISTANT_OK, "success": True}


# -- sendMqtt 315, 335, 339, 341 ---------------------------------------------------------------
def maintenance(api, gw, req) -> dict:
    """315 → 316: opt 1 with reboot 1 reboots the emulated aGate (as Direct Connect 1721);
    cleanAlarmFlag 1 clears a forced alarm. The other flags are echoed as 0."""
    opt = int(req.get("opt", 0))
    reboot = int(req.get("reboot", 0))
    if opt == 1 and int(req.get("cleanAlarmFlag", 0)) == 1:
        gw.site.clear_alarm()
    if opt == 1 and reboot == 1:
        gw.reboot()
    return {"paraType": int(req.get("paraType", 0)), "opt": opt, "reboot": reboot, "cleanUnlockAlarm": 0,
            "cleanLockAlarm": 0, "cleanAlarmFlag": int(req.get("cleanAlarmFlag", 0)), "reset": 0, "result": 0}


def wifi_scan(api, gw, req) -> dict:
    """335 → 336: networks in range: the aGate's own Wi-Fi (337) and one placeholder neighbour.
    RSSI in dBm (emulator's choice)."""
    wifi = _wifi(api, gw)
    return {"result": 0, "reason": 0,
            "wifi_Info": [{"wifi_SSID": wifi["wifi_SSID"], "wifi_RSSI": -55, "wifi_Safety": 1},
                          {"wifi_SSID": "emulator-neighbour", "wifi_RSSI": -78, "wifi_Safety": 1}]}


def _switches(api, gw) -> dict:
    return _block(api, gw, "netswitch", lambda: {"ethernet0NetSwitch": 1, "ethernet1NetSwitch": 1,
                                                  "wifiNetSwitch": 1, "4GNetSwitch": 0})


def network_status(api, gw, req) -> dict:
    """339 → 340: the connection as 317 keeps it (currentNetType) and the switches as 341 keeps
    them; connected to the router and the cloud."""
    net, sw = _net(api, gw), _switches(api, gw)
    kind = int(net["currentNetType"])
    signal = int(api.composite(gw)["runtimeData"].get("wifiSignal", 0) or 0)
    return {"opt": int(req.get("opt", 0)), "result": 0, "reason": 0, "routerStatus": 1, "netStatus": 1,
            "awsStatus": int(net["awsStatus"]),
            "EthConnectRouterStatus": int(kind in (1, 2) and bool(sw["ethernet0NetSwitch"] or sw["ethernet1NetSwitch"])),
            "wifiConnectRouterStatus": int(kind == 3 and bool(sw["wifiNetSwitch"])),
            "4GConnectBSStatus": int(kind == 4 and bool(sw["4GNetSwitch"])), "WifiSignalStrength": signal,
            "4GSignalStrength": 0, "currentNetType": kind}


def network_switches(api, gw, req) -> dict:
    """341 → 342: which interfaces are enabled; opt 1 writes the keys sent."""
    sw = _switches(api, gw)
    opt = int(req.get("opt", 0))
    if opt == 1:
        sw.update({k: int(req[k]) for k in sw if k in req})
    return {"opt": opt, "result": 0, "reason": 0, **sw}


CALLS = {
    ("GET", "terminal/site/list/siteAndDeviceInfo"): site_and_device_info,
    ("GET", "terminal/listAppUserDevice"): app_user_devices,
    ("GET", "terminal/newCompliance/getComplianceNameList"): compliance_names,
    ("GET", "terminal/newCompliance/getComplianceDetailById"): compliance_detail,
    ("GET", "common/obtainGeographyList"): geography,
    ("POST", "terminal/tou/getCustomEnergyDispatchList"): custom_dispatch,
    ("GET", "terminal/getHotSpotInfo/v2"): hotspot,
    ("GET", "terminal/listDeviceMessagesByType"): device_messages,
    ("GET", "terminal/solar/selectAnyOneSolarOpen"): solar_open,
    ("GET", "terminal/getEquipmentLocationDetail"): location_detail,
    ("POST", "terminal/updateTerPushMessageReadByType"): no_push_count,
    ("GET", "terminal/selectTerPushMessageListApp"): no_push_list,
    ("GET", "terminal/v2/warrantyInfo"): warranty,
    ("GET", "terminal/site/get/SiteDetail"): site_detail,
    ("GET", "terminal/span/getSpanSetting"): span_setting,
    ("GET", "terminal/selectEventClassification"): event_classes,
    ("GET", "common/v2/obtainUrlByKey"): url_by_key,
    ("GET", "terminal/getOffGridFlag"): off_grid_flag,
    ("POST", "terminal/tou/getGatewayTouList"): gateway_tou_list,
    ("GET", "terminal/getHotSpotInfo"): hotspot_name,
    ("POST", "terminal/setEventPushToUser"): set_event_push,
    ("POST", "terminal/tou/getRecommendEnergyDispatchList"): recommend_dispatch,
    ("GET", "terminal/iotChargePowers"): charge_powers,
    ("GET", "terminal/getWarnFlagNotice"): warn_flag,
    ("GET", "common/getMaintenanceInfo"): none_query,
    ("GET", "terminal/getDeviceInfo"): device_info_v1,
    ("GET", "terminal/selectWorkingHours"): working_hours,
    ("GET", "terminal/selectIotUserRuntimeDataLog"): runtime_log,
    ("GET", "terminal/getGatewaySystemSetting"): system_setting,
    ("GET", "terminal/system/getSystemSetting"): system_setting,
    ("GET", "common/getDeviceRealTimeData"): real_time_soc,
    ("GET", "terminal/selectTerPushMessageUnread"): no_push_count,
    ("GET", "terminal/selectTerPushMessageList"): no_push_list,
    ("GET", "terminal/selectTerPushMessageV2"): push_message,
    ("POST", "terminal/site/update/DeviceDetail"): site_update,
    ("POST", "terminal/site/update/SiteDetail"): site_update,
    ("GET", "terminal/site/check/OverlapSite"): overlap_site,
    ("GET", "terminal/initialize/selectTerStartupPage"): none_query,
    ("GET", "terminal/chargePowerTotal"): charge_power_total,
    ("GET", "terminal/iotDayPowerStatistics"): day_power,
    ("GET", "terminal/electricStatistics"): electric_statistics,
    ("GET", "terminal/countLoad"): count_load,
    ("GET", "terminal/getCompliance"): compliance_params,
    ("POST", "terminal/getProvincesListBYGatewayId"): provinces_by_gateway,
    ("GET", "terminal/tou/getVppTipForUpdateTou"): vpp_tip,
    ("POST", "terminal/tou/getTouCompanyListPageV2"): no_tariffs,
    ("GET", "terminal/tou/getTariffListByCompanyId"): no_tariffs,
    ("GET", "terminal/tou/getTariffDetailByIdV2"): tariff_detail,
    ("GET", "terminal/v2/queryTerminalUserInfo"): terminal_user_info,
    ("GET", "newApi/api-user/app/resource/getUserResources"): user_resources_v1,
}
# The assistant: full paths (not below /hes-gateway/).
SMART_CALLS = {
    ("GET", "/api-smart/terminal/recommendQuestion"): recommend_questions,
    ("POST", "/api-smart/terminal/askQuestion"): ask_question,
    ("POST", "/api-smart/terminal/feedback"): assistant_feedback,
}
MQTT_CALLS = {315: maintenance, 335: wifi_scan, 339: network_status, 341: network_switches}
