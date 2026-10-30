"""HTTP control API for steering running gateways (stdlib only, JSON in/out).

    GET  /schema                        every action, its fields and examples (schema.py)
    GET  /gateways                      list gateways
    GET  /gateways/<id>                 live state
    POST /gateways/<id>/<action>        an action from schema.ACTIONS; returns the new state

Unknown actions get 404; unknown body keys and missing required ones get 400.

Binds to 127.0.0.1 by default: it can black out an emulated house, not a real one, but
there is no authentication.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, fields
from typing import TYPE_CHECKING

from .schema import ACTIONS, check_body, schema
from .server import Faults
from .site import ALARMS, INV_NAMES, MODE_NAMES, RUN_NAMES, W_PER_APOWER, WH_PER_APOWER
from .weather import SKIES, sun_position

if TYPE_CHECKING:
    from .gateway import Gateway

log = logging.getLogger(__name__)


@dataclass
class Raw:
    """A non-JSON response (a page or a static file)."""
    content_type: str
    body: bytes


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def gateway_state(gw: "Gateway") -> dict:
    snap = gw.site.step()
    site = gw.site
    state = asdict(snap)
    state["alarm_names"] = [n for n, b in ALARMS.items() if snap.alarms & (1 << b)]
    state.update({
        "id": gw.config.id,
        "serial": gw.serial,
        "address": list(gw.address),
        "dc_address": list(gw.dc_address) if gw.dc_address else None,
        "profile": gw.profile.name,
        "native_mode_name": MODE_NAMES.get(snap.native_mode, str(snap.native_mode)),
        "run_status_name": RUN_NAMES.get(snap.run_status, str(snap.run_status)),
        "inverter_state_name": INV_NAMES.get(snap.inverter_state, str(snap.inverter_state)),
        "sun": _sun(site, snap.ts),
        "base_aliases": list(gw.profile.base_aliases),
        "write_rejects": list(gw.device.write_rejects),
        "write_bases": None if gw.profile.write_bases is None else list(gw.profile.write_bases),
        "utility": asdict(site.utility),
        "grid_code": site.grid.name,
        "ac_type": site.config.ac_type,
        "legs": list(site.phases),
        "pv_relays": dict(site.pv_relays),
        "pv_inputs": dict(site.config.pv_inputs),
        "apower_units": site.config.apower_units,
        "apower_rating": {"wh": WH_PER_APOWER, "w": W_PER_APOWER},   # per aPower, emulator constants
        "forced_alarms": dict(site.forced_alarms),
        "remote_control": asdict(site.remote),
        "self_reserve": site.self_reserve,
        "tou_reserve": site.tou_reserve,
        "weather": None if site.weather is None else {
            "model": site.weather.cfg.model, "sky": site.weather.cfg.sky,
            "kwp": site.weather.kwp, "poa_w_m2": round(site.weather.poa(snap.ts), 1)},
        "generator": site.generator.status(),
        "faults": asdict(gw.faults),
        "dc_faults": asdict(gw.dc_faults),
        "modbus_enabled": gw.modbus_enabled,
        "episode_phase": gw.episode_phase,
        "export_limit_enforced": site.export_limit_enforced,
        "export_breach_s": site.export_breach_s,
        "export_breach_wh": site.export_breach_wh,
        "csip": site.csip.summary(snap.ts),
        "local_time": site.local_datetime(snap.ts).isoformat(timespec="seconds"),
        "utc_offset_hours": site.utc_offset_hours(snap.ts),
        "dst": site.is_dst(snap.ts),
        "location": asdict(site.location),
        "today_kwh": {k: round(v, 4) for k, v in site.today_kwh().items()},
        "circuits": {str(n): {"name": c.name, "on": c.on, "power_w": round(snap.circuit_w.get(n, 0.0), 1),
                              "energy_kwh": round(c.energy_wh / 1000.0, 3),
                              "soc_low_cutoff": c.soc_low_cutoff}
                     for n, c in site.circuits.items()},
        "appliances": {} if site.loads is None else {
            c.name: {"type": c.type, "circuit": c.circuit, "forced": c.forced,
                     **({"session_kwh": round(c.state.get("delivered", 0.0) / 1000.0, 2)}
                        if c.type == "ev" else {})}
            for c in site.loads.components},
    })
    return state


def _sun(site, ts: float) -> dict | None:
    """The sun's elevation at the site (None without a location)."""
    loc = site.location
    if loc.latitude is None or loc.longitude is None:
        return None
    zenith, _ = sun_position(ts, loc.latitude, loc.longitude)
    return {"elevation_deg": round(90.0 - zenith, 1), "up": zenith < 90.0}


def _num_or_none(body: dict, key: str):
    v = body[key]
    return None if v is None else float(v)


def apply(gw: "Gateway", action: str, body: dict) -> None:
    if action not in ACTIONS:
        raise ApiError(404, f"unknown action {action!r}")
    problem = check_body(action, body)
    if problem:
        raise ApiError(400, problem)
    site = gw.site
    try:
        if action == "grid":
            if body.get("restore"):
                site.restore_grid()
                return
            kw = {}
            if "available" in body:
                kw["available"] = bool(body["available"])
            for key in ("v_pu", "voltage", "hz", "hz_offset"):
                if key in body:
                    kw[key] = float(body[key])
            if not kw:
                raise ApiError(400, "grid: nothing to change")
            site.set_grid(phase=body.get("phase"), **kw)
        elif action == "offgrid":
            site.request_offgrid(bool(body["on"]))
        elif action == "write-reject":
            if body.get("clear"):
                gw.device.write_rejects.clear()
            else:
                bad = set(body) - gw.device.REJECT_KEYS
                if bad:
                    raise ApiError(400, f"write-reject: unknown keys {sorted(bad)}")
                gw.device.reject_writes(**body)
        elif action == "addressing":
            gw.set_addressing(**body)
        elif action == "episode":
            gw.episode(float(body["down_s"]), float(body["offgrid_s"]))
        elif action == "pv":
            if "input" in body:
                site.set_pv_relay(body["input"], bool(body.get("relay", True)))
            if "solar_w" in body:
                site.solar_override_w = _num_or_none(body, "solar_w")
        elif action == "load":
            site.load_override_w = _num_or_none(body, "w")
        elif action == "ambient":
            site.ambient_override_c = _num_or_none(body, "c")
        elif action == "alarms":
            for name in body.get("set", []):
                site.force_alarm(name, latched=bool(body.get("latched", True)))
            clear = body.get("clear")
            if clear == "all":
                site.clear_alarm()
            elif clear:
                for name in clear:
                    site.clear_alarm(name)
            if body.get("reset"):
                site.reset_alarms()
            if "dc_bits" in body:
                site.dc_alarms = int(body["dc_bits"])
        elif action == "csip":
            if any(k in body for k in ("enable", "uri", "pin")):
                site.configure_csip(enabled=body.get("enable"), uri=body.get("uri"),
                                    pin=body.get("pin"))
            if "server_up" in body:
                site.set_csip_server(bool(body["server_up"]))
            if body.get("clear"):
                site.clear_csip_events()
            if "default" in body:
                site.set_csip_default(body["default"] or {})
            if "control" in body:
                site.post_csip_control(body["control"], body.get("duration_s"))
        elif action == "der-comms":
            if "modbus" in body:
                gw.set_modbus_enabled(bool(body["modbus"]))
            if "csip" in body:
                site.configure_csip(enabled=bool(body["csip"]))
        elif action == "vpp":
            # A VPP dispatch as franklinwh-modbus sends it: watts (+ charge) or pct_hw.
            if "watts" in body:
                pct = -float(body["watts"]) / W_PER_APOWER * 100.0
            else:
                pct = float(body["pct_hw"])
            site.update_remote(enabled=True, mode=0, pct_hw=pct,
                               revert_effective=gw.profile.reversion_effective)
        elif action == "release":
            site.update_remote(enabled=False, pct_hw=0.0, watts=0)
        elif action == "mode":
            site.set_native_mode(int(body["mode"]))
        elif action == "reserve":
            site.set_reserves(self_reserve=body.get("self"), tou_reserve=body.get("tou"))
        elif action == "soc":
            with site.lock:
                site.step()
                site.soc = max(0.0, min(100.0, float(body["value"])))
        elif action == "circuit":
            site.set_circuit(int(body["n"]), bool(body["on"]))
        elif action == "appliance":
            state = body.get("on", None)
            site.force_appliance(body["name"], None if state in (None, "auto") else bool(state))
        elif action == "generator":
            gen = site.generator
            site.step()
            if "set" in body:
                for k, v in dict(body["set"]).items():
                    if k not in gen.block:
                        raise ApiError(400, f"generator: unknown 1901 field {k!r}")
                    gen.block[k] = v
            if "installed" in body:
                gen.hw.installed = bool(body["installed"])
            if body.get("start"):
                gen.start_manual()
            if body.get("stop"):
                gen.stop_manual()
            if "fault" in body:
                f = body["fault"]
                if f not in (None, "fail-start", "trip"):
                    raise ApiError(400, "generator.fault: fail-start | trip | null")
                gen.forced_fault = f
            if body.get("clear"):
                gen.clear_fault()
            if "refuel" in body:
                gen.refuel(None if body["refuel"] is None else float(body["refuel"]))
        elif action == "weather":
            if site.weather is None:
                raise ApiError(400, "weather: site uses the bell curve (set weather.model)")
            for key in ("sky", "flicker"):
                if key in body:
                    setattr(site.weather.cfg, key, body[key])
            if site.weather.cfg.sky not in SKIES:
                raise ApiError(400, f"weather: sky must be one of {sorted(SKIES)}")
        elif action == "export":
            site.set_export_limit_enforced(bool(body["enforced"]))
        elif action == "range-fault":
            if body.get("clear"):
                gw.faults.range_fault = None
            else:
                gw.faults.fail_range(body["start"], body["end"], body.get("mode", "exception"),
                                     body.get("code", 4), body.get("for_s"))
        elif action in ("faults", "dc-faults"):
            target = gw.faults if action == "faults" else gw.dc_faults
            known = {f.name for f in fields(Faults)}
            bad = set(body) - known
            if bad:
                raise ApiError(400, f"unknown faults {sorted(bad)}; known {sorted(known)}")
            for k, v in body.items():
                setattr(target, k, v)
        else:
            raise ApiError(404, f"unknown action {action!r}")
    except (KeyError, TypeError, ValueError) as e:
        raise ApiError(400, f"{action}: {e}") from None


class ControlApi:
    def __init__(self, gateways: dict[str, "Gateway"], host: str = "127.0.0.1", port: int = 8502):
        self.gateways = gateways
        self.host = host
        self.port = port
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

    def _route(self, method: str, path: str, body: dict):
        parts = [p for p in path.split("?")[0].split("/") if p]
        if parts == ["schema"] and method == "GET":
            return schema()
        if parts == ["gateways"] and method == "GET":
            return [{"id": g.config.id, "serial": g.serial, "address": list(g.address)}
                    for g in self.gateways.values()]
        if len(parts) >= 2 and parts[0] == "gateways":
            gw = self.gateways.get(parts[1])
            if gw is None:
                raise ApiError(404, f"no gateway {parts[1]!r}")
            if len(parts) == 2 and method == "GET":
                return gateway_state(gw)
            if len(parts) == 3 and method == "POST":
                apply(gw, parts[2], body)
                return gateway_state(gw)
        raise ApiError(404, f"{method} {path}: not found")

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.readline()
            method, path, _ = request.decode("latin-1").split(" ", 2)
            length = 0
            while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                name, _, value = line.decode("latin-1").partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
            raw = await reader.readexactly(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
                if not isinstance(body, dict):
                    raise ApiError(400, "body must be a JSON object")
                status, payload = 200, self._route(method.upper(), path, body)
            except ApiError as e:
                status, payload = e.status, {"error": str(e)}
            except json.JSONDecodeError as e:
                status, payload = 400, {"error": f"bad JSON: {e}"}
            if isinstance(payload, Raw):
                data, ctype = payload.body, payload.content_type
            else:
                data, ctype = json.dumps(payload, indent=1).encode(), "application/json"
            reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "Error")
            writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\n"
                         "Cache-Control: no-store\r\n"
                         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
            await writer.drain()
        except (ValueError, asyncio.IncompleteReadError, ConnectionError) as e:
            log.debug("control api: bad request: %s", e)
        finally:
            writer.close()
