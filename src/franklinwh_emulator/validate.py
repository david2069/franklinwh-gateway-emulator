"""Fleet, scenario, session and recording files: what each key means, and checking a file
without running it (``franklinwh-emulator validate``, #200).

``CONFIG`` describes every gateway key (types and defaults come from ``GatewayConfig`` and
``SiteConfig``, so they can't drift; the docs are here). ``validate_fleet`` and
``validate_scenario`` build everything the run would build, except servers, and return every
problem found as ``{"path", "error"}`` instead of stopping at the first. The site is built at
the time the run would start (now, or the scenario's start), so an ``open-meteo`` gateway that
isn't offline fetches that day, as starting it would.
"""

from __future__ import annotations

from dataclasses import MISSING, fields
from pathlib import Path
import time
from typing import Any

from .clock import ManualClock
from .gateway import Gateway, GatewayConfig, fix_bool_keys, mock_serial
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
    "profile": "firmware profile (profiles.py): agate-v12r02b60d00-candidate (default), agate-v10r01b04d00, spec",
    "quirks": "overrides of the profile's switches",
    "time_scale": "site clock speed; > 1 fast-forwards",
    "dc_port": "Direct Connect port; 0 = ephemeral. aGate profiles always run one (see directconnect)",
    "setup": "factory (default: Modbus TCP off until a Direct Connect 1205 write) or in-service "
             "(already set up: Modbus TCP on)",
    "modbus_enabled": "start with the Modbus TCP Server on or off, whatever the setup",
    "events": "off | normal (default) | verbose, or {level, file}: what the gateway did (mode, battery status, trips, alarms, clients); file appends JSON lines",
    "commissioned": "commissioning date, YYYY-MM-DD (Direct Connect sno); default: the start day "
                    "(factory) or the reference site's 2024-07-25 (in-service), so in-service "
                    "gateways read the same sno unless each sets its own",
    "history_days": "in-service: run the model over this many days before start (max 105) to fill "
                    "the history, today's totals and the lifetime counters; ~0.2 s per day",
    "apower_units": "number of aPowers",
    "ac_type": "0 single phase, 1 split phase, 2 three phase",
    "phases": "legs wired, e.g. [L1, L2]; default from ac_type",
    "load_split": "share of home load per leg, e.g. {L1: 2, L2: 1}; default even",
    "gen_split": "share of aGate generation per leg; default even",
    "grid_code": "as4777-au-a, ieee1547-cat2, ieee1547-cat3, ausgrid; default from ac_type",
    "grid_overrides": "grid-code values to change, e.g. {es_delay_s: 5}",
    "location": "{latitude, longitude, timezone (IANA, DST-aware), postcode, name, country (ISO alpha-2), "
                "state, city, site_id}; country/state/city/site_id only group gateways in the UI",
    "tz_offset_hours": "fixed UTC offset, no DST; location.timezone wins",
    "native_mode": "Modbus 15507 numbering: 1 Emergency Backup, 2 Self-Consumption, 3 TOU "
                   "(Direct Connect and the cloud number them differently)",
    "self_reserve": "Self-Consumption reserve, %",
    "tou_reserve": "TOU reserve, %",
    "initial_soc": "starting state of charge, %; default derived from the seed",
    "solar_peak_w": "bell-curve solar peak, W; default derived from the seed",
    "home_base_w": "base house load, W; default derived from the seed",
    "weather": "{model: bell|synthetic|open-meteo, kwp, tilt, azimuth, sky}; inherits lat/lon",
    "loads": "house load: a preset name, a list of components, or {preset, add, remove, set}",
    "replay": "recorded solar/load to replay: a path, or {source, use, align, loop}",
    "circuits": "smart circuits: 1–2 on AU aGates, 1–3 on North American: {1: {name, on, soc_low_cutoff}}",
    "pv_inputs": "share of solar per PV input: proximal (aGate), remote1/remote2 (aPbox)",
    "offgrid_floor_soc": "islanded discharge floor, %",
    "tou_schedule": "TOU blocks (franklinwh-cloud format): name, startHourTime, endHourTime, "
                    "waveType, dispatchId; default: solar-charge off-peak, self-consumption 17-21",
    "tou_rates": "prices per tariff period, {waveType: {buy, sell}} in $/kWh (display only)",
    "cap_forced_solar": "forced solar (pv solar_w) can't exceed the solar model's value now, 0 at "
                        "night (default true); false = use it as given (lab tests)",
    "utility": "the grid operator's rules: {operator, export: allowed|limited|prohibited, export_limit_w, "
               "export_per_phase_w, import_limit_w, grid_charge, grid_charge_max_w, battery_export, export_rule_source}; enforced (PCS "
               "settings). Also accepted as dnsp (AU) or lines_company (NZ)",
    "dnsp": "AU name for utility (the same block)",
    "lines_company": "NZ name for utility (the same block)",
    "service": "the main breaker: {main_breaker_a}; import above it trips the site off the grid "
               "(inverse-time) until reset; the site also holds grid import below it",
    "install": "where the aGate/aPower sits: {indoor: heated|unheated|uninsulated} or {outdoor: {sun: "
               "morning|afternoon|all-day|shade, cover: none|roof|enclosure}}, plus cabinet_lag_h, "
               "self_heat_c_per_kw, room_c; drives the cabinet temperature. Default: ambient + 8 °C",
    "battery_temperature": "battery temperature limits (overrides of thermal.BATTERY_THERMAL: charge/"
                           "discharge taper, hot derate, heater, fan, OVER_TEMP); false = off; "
                           "default on when install is set",
    "pv_temperature": "PV output falls as panels heat up: {temp_coeff (per °C, -0.004), noct (°C, 45), "
                      "ref_cell_c (°C, 45: the 0.85 efficiency's typical warm day)}; false = off",
    "export_limit_enforced": "enforce the grid code's export limit (default: tracked only)",
    "battery": "loss model: agate-measured, ideal, legacy, or {preset, ...}",
    "generator": "{installed, rated_w, ...} plus 1901 fields; null = none fitted",
    "model": "hardware models (#180): {agate, apower}, each a code, SKU or name, e.g. {agate: 102, apower: 2}; the aPower model sets kWh, kW and charge kW per unit, Direct Connect 1102 serves the codes; default by region: AU/NZ 102 + 2, North America 103 + 0 (docs/sources-and-regions.md)",
    "pv_non_1547": "PV inputs whose inverters don't follow IEEE 1547-2018 (no Freq/Watt droop), e.g. [remote1]; off-grid, a North American aGate opens their relay instead, for 300 s at a time (#178)",
    "accessories": "accessory modules fitted (#189): {smart_circuits, generator_module, apbox}, each true or false; left out = fitted. Without smart_circuits there are no circuits (Direct Connect keeps the keys, zeroed); generator.installed needs generator_module; remote1/remote2 PV inputs need apbox",
    "grid_import_wh": "lifetime grid import counter at start, Wh; default 0 (factory) or the reference aGate's (in-service)",
    "grid_export_wh": "lifetime grid export counter at start, Wh; default 0 (factory) or the reference aGate's (in-service)",
    "pv_wh": "lifetime PV counter at start, Wh; default 0 (factory) or the reference aGate's (in-service)",
    "batt_discharge_wh": "lifetime battery discharge counter at start, Wh; default 0 (factory) or the reference aGate's (in-service)",
    "batt_charge_wh": "lifetime battery charge counter at start, Wh; default 0 (factory) or the reference aGate's (in-service)",
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
        "dnsp": {"type": "object", "doc": DOCS["dnsp"]},
        "lines_company": {"type": "object", "doc": DOCS["lines_company"]},
    }
    for f in fields(GatewayConfig):
        if f.name != "site":
            out[f.name] = _describe(f)
    for f in fields(SiteConfig):
        if f.name != "seed":
            out[f.name] = _describe(f)
    return out


def _unknown_key(key: Any, known) -> str:
    """"unknown key", the closest known key if one is close, and where every key is listed."""
    import difflib
    near = difflib.get_close_matches(str(key), list(known), n=1)
    hint = f"; did you mean {near[0]!r}?" if near else ""
    return f"unknown key{hint} (every key: docs/configuration.md, or GET /schema gateway_config)"


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
            out.append(_problem(f"{prefix}defaults.{k}", _unknown_key(k, known)))
    gws = d.get("gateways")
    if not isinstance(gws, list) or not gws:
        return out + [_problem(f"{prefix}gateways", "needs a list of at least one gateway")]
    ids: dict[str, int] = {}
    ports: dict[tuple, str] = {}
    serials: dict[str, int] = {}
    for i, g in enumerate(gws):
        path = f"{prefix}gateways[{i}]"
        if not isinstance(g, dict):
            out.append(_problem(path, "must be a mapping"))
            continue
        bad = [k for k in g if k not in known]
        out += [_problem(f"{path}.{k}", _unknown_key(k, known)) for k in bad]
        merged = {k: v for k, v in {**defaults, **g}.items() if k in known}
        try:
            cfg = GatewayConfig.from_dict(merged)
        except Exception as e:                       # noqa: BLE001 - reported, not raised
            out.append(_problem(path, e))
            continue
        rec = cfg.site.replay if cfg.site is not None else None
        rec_problems = check_recording(rec, prefix=f"{path}.replay")[0] if rec else []
        out += rec_problems
        if not rec_problems:                         # a bad recording would only fail again here
            try:
                Gateway(cfg, clock=ManualClock(time.time() if at is None else at))
            except Exception as e:                   # noqa: BLE001
                out.append(_problem(path, e))
        if cfg.id in ids:
            out.append(_problem(f"{path}.id", f"duplicate of gateways[{ids[cfg.id]}]"))
        ids.setdefault(cfg.id, i)
        # #162: one seed, one serial. Only a seed or serial that is set counts: gateways that
        # leave both out share seed 0, and refusing them would stop fleets that start today.
        what = "serial" if cfg.serial else "seed" if "seed" in merged else None
        if what:
            serial = cfg.serial or mock_serial(cfg.seed)
            if serial in serials:
                out.append(_problem(f"{path}.{what}", f"gives serial {serial}, as gateways[{serials[serial]}]; "
                                                      "clients keyed by serial would collide"))
            serials.setdefault(serial, i)
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


# -- recordings (replay sources) and sessions (#200) -------------------------------------------
# The recorded columns each ``use:`` entry plays from (replay.py).
USE_COLUMNS = {"solar": ("solar_w",), "load": ("home_w",), "ambient": ("ambient_temp_c",),
               "battery": ("battery_w",), "dispatch": ("battery_w", "soc"), "mode": ("mode_name",)}


def check_recording(cfg: Any, *, prefix: str = "replay") -> tuple[list[dict[str, str]], dict]:
    """Problems with a replay recording (a path, or a fleet's ``replay:`` mapping) and what it
    holds: rows, first and last time (UTC), span, the largest gap, gaps over ``max_gap`` and the
    columns with values. Every problem is reported, not only the first."""
    import datetime as dt
    from .replay import CANONICAL, STATE, ReplayConfig, load_rows
    try:
        rc = ReplayConfig.from_dict(cfg)
    except Exception as e:                           # noqa: BLE001
        return [_problem(prefix, e)], {}
    src = Path(rc.source).expanduser()
    if not src.is_file():
        return [_problem(f"{prefix}.source", f"no such file: {src}")], {}
    try:
        rows = load_rows(rc)
    except Exception as e:                           # noqa: BLE001
        hint = (" (CSV: a `timestamp` or `ts` column; SQLite: a `metrics` or `metrics_archive` "
                "table, as the Modbus bridge writes)")
        return [_problem(f"{prefix}.source", f"{e}{hint}")], {}
    ts = [r["ts"] for r in rows]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    present = [c for c in CANONICAL + STATE if any(r.get(c) is not None for r in rows)]
    iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")
    info = {"rows": len(rows), "first": iso(ts[0]), "last": iso(ts[-1]),
            "span_h": round((ts[-1] - ts[0]) / 3600, 1), "max_gap_s": round(max(gaps)),
            "gaps_over_max_gap": sum(g > rc.max_gap for g in gaps), "columns": present}
    out = []
    for use in rc.use:
        missing = [c for c in USE_COLUMNS[use] if c not in present]
        if missing:
            out.append(_problem(f"{prefix}.use", f"{use!r} plays {missing}, which has no values in "
                                                 f"{src.name}; columns with values: {present}"))
    if rc.columns:
        for canon in rc.columns:
            if canon not in CANONICAL + STATE + ("ts",):
                out.append(_problem(f"{prefix}.columns.{canon}",
                                    f"unknown column; known {['ts', *CANONICAL, *STATE]}"))
    return out, info


def validate_session(d: Any) -> list[dict[str, str]]:
    """Every problem in a saved session (sessions/<name>.yaml), or []."""
    if not isinstance(d, dict):
        return [_problem("session", "must be a mapping with `actions`")]
    d = fix_bool_keys(d)
    acts = d.get("actions")
    if not isinstance(acts, list):
        return [_problem("actions", "needs a list of {gateway, do, with}")]
    gws = d.get("gateways")
    out = []
    for i, a in enumerate(acts):
        path = f"actions[{i}]"
        if not isinstance(a, dict):
            out.append(_problem(path, "must be a mapping {gateway, do, with}"))
            continue
        bad = sorted(set(a) - {"gateway", "do", "with"})
        if bad:
            out.append(_problem(path, f"unknown keys {bad}; known ['do', 'gateway', 'with']"))
        if not a.get("gateway"):
            out.append(_problem(f"{path}.gateway", "missing"))
        elif isinstance(gws, list) and a["gateway"] not in gws:
            out.append(_problem(f"{path}.gateway", f"{a['gateway']!r} is not in this session's "
                                                   f"gateways {gws}"))
        do = a.get("do")
        if do not in ACTIONS:
            out.append(_problem(f"{path}.do", f"unknown action {do!r}; known {sorted(ACTIONS)}"))
            continue
        problem = check_body(do, dict(a.get("with") or {}))
        if problem:
            out.append(_problem(f"{path}.with", problem))
    return out


RECORDING_SUFFIXES = (".csv", ".db", ".sqlite", ".sqlite3")


def file_kind(path: Path, doc: Any = None) -> str:
    """fleet | scenario | session | recording | unknown, from the suffix and the top-level keys."""
    if path.suffix.lower() in RECORDING_SUFFIXES:
        return "recording"
    if not isinstance(doc, dict):
        return "unknown"
    if "actions" in doc:
        return "session"
    if "steps" in doc or "fleet" in doc:
        return "scenario"
    if "gateways" in doc:
        return "fleet"
    return "unknown"


def validate_file(path: str | Path) -> tuple[str, list[dict[str, str]], dict]:
    """(kind, problems, info) for one file: what ``franklinwh-emulator validate`` prints."""
    import yaml
    from .gateway import load_yaml
    p = Path(path).expanduser()
    if not p.is_file():
        return "unknown", [_problem("file", f"no such file: {p}")], {}
    if p.suffix.lower() in RECORDING_SUFFIXES:
        problems, info = check_recording(str(p), prefix="recording")
        return "recording", problems, info
    try:
        doc = load_yaml(p)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "file"
        return "unknown", [_problem(where, f"not valid YAML: {getattr(e, 'problem', e)}")], {}
    kind = file_kind(p, doc)
    if kind == "fleet":
        gws = doc.get("gateways")
        return kind, validate_fleet(doc), {"gateways": len(gws) if isinstance(gws, list) else 0}
    if kind == "scenario":
        return kind, validate_scenario(doc), {"steps": len(doc.get("steps") or [])}
    if kind == "session":
        return kind, validate_session(doc), {"actions": len(doc.get("actions") or [])}
    return kind, [_problem("file", "not a fleet (gateways:), scenario (steps:), session (actions:) "
                                    "or recording (.csv, .db, .sqlite)")], {}
