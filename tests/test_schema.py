"""The control-action schema: the one description shared by the API, scenarios, docs and UI."""

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from franklinwh_emulator import control
from franklinwh_emulator.control import ApiError, apply
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.schema import ACTIONS

FLEET = {"gateways": [{
    "id": "site", "seed": 7, "ac_type": 2, "loads": "sydney-family",
    "location": {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"},
    "weather": {"model": "synthetic", "kwp": 6.6, "sky": "broken"},
    "generator": {"installed": True, "rated_w": 6000}, "directconnect": {"port": 0}}]}


@pytest.fixture(scope="module")
def fleet():
    f = Fleet.from_dict(FLEET)
    f.start(control_port=0)
    yield f
    f.stop()


@pytest.mark.parametrize("action,body", [(a, b) for a, spec in ACTIONS.items()
                                         for b in spec["examples"]])
def test_every_example_runs(fleet, action, body):
    body = dict(body)
    if action == "episode":       # same shape, short enough to finish before the fleet stops
        body = {k: 0.05 for k in body}
    fleet.control.apply_logged(fleet["site"], action, body)   # as the API does (dc-modbus too)
    if action == "episode":
        time.sleep(0.5)


def test_every_handler_is_in_the_schema():
    """Each action control.apply handles is described, and each described action is handled."""
    src = Path(control.__file__).read_text()
    body = src[src.index("def apply("):src.index("class ControlApi")]
    handled = set(re.findall(r'action == "([a-z-]+)"', body))
    for group in re.findall(r"action in \(([^)]*)\)", body):
        handled |= set(re.findall(r'"([a-z-]+)"', group))
    handled |= control.DC_ACTIONS                 # sent over Direct Connect by ControlApi.dc_write
    assert handled == set(ACTIONS)


def test_every_action_is_documented():
    for name, spec in ACTIONS.items():
        assert spec["doc"] and spec["examples"], name
        for key in spec.get("required", []):
            assert key in spec["fields"], (name, key)
        for ex in spec["examples"]:
            assert set(ex) <= set(spec["fields"]), (name, ex)


def test_unknown_action_is_404(fleet):
    with pytest.raises(ApiError) as e:
        apply(fleet["site"], "explode", {})
    assert e.value.status == 404


@pytest.mark.parametrize("action,body,text", [
    ("grid", {"hz": 51, "phse": "L2"}, "unknown keys ['phse']"),
    ("release", {"now": True}, "unknown keys ['now']"),
    ("circuit", {"n": 2}, "missing ['on']"),
    ("episode", {"down_s": 5}, "missing ['offgrid_s']"),
])
def test_bad_body_is_400_and_changes_nothing(fleet, action, body, text):
    gw = fleet["site"]
    before = control.gateway_state(gw)["utility"]
    with pytest.raises(ApiError) as e:
        apply(gw, action, body)
    assert e.value.status == 400 and text in str(e.value)
    assert control.gateway_state(gw)["utility"] == before


def test_schema_over_http(fleet):
    base = f"http://127.0.0.1:{fleet.control.port}"
    with urllib.request.urlopen(base + "/schema", timeout=3) as r:
        served = json.load(r)
    assert set(served["actions"]) == set(ACTIONS)
    assert served["actions"]["vpp"]["fields"]["watts"]["doc"].startswith("+ = charge, - = discharge")
    req = urllib.request.Request(base + "/gateways/site/grid", method="POST",
                                 data=json.dumps({"voltage": 230, "leg": "L1"}).encode())
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=3)
    assert e.value.code == 400


def test_fault_fields_follow_the_dataclass():
    from dataclasses import fields as dc_fields
    from franklinwh_emulator.server import Faults
    assert set(ACTIONS["faults"]["fields"]) == {f.name for f in dc_fields(Faults)}
    assert ACTIONS["faults"]["fields"]["reset_after"] == {"type": "integer", "nullable": True}


def test_mode_values_are_the_sites():
    from franklinwh_emulator.site import MODE_NAMES
    assert ACTIONS["mode"]["fields"]["mode"]["enum"] == list(MODE_NAMES)


def test_mode_labels_and_the_other_numbering():
    """15507 and Direct Connect / cloud number the modes differently; both are stated."""
    from franklinwh_emulator.schema import DC_MODE_NUMBERS
    from franklinwh_emulator.site import MODE_NAMES
    field = ACTIONS["mode"]["fields"]["mode"]
    assert field["labels"] == {str(k): v for k, v in MODE_NAMES.items()}
    assert field["labels"]["1"] == "Emergency Backup"            # 15507 = 1
    pytest.importorskip("franklinwh_local")
    from franklinwh_emulator.dc_payloads import PROGRAMMES
    assert DC_MODE_NUMBERS == {name: sched for _, (_, name, sched) in PROGRAMMES.items()}
    assert DC_MODE_NUMBERS["Time-of-Use"] == 1                    # scheduling_type = 1
    for name, n in DC_MODE_NUMBERS.items():
        assert f"{n} {name}" in ACTIONS["mode"]["doc"]


def test_every_boolean_says_what_true_and_false_do():
    """A bare "boolean" left the owner guessing (offgrid on=false, #30)."""
    bare = [(a, f) for a, spec in ACTIONS.items() for f, fs in spec["fields"].items()
            if fs["type"] == "boolean" and not fs.get("doc") and a not in ("faults", "dc-faults")]
    assert bare == []
