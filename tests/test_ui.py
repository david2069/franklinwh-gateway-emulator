"""The UI server: pages and static files beside the control API, on one loopback port."""

import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from franklinwh_emulator.cli import main
from franklinwh_emulator.control import gateway_state
from franklinwh_emulator.gateway import Fleet
from franklinwh_emulator.ui.server import STATIC

ALPINE_SHA256 = "358d9afbb1ab5befa2f48061a30776e5bcd7707f410a606ba985f98bc3b1c034"  # upstream 3.14.1

FLEET = {"gateways": [
    {"id": "site", "seed": 7, "loads": "sydney-family", "dc_port": 0,
     "location": {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"},
     "weather": {"model": "synthetic", "kwp": 6.6, "sky": "broken"}},
    {"id": "us-split", "seed": 42, "ac_type": 1}]}


@pytest.fixture(scope="module")
def ui():
    fleet = Fleet.from_dict(FLEET)
    fleet.start(control_port=0, ui=True)
    yield fleet, f"http://127.0.0.1:{fleet.control.port}"
    fleet.stop()


def get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, r.headers["Content-Type"], r.read()


def test_page_and_its_files(ui):
    _, base = ui
    status, ctype, body = get(base + "/")
    assert status == 200 and ctype.startswith("text/html")
    page = body.decode()
    refs = re.findall(r'(?:src|href)="/static/([^"]+)"', page)
    assert set(refs) == {"style.css", "app.js", "diagram.js", "actions.js", "alpinejs-3.14.1.min.js"}
    for name in refs:
        status, ctype, _ = get(f"{base}/static/{name}")
        assert status == 200
        assert ctype.split(";")[0] == {"css": "text/css", "js": "text/javascript"}[name.rsplit(".", 1)[1]]


@pytest.mark.parametrize("path", ["/static/../server.py", "/static/server.py", "/static/%2e%2e/server.py",
                                  "/static/", "/static/nope.js", "/server.py"])
def test_only_shipped_files_are_served(ui, path):
    _, base = ui
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + path)
    assert e.value.code == 404


def test_control_api_on_the_same_port(ui):
    _, base = ui
    _, ctype, body = get(base + "/gateways")
    assert ctype == "application/json" and [g["id"] for g in json.loads(body)] == ["site", "us-split"]
    req = urllib.request.Request(base + "/gateways/site/load", method="POST",
                                 data=json.dumps({"w": 1234}).encode())
    with urllib.request.urlopen(req, timeout=3) as r:
        assert json.load(r)["load_w"] == pytest.approx(1234, abs=1)
    assert "actions" in json.loads(get(base + "/schema")[2])


def test_vendored_alpine_is_upstream():
    data = (STATIC / "alpinejs-3.14.1.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == ALPINE_SHA256


def test_page_reads_only_fields_the_state_has(ui):
    """Every g.<field> / current.<field> the page uses exists in the control API's state."""
    fleet, _ = ui
    state = gateway_state(fleet["site"])
    text = "".join((STATIC / n).read_text() for n in ("index.html", "app.js", "diagram.js", "actions.js"))
    used = set(re.findall(r"\b(?:g|current)\.([a-z_]+)", text))
    assert used and used <= set(state), sorted(used - set(state))
    leg_fields = set(re.findall(r"\bleg\.([a-z_]+)", text))
    leg_state = set(next(iter(state["phases"].values())))
    assert leg_fields and leg_fields <= leg_state, sorted(leg_fields - leg_state)
    assert state["native_mode_name"] == "Self-Consumption"


def test_ui_command_refuses_a_bad_fleet(tmp_path, capsys):
    bad = tmp_path / "fleet.yaml"
    bad.write_text("gateways:\n  - {id: a, colour: red}\n  - {id: a}\n")
    assert main(["ui", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "gateways[0].colour: unknown key" in err and "gateways[1].id: duplicate" in err


def test_state_carries_what_the_diagram_draws(ui):
    fleet, _ = ui
    state = gateway_state(fleet["site"])
    assert state["pv_inputs"] == {"proximal": 1.0}
    assert state["apower_units"] == 1
    assert state["apower_rating"] == {"wh": 13600, "w": 5000}    # emulator constants
    assert set(state["pv_by_input"]) >= set(state["pv_inputs"])


# -- actions panel -----------------------------------------------------------------------------
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


def test_every_action_has_one_group():
    from franklinwh_emulator.schema import ACTIONS, GROUPS
    listed = [a for names in GROUPS.values() for a in names]
    assert sorted(listed) == sorted(ACTIONS) and len(listed) == len(set(listed))
    assert all(ACTIONS[a]["group"] for a in ACTIONS)


def test_page_handles_every_field_type():
    from franklinwh_emulator.schema import ACTIONS
    kinds = json.loads(re.search(r"const FIELD_KINDS = (\[.*?\]);",
                                 (STATIC / "actions.js").read_text()).group(1).replace("'", '"'))
    used = {t for a in ACTIONS.values() for f in a["fields"].values()
            for t in (f["type"] if isinstance(f["type"], list) else [f["type"]])}
    assert used <= set(kinds), sorted(used - set(kinds))


@pytest.mark.skipif(not JSC.exists(), reason="needs a JavaScript runtime (JavaScriptCore)")
def test_forms_rebuild_every_example(tmp_path):
    """Each schema example, entered as form values, builds the same request body again."""
    from franklinwh_emulator.schema import schema
    harness = tmp_path / "harness.js"
    harness.write_text((STATIC / "actions.js").read_text() + f"""
const S = {json.dumps(schema()["actions"])};
const out = [];
for (const [name, a] of Object.entries(S)) {{
  for (const ex of a.examples) {{
    let got;
    try {{ got = buildBody(a, formFromBody(a, ex)); }} catch (e) {{ got = 'ERROR ' + e.message; }}
    out.push([name, ex, got]);
  }}
}}
const bad = [];
for (const [n, f] of [['load', {{w: 'abc'}}], ['csip', {{control: '[1]'}}], ['csip', {{control: '{{'}}]]) {{
  try {{ buildBody(S[n], f); bad.push('accepted'); }} catch (e) {{ bad.push(e.message); }}
}}
print(JSON.stringify({{out, bad, blank: buildBody(S.grid, formFromBody(S.grid, {{}})), modeOpts: choiceOptions(S.mode.fields.mode)}}));
""")
    import subprocess
    res = json.loads(subprocess.run([str(JSC), str(harness)], capture_output=True, text=True,
                                    check=True).stdout)
    mismatches = [(n, ex, got) for n, ex, got in res["out"] if got != ex]
    assert not mismatches, mismatches
    assert len(res["out"]) == sum(len(a["examples"]) for a in schema()["actions"].values())
    assert res["bad"] == ["w: not a number", "control: needs a JSON object", "control: not valid JSON"]
    assert res["blank"] == {}
    assert res["modeOpts"] == [["", "— not set —"], ["1", "1 · Emergency Backup"],
                               ["2", "2 · Self-Consumption"], ["3", "3 · TOU"]]


def test_state_has_agate_status_sun_and_temperatures(ui):
    from franklinwh_emulator.clock import ManualClock
    fleet, _ = ui
    s = gateway_state(fleet["site"])
    assert s["run_status_name"] in ("Standby", "Charging", "Discharging")
    assert s["inverter_state_name"] in ("Running", "Standby", "Throttled", "Fault", "Off")
    assert "cabinet_c" in s and "ambient_c" in s
    assert gateway_state(fleet["us-split"])["sun"] is None          # no location, no sun
    noon = Fleet.from_dict(FLEET, clock=ManualClock(1768446000.0))   # 2026-01-15 14:00 Sydney
    night = Fleet.from_dict(FLEET, clock=ManualClock(1768474800.0))  # 22:00
    day_sun = gateway_state(noon["site"])["sun"]
    night_sun = gateway_state(night["site"])["sun"]
    assert day_sun["up"] and day_sun["elevation_deg"] > 60
    assert not night_sun["up"] and night_sun["elevation_deg"] < 0


def test_version_endpoint(ui):
    from franklinwh_emulator import __version__
    _, base = ui
    v = json.loads(get(base + "/version")[2])
    assert v["version"] == __version__
    assert v["commit"] is None or re.fullmatch(r"[0-9a-f]{7,12}", v["commit"])


def test_overrides_are_in_the_state(ui):
    from franklinwh_emulator.control import apply
    fleet, _ = ui
    gw = fleet["us-split"]
    assert gateway_state(gw)["overrides"] == {"solar_w": None, "load_w": None, "ambient_c": None}
    apply(gw, "pv", {"solar_w": 6000})
    s = gateway_state(gw)
    assert s["overrides"]["solar_w"] == 6000 and s["solar_w"] == pytest.approx(6000, abs=1)
    apply(gw, "pv", {"solar_w": None})
    assert gateway_state(gw)["overrides"]["solar_w"] is None
