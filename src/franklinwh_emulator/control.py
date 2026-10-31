"""HTTP control API for steering running gateways (stdlib only, JSON in/out).

    GET  /version                       the emulator's version (and commit, from a git checkout)
    GET  /schema                        every action, its fields and examples (schema.py)
    GET  /log                           every action applied, oldest first (also failed ones)
    GET  /monitor                       clients per gateway and overall; the process's CPU, memory, threads
    GET  /sessions                      saved sessions in <workspace>/sessions
    POST /session/save                  {"name": "evening-test"} (optional): write the session
    POST /session/restore               {"name": "evening-test"}: replay its actions
    GET  /gateways                      list gateways
    GET  /gateways/<id>                 live state
    GET  /gateways/<id>/events          what the gateway did (?since=<rev>&limit=<n>), oldest first
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

from .monitor import ProcessMonitor
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


import datetime as _dt
_STARTED = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")   # this process


def version_info() -> dict:
    """Package version, the short commit when running from a git checkout, and when this
    process started (so a page can tell the emulator restarted)."""
    import subprocess
    from pathlib import Path
    from . import __version__
    commit = None
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists():
        try:
            commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                    capture_output=True, text=True, timeout=2).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            commit = None
    return {"version": __version__, "commit": commit, "started": _STARTED}


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
        "firmware": gw.profile.firmware + (" (candidate)" if gw.profile.name.endswith("-candidate") else "")
                    if gw.profile.name != "spec" else "SunSpec DER (spec)",
        "active_reserve": site.active_reserve(),          # the reserve held now, by mode
        "tou": site.tou_now(snap.ts),
        "offgrid_floor_soc": site.config.offgrid_floor_soc,
        "native_mode_name": MODE_NAMES.get(snap.native_mode, str(snap.native_mode)),
        "run_status_name": RUN_NAMES.get(snap.run_status, str(snap.run_status)),
        "inverter_state_name": INV_NAMES.get(snap.inverter_state, str(snap.inverter_state)),
        "sun": _sun(site, snap.ts),
        # Values forced through the pv / load / ambient actions; null = the model's own.
        "overrides": {"solar_w": site.solar_override_w, "load_w": site.load_override_w,
                      "ambient_c": site.ambient_override_c},
        "cap_forced_solar": site.config.cap_forced_solar,
        "pv_temperature": _pv_temperature(site, snap.ts),
        "battery_temperature": _battery_temperature(site),
        # The grid operator's rules and the main breaker (#123).
        "utility_rules": None if site.utility_rules is None else {
            **asdict(site.utility_rules),
            "export_limit_in_force_w": site.utility_rules.export_limit(len(site.phases))},
        "import_cap_w": site.import_cap_w(),
        "import_clipped_w": round(site.import_clipped_w, 1),
        "service": None if site.service is None else {
            "main_breaker_a": site.service.main_breaker_a,
            "rating_w": round(site.service.rating_w(site.grid.nominal_v_ln, len(site.phases))),
            "trip_progress": round(site.breaker_progress, 3), "open": site.main_breaker_open},
        # Placement and the temperature around the cabinet (#110); None = the original estimate.
        "install": None if site.install is None else {
            "label": site.install.label, "surroundings_c": round(site.surroundings_c(snap.ts), 1)},
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
        "apower_off": sorted(site.apower_off),                # switched off at the unit (#71)
        "apower_rating": {"wh": WH_PER_APOWER, "w": W_PER_APOWER},   # per aPower, emulator constants
        "forced_alarms": dict(site.forced_alarms),
        "remote_control": asdict(site.remote),
        # FORCE = an M704 setpoint is active (#91). Direct Connect then reports 1707 programme 9,
        # which the app shows as "VPP Mode"; a FranklinWH-server VPP isn't modelled.
        "force": site.force_status(),
        "self_reserve": site.self_reserve,
        "tou_reserve": site.tou_reserve,
        "weather": None if site.weather is None else {
            "model": site.weather.cfg.model, "sky": site.weather.cfg.sky,
            "kwp": site.weather.kwp, "poa_w_m2": round(site.weather.poa(snap.ts), 1)},
        "generator": site.generator.status(),
        "faults": asdict(gw.faults),
        "dc_faults": asdict(gw.dc_faults),
        "modbus_enabled": gw.modbus_enabled,
        "modbus_listening": gw.server._server is not None,   # :502 open now (lags a 1205 write ~3 s)
        "rebooting": gw.rebooting,                          # both listeners down for a 1721 reboot
        "episode_phase": gw.episode_phase,
        "debug_mode": gw.debug_mode,              # Debug Mode (DC 1821): battery stopped
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


def _battery_temperature(site) -> dict | None:
    """The temperature limits on the battery now (#111); None when they're off."""
    if site.batt_temp is None:
        return None
    t = site.battery_temp_c()
    fd, fc = site.battery_temperature_factors()
    derated = None
    if fd < 1 or fc < 1:
        derated = "hot" if t >= site.batt_temp["hot_derate_c"] else "cold"
    from .thermal import aux_load_w
    return {"battery_c": round(t, 1), "discharge_factor": round(fd, 3), "charge_factor": round(fc, 3),
            "derated": derated, "aux_w": aux_load_w(t, site.batt_temp, site.apower_units_on()),
            "over_temp_auto": site.over_temp_auto}


def _pv_temperature(site, ts: float) -> dict | None:
    """The panel-temperature loss now (#109); None when it's off."""
    if site.pv_temp is None:
        return None
    cell, factor = site.pv_temperature_factor(ts)
    return {"cell_c": None if cell is None else round(cell, 1), "factor": round(factor, 3), **site.pv_temp}


def _num_or_none(body: dict, key: str):
    v = body[key]
    return None if v is None else float(v)


def apply(gw: "Gateway", action: str, body: dict) -> None:
    if action not in ACTIONS:
        raise ApiError(404, f"unknown action {action!r}")
    problem = check_body(action, body)
    if problem:
        raise ApiError(400, problem)
    if action in DC_ACTIONS:                   # network writes: ControlApi.dc_write sends them
        raise ApiError(400, f"{action}: sent over Direct Connect by the control API "
                            f"(POST /gateways/<id>/{action}), not applied in-process")
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
        elif action == "utility":
            if body.get("clear"):
                site.set_utility(None)
            else:
                site.set_utility({k: v for k, v in body.items() if k != "clear"})
        elif action == "service":
            if body.get("reset"):
                site.reset_main_breaker()
        elif action == "debug-mode":
            gw.set_debug_mode(bool(body["on"]))
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
                               revert_effective=gw.profile.reversion_effective,
                               revert_inverted=gw.profile.reversion_inverted)
        elif action == "release":
            site.update_remote(enabled=False, pct_hw=0.0, watts=0)
        elif action == "mode":
            site.set_native_mode(int(body["mode"]))
        elif action == "tou":
            if body.get("reset"):
                site.set_tou_schedule(None, {})
            if "schedule" in body or "rates" in body:
                site.set_tou_schedule(body["schedule"] if "schedule" in body else False,
                                      body.get("rates"))
        elif action == "reserve":
            site.set_reserves(self_reserve=body.get("self"), tou_reserve=body.get("tou"))
        elif action == "soc":
            with site.lock:
                site.step()
                site.soc = max(0.0, min(100.0, float(body["value"])))
        elif action == "circuit":
            site.set_circuit(int(body["n"]), bool(body["on"]))
        elif action == "apower":
            site.set_apower(int(body["n"]), bool(body["on"]))
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
            if "rated_w" in body:
                gen.block["genRatedPower"] = int(body["rated_w"])
            if "installed" in body:
                if body["installed"]:
                    gen.fit()                     # as installed: true in a fleet file
                else:
                    gen.hw.installed = False
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
                raise ApiError(400, "weather: this gateway has no weather model (it uses the bell "
                                    "curve): add `weather: {model: synthetic}` to its fleet entry "
                                    "and restart")
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
        msg = str(e)                       # already names the action ("tou block 2: …"): as is
        raise ApiError(400, msg if msg.startswith((f"{action}:", f"{action} ")) else f"{action}: {msg}") from None


# Actions not replayed by a restore: they are events, not settings.
TRANSIENT = {"episode", "dc-reboot"}      # not replayed on restore
DC_ACTIONS = {"dc-modbus", "dc-reboot"}    # sent over the gateway's Direct Connect port
LOG_MAX = 10_000
_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class ControlApi:
    def __init__(self, gateways: dict[str, "Gateway"], host: str = "127.0.0.1", port: int = 8502,
                 *, workspace: str = "emulator-workspace", fleet_file: str | None = None):
        self.gateways = gateways
        self.host = host
        self.port = port
        self.workspace = workspace
        self.fleet_file = fleet_file
        self.log: list[dict] = []
        self._server: asyncio.base_events.Server | None = None
        self._version: dict | None = None
        self.process = ProcessMonitor()
        self._rates: dict[tuple[str, str], tuple[float, int]] = {}   # (gateway, listener) -> (t, requests)

    async def start(self) -> tuple[str, int]:
        self.process.start()
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.host, self.port

    async def stop(self) -> None:
        await self.process.stop()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # -- monitor (#141) ------------------------------------------------------------------
    def _rate(self, gw_id: str, name: str, requests: int) -> float:
        """Requests per second on one listener since the previous /monitor read."""
        import time
        now = time.monotonic()
        t0, n0 = self._rates.get((gw_id, name), (now, requests))
        self._rates[(gw_id, name)] = (now, requests)
        return round((requests - n0) / (now - t0), 2) if now > t0 else 0.0

    @staticmethod
    def events(gw: "Gateway", path: str) -> dict:
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(path).query)
        try:
            since = int(q.get("since", ["0"])[0])
            limit = int(q.get("limit", ["200"])[0])
        except ValueError:
            raise ApiError(400, "events: since and limit are integers") from None
        ev = gw.events
        return {"level": ev.level, "dropped": ev.dropped, "events": ev.recent(limit, since)}

    def monitor(self) -> dict:
        rows, conns, refused, rate, accepted = [], 0, 0, 0.0, 0
        for gid, gw in self.gateways.items():
            row = {"id": gid}
            for name, srv, listening in (("modbus", gw.server, gw.server._server is not None),
                                         ("dc", gw.dc, gw.dc is not None and gw.dc._server is not None)):
                if srv is None:
                    row[name] = None
                    continue
                snap = srv.clients.snapshot()
                snap.update(listening=listening, port=srv.port,
                            requests_per_s=self._rate(gid, name, snap["requests"]))
                conns += len(snap["clients"])
                accepted += snap["accepted"]
                refused += snap["refused_1h"]
                rate += snap["requests_per_s"]
                row[name] = snap
            rows.append(row)
        active = sum(1 for r in rows if (r["modbus"] and r["modbus"]["clients"]) or (r["dc"] and r["dc"]["clients"]))
        return {"process": self.process.snapshot(),
                "totals": {"gateways": len(rows), "with_clients": active, "connections": conns,
                           "accepted": accepted, "refused_1h": refused, "requests_per_s": round(rate, 2)},
                "gateways": rows}

    # -- action log, sessions ------------------------------------------------------------
    def apply_logged(self, gw: "Gateway", action: str, body: dict, source: str = "api") -> None:
        """``apply``, recording what was asked and what happened."""
        import datetime as dt
        entry = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 "site_time": gw.site.local_datetime(gw.site.clock.now()).isoformat(timespec="seconds"),
                 "gateway": gw.config.id, "action": action, "body": body, "source": source,
                 "ok": True, "error": None}
        try:
            if action in DC_ACTIONS:
                self.dc_write(gw, action, body)
            else:
                apply(gw, action, body)
        except ApiError as e:
            entry.update(ok=False, error=str(e))
            raise
        finally:
            self.log.append(entry)
            del self.log[:-LOG_MAX]

    @staticmethod
    def dc_write(gw: "Gateway", action: str, body: dict) -> None:
        """Send a Direct Connect write to the gateway's own listener, as an installer's tool
        would: dc-modbus = 1205 sunsMdEn, dc-reboot = 1721 reboot. Blocking: call it off the
        event loop that serves the gateway (``_client`` and the CLI's restore use a worker
        thread)."""
        problem = check_body(action, body)
        if problem:
            raise ApiError(400, problem)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(f"{action} blocks: call it from a worker thread, not the event loop")
        if gw.dc_address is None:
            raise ApiError(400, f"{action}: {gw.config.id} has no Direct Connect listener")
        from franklinwh_direct_connect_api.client import DirectConnectClient
        try:
            with DirectConnectClient(*gw.dc_address, timeout=5, retries=0) as c:
                c.login()
                if action == "dc-modbus":
                    c.set_der_comms(sunspec_modbus=bool(body["on"]))
                else:                          # the aGate drops the socket: the library's "dropped"
                    c.reboot()
        except Exception as e:                 # the library's transport errors, refusals
            raise ApiError(400, f"{action}: the Direct Connect write failed: {e}") from None

    def _sessions_dir(self):
        from pathlib import Path
        return Path(self.workspace) / "sessions"

    def sessions(self) -> list[dict]:
        d = self._sessions_dir()
        if not d.is_dir():
            return []
        return [{"name": p.stem, "saved": p.stat().st_mtime}
                for p in sorted(d.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)]

    @staticmethod
    def _check_name(name: str) -> str:
        if not name or len(name) > 64 or set(name) - _NAME_OK:
            raise ApiError(400, "session name: letters, digits, - and _ only (max 64)")
        return name

    def save_session(self, name: str | None = None) -> dict:
        """Write the successful, non-transient actions so far, then each gateway's SoC."""
        import datetime as dt
        import yaml
        now = dt.datetime.now(dt.timezone.utc)
        name = self._check_name(name or now.strftime("session-%Y%m%d-%H%M%S"))
        actions = [{"gateway": e["gateway"], "do": e["action"], "with": e["body"]}
                   for e in self.log if e["ok"] and e["action"] not in TRANSIENT]
        for gid, gw in self.gateways.items():
            actions.append({"gateway": gid, "do": "soc",
                            "with": {"value": round(gateway_state(gw)["soc"], 1)}})
        doc = {"saved": now.isoformat(timespec="seconds"), "emulator": version_info(),
               "fleet": self.fleet_file, "gateways": sorted(self.gateways), "actions": actions}
        d = self._sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.yaml"
        path.write_text("# franklinwh-emulator session: the actions applied, in order, then each "
                        "gateway's SoC.\n# Restore with the UI's Restore, POST /session/restore, or "
                        "`ui FLEET --restore NAME`.\n" + yaml.safe_dump(doc, sort_keys=False))
        return {"name": name, "path": str(path), "actions": len(actions)}

    def restore_session(self, name: str) -> dict:
        """Replay a saved session's actions in order; returns each one's result."""
        import yaml
        path = self._sessions_dir() / f"{self._check_name(name)}.yaml"
        if not path.is_file():
            raise ApiError(404, f"no session {name!r}")
        from .gateway import fix_bool_keys            # a hand-written bare on:/off: key, as fleet files
        doc = fix_bool_keys(yaml.safe_load(path.read_text())) or {}
        results = []
        for a in doc.get("actions", []):
            gw = self.gateways.get(a.get("gateway"))
            if gw is None:
                results.append({**a, "ok": False, "error": f"no gateway {a.get('gateway')!r} in this fleet"})
                continue
            try:
                self.apply_logged(gw, a["do"], dict(a.get("with") or {}), source=f"restore:{name}")
                results.append({**a, "ok": True, "error": None})
            except ApiError as e:
                results.append({**a, "ok": False, "error": str(e)})
        return {"name": name, "applied": sum(r["ok"] for r in results),
                "failed": sum(not r["ok"] for r in results), "results": results}

    def _route(self, method: str, path: str, body: dict):
        parts = [p for p in path.split("?")[0].split("/") if p]
        if parts == ["version"] and method == "GET":
            if self._version is None:
                self._version = version_info()
            return self._version
        if parts == ["log"] and method == "GET":
            return self.log
        if parts == ["monitor"] and method == "GET":
            return self.monitor()
        if parts == ["sessions"] and method == "GET":
            return self.sessions()
        if parts == ["session", "save"] and method == "POST":
            return self.save_session(body.get("name"))
        if parts == ["session", "restore"] and method == "POST":
            if "name" not in body:
                raise ApiError(400, "restore: needs a session name")
            return self.restore_session(body["name"])
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
            if parts[2:] == ["events"] and method == "GET":
                return self.events(gw, path)
            if len(parts) == 3 and method == "POST":
                self.apply_logged(gw, parts[2], body)
                return gateway_state(gw)
        raise ApiError(404, f"{method} {path}: not found")

    @staticmethod
    def _blocking(method: str, path: str) -> bool:
        """Requests that may send Direct Connect frames to a gateway served by this event
        loop, so must run in a worker thread: the DC_ACTIONS, and a restore that may replay one."""
        p = path.split("?")[0].rstrip("/")
        return method == "POST" and (p.rsplit("/", 1)[-1] in DC_ACTIONS or p == "/session/restore")

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
                if self._blocking(method.upper(), path):     # sends Direct Connect frames
                    payload = await asyncio.to_thread(self._route, method.upper(), path, body)
                else:
                    payload = self._route(method.upper(), path, body)
                status = 200
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
