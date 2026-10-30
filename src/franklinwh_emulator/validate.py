"""Fleet and scenario files: what each key means, and checking a file without running it.

``CONFIG`` describes every gateway key (types and defaults come from ``GatewayConfig`` and
``SiteConfig``, so they can't drift; the docs are here). ``validate_fleet`` and
``validate_scenario`` build everything the run would build, except servers, and return every
problem found as ``{"path", "error"}`` instead of stopping at the first. The site is built at
the time the run would start (now, or the scenario's start), so an ``open-meteo`` gateway that
isn't offline fetches that day, as starting it would.
"""

from __future__ import annotations

from dataclasses import MISSING, fields
import time
from typing import Any

from .clock import ManualClock
from .gateway import Gateway, GatewayConfig, fix_bool_keys
from .schema import ACTIONS, check_body
from .site import SiteConfig

FLEET_KEYS = ("defaults", "gateways")

# One line per gateway key. Shorthands first, then GatewayConfig, then SiteConfig.
DOCS: dict[str, str] = {
    "modbus": "shorthand for host/port: {port: 5020, host: ...}",
    "directconnect": "Direct Connect (TCP 9000) frontend: {port: 9020}; 0 = ephemeral",
    "id": "gateway id, unique in the fleet; the control API's <id>",
    "seed": "seeds the serial and the site's derived values",
    "serial": "20-character serial; default derived from the seed",
    "host": "address the Modbus TCP Server binds to",
    "port": "Modbus TCP Server port; 0 = ephemeral",
    "profile": "firmware profile (profiles.py), e.g. agate-v10r01b04d00",
    "quirks": "overrides of the profile's switches",
    "time_scale": "site clock speed; > 1 fast-forwards",
    "dc_port": "Direct Connect port; null = off, 0 = ephemeral (see directconnect)",
    "apower_units": "number of aPowers",
    "ac_type": "0 single phase, 1 split phase, 2 three phase",
    "phases": "legs wired, e.g. [L1, L2]; default from ac_type",
    "load_split": "share of home load per leg, e.g. {L1: 2, L2: 1}; default even",
    "gen_split": "share of aGate generation per leg; default even",
    "grid_code": "as4777-au-a, ieee1547-cat2, ieee1547-cat3, ausgrid; default from ac_type",
    "grid_overrides": "grid-code values to change, e.g. {es_delay_s: 5}",
    "location": "{latitude, longitude, timezone (IANA, DST-aware), postcode}",
    "tz_offset_hours": "fixed UTC offset, no DST; location.timezone wins",
    "native_mode": "1 Emergency Backup, 2 Self-Consumption, 3 TOU",
    "self_reserve": "Self-Consumption reserve, %",
    "tou_reserve": "TOU reserve, %",
    "initial_soc": "starting state of charge, %; default derived from the seed",
    "solar_peak_w": "bell-curve solar peak, W; default derived from the seed",
    "home_base_w": "base house load, W; default derived from the seed",
    "weather": "{model: bell|synthetic|open-meteo, kwp, tilt, azimuth, sky}; inherits lat/lon",
    "loads": "house load: a preset name, a list of components, or {preset, add, remove, set}",
    "replay": "recorded solar/load to replay: a path, or {source, use, align, loop}",
    "circuits": "smart circuits 1-3: {1: {name, on, soc_low_cutoff}}",
    "pv_inputs": "share of solar per PV input: proximal (aGate), remote1/remote2 (aPbox)",
    "offgrid_floor_soc": "islanded discharge floor, %",
    "export_limit_enforced": "enforce the grid code's export limit (default: tracked only)",
    "battery": "loss model: agate-measured, ideal, legacy, or {preset, ...}",
    "generator": "{installed, rated_w, ...} plus 1901 fields; null = none fitted",
    "grid_import_wh": "lifetime grid import counter at start, Wh",
    "grid_export_wh": "lifetime grid export counter at start, Wh",
    "pv_wh": "lifetime PV counter at start, Wh",
    "batt_discharge_wh": "lifetime battery discharge counter at start, Wh",
    "batt_charge_wh": "lifetime battery charge counter at start, Wh",
}

_KINDS = {"int": "integer", "float": "number", "str": "string", "bool": "boolean",
          "dict": "object", "list": "array", "Any": "any"}


def _describe(f) -> dict[str, Any]:
    ann = str(f.type)
    parts = [p.strip() for p in ann.split("|")]
    base = parts[0].split("[")[0]
    d: dict[str, Any] = {"type": _KINDS.get(base, "any")}
    if "None" in parts[1:]:
        d["nullable"] = True
    if f.default is not MISSING:
        d["default"] = f.default
    elif f.default_factory is not MISSING:
        d["default"] = f.default_factory()
    d["doc"] = DOCS.get(f.name, "")
    return d


def config_schema() -> dict[str, dict]:
    """Every key a gateway entry (or ``defaults``) may hold."""
    out: dict[str, dict] = {
        "modbus": {"type": "object", "doc": DOCS["modbus"]},
        "directconnect": {"type": "object", "doc": DOCS["directconnect"]},
    }
    for f in fields(GatewayConfig):
        if f.name != "site":
            out[f.name] = _describe(f)
    for f in fields(SiteConfig):
        if f.name != "seed":
            out[f.name] = _describe(f)
    return out


def _problem(path: str, error: Any) -> dict[str, str]:
    return {"path": path, "error": str(error)}


def validate_fleet(d: Any, *, prefix: str = "", at: float | None = None) -> list[dict[str, str]]:
    """Every problem in a fleet file (as loaded), or []. ``at``: when the run starts (epoch s)."""
    if not isinstance(d, dict):
        return [_problem(prefix or "fleet", "must be a mapping with `gateways`")]
    d = fix_bool_keys(d)
    out = [_problem(f"{prefix}{k}", f"unknown key; known {list(FLEET_KEYS)}")
           for k in d if k not in FLEET_KEYS]
    known = config_schema()
    defaults = d.get("defaults") or {}
    if not isinstance(defaults, dict):
        out.append(_problem(f"{prefix}defaults", "must be a mapping"))
        defaults = {}
    for k in defaults:
        if k not in known:
            out.append(_problem(f"{prefix}defaults.{k}", "unknown key"))
    gws = d.get("gateways")
    if not isinstance(gws, list) or not gws:
        return out + [_problem(f"{prefix}gateways", "needs a list of at least one gateway")]
    ids: dict[str, int] = {}
    ports: dict[tuple, str] = {}
    for i, g in enumerate(gws):
        path = f"{prefix}gateways[{i}]"
        if not isinstance(g, dict):
            out.append(_problem(path, "must be a mapping"))
            continue
        bad = [k for k in g if k not in known]
        out += [_problem(f"{path}.{k}", "unknown key") for k in bad]
        merged = {k: v for k, v in {**defaults, **g}.items() if k in known}
        try:
            cfg = GatewayConfig.from_dict(merged)
        except Exception as e:                       # noqa: BLE001 - reported, not raised
            out.append(_problem(path, e))
            continue
        try:
            Gateway(cfg, clock=ManualClock(time.time() if at is None else at))
        except Exception as e:                       # noqa: BLE001
            out.append(_problem(path, e))
        if cfg.id in ids:
            out.append(_problem(f"{path}.id", f"duplicate of gateways[{ids[cfg.id]}]"))
        ids.setdefault(cfg.id, i)
        for kind, port in (("modbus", cfg.port), ("directconnect", cfg.dc_port)):
            if port:
                key = (cfg.host, port)
                if key in ports:
                    out.append(_problem(f"{path}.{kind}", f"port {port} already used by {ports[key]}"))
                ports.setdefault(key, f"{cfg.id} ({kind})")
    return out


def validate_scenario(d: Any) -> list[dict[str, str]]:
    """Every problem in a scenario file (as loaded), or []."""
    from .scenario import Scenario
    if not isinstance(d, dict):
        return [_problem("scenario", "must be a mapping")]
    d = fix_bool_keys(d)
    out: list[dict[str, str]] = []
    try:
        sc = Scenario.from_dict(d)
    except Exception as e:                           # noqa: BLE001
        return [_problem("scenario", e)] + validate_fleet(d.get("fleet"), prefix="fleet.")
    try:
        at = sc.start_epoch()
    except Exception as e:                           # noqa: BLE001
        out.append(_problem("start", e))
        at = None
    out += validate_fleet(sc.fleet, prefix="fleet.", at=at)
    gw_ids = {str(g.get("id", "agate")) for g in sc.fleet.get("gateways") or []
              if isinstance(g, dict)}
    for i, step in enumerate(d.get("steps", [])):
        path = f"steps[{i}]"
        if step.get("gateway") is not None and str(step["gateway"]) not in gw_ids:
            out.append(_problem(f"{path}.gateway", f"no gateway {step['gateway']!r}"))
        do = step.get("do")
        if do is None:
            continue
        if do not in ACTIONS:
            out.append(_problem(f"{path}.do", f"unknown action {do!r}"))
            continue
        problem = check_body(do, dict(step.get("with") or {}))
        if problem:
            out.append(_problem(f"{path}.with", problem))
    return out
