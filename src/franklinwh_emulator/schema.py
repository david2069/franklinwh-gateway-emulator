"""The control actions: one description that the control API, scenarios, docs and the UI share.

Each action names its body fields (type, unit, allowed values, sign), which of them are
required, and examples. ``control.apply`` dispatches only these actions and refuses keys they
don't list; ``GET /schema`` serves this table. Types are documentation: ``apply`` still coerces
values as before.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from .server import Faults
from .site import ALARMS, MODE_NAMES, PV_INPUTS
from .weather import SKIES

LEGS = ["L1", "L2", "L3"]
# Direct Connect / cloud number the same modes differently (1726 scheduling_type, cloud
# workMode; dc_payloads.PROGRAMMES): checked against PROGRAMMES in tests/test_schema.py.
DC_MODE_NUMBERS = {"Time-of-Use": 1, "Self-Consumption": 2, "Emergency Backup": 3}


def _f(type_: str | list[str], doc: str = "", *, unit: str | None = None,
       enum: list | None = None, nullable: bool = False,
       labels: dict | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": type_}
    if unit:
        d["unit"] = unit
    if enum is not None:
        d["enum"] = enum
    if labels:
        d["labels"] = {str(k): v for k, v in labels.items()}   # what each enum value means
    if nullable:
        d["nullable"] = True
    if doc:
        d["doc"] = doc
    return d


def _fault_fields() -> dict[str, dict]:
    """From the ``Faults`` dataclass, so a new fault is described as soon as it exists."""
    kinds = {"float": "number", "bool": "boolean", "int": "integer", "dict": "object"}
    out = {}
    for f in fields(Faults):
        ann = str(f.type)
        base = ann.split("|")[0].strip()
        out[f.name] = _f(kinds.get(base, "string"), nullable="None" in ann)
    return out


ACTIONS: dict[str, dict[str, Any]] = {
    "grid": {
        "doc": "Change the grid, all legs or one. restore: back to nominal and available.",
        "fields": {
            "available": _f("boolean", "false = outage"),
            "voltage": _f("number", unit="V"),
            "v_pu": _f("number", "voltage as a fraction of nominal"),
            "hz": _f("number", unit="Hz"),
            "hz_offset": _f("number", "offset from nominal", unit="Hz"),
            "phase": _f("string", "one leg only", enum=LEGS),
            "restore": _f("boolean", "true = every leg back to nominal and available"),
        },
        "examples": [{"available": False}, {"voltage": 195, "phase": "L2"}, {"hz": 51.2},
                     {"restore": True}],
    },
    "offgrid": {
        "doc": "User-requested islanding (Direct Connect 1723 offgridSet).",
        "fields": {"on": _f("boolean", "true = island now (trip reason \"requested\"); false = end the "
                                       "request and reconnect when the grid code allows")},
        "required": ["on"],
        "examples": [{"on": True}],
    },
    "range-fault": {
        "doc": "Fail Modbus requests touching registers start..end.",
        "fields": {
            "start": _f("integer", unit="register"),
            "end": _f("integer", unit="register"),
            "mode": _f("string", enum=["exception", "silent", "short"]),
            "code": _f("integer", "Modbus exception code"),
            "for_s": _f("number", "then clears by itself", unit="s"),
            "clear": _f("boolean", "true = remove it"),
        },
        "examples": [{"start": 15500, "end": 15509, "mode": "exception", "code": 4, "for_s": 30},
                     {"clear": True}],
    },
    "write-reject": {
        "doc": "Refuse writes to model.point: all, or raw values beyond a bound.",
        "fields": {
            "model": _f("integer", "SunSpec model id"),
            "point": _f("string"),
            "abs_gt": _f("integer", "refuse |raw| > this"),
            "gt": _f("integer", "refuse raw > this"),
            "lt": _f("integer", "refuse raw < this"),
            "code": _f("integer", "Modbus exception code"),
            "clear": _f("boolean", "true = remove it"),
        },
        "examples": [{"model": 704, "point": "WSetPct", "abs_gt": 100, "code": 3},
                     {"clear": True}],
    },
    "addressing": {
        "doc": "Base-address aliases served, and the bases writes are accepted at.",
        "fields": {
            "base_aliases": _f("array", "extra bases mirroring the map, e.g. [40000]"),
            "write_bases": _f("array", "null = all", nullable=True),
        },
        "examples": [{"base_aliases": [40000], "write_bases": [0]}],
    },
    "episode": {
        "doc": "Drop the Modbus server, then return islanded.",
        "fields": {"down_s": _f("number", unit="s"), "offgrid_s": _f("number", unit="s")},
        "required": ["down_s", "offgrid_s"],
        "examples": [{"down_s": 60, "offgrid_s": 120}],
    },
    "pv": {
        "doc": "Open or close a PV input's relay, or override solar output.",
        "fields": {
            "input": _f("string", enum=list(PV_INPUTS)),
            "relay": _f("boolean", "false = open (input drops to 0 W)"),
            "solar_w": _f("number", "null = back to the solar model", unit="W", nullable=True),
        },
        "examples": [{"input": "proximal", "relay": False}, {"solar_w": 3000},
                     {"solar_w": None}],
    },
    "load": {
        "doc": "Override the house load.",
        "fields": {"w": _f("number", "null = back to the load model", unit="W", nullable=True)},
        "required": ["w"],
        "examples": [{"w": 4500}, {"w": None}],
    },
    "ambient": {
        "doc": "Override the ambient temperature.",
        "fields": {"c": _f("number", "null = back to the weather", unit="°C", nullable=True)},
        "required": ["c"],
        "examples": [{"c": 48}, {"c": None}],
    },
    "alarms": {
        "doc": "Raise, clear or reset 701.Alrm bits; set the DC alarm bits.",
        "fields": {
            "set": _f("array", "alarm names", enum=list(ALARMS)),
            "latched": _f("boolean", "latched alarms clear only on reset"),
            "clear": _f(["array", "string"], 'alarm names, or "all"'),
            "reset": _f("boolean", "AlarmReset (715)"),
            "dc_bits": _f("integer"),
        },
        "examples": [{"set": ["OVER_TEMP"], "latched": True}, {"clear": ["OVER_TEMP"]},
                     {"clear": "all"}, {"reset": True}, {"dc_bits": 3}],
    },
    "faults": {
        "doc": "Modbus TCP Server fault injection.",
        "fields": _fault_fields(),
        "examples": [{"latency_s": 0.5, "session_held": True}],
    },
    "dc-faults": {
        "doc": "Direct Connect server fault injection.",
        "fields": _fault_fields(),
        "examples": [{"latency_s": 0.5}],
    },
    "export": {
        "doc": "Enforce the static export limit (default: declared, only tracked).",
        "fields": {"enforced": _f("boolean", "true = enforce the limit; false = only track breaches")},
        "required": ["enforced"],
        "examples": [{"enforced": True}],
    },
    "vpp": {
        "doc": "A VPP dispatch as franklinwh-modbus sends it: watts or pct_hw.",
        "fields": {
            "watts": _f("number", "+ = charge, - = discharge", unit="W"),
            "pct_hw": _f("number", "of the hardware rating", unit="%"),
        },
        "examples": [{"watts": -5000}, {"pct_hw": 100}],
    },
    "release": {
        "doc": "End the VPP dispatch.",
        "fields": {},
        "examples": [{}],
    },
    "mode": {
        "doc": "Native operating mode, in Modbus 15507 numbering. Direct Connect and the cloud "
               "number modes differently: " +
               ", ".join(f"{v} {k}" for k, v in DC_MODE_NUMBERS.items()) + ".",
        "fields": {"mode": _f("integer", "Modbus 15507 numbering", enum=list(MODE_NAMES),
                              labels=MODE_NAMES)},
        "required": ["mode"],
        "examples": [{"mode": 3}],
    },
    "reserve": {
        "doc": "Backup reserves.",
        "fields": {"self": _f("integer", "Self-Consumption reserve", unit="%"),
                   "tou": _f("integer", "TOU reserve", unit="%")},
        "examples": [{"self": 20, "tou": 40}],
    },
    "soc": {
        "doc": "Set the state of charge (scenario setup).",
        "fields": {"value": _f("number", unit="%")},
        "required": ["value"],
        "examples": [{"value": 55}],
    },
    "weather": {
        "doc": "Change the sky (synthetic and open-meteo weather only).",
        "fields": {"sky": _f("string", enum=sorted(SKIES)), "flicker": _f("boolean", "open-meteo: add sub-15-minute cloud edges")},
        "examples": [{"sky": "storm"}],
    },
    "circuit": {
        "doc": "Switch a smart circuit.",
        "fields": {"n": _f("integer", enum=[1, 2, 3]),
                   "on": _f("boolean", "true = circuit on; false = switched off")},
        "required": ["n", "on"],
        "examples": [{"n": 2, "on": False}],
    },
    "appliance": {
        "doc": "Force an appliance on or off, or hand it back to its schedule.",
        "fields": {"name": _f("string"),
                   "on": _f(["boolean", "string"], 'null or "auto" = its own schedule',
                            nullable=True)},
        "required": ["name"],
        "examples": [{"name": "ev", "on": True}, {"name": "ev", "on": None}],
    },
    "generator": {
        "doc": "The generator: 1901 settings, hardware, manual start/stop, faults, fuel.",
        "fields": {
            "set": _f("object", "1901 fields to change"),
            "installed": _f("boolean", "true = a generator is fitted"),
            "start": _f("boolean", "manual start"),
            "stop": _f("boolean", "manual stop"),
            "fault": _f("string", "null clears the forced fault", enum=["fail-start", "trip"],
                        nullable=True),
            "clear": _f("boolean", "clear a latched fault"),
            "refuel": _f("number", "null = full", unit="L", nullable=True),
        },
        "examples": [{"installed": True}, {"start": True}, {"stop": True},
                     {"fault": "trip"}, {"refuel": None}],
    },
    "der-comms": {
        "doc": "DER communications: Modbus TCP (1205 sunsMdEn) and CSIP.",
        "fields": {"modbus": _f("boolean", "false = stop the Modbus TCP Server (1205 sunsMdEn = 0), "
                                             "after ~3 s; true = start it"),
                   "csip": _f("boolean", "enable or disable the CSIP client")},
        "examples": [{"modbus": False}, {"modbus": True}, {"csip": True}],
    },
    "csip": {
        "doc": "The CSIP-AUS utility server: registration, availability, events, default.",
        "fields": {
            "enable": _f("boolean", "enable or disable the aGate's CSIP client"),
            "uri": _f("string"),
            "pin": _f("string"),
            "server_up": _f("boolean", "false = the utility server is unreachable (comms loss)"),
            "control": _f("object", "DERControl, e.g. {opModExpLimW: 1500}"),
            "duration_s": _f("number", unit="s", nullable=True),
            "default": _f("object", "DefaultDERControl"),
            "clear": _f("boolean", "clear all events"),
        },
        "examples": [{"enable": True}, {"server_up": False},
                     {"control": {"opModExpLimW": 1500}, "duration_s": 1800},
                     {"default": {"opModExpLimW": 5000}}, {"clear": True}],
    },
}


# How the UI and docs group the actions, in display order.
GROUPS: dict[str, list[str]] = {
    "Grid and phases": ["grid", "offgrid"],
    "Inputs": ["pv", "load", "ambient", "weather"],
    "Battery and modes": ["vpp", "release", "mode", "reserve", "soc"],
    "Loads": ["circuit", "appliance"],
    "Generator": ["generator"],
    "CSIP and comms": ["csip", "der-comms", "export"],
    "Alarms and faults": ["alarms", "faults", "dc-faults", "range-fault", "write-reject",
                          "addressing", "episode"],
}
for _group, _names in GROUPS.items():
    for _name in _names:
        ACTIONS[_name]["group"] = _group


def check_body(action: str, body: dict) -> str | None:
    """The problem with ``body`` for ``action``, or None. Unknown actions are the caller's."""
    spec = ACTIONS[action]
    unknown = sorted(set(body) - set(spec["fields"]))
    if unknown:
        return f"{action}: unknown keys {unknown}; known {sorted(spec['fields'])}"
    missing = [k for k in spec.get("required", []) if k not in body]
    if missing:
        return f"{action}: missing {missing}"
    return None


def schema() -> dict:
    """What ``GET /schema`` serves: the actions, and the keys a fleet's gateway may hold."""
    from .validate import config_schema
    return {"actions": ACTIONS, "groups": GROUPS, "gateway_config": config_schema()}
