"""FranklinWH Cloud API (#295): the REST API the FranklinWH app and franklinwh-cloud use,
served from the fleet's gateways.

A client is pointed here by its base URL and nothing else, e.g. franklinwh-cloud's
``Client(auth, serial, url_base="http://127.0.0.1:8503/")``. The emulator never calls or
forwards to the real cloud; it listens on loopback.

Phase 1a (#297): login, the account's gateway list, each gateway's composite info, and the
``sendMqtt`` relay's 353 (smart-circuit loads), which franklinwh-cloud's ``get_stats`` sends
whenever a circuit is on. Phase 1b (#299): ``sendMqtt`` 203, 211 (type 1) and 311, and
``selectOffgrid``.

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
* ``selectOffgrid``: ``offgridSet``/``offgridState`` as Direct Connect 1412's off-grid read;
  ``get_stats`` asks for it when the grid relay is open, to tell a requested off-grid from an
  outage.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import zlib
import logging
import secrets
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from .dc_payloads import DcPayloads
from .models import AUNZ, NA

if TYPE_CHECKING:
    from .gateway import Gateway

log = logging.getLogger(__name__)

DEFAULT_ACCOUNT = "owner@example.com"
DEFAULT_PASSWORD = "emulator"
CLOUD_KEYS = {"account", "password"}
PREFIX = "/hes-gateway/terminal/"
REPLY = {203: 201}                     # response cmdType where it is not request + 1
_DC_STATUS = ("opt", "result", "reason")
# franklinwh-cloud's device catalogue region ids (const/device_catalog.json): 2 US, 3 AU.
# Canada and New Zealand are given their region's id: not established.
COUNTRY_ID = {NA: 2, AUNZ: 3}
V2L_RUN_STATE = {"Disabled": 0, "Standby": 1, "Active": 2}   # franklinwh-cloud v2lRunState


class CloudError(Exception):
    def __init__(self, code: int, message: str, http: int = 200):
        super().__init__(message)
        self.code, self.http = code, http


def _envelope(result=None, code: int = 200, message: str = "Query success!") -> dict:
    return {"code": code, "message": message, "result": result, "success": code == 200}


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
        self.gateways = gateways
        self.by_serial = {gw.serial: gw for gw in gateways.values()}
        self.host, self.port = host, port
        self.tokens: set[str] = set()
        self._payloads: dict[str, DcPayloads] = {}
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> tuple[str, int]:
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.host, self.port

    async def stop(self) -> None:
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
        if not path.startswith(PREFIX):
            raise CloudError(404, f"no such path: {path}", http=404)
        if headers.get("logintoken") not in self.tokens:
            raise CloudError(10009, "Token invalid, please log in again")
        name = path[len(PREFIX):]
        if method == "GET" and name == "getHomeGatewayList":
            return _envelope([self.gateway_entry(gw) for gw in self.gateways.values()])
        if method == "GET" and name == "getDeviceCompositeInfo":
            return _envelope(self.composite(self._gateway(q)))
        if method == "GET" and name == "selectOffgrid":
            return _envelope(self._strip(self.payloads(self._gateway(q)).offgrid({})))
        if method == "POST" and name == "sendMqtt":
            return self.send_mqtt(body)
        raise CloudError(404, f"no such path: {path}", http=404)

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
        self.tokens.add(token)
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
            return self._strip(dc.smart_circuits({}))
        if ty == 211 and int(req.get("type", 1)) == 1:
            return self.power_info(gw)
        if ty == 203:
            return self.composite(gw)["runtimeData"]
        raise CloudError(400, f"sendMqtt: cmdType {ty} is not emulated yet")

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
        return {"id": gw.serial, "name": gw.config.id, "status": 0 if gw.faults.offline else 1,
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
        return {"currentWorkMode": work_mode, "deviceStatus": 1,
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
            status = 200
            try:
                payload = self.route(method.upper(), target, headers, raw)
            except CloudError as e:
                status, payload = e.http, _envelope(None, e.code, str(e))
            data = json.dumps(payload).encode()
            reason = {200: "OK", 404: "Not Found"}.get(status, "Error")
            writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
            await writer.drain()
        except (ValueError, asyncio.IncompleteReadError, ConnectionError) as e:
            log.debug("cloud api: bad request: %s", e)
        finally:
            writer.close()
