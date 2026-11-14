"""FranklinWH Cloud API (#295): the REST API the FranklinWH app and franklinwh-cloud use,
served from the fleet's gateways.

A client is pointed here by its base URL and nothing else, e.g. franklinwh-cloud's
``Client(auth, serial, url_base="http://127.0.0.1:8503/")``. The emulator never calls or
forwards to the real cloud; it listens on loopback.

Phase 1a (#297): login, the account's gateway list, each gateway's composite info, and the
``sendMqtt`` relay's 353 (smart-circuit loads), which franklinwh-cloud's ``get_stats`` sends
whenever a circuit is on. Phase 1b (#299): ``sendMqtt`` 203, 211 (type 1) and 311, and
``selectOffgrid``. Phase 1c (#301): energy history (``api-energy/...``, cloud_energy.py).
Phase 2a (#303): the mode, reserve SoC, off-grid and smart-circuit writes, and the reads that
read them back. Phase 2b (#305): TOU schedules (cloud_tou.py). Phase 2c (#307): storm,
generator and grid-limit settings. Phase 3 (#309): faults (``gw.cloud_faults``, the
``cloud-faults`` action) and token expiry (``cloud: {token_ttl_s}``). #311: broken replies
below the envelope (``bad_reply``: hangs, resets, truncated or non-JSON bodies, HTML error
pages, broken ``sendMqtt`` replies) and an account-wide rate limit
(``cloud: {rate_limit_per_min}``).

Sources, not FranklinWH documentation: franklinwh-cloud's request code (auth.py, client.py,
mixins), its ``tests/fixtures`` (login, composite info) and ``docs/franklinwh_openapi.json``
(response schemas inferred from captures, no examples). Field names and the envelope follow
those; every value is this emulator's own model.

* Envelope: ``{code, message, result, success}``; ``code`` 200 is success. Errors are in the
  body with HTTP 200, as franklinwh-cloud reads them: 401 wrong account or password (login),
  10009 a missing or unknown ``loginToken``. An unknown path is HTTP 404 with the envelope
  (the emulator's own choice; the real cloud's answer is not established).
* Login: a form POST with ``account``, ``password`` (MD5 hex of the password) and ``type``;
  the account and password come from the fleet file's ``cloud:`` (defaults below). Tokens
  don't expire here.
* ``getDeviceCompositeInfo``'s ``runtimeData`` is the same live site as Direct Connect 1411
  (whose field names match the cloud's); ``pro_load`` holds the smart-circuit switch states,
  as franklinwh-cloud reads it. Whether the cloud carries the same values as Direct Connect
  is not established.
* ``sendMqtt``: the body is the command envelope (``cmdType``, ``equipNo``, ``snno``, ``crc``,
  ``dataArea``); the reply's ``result`` echoes it with ``cmdType`` + 1 and ``dataArea`` as a
  JSON string. Replies are request + 1 except 203, which the cloud answers with 201
  (franklinwh-cloud ``MqttResponse``, matched pairs in its HAR survey). Each dataArea is the
  Direct Connect payload whose field names match: 353 is 1411's smart-circuit meter, 311 is
  1410's circuit names and modes, 211 type 1 is 1412's run status and electrical readings
  (plus the client's spellings ``dspSetFreq``, ``dspRunStatus`` and the relays it reads that
  1412 lacks), 203 is the composite's ``runtimeData`` (its shape is not established; nothing
  in franklinwh-cloud's ``get_stats`` sends it). Other commands, and 211 types 2/3 (BMS):
  code 400 (not emulated yet).
* Modes: the cloud's ``workMode`` 1 TOU, 2 self-consumption, 3 emergency backup; its
  ``oldIndex`` 3/2/1 is the Modbus numbering the site uses. The list item ids are
  franklinwh-cloud's legacy ``currendId`` values (9322/9323/9324). ``soc`` of the TOU and
  self-consumption items is the site's reserve; the emergency-backup item's is stored and
  inert (the site fills to 100 %). An emergency backup for a fixed time
  (``backupForeverFlag`` 2, ``durationMinute``) ends in ``nextWorkMode`` at the first cloud
  call after it expires, not on its own.
* ``updateOffgrid`` is Direct Connect 1412's off-grid write; ``sendMqtt`` 311 with ``opt`` 1
  is 1410's smart-circuit write.
* Storm Hedge (#307): the settings are kept and read back; no storm events are emulated, so
  it never acts. The generator: ``selectIotGenerator`` is Direct Connect 1902's block and live
  state; ``updateIotGenerator`` merges the keys sent into that block (the cloud's write is
  partial, Direct Connect's is the full block). franklinwh-cloud's DEF-GEN-MODE-WRITES-MANUSW
  saw the cloud treat ``manuSw`` as a start/stop command; the generator model reads it as
  1902's mode. Grid limits (``getPowerControlSetting``/``setPowerControl``, kW, -1 unlimited,
  0 off): the charge limit is the site's utility ``grid_charge``/``grid_charge_max_w``; a
  discharge limit of 0 turns battery export off, and other values are kept, not enforced.
* ``selectOffgrid``: ``offgridSet``/``offgridState`` as Direct Connect 1412's off-grid read;
  ``get_stats`` asks for it when the grid relay is open, to tell a requested off-grid from an
  outage.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import json
import time
import zlib
import logging
import random
import secrets
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from . import cloud_energy, cloud_tou
from .dc_payloads import DcPayloads
from .models import AUNZ, NA
from .server import MQTT_BAD_REPLIES
from .site import MODE_BACKUP, MODE_SELF, MODE_TOU

if TYPE_CHECKING:
    from .gateway import Gateway

log = logging.getLogger(__name__)
_rng = random.Random(311)               # which requests get a bad_reply_p < 1: repeatable

DEFAULT_ACCOUNT = "owner@example.com"
DEFAULT_PASSWORD = "emulator"
CLOUD_KEYS = {"account", "password", "token_ttl_s", "rate_limit_per_min"}
PREFIX = "/hes-gateway/terminal/"
ENERGY = {"/api-energy/power/getFhpPowerByDay": cloud_energy.power_by_day,
          "/api-energy/electric/getFhpElectricData": cloud_energy.electric_data}
REPLY = {203: 201}                     # response cmdType where it is not request + 1
_DC_STATUS = ("opt", "result", "reason")
# franklinwh-cloud's device catalogue region ids (const/device_catalog.json): 2 US, 3 AU.
# Canada and New Zealand are given their region's id: not established.
COUNTRY_ID = {NA: 2, AUNZ: 3}
# cloud workMode -> (site mode = oldIndex, list item id, name). The ids are franklinwh-cloud's
# legacy Mode.currendId values; the names its getGatewayTouListV2 handling expects.
WORK_MODES = {1: (MODE_TOU, 9322, "Time-of-Use"), 2: (MODE_SELF, 9323, "Self-Consumption"),
              3: (MODE_BACKUP, 9324, "Emergency Backup")}
SITE_TO_WORK = {site_mode: wm for wm, (site_mode, _, _) in WORK_MODES.items()}
MIN_RESERVE, MAX_RESERVE = 5, 100
# Storm Hedge writes: path -> {key: allowed range} (franklinwh-cloud set_storm_settings' ranges).
STORM_WRITES = {"weather/switchStorm": {"stormEn": (0, 1)},
                "weather/setAdvanceBackupTime": {"advanceBackupTime": (30, 300)},
                "weather/setStormNotice": {"stormNoticeEn": (0, 1), "advanceTime": (5, 30)}}
V2L_RUN_STATE = {"Disabled": 0, "Standby": 1, "Active": 2}   # franklinwh-cloud v2lRunState


class CloudError(Exception):
    def __init__(self, code: int, message: str, http: int = 200, retry_after: int | None = None):
        super().__init__(message)
        self.code, self.http, self.retry_after = code, http, retry_after


def _envelope(result=None, code: int = 200, message: str = "Query success!") -> dict:
    return {"code": code, "message": message, "result": result, "success": code == 200}


def _body_gateway(body: bytes) -> str | None:
    """The gateway a JSON body names (sendMqtt's equipNo, the storm writes' equipNo, ...)."""
    if not body or not body.lstrip().startswith(b"{"):
        return None
    try:
        d = json.loads(body)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    template = d.get("template")
    sn = d.get("equipNo") or d.get("gatewayId") or (
        template.get("gatewayId") if isinstance(template, dict) else None)
    return sn if isinstance(sn, str) else None


def _json(body: bytes) -> dict:
    try:
        out = json.loads(body or b"{}")
    except ValueError:
        raise CloudError(400, "the body must be JSON")
    return out if isinstance(out, dict) else {}


def _data_area(raw) -> dict:
    """The command's dataArea: an object, or (as franklinwh-cloud sends it) a JSON string."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


class CloudApi:
    def __init__(self, gateways: dict[str, "Gateway"], host: str = "127.0.0.1", port: int = 8503,
                 cloud: dict | None = None):
        cfg = dict(cloud or {})
        bad = set(cfg) - CLOUD_KEYS
        if bad:
            raise ValueError(f"cloud: unknown keys {sorted(bad)}; known {sorted(CLOUD_KEYS)}")
        self.account = str(cfg.get("account", DEFAULT_ACCOUNT))
        self.password = str(cfg.get("password", DEFAULT_PASSWORD))
        ttl = cfg.get("token_ttl_s")
        self.token_ttl_s = None if ttl is None else float(ttl)
        limit = cfg.get("rate_limit_per_min")
        self.rate_limit_per_min = None if limit is None else int(limit)   # all gateways together
        self.gateways = gateways
        self.by_serial = {gw.serial: gw for gw in gateways.values()}
        self.host, self.port = host, port
        self.tokens: dict[str, float] = {}          # token -> when it was issued
        self._hits: dict[str, list[float]] = {}     # per gateway: request times (rate limit)
        self._payloads: dict[str, DcPayloads] = {}
        self._modes: dict[str, dict] = {}            # per gateway: what only the cloud keeps
        self._tou: dict[str, cloud_tou.TouStore] = {}
        self._server: asyncio.base_events.Server | None = None
        self._hung: set[asyncio.StreamWriter] = set()   # bad_reply "hang": held until stop

    async def start(self) -> tuple[str, int]:
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.host, self.port

    async def stop(self) -> None:
        for writer in list(self._hung):
            writer.transport.abort()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # -- routes --------------------------------------------------------------------------
    def route(self, method: str, target: str, headers: dict[str, str], body: bytes) -> dict:
        url = urlsplit(target)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if method == "POST" and path == PREFIX + "initialize/appUserOrInstallerLogin":
            return self.login({k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()})
        if not path.startswith(PREFIX) and path not in ENERGY:
            raise CloudError(404, f"no such path: {path}", http=404)
        if not self._token_ok(headers.get("logintoken")):
            raise CloudError(10009, "Token invalid, please log in again")
        self._rate_limit(None)
        if method == "GET" and path in ENERGY:
            return _envelope(ENERGY[path](self._gateway(q), q))
        name = path[len(PREFIX):]
        sn = q.get("gatewayId") or q.get("equipNo") or _body_gateway(body)
        if sn in self.by_serial:
            gw = self.by_serial[sn]
            self._rate_limit(gw)
            self._expire_backup(gw)
            self.tou(gw).sync()
            if name == "getDeviceCompositeInfo" and gw.cloud_faults.empty_composite:
                return _envelope(None)
        if method == "GET" and name == "getHomeGatewayList":
            return _envelope([self.gateway_entry(gw) for gw in self.gateways.values()])
        if method == "GET" and name == "getDeviceCompositeInfo":
            return _envelope(self.composite(self._gateway(q)))
        if method == "GET" and name == "selectOffgrid":
            return _envelope(self._strip(self.payloads(self._gateway(q)).offgrid({})))
        if method == "POST" and name == "sendMqtt":
            return self.send_mqtt(body)
        if method == "POST" and name == "tou/getGatewayTouListV2":
            return _envelope(self.tou_list(self._gateway(q)))
        if method == "POST" and name == "tou/updateTouModeV2":
            return self.update_mode(self._gateway(q), q)
        if method == "POST" and name == "tou/updateSocV2":
            return self.update_soc(self._gateway(q), q)
        if method == "POST" and name == "updateOffgrid":
            req = _json(body)
            gw = self._gateway({"gatewayId": req.get("gatewayId") or q.get("gatewayId")})
            area = {"opt": 1, **{k: int(req[k]) for k in ("offgridSet", "offgridSoc") if k in req}}
            self.payloads(gw).offgrid(area)
            return _envelope(None, message="Operation success!")
        if method == "GET" and name == "weather/getStormSetting":
            st = self.mode_state(self._gateway(q))
            return _envelope({"enableStorm": st["stromEn"], "advanceBackupTime": st["advanceBackupTime"],
                              "noticeEnable": st["stormNoticeEn"], "advanceTime": st["advanceTime"],
                              "stormEventVOList": [], "showEventTypeFlag": 0, "tipsMap": {}})
        if method == "GET" and name in ("weather/getStormList", "weather/getProgressingStormList"):
            self._gateway(q)
            return _envelope([])
        if method == "POST" and name in STORM_WRITES:
            req = _json(body)
            st = self.mode_state(self._gateway({"equipNo": req.get("equipNo")}))
            for key, (lo, hi) in STORM_WRITES[name].items():
                if key in req:
                    v = int(req[key])
                    if not lo <= v <= hi:
                        raise CloudError(400, f"{key} must be {lo}-{hi}")
                    st["stromEn" if key == "stormEn" else key] = v
            return _envelope(None, message="Operation success!")
        if method == "GET" and name == "selectIotGenerator":
            return _envelope(self.generator(self._gateway(q)))
        if method == "POST" and name == "updateIotGenerator":
            req = _json(body)
            gw = self._gateway({"gatewayId": req.get("gatewayId") or q.get("gatewayId")})
            block = gw.site.generator.block
            with gw.site.lock:
                gw.site.step()
                for k, v in req.items():
                    if k in block:
                        block[k] = v
            return _envelope(None, message="Operation success!")
        if method == "GET" and name == "tou/getPowerControlSetting":
            return _envelope(self.power_control(self._gateway(q)))
        if method == "POST" and name == "tou/setPowerControl":
            req = _json(body)
            gw = self._gateway({"gatewayId": req.get("gatewayId") or q.get("gatewayId")})
            self.set_power_control(gw, req)
            return _envelope({"powerControlTipMsg": None}, message="Operation success!")
        if method == "GET" and name == "tou/getTouDispatchDetail":
            return _envelope(self.tou(self._gateway(q)).detail())
        if method == "POST" and name == "tou/saveTouDispatch":
            req = _json(body)
            gw = self._gateway({"gatewayId": q.get("gatewayId")
                                or (req.get("template") or {}).get("gatewayId")})
            try:
                return _envelope({"id": self.tou(gw).save(req)}, message="Operation success!")
            except cloud_tou.TouError as e:
                raise CloudError(400, str(e))
        if method == "POST" and name == "tou/getPcsHintInfo":
            return _envelope({"tipMsg": None, "hintFlag": 0})
        if method == "GET" and name == "selectTerPushMessageUnreadCount":
            return _envelope(0)
        raise CloudError(404, f"no such path: {path}", http=404)

    def _token_ok(self, token: str | None) -> bool:
        issued = self.tokens.get(token or "")
        if issued is None:
            return False
        if self.token_ttl_s is not None and time.monotonic() - issued > self.token_ttl_s:
            del self.tokens[token]
            return False
        return True

    def _rate_limit(self, gw: "Gateway | None") -> None:
        """One gateway's limit, or (gw None) the account's."""
        limit = self.rate_limit_per_min if gw is None else gw.cloud_faults.rate_limit_per_min
        hits = self._hits.setdefault("" if gw is None else gw.serial, [])
        now = time.monotonic()
        hits[:] = [t for t in hits if now - t < 60]
        if limit is not None and len(hits) >= int(limit):
            raise CloudError(429, "Too many requests", http=429,
                             retry_after=max(1, int(60 - (now - hits[0])) + 1))
        hits.append(now)

    def _gateway(self, q: dict) -> "Gateway":
        sn = q.get("gatewayId") or q.get("equipNo")
        gw = self.by_serial.get(sn or "")
        if gw is None:
            raise CloudError(400, f"unknown gateway {sn!r}")
        return gw

    def login(self, form: dict) -> dict:
        md5 = hashlib.md5(self.password.encode()).hexdigest()
        if form.get("account", "").lower() != self.account.lower() or form.get("password") != md5:
            return _envelope(None, 401, "Incorrect account or password")
        token = secrets.token_hex(16)
        self.tokens[token] = time.monotonic()
        return _envelope({"token": token, "userId": 1, "email": self.account, "version": None,
                          "currentType": int(form.get("type") or 0), "userTypes": [0],
                          "roles": [], "affiliateCompany": [], "passwordUpdateFlag": 0,
                          "ninetyDaysPwdUpdate": 0, "surveyFlag": 0, "needAgreeTerm": 0},
                         message="Login success!")

    def send_mqtt(self, body: bytes) -> dict:
        try:
            cmd = json.loads(body)
            ty, sn = int(cmd["cmdType"]), str(cmd["equipNo"])
        except (ValueError, KeyError, TypeError):
            raise CloudError(400, "sendMqtt: the body is a command envelope with cmdType and equipNo")
        gw = self._gateway({"equipNo": sn})
        if gw.cloud_faults.offline or gw.faults.offline:
            raise CloudError(136, "The device is offline")
        if gw.cloud_faults.timeout:
            raise CloudError(102, "Device response timeout")
        area = self.command(gw, ty, _data_area(cmd.get("dataArea")))
        blob = json.dumps(area, separators=(",", ":"))
        return _envelope({"cmdType": REPLY.get(ty, ty + 1), "equipNo": sn, "type": 0, "timeStamp": int(time.time()),
                          "snno": cmd.get("snno", 0), "len": len(blob),
                          "crc": f"{zlib.crc32(blob.encode()):08X}", "dataArea": blob})

    def command(self, gw: "Gateway", ty: int, req: dict) -> dict:
        dc = self.payloads(gw)
        if ty == 353:
            return self._strip(dc.smart_circuit_meter({}))
        if ty == 311:
            return self._strip(dc.smart_circuits(req if req.get("opt") == 1 else {}))
        if ty == 211 and int(req.get("type", 1)) == 1:
            return self.power_info(gw)
        if ty == 203:
            return self.composite(gw)["runtimeData"]
        raise CloudError(400, f"sendMqtt: cmdType {ty} is not emulated yet")

    # -- modes and reserves (#303) -----------------------------------------------------
    def mode_state(self, gw: "Gateway") -> dict:
        return self._modes.setdefault(gw.serial, {
            "backup_soc": 20, "backupForeverFlag": 1, "nextWorkMode": 2, "stromEn": 0,
            "timer": None, "advanceBackupTime": 120, "stormNoticeEn": 0, "advanceTime": 15,
            "discharge_max_kw": -1.0})

    # -- generator and grid limits (#307) --------------------------------------------------
    def generator(self, gw: "Gateway") -> dict:
        out = self._strip(self.payloads(gw).generator({}))
        v2l = gw.site.v2l
        out.update(generatorAlarmFlag=0, v2lModeEnable=int(bool(v2l and v2l.enabled)))
        return out

    def power_control(self, gw: "Gateway") -> dict:
        u = gw.site.utility_rules
        if u is not None and not u.grid_charge:
            charge = 0.0
        elif u is not None and u.grid_charge_max_w is not None:
            charge = round(float(u.grid_charge_max_w) / 1000, 2)
        else:
            charge = -1.0
        discharge = self.mode_state(gw)["discharge_max_kw"]
        if not gw.site.battery_export():
            discharge = 0.0
        return {"globalGridDischargeMax": discharge, "globalGridChargeMax": charge,
                "globalSettingStatus": 0, "solarTipMsg": None, "sgipFlag": 0, "itcFlag": 0,
                "isNem3": 0, "vppAndPcsTip": None, "bonusTipMsg": None, "isCalifornia": 0,
                "countryId": COUNTRY_ID.get(gw.site.model.region), "gridFlag": 1}

    def set_power_control(self, gw: "Gateway", req: dict) -> None:
        try:
            dis = float(req.get("globalGridDischargeMax", self.power_control(gw)["globalGridDischargeMax"]))
            chg = float(req.get("globalGridChargeMax", self.power_control(gw)["globalGridChargeMax"]))
        except (TypeError, ValueError):
            raise CloudError(400, "globalGridDischargeMax and globalGridChargeMax are kW, -1 or 0")
        if dis < 0 and dis != -1 or chg < 0 and chg != -1:
            raise CloudError(400, "a limit is kW, -1 (unlimited) or 0 (off)")
        u = asdict(gw.site.utility_rules) if gw.site.utility_rules is not None else {}
        u["grid_charge"] = chg != 0
        u["grid_charge_max_w"] = chg * 1000 if chg > 0 else None
        u["battery_export"] = False if dis == 0 else (True if dis > 0 else None)
        gw.site.set_utility(u)
        self.mode_state(gw)["discharge_max_kw"] = dis if dis != 0 else -1.0

    def tou(self, gw: "Gateway") -> cloud_tou.TouStore:
        if gw.serial not in self._tou:
            self._tou[gw.serial] = cloud_tou.TouStore(gw)
        return self._tou[gw.serial]

    def _expire_backup(self, gw: "Gateway") -> None:
        st = self.mode_state(gw)
        if st["timer"] and time.time() >= st["timer"][1]:
            st["timer"] = None
            if gw.site.native_mode == MODE_BACKUP:
                gw.site.set_native_mode(WORK_MODES.get(st["nextWorkMode"], WORK_MODES[2])[0])

    def tou_list(self, gw: "Gateway") -> dict:
        site, st = gw.site, self.mode_state(gw)
        current = SITE_TO_WORK[site.native_mode]
        soc = {1: site.tou_reserve, 2: site.self_reserve, 3: st["backup_soc"]}
        items = [{"id": mid, "oldIndex": site_mode, "name": name, "soc": float(soc[wm]),
                  "maxSoc": MAX_RESERVE, "minSoc": MIN_RESERVE, "dischargeDepthSoc": MIN_RESERVE,
                  "editSocFlag": 1, "multiSOCFlag": 0, "workMode": wm, "energyIncentivesType": 0,
                  "electricityType": 1, "displayFlag": 1}
                 for wm, (site_mode, mid, name) in WORK_MODES.items()]
        timer = st["timer"]
        fmt = lambda t: time.strftime("%H:%M:%S", time.localtime(t)) + ".000000"
        return {"currendId": WORK_MODES[current][1], "list": items, "stromEn": st["stromEn"],
                "sgipFlag": 0, "itcFlag": 0, "stopMode": 0, "gridChargeEn": 1,
                "touSendStatus": None, "touAlertMessage": None,
                "backupForeverFlag": st["backupForeverFlag"], "nextWorkMode": st["nextWorkMode"],
                "timerStartTime": fmt(timer[0]) if timer else None,
                "timerEndTime": fmt(timer[1]) if timer else None,
                "timerStartTimeZero": None, "timerEndTimeZero": None,
                "zoneInfo": site.location.timezone, "todayVppVo": None, "tariffSettingFlag": False,
                "vppSocVo": None}

    def _set_reserve(self, gw: "Gateway", wm: int, raw) -> None:
        soc = int(float(raw))
        if not MIN_RESERVE <= soc <= MAX_RESERVE:
            raise CloudError(400, f"soc must be {MIN_RESERVE}-{MAX_RESERVE}")
        if wm == 1:
            gw.site.set_reserves(tou_reserve=soc)
        elif wm == 2:
            gw.site.set_reserves(self_reserve=soc)
        else:
            self.mode_state(gw)["backup_soc"] = soc

    def update_mode(self, gw: "Gateway", q: dict) -> dict:
        try:
            wm = int(q["workMode"])
            site_mode = WORK_MODES[wm][0]
        except (KeyError, ValueError):
            raise CloudError(400, "workMode must be 1, 2 or 3")
        st = self.mode_state(gw)
        if "soc" in q and wm != 3:
            self._set_reserve(gw, wm, q["soc"])
        if "stromEn" in q:
            st["stromEn"] = int(q["stromEn"])
        st["timer"] = None
        if wm == 3:
            st["backupForeverFlag"] = int(q.get("backupForeverFlag") or 1)
            if q.get("nextWorkMode"):
                st["nextWorkMode"] = int(q["nextWorkMode"])
            if st["backupForeverFlag"] == 2:
                minutes = int(q.get("durationMinute") or 0)
                if not 30 <= minutes <= 4320:
                    raise CloudError(400, "durationMinute must be 30-4320")
                now = time.time()
                st["timer"] = (now, now + minutes * 60)
        gw.site.set_native_mode(site_mode)
        return _envelope({"tariffSettingFlag": False}, message="Operation success!")

    def update_soc(self, gw: "Gateway", q: dict) -> dict:
        try:
            wm = int(q["workMode"])
            WORK_MODES[wm]
            self._set_reserve(gw, wm, q["soc"])
        except (KeyError, ValueError):
            raise CloudError(400, "workMode (1-3) and soc are required")
        return _envelope(None, message="Operation success!")

    @staticmethod
    def _strip(payload: dict) -> dict:
        return {k: v for k, v in payload.items() if k not in _DC_STATUS}

    # -- bodies ----------------------------------------------------------------------------
    def payloads(self, gw: "Gateway") -> DcPayloads:
        if gw.serial not in self._payloads:
            self._payloads[gw.serial] = DcPayloads(gw)
        return self._payloads[gw.serial]

    def gateway_entry(self, gw: "Gateway") -> dict:
        site = gw.site
        ibg, _ = self.payloads(gw).dc_firmware()
        offline = gw.faults.offline or gw.cloud_faults.offline
        return {"id": gw.serial, "name": gw.config.id, "status": 0 if offline else 1,
                "activeStatus": 1, "account": self.account, "version": ibg,
                "zoneInfo": site.location.timezone, "countryId": COUNTRY_ID.get(site.model.region),
                "groupFlag": 0}

    def composite(self, gw: "Gateway") -> dict:
        site = gw.site
        flow = self.payloads(gw).power_flow({})          # steps the site
        s = site._last
        runtime = {k: v for k, v in flow.items() if k not in ("opt", "result", "reason")}
        islanded = not s.grid_connected
        off_grid_flag = 1 if islanded and s.trip_reason != "requested" else 0
        circuits = [1 if site.circuits[n].on else 0 for n in sorted(site.circuits)][:3]
        v2l = site.v2l
        runtime.update(
            pro_load=circuits + [0] * (3 - len(circuits)),
            genEn=int(site.generator.block.get("genEn", 0)),
            v2lModeEnable=int(bool(v2l and v2l.enabled)),
            v2lRunState=V2L_RUN_STATE[v2l.state(islanded, site.generator.relay_closed)] if v2l else 0,
            offGridFlag=off_grid_flag,
        )
        _, _, work_mode = self.payloads(gw).programme()
        return {"currentWorkMode": work_mode, "deviceStatus": 1, "valid": 1,
                "currentAlarmVOList": [],
                "solarHaveVo": {"offGridFlag": off_grid_flag, "offGridReason": runtime["offgridreason"]},
                "runtimeData": runtime}

    def power_info(self, gw: "Gateway") -> dict:
        """211 type 1: Direct Connect 1412 (the same field names) plus the spellings
        franklinwh-cloud reads where 1412's differ, and the relays it reads that 1412 lacks:
        load/solar contactors closed with their circuit, the EV relay with V2L active, the
        aPbox relay 0 (no reading)."""
        dc = self.payloads(gw)
        out = self._strip(dc.ibg_run_status({}))
        site, s = gw.site, gw.site._last
        v2l = site.v2l
        islanded = not s.grid_connected
        out.update(dspSetFreq=out["dspSettingFreq"], dspRunStatus=out["dspRunstatus"],
                   loadSolarRelay1Stat=out["smartRelay1Status"],
                   loadSolarRelay2Stat=out["smartRelay2Status"],
                   evRelayStat=int(bool(v2l) and v2l.state(islanded, site.generator.relay_closed)
                                   == "Active"),
                   BFPVApboxRelay=0)
        return out

    def _named(self, target: str, body: bytes) -> "Gateway | None":
        """The gateway a request names, for its faults."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(target).query).items()}
        return self.by_serial.get(q.get("gatewayId") or q.get("equipNo") or _body_gateway(body) or "")

    @staticmethod
    def _bad_reply(gw: "Gateway | None", target: str) -> str | None:
        """This request's bad_reply, if it gets one (counting it down)."""
        f = gw.cloud_faults if gw is not None else None
        if f is None or not f.bad_reply:
            return None
        if f.bad_reply in MQTT_BAD_REPLIES and not urlsplit(target).path.endswith("/sendMqtt"):
            return None
        if f.bad_reply_p < 1 and _rng.random() >= f.bad_reply_p:
            return None
        kind = f.bad_reply
        if f.bad_reply_count > 0:
            f.bad_reply_count -= 1
            if f.bad_reply_count == 0:
                f.bad_reply = None
        return kind

    @staticmethod
    def _spoil(kind: str, payload: dict) -> dict:
        """The envelope-level bad replies: the reply as routed, made wrong."""
        res = payload.get("result")
        if kind == "not_envelope":
            return {"status": "ok", "data": res}
        if kind == "success_false":
            return {**payload, "success": False}
        if payload.get("code") == 200 and isinstance(res, dict):
            if kind == "wrong_cmd_type":
                return {**payload, "result": {**res, "cmdType": int(res.get("cmdType", 0)) + 100}}
            if kind == "bad_crc":
                return {**payload, "result": {**res, "crc": "00000000"}}
            if kind == "bad_data_area":
                return {**payload, "result": {**res, "dataArea": str(res.get("dataArea", ""))[:-1] or "{"}}
        return payload

    # -- HTTP ------------------------------------------------------------------------------
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.readline()
            method, target, _ = request.decode("latin-1").split(" ", 2)
            headers: dict[str, str] = {}
            while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            length = int(headers.get("content-length") or 0)
            raw = await reader.readexactly(length) if length else b""
            status, extra = 200, ""
            try:
                payload = self.route(method.upper(), target, headers, raw)
            except CloudError as e:
                status, payload = e.http, _envelope(None, e.code, str(e))
                if e.retry_after is not None:
                    extra = f"Retry-After: {e.retry_after}\r\n"
            gw = self._named(target, raw)
            if gw is not None and gw.cloud_faults.latency_s:
                await asyncio.sleep(float(gw.cloud_faults.latency_s))
            kind = self._bad_reply(gw, target)
            if kind == "hang":
                self._hung.add(writer)
                try:
                    await reader.read()                    # until the client gives up
                finally:
                    self._hung.discard(writer)
                return
            if kind == "reset":
                writer.transport.abort()
                return
            ctype = "application/json"
            if kind == "not_json":
                data = b"upstream error: not JSON"
            elif kind and kind.startswith("html_"):
                status, ctype = int(kind[5:]), "text/html"
                data = (f"<html><head><title>{status} Error</title></head><body><h1>{status} Error</h1>"
                        "<p>The request could not be satisfied.</p></body></html>").encode()
            else:
                data = json.dumps(self._spoil(kind, payload) if kind else payload).encode()
            reason = {200: "OK", 404: "Not Found", 429: "Too Many Requests", 500: "Internal Server Error",
                      502: "Bad Gateway", 503: "Service Unavailable",
                      504: "Gateway Timeout"}.get(status, "Error")
            head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\n{extra}"
                    f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode()
            if kind == "truncate":
                writer.write(head + data[:len(data) // 2])
                await writer.drain()
                writer.transport.abort()
                return
            writer.write(head + data)
            await writer.drain()
        except (ValueError, asyncio.IncompleteReadError, ConnectionError) as e:
            log.debug("cloud api: bad request: %s", e)
        finally:
            writer.close()
